# AgensGraph v2.18.4.0 (official image, pinned by digest) + pgvector compiled
# against its own server headers. Distro package mirrors are blocked in cloud sessions, so
# the compiler comes from an image that already has gcc + musl (rust:alpine, same Alpine
# release as AgensGraph). Build with db/agensgraph/build.sh, which fetches the pgvector source.
ARG AG_IMAGE=skaiworldwide/agensgraph@sha256:1dccd703df570e20980693fe1407ec3af887ece29892bb7d69e5cd5a1176a4d7
ARG CC_IMAGE=mirror.gcr.io/library/rust:1-alpine

FROM ${AG_IMAGE} AS ag

FROM ${CC_IMAGE} AS build
COPY --from=ag /usr/local/include/postgresql /usr/local/include/postgresql
COPY pgvector /src
WORKDIR /src
# pgvector's Makefile flags (OPTFLAGS="" for portability) plus pg_config --cflags --cflags_sl.
RUN set -e; mkdir -p /out/lib /out/ext; \
    for f in src/*.c; do \
      gcc -O2 -fPIC -fno-strict-aliasing -fwrapv -fexcess-precision=standard \
          -ftree-vectorize -fassociative-math -fno-signed-zeros -fno-trapping-math -ffp-contract=fast \
          -I/usr/local/include/postgresql/server -c "$f" -o "${f%.c}.o"; \
    done; \
    gcc -shared -o /out/lib/vector.so src/*.o; \
    ver=$(sed -n "s/^default_version = '\(.*\)'/\1/p" vector.control); \
    cp sql/vector.sql "/out/ext/vector--${ver}.sql"; \
    cp sql/vector--*--*.sql vector.control /out/ext/

FROM ${AG_IMAGE}
COPY --from=build /out/lib/vector.so /usr/local/lib/postgresql/
COPY --from=build /out/ext/ /usr/local/share/postgresql/extension/
