# Installed modules

`kk module install modules/<name>` writes two files here per worker or command module:

- `<name>.yaml`: its compose service, locked down (read-only filesystem, no capabilities, no new
  privileges, `nobody`, memory and process limits, only the internal network that reaches Postgres);
- `<name>.env`: its own database login (mode 600).

Both are specific to one deployment and are not committed. Start a module with
`docker compose -f deploy/compose.yaml -f deploy/modules/<name>.yaml --env-file deploy/.env up -d`.

A command module (e.g. the event-log encoder) is one-shot, in the compose profile `run`; it reads its
input from the folder in `KK_INPUT`, mounted read-only at `/input`:
`KK_INPUT=$PWD/examples/order-to-cash docker compose -f deploy/compose.yaml -f deploy/modules/encoder-eventlog.yaml --env-file deploy/.env run --rm module-encoder-eventlog --process urn:kk:process:order-to-cash /input/order-to-cash.csv`.
