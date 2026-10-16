# kk-rulego: our RuleGo-Server build

RuleGo-Server **v0.38.0** (the `server/` module of github.com/rulego/rulego at commit `1378763`)
with the components the Knowledge Kernel needs. `cmd/kk-rulego/main.go` is RuleGo-Server's own
`main`; components register themselves in `init()`, so a component is enabled by a blank import.

| Component | Type | From | Why |
| --- | --- | --- | --- |
| Postgres LISTEN endpoint | `endpoint/pgNotify` | `components/pgnotify` (this repo) | `notify` triggers: kernel events instead of polling (D-32) |
| Python node | `x/python` | rulego-components `action/python` | Python steps (D-33) |

Dependencies are exactly the versions RuleGo-Server v0.38.0 pins (`go.mod`): rulego core
`972e69b`, rulego-components `1a8a042`, lib/pq v1.10.9.

## Build and test

```sh
go vet ./... && go build ./cmd/kk-rulego
KK_TEST_PG_DSN=postgres://user:pw@localhost:5432/db?sslmode=disable go test ./...   # endpoint tests need Postgres
docker build -t kk-rulego modules/chain-runner-rulego                               # image with python3
```

## Adding a component

Checked against the v0.38.0 sources; RuleGo's own guide (rulego.cc, "Endpoint extension") only
names the interfaces.

- **Node** (a step): implement `types.Node`: `Type() string`, `New() types.Node`,
  `Init(types.Config, types.Configuration) error` (read config with `maps.Map2Struct`),
  `OnMsg(types.RuleContext, types.RuleMsg)` (finish with `ctx.TellSuccess(msg)`,
  `ctx.TellFailure(msg, err)` or `ctx.TellNext(msg, "<label>")`), `Destroy()`. Register with
  `rulego.Registry.Register(&MyNode{})` in `init()`. Types of components outside RuleGo's core
  are prefixed, e.g. `x/python`; ours use `kk/`.
- **Endpoint** (a trigger): embed `impl.BaseEndpoint` and implement `endpoint.Endpoint`: `Id()`,
  `Start()`, `AddRouter(router, ...)` (the router's `from` path says what to subscribe to; call
  `CheckAndSetRouterId`), `RemoveRouter(id, ...)`, `Destroy()`, plus the node methods. For each
  incoming event build an `endpoint.Exchange` with `In`/`Out` messages implementing
  `endpoint.Message` and call `x.DoProcess(ctx, router, exchange)`. Register with
  `endpoint.Registry.Register(&MyEndpoint{})`. Type prefix: `endpoint/`. Model to read:
  rulego-components `endpoint/redis`.
- Configuration values are not run through `${global.X}` substitution for endpoints; resolve them
  in `Init` with `str.ExecuteTemplate(value, {"global": ruleConfig.Properties.Values()})` as
  `pgnotify` does, so secrets live in RuleGo's `[global]` config, never in chain files.
- Add the blank import to `cmd/kk-rulego/main.go`, a test next to the component, and a line to the
  table above. The chain decoder (`modules/chain-decoder-rulego`) is what emits the component in
  compiled chains.
