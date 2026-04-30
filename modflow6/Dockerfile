FROM debian:bookworm-slim

ARG MF6_VERSION=6.6.3

RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends ca-certificates curl python3 wget unzip; \
    rm -rf /var/lib/apt/lists/*; \
    mkdir -p /tmp/mf6; \
    MF6_URL="https://github.com/MODFLOW-ORG/modflow6/releases/download/${MF6_VERSION}/mf${MF6_VERSION}_linux.zip"; \
    wget -q "$MF6_URL" -O /tmp/mf6.zip; \
    unzip -q /tmp/mf6.zip -d /tmp/mf6; \
    MF6_BIN="$(find /tmp/mf6 -maxdepth 3 -type f -name mf6 -print -quit)"; \
    test -n "$MF6_BIN"; \
    install -m 755 "$MF6_BIN" /usr/local/bin/mf6; \
    rm -rf /tmp/mf6.zip /tmp/mf6

WORKDIR /tapis

COPY run.sh /tapis/run.sh
COPY modflow.py /tapis/modflow.py
COPY resolve_sim_nam.py /tapis/resolve_sim_nam.py

RUN chmod 755 /tapis/run.sh

ENTRYPOINT [ "/tapis/run.sh" ]
