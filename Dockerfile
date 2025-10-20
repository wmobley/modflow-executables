FROM debian:bullseye-slim

ARG MF6_VERSION=6.4.2

RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends ca-certificates wget unzip; \
    rm -rf /var/lib/apt/lists/*; \
    mkdir -p /tmp/mf6; \
    wget -q "https://github.com/MODFLOW-USGS/modflow6/releases/download/${MF6_VERSION}/mf${MF6_VERSION}_linux.zip" -O /tmp/mf6.zip; \
    unzip -q /tmp/mf6.zip -d /tmp/mf6; \
    install -m 755 /tmp/mf6/mf${MF6_VERSION}/bin/mf6 /usr/local/bin/mf6; \
    rm -rf /tmp/mf6.zip /tmp/mf6

WORKDIR /tapis

COPY --chmod=755 run.sh /tapis/run.sh

ENTRYPOINT [ "/tapis/run.sh" ]
