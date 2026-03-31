FROM node:22-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
        socat \
        sqlite3 \
    && rm -rf /var/lib/apt/lists/* \
    && npm install -g joplin@latest \
    && npm cache clean --force

COPY joplin-cli-entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

EXPOSE 41184

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
