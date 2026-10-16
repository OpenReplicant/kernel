# kk-rulego: RuleGo-Server v0.38.0 plus this repository's components (cmd/kk-rulego).
# Build context: modules/chain-runner-rulego. The runtime image has python3 for the x/python node.
FROM golang:1.25.14-bookworm AS build
WORKDIR /src
COPY go.mod go.sum ./
# Optional build secret "ca": a CA bundle for build machines behind a TLS-intercepting proxy.
RUN --mount=type=secret,id=ca,required=false \
    if [ -f /run/secrets/ca ]; then export SSL_CERT_FILE=/run/secrets/ca; fi; \
    go mod download
COPY cmd ./cmd
COPY components ./components
RUN CGO_ENABLED=0 go build -trimpath -ldflags "-s -w -X main.Version=0.1.0" -o /out/kk-rulego ./cmd/kk-rulego

FROM python:3.13.16-slim-bookworm
COPY --from=build /out/kk-rulego /usr/local/bin/kk-rulego
WORKDIR /data
# /config/config.conf is mounted read-only; chains are read from <data_dir>/workflows/kk/rules.
EXPOSE 9090
USER nobody
ENTRYPOINT ["kk-rulego", "-c", "/config/config.conf"]
