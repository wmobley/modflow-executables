FROM debian:bookworm-slim AS mf96-builder

ARG MODFLOW96_SOURCE_URL=https://water.usgs.gov/software/code/ground_water/modflow/UNIX/modflw96.3_3.source.tar.gz

RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends ca-certificates curl gfortran make; \
    rm -rf /var/lib/apt/lists/*; \
    curl -fsSL "$MODFLOW96_SOURCE_URL" -o /tmp/modflw96.3_3.source.tar.gz; \
    tar -xzf /tmp/modflw96.3_3.source.tar.gz -C /tmp; \
    make -C /tmp/modflw96.3_3/src \
        FC=gfortran \
        F77=gfortran \
        "FFLAGS=-std=legacy -fallow-argument-mismatch -O2" \
        LDFLAGS= \
        BINDIR=/tmp/mf96-bin; \
    install -m 755 /tmp/modflw96.3_3/bin/modflw96 /usr/local/bin/mf96

FROM debian:bookworm-slim

RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends ca-certificates libgfortran5 python3 unzip; \
    rm -rf /var/lib/apt/lists/*

WORKDIR /tapis

COPY run.sh /tapis/run.sh
COPY modflow.py /tapis/modflow.py
COPY resolve_nam.py /tapis/resolve_nam.py
COPY --from=mf96-builder /usr/local/bin/mf96 /usr/local/bin/mf96

RUN chmod 755 /tapis/run.sh /usr/local/bin/mf96

ENTRYPOINT ["/tapis/run.sh"]
