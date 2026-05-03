"""Immich photo indexer: Claude Vision analysis → PostgreSQL + Meilisearch.

Replaces Immich CLIP search with rich structured metadata.
Designed to run as a Cronicle job via: python -m mcp_search.immich_indexer
"""

import asyncio
import base64
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import anthropic
import asyncpg
import httpx

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

IMMICH_URL = os.environ.get("IMMICH_URL", "http://immich-server:2283")
IMMICH_KEY = os.environ["IMMICH_API_KEY"]

ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]
CLAUDE_MODEL = os.environ.get("CLAUDE_MODEL", "claude-haiku-4-5-20251001")

POSTGRES_HOST = os.environ.get("POSTGRES_HOST", "postgres")
POSTGRES_PORT = int(os.environ.get("POSTGRES_PORT", "5432"))
POSTGRES_USER = os.environ.get("POSTGRES_USER", "pif")
POSTGRES_PASSWORD = os.environ["POSTGRES_PASSWORD"]
POSTGRES_DB = os.environ.get("POSTGRES_DB", "pif")

MEILI_URL = os.environ.get("MEILISEARCH_URL", "http://meilisearch-docs:7700")
MEILI_KEY = os.environ.get("MEILISEARCH_KEY", "")

MEILI_INDEX = "immich_photos"
BATCH_SIZE = 10  # concurrent Claude calls per batch
BATCH_DELAY = 0.5  # seconds between batches
MAX_PER_RUN = int(os.environ.get("MAX_PER_RUN", "500"))

STATE_DIR = os.environ.get("STATE_DIR", "/state")

PROMPT_TEMPLATE = (Path(__file__).parent / "prompts" / "immich_index.txt").read_text()

# ---------------------------------------------------------------------------
# Immich API helpers
# ---------------------------------------------------------------------------


async def fetch_immich_asset(client: httpx.AsyncClient, asset_id: str) -> dict:
    """Fetch full metadata for an Immich asset (asset + albums)."""
    asset_resp, albums_resp = await asyncio.gather(
        client.get(f"{IMMICH_URL}/api/assets/{asset_id}"),
        client.get(f"{IMMICH_URL}/api/albums", params={"assetId": asset_id}),
    )
    asset_resp.raise_for_status()
    albums_resp.raise_for_status()

    asset = asset_resp.json()
    albums = albums_resp.json()
    exif = asset.get("exifInfo") or {}

    people_raw = asset.get("people") or []
    named = [p["name"] for p in people_raw if p.get("name") and p["name"].strip()]
    unnamed_count = sum(1 for p in people_raw if not p.get("name") or not p["name"].strip())

    return {
        "asset_id": asset["id"],
        "immich_updated_at": asset["updatedAt"],
        "taken_at": asset.get("fileCreatedAt"),
        "is_video": asset.get("type") == "VIDEO",
        "original_filename": asset.get("originalFileName"),
        "user_description": asset.get("exifInfo", {}).get("description") or "",
        "user_tags": [t.get("value") or t.get("name") or "" for t in asset.get("tags") or []],
        "albums": [a["albumName"] for a in albums],
        "people": named,
        "unnamed_face_count": unnamed_count,
        "city": exif.get("city"),
        "country": exif.get("country"),
        "gps_lat": exif.get("latitude"),
        "gps_lon": exif.get("longitude"),
        "camera": " ".join(filter(None, [exif.get("make"), exif.get("model")])),
    }


async def fetch_thumbnail(client: httpx.AsyncClient, asset_id: str) -> bytes:
    """Fetch preview-size thumbnail from Immich."""
    resp = await client.get(
        f"{IMMICH_URL}/api/assets/{asset_id}/thumbnail",
        params={"size": "preview"},
    )
    resp.raise_for_status()
    return resp.content


