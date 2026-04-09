-- Immich Asset Index: Claude Vision-generated metadata for photo search
-- Target database: pif (port 5432)

CREATE TABLE IF NOT EXISTS immich_asset_index (
    asset_id        UUID PRIMARY KEY,
    indexed_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    immich_updated_at TIMESTAMPTZ NOT NULL,
    model_version   TEXT NOT NULL,

    -- Immich ground-truth metadata
    taken_at        TIMESTAMPTZ,
    is_video        BOOLEAN NOT NULL DEFAULT false,
    original_filename TEXT,
    user_description TEXT,
    user_tags       TEXT[],
    albums          TEXT[],
    people          TEXT[],
    city            TEXT,
    country         TEXT,
    gps_lat         DOUBLE PRECISION,
    gps_lon         DOUBLE PRECISION,
    camera          TEXT,

    -- Claude Vision analysis
    description     TEXT,
    visual_tags     TEXT[],
    objects         TEXT[],
    scene_type      TEXT,
    people_count    INT,
    people_desc     TEXT,
    activities      TEXT[],
    text_content    TEXT,
    dominant_colors TEXT[],
    mood            TEXT,
    time_of_day     TEXT,
    season_hint     TEXT,
    location_hints  TEXT[],

    -- Full-text search vector (maintained by trigger)
    fts_vector      TSVECTOR
);

-- Trigger to maintain fts_vector on insert/update
CREATE OR REPLACE FUNCTION immich_asset_index_fts_trigger() RETURNS trigger AS $$
BEGIN
    NEW.fts_vector := to_tsvector('english',
        coalesce(NEW.description, '')                              || ' ' ||
        coalesce(NEW.user_description, '')                         || ' ' ||
        coalesce(array_to_string(NEW.people, ' '), '')             || ' ' ||
        coalesce(array_to_string(NEW.albums, ' '), '')             || ' ' ||
        coalesce(array_to_string(NEW.user_tags, ' '), '')          || ' ' ||
        coalesce(array_to_string(NEW.visual_tags, ' '), '')        || ' ' ||
        coalesce(array_to_string(NEW.activities, ' '), '')         || ' ' ||
        coalesce(NEW.text_content, '')                             || ' ' ||
        coalesce(NEW.city, '')                                     || ' ' ||
        coalesce(NEW.country, '')
    );
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_immich_asset_index_fts ON immich_asset_index;
CREATE TRIGGER trg_immich_asset_index_fts
    BEFORE INSERT OR UPDATE ON immich_asset_index
    FOR EACH ROW EXECUTE FUNCTION immich_asset_index_fts_trigger();

-- Indexes
CREATE INDEX IF NOT EXISTS idx_immich_fts           ON immich_asset_index USING GIN (fts_vector);
CREATE INDEX IF NOT EXISTS idx_immich_people         ON immich_asset_index USING GIN (people);
CREATE INDEX IF NOT EXISTS idx_immich_albums         ON immich_asset_index USING GIN (albums);
CREATE INDEX IF NOT EXISTS idx_immich_user_tags      ON immich_asset_index USING GIN (user_tags);
CREATE INDEX IF NOT EXISTS idx_immich_visual_tags    ON immich_asset_index USING GIN (visual_tags);
CREATE INDEX IF NOT EXISTS idx_immich_activities     ON immich_asset_index USING GIN (activities);
CREATE INDEX IF NOT EXISTS idx_immich_taken          ON immich_asset_index (taken_at);
CREATE INDEX IF NOT EXISTS idx_immich_updated        ON immich_asset_index (immich_updated_at);
CREATE INDEX IF NOT EXISTS idx_immich_geo            ON immich_asset_index (gps_lat, gps_lon);
CREATE INDEX IF NOT EXISTS idx_immich_people_count   ON immich_asset_index (people_count);

-- Read-only access for MCP
GRANT SELECT ON immich_asset_index TO mcp_readonly;
