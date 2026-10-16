# Installed modules

`kk module install modules/<name>` writes two files here per worker module:

- `<name>.yaml`: its compose service, locked down (read-only filesystem, no capabilities, no new
  privileges, `nobody`, memory and process limits, only the internal network that reaches Postgres);
- `<name>.env`: its own database login (mode 600).

Both are specific to one deployment and are not committed. Start a module with
`docker compose -f deploy/compose.yaml -f deploy/modules/<name>.yaml --env-file deploy/.env up -d`.