async def fetch_changed_assets(
    client: httpx.AsyncClient, updated_after: str | None, page: int = 1, size: int = 1000
) -> list[dict]:
    """List Immich assets, optionally filtered by updatedAfter."""
    params: dict = {"page": page, "size": size, "order": "desc"}
    if updated_after:
        params["updatedAfter"] = updated_after
    resp = await client.get(f"{IMMICH_URL}/api/timeline/buckets", params=params)
    # Use search/metadata for listing — more flexible
    body: dict = {"page": page, "size": size, "order": "desc"}
    if updated_after:
        body["updatedAfter"] = updated_after
    resp = await client.post(f"{IMMICH_URL}/api/search/metadata", json=body)
    resp.raise_for_status()
    data = resp.json()
    return data.get("assets", {}).get("items", [])


# ---------------------------------------------------------------------------
# Claude Vision
# ---------------------------------------------------------------------------


def build_prompt(meta: dict) -> str:
    """Build the Claude prompt with ground-truth metadata."""
    summary = {
        "known_people": meta["people"] or "none identified yet",
        "unnamed_faces": meta["unnamed_face_count"],
        "albums": meta["albums"] or [],
        "user_tags": meta["user_tags"] or [],
        "user_description": meta["user_description"] or None,
        "location": {
            "city": meta["city"],
            "country": meta["country"],
            "has_gps": meta["gps_lat"] is not None,
        },
        "camera": meta["camera"],
        "taken_at": meta["taken_at"],
        "is_video": meta["is_video"],
        "filename": meta["original_filename"],
    }
    return PROMPT_TEMPLATE.replace("{metadata_json}", json.dumps(summary, indent=2, default=str))


def parse_claude_json(text: str) -> dict | None:
    """Parse JSON from Claude response, stripping markdown fences if present."""
    import re
    text = text.strip()
    # Strip markdown heading preamble (e.g. "# Photo Description\n\n")
    text = re.sub(r"^#+\s*[A-Za-z ]*Description\s*\n+", "", text)
    # Strip markdown fences
    if text.startswith("```"):
        lines = text.split("\n")
        lines = [line for line in lines if not line.strip().startswith("```")]
        text = "\n".join(lines).strip()
    # Try to extract JSON object from mixed content
    m = re.search(r"\{[\s\S]*\}", text)
    if m:
        try:
            result = json.loads(m.group())
            # Clean description field of markdown noise
            if "description" in result and isinstance(result["description"], str):
                result["description"] = re.sub(
                    r"^#+\s*[A-Za-z ]*[Dd]escription\s*\n+", "", result["description"]
                ).strip().rstrip("`").strip()
            return result
        except json.JSONDecodeError:
            pass
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


async def analyse_image(
    claude: anthropic.Anthropic, image_bytes: bytes, meta: dict
) -> dict | None:
    """Submit image to Claude Vision for analysis."""
    b64 = base64.standard_b64encode(image_bytes).decode()
    prompt = build_prompt(meta)

    response = claude.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=1000,
        messages=[{
            "role": "user",
            "content": [
                {
                    "type": "image",
                    "source": {"type": "base64", "media_type": "image/jpeg", "data": b64},
                },
                {"type": "text", "text": prompt},
            ],
        }],
    )

    return parse_claude_json(response.content[0].text)


# ---------------------------------------------------------------------------
# PostgreSQL upsert
# ---------------------------------------------------------------------------

