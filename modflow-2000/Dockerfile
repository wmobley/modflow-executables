FROM debian:bookworm-slim

ARG MODFLOW_EXECUTABLES_TAG=23.0

RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends ca-certificates curl python3 unzip; \
    rm -rf /var/lib/apt/lists/*; \
    curl -fsSL "https://github.com/MODFLOW-ORG/executables/releases/download/${MODFLOW_EXECUTABLES_TAG}/linux.zip" -o /tmp/modflow-executables-linux.zip; \
    unzip -q /tmp/modflow-executables-linux.zip -d /tmp/modflow-executables; \
    install -m 755 /tmp/modflow-executables/mf2000 /usr/local/bin/mf2000; \
    rm -rf /tmp/modflow-executables-linux.zip /tmp/modflow-executables

WORKDIR /tapis

COPY run.sh /tapis/run.sh
COPY modflow.py /tapis/modflow.py
COPY resolve_nam.py /tapis/resolve_nam.py

RUN chmod 755 /tapis/run.sh

ENTRYPOINT ["/tapis/run.sh"]
