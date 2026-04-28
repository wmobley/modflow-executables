FROM debian:bookworm-slim

RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends ca-certificates python3 unzip; \
    rm -rf /var/lib/apt/lists/*

WORKDIR /tapis

COPY run.sh /tapis/run.sh
COPY modflow.py /tapis/modflow.py
COPY resolve_nam.py /tapis/resolve_nam.py

RUN chmod 755 /tapis/run.sh && \
    printf '%s\n' \
    '#!/bin/sh' \
    'echo "MODFLOW-96 executable is not installed in this image yet." >&2' \
    'echo "Provide a vetted MODFLOW-96 Linux executable and update modflow-96/Dockerfile." >&2' \
    'exit 1' \
    > /usr/local/bin/mf96 && \
    chmod 755 /usr/local/bin/mf96

ENTRYPOINT ["/tapis/run.sh"]