UPSERT_SQL = """
INSERT INTO immich_asset_index (
    asset_id, indexed_at, immich_updated_at, model_version,
    taken_at, is_video, original_filename, user_description,
    user_tags, albums, people, city, country, gps_lat, gps_lon, camera,
    description, visual_tags, objects, scene_type,
    people_count, people_desc, activities, text_content,
    dominant_colors, mood, time_of_day, season_hint, location_hints
) VALUES (
    $1, now(), $2, $3,
    $4, $5, $6, $7,
    $8, $9, $10, $11, $12, $13, $14, $15,
    $16, $17, $18, $19,
    $20, $21, $22, $23,
    $24, $25, $26, $27, $28
) ON CONFLICT (asset_id) DO UPDATE SET
    indexed_at = now(),
    immich_updated_at = EXCLUDED.immich_updated_at,
    model_version = EXCLUDED.model_version,
    taken_at = EXCLUDED.taken_at,
    is_video = EXCLUDED.is_video,
    original_filename = EXCLUDED.original_filename,
    user_description = EXCLUDED.user_description,
    user_tags = EXCLUDED.user_tags,
    albums = EXCLUDED.albums,
    people = EXCLUDED.people,
    city = EXCLUDED.city,
    country = EXCLUDED.country,
    gps_lat = EXCLUDED.gps_lat,
    gps_lon = EXCLUDED.gps_lon,
    camera = EXCLUDED.camera,
    description = EXCLUDED.description,
    visual_tags = EXCLUDED.visual_tags,
    objects = EXCLUDED.objects,
    scene_type = EXCLUDED.scene_type,
    people_count = EXCLUDED.people_count,
    people_desc = EXCLUDED.people_desc,
    activities = EXCLUDED.activities,
    text_content = EXCLUDED.text_content,
    dominant_colors = EXCLUDED.dominant_colors,
    mood = EXCLUDED.mood,
    time_of_day = EXCLUDED.time_of_day,
    season_hint = EXCLUDED.season_hint,
    location_hints = EXCLUDED.location_hints
"""


async def upsert_index(db: asyncpg.Connection, meta: dict, claude: dict) -> None:
    """Upsert a single asset into immich_asset_index."""
    taken_at = None
    if meta["taken_at"]:
        try:
            taken_at = datetime.fromisoformat(meta["taken_at"].replace("Z", "+00:00"))
        except (ValueError, AttributeError):
            pass

    immich_updated = datetime.fromisoformat(
        meta["immich_updated_at"].replace("Z", "+00:00")
    )

    await db.execute(
        UPSERT_SQL,
        meta["asset_id"],
        immich_updated,
        CLAUDE_MODEL,
        taken_at,
        meta["is_video"],
        meta["original_filename"],
        meta["user_description"] or None,
        meta["user_tags"] or [],
        meta["albums"] or [],
        meta["people"] or [],
        meta["city"],
        meta["country"],
        meta["gps_lat"],
        meta["gps_lon"],
        meta["camera"] or None,
        claude.get("description"),
        claude.get("visual_tags") or [],
        claude.get("objects") or [],
        claude.get("scene_type"),
        claude.get("people_count") or 0,
        claude.get("people_desc"),
        claude.get("activities") or [],
        claude.get("text_content"),
        claude.get("dominant_colors") or [],
        claude.get("mood"),
        claude.get("time_of_day"),
        claude.get("season_hint"),
        claude.get("location_hints") or [],
    )


# ---------------------------------------------------------------------------
# Meilisearch sync
# ---------------------------------------------------------------------------


async def ensure_meili_index(meili: httpx.AsyncClient) -> None:
    """Create or update the Meilisearch immich_photos index."""
    resp = await meili.post(
        f"{MEILI_URL}/indexes",
        json={"uid": MEILI_INDEX, "primaryKey": "asset_id"},
    )
    if resp.status_code not in (200, 202, 409):
        resp.raise_for_status()
    if resp.status_code == 202:
        task = resp.json()
        try:
            await _wait_meili_task(meili, task["taskUid"])
        except RuntimeError as e:
            if "index_already_exists" not in str(e):
                raise

    settings = {
        "searchableAttributes": [
            "people", "description", "albums", "user_tags",
            "activities", "visual_tags", "city", "country", "text_content",
        ],
        "filterableAttributes": [
            "people", "albums", "user_tags", "visual_tags", "activities",
            "city", "country", "scene_type", "season_hint",
            "is_video", "people_count", "camera",
        ],
        "sortableAttributes": ["taken_at"],
        "displayedAttributes": ["*"],
    }
    resp = await meili.patch(f"{MEILI_URL}/indexes/{MEILI_INDEX}/settings", json=settings)
    if resp.status_code == 202:
        await _wait_meili_task(meili, resp.json()["taskUid"])


async def _wait_meili_task(meili: httpx.AsyncClient, task_uid: int, timeout: int = 300) -> None:
    """Wait for a Meilisearch task to complete."""
    start = time.time()
    while time.time() - start < timeout:
        resp = await meili.get(f"{MEILI_URL}/tasks/{task_uid}")
        resp.raise_for_status()
        task = resp.json()
        status = task.get("status")
        if status == "succeeded":
            return
        if status == "failed":
            raise RuntimeError(f"Meilisearch task {task_uid} failed: {task.get('error')}")
        await asyncio.sleep(1)
    raise TimeoutError(f"Meilisearch task {task_uid} timed out after {timeout}s")


def _build_meili_doc(meta: dict, claude: dict) -> dict:
    """Build a Meilisearch document from metadata + Claude analysis."""
    return {
        "asset_id": meta["asset_id"],
        "taken_at": meta["taken_at"],
        "is_video": meta["is_video"],
        "original_filename": meta["original_filename"],
        "user_description": meta["user_description"],
        "user_tags": meta["user_tags"] or [],
        "albums": meta["albums"] or [],
        "people": meta["people"] or [],
        "city": meta["city"],
        "country": meta["country"],
        "camera": meta["camera"],
        "description": claude.get("description"),
        "visual_tags": claude.get("visual_tags") or [],
        "objects": claude.get("objects") or [],
        "scene_type": claude.get("scene_type"),
        "people_count": claude.get("people_count") or 0,
        "activities": claude.get("activities") or [],
        "text_content": claude.get("text_content"),
        "dominant_colors": claude.get("dominant_colors") or [],
        "mood": claude.get("mood"),
        "time_of_day": claude.get("time_of_day"),
        "season_hint": claude.get("season_hint"),
        "location_hints": claude.get("location_hints") or [],
    }


async def sync_to_meilisearch(
    meili: httpx.AsyncClient, docs: list[dict]
) -> None:
    """Batch upload documents to Meilisearch."""
    if not docs:
        return
    resp = await meili.post(
        f"{MEILI_URL}/indexes/{MEILI_INDEX}/documents",
        json=docs,
    )
    resp.raise_for_status()
    await _wait_meili_task(meili, resp.json()["taskUid"])


# ---------------------------------------------------------------------------
# Core indexing pipeline
# ---------------------------------------------------------------------------


async def index_asset(
    immich: httpx.AsyncClient,
    claude_client: anthropic.Anthropic,
    pool: asyncpg.Pool,
    asset_id: str,
    force: bool = False,
) -> tuple[str, dict | None]:
    """Index a single Immich asset.

    Returns (status, meili_doc) where status is 'indexed', 'skipped', or 'error'.
    meili_doc is set only when status == 'indexed'.
    """
    try:
        meta = await fetch_immich_asset(immich, asset_id)

        async with pool.acquire() as db:
            if not force:
                existing = await db.fetchrow(
                    "SELECT immich_updated_at FROM immich_asset_index WHERE asset_id = $1",
                    meta["asset_id"],
                )
                if existing:
                    immich_ts = datetime.fromisoformat(
                        meta["immich_updated_at"].replace("Z", "+00:00")
                    )
                    if existing["immich_updated_at"] >= immich_ts:
                        return "skipped", None

            # Fetch thumbnail
            thumb = await fetch_thumbnail(immich, asset_id)

            # Analyse with Claude
            analysis = await analyse_image(claude_client, thumb, meta)
            if not analysis:
                print(f"  WARNING: Claude returned unparseable JSON for {asset_id}")
                return "error", None

            # Upsert to PostgreSQL
            await upsert_index(db, meta, analysis)

        return "indexed", _build_meili_doc(meta, analysis)

    except Exception as e:
        print(f"  ERROR indexing {asset_id}: {e}")
        return "error", None


# ---------------------------------------------------------------------------
# State management
# ---------------------------------------------------------------------------


def _state_path() -> Path:
    return Path(STATE_DIR) / "immich_indexer_state.json"


def _load_state() -> dict:
    p = _state_path()
    if p.exists():
        return json.loads(p.read_text())
    return {}


def _save_state(state: dict) -> None:
    p = _state_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(state, indent=2))


# ---------------------------------------------------------------------------
# Bulk indexer job (Cronicle entry point)
# ---------------------------------------------------------------------------


async def run_indexer_job():
    """Main entry point: detect changed assets, index them."""
    state = _load_state()
    last_run = state.get("last_immich_updated")

    immich = httpx.AsyncClient(
        headers={"x-api-key": IMMICH_KEY},
        timeout=60.0,
    )
    meili = httpx.AsyncClient(
        headers={"Authorization": f"Bearer {MEILI_KEY}"},
        timeout=60.0,
    )
    claude_client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    pool = await asyncpg.create_pool(
        host=POSTGRES_HOST,
        port=POSTGRES_PORT,
        user=POSTGRES_USER,
        password=POSTGRES_PASSWORD,
        database=POSTGRES_DB,
        min_size=1,
        max_size=BATCH_SIZE,
    )

    try:
        print("Ensuring Meilisearch index and settings...")
        await ensure_meili_index(meili)

        # Find candidates: all assets from Immich, compare against our index
        print(f"Fetching assets from Immich (updated after: {last_run or 'full scan'})...")
        all_candidates = []
        page = 1
        while len(all_candidates) < MAX_PER_RUN:
            assets = await fetch_changed_assets(immich, last_run, page=page, size=200)
            if not assets:
                break
            all_candidates.extend(assets)
            page += 1

        if not all_candidates:
            print("No assets to index")
            return

        print(f"Found {len(all_candidates)} candidate assets, processing up to {MAX_PER_RUN}...")
        candidates = all_candidates[:MAX_PER_RUN]

        results = {"indexed": 0, "skipped": 0, "error": 0}
        max_updated = last_run

        for i in range(0, len(candidates), BATCH_SIZE):
            batch = candidates[i:i + BATCH_SIZE]
            outcomes = await asyncio.gather(
                *[
                    index_asset(immich, claude_client, pool, a["id"])
                    for a in batch
                ],
                return_exceptions=True,
            )

            # Collect Meilisearch docs from successful indexes
            meili_docs = []
            for j, outcome in enumerate(outcomes):
                if isinstance(outcome, Exception):
                    print(f"  EXCEPTION: {outcome}")
                    results["error"] += 1
                else:
                    status, meili_doc = outcome
                    results[status] += 1
                    if meili_doc:
                        meili_docs.append(meili_doc)

                # Track max updatedAt for state
                asset_updated = batch[j].get("updatedAt")
                if asset_updated and (not max_updated or asset_updated > max_updated):
                    max_updated = asset_updated

            # Batch sync to Meilisearch
            if meili_docs:
                try:
                    await sync_to_meilisearch(meili, meili_docs)
                except Exception as e:
                    print(f"  WARNING: Meilisearch batch sync failed: {e}")

            processed = min(i + BATCH_SIZE, len(candidates))
            print(f"  Progress: {processed}/{len(candidates)} "
                  f"(indexed={results['indexed']}, skipped={results['skipped']}, errors={results['error']})")

            if i + BATCH_SIZE < len(candidates):
                await asyncio.sleep(BATCH_DELAY)

        # Save state
        if max_updated:
            state["last_immich_updated"] = max_updated
        state["last_run"] = datetime.now(timezone.utc).isoformat()
        state["last_results"] = results
        _save_state(state)

        print(f"\nIndexing complete: {results}")

    finally:
        await pool.close()
        await immich.aclose()
        await meili.aclose()


def main():
    asyncio.run(run_indexer_job())


if __name__ == "__main__":
    main()
