// Package pgnotify is a RuleGo endpoint that LISTENs on Postgres channels (D-32).
//
// Every NOTIFY on a channel named in a router's "from" path becomes one rule message: data is the
// notification payload (JSON for the kernel's channels), metadata "channel" the channel name. The
// kernel sends kernel_change_set {id, status, scope, lane} on every change-set insert and apply; a
// chain reads whatever else it needs through the API under its own token, so listening grants no
// access to content.
//
// Chain DSL:
//
//	"endpoints": [{"id": "events", "type": "endpoint/pgNotify",
//	  "configuration": {"dsn": "${global.KK_LISTEN_DSN}"},
//	  "routers": [{"from": {"path": "kernel_change_set"}}]}]
//
// The dsn may use ${global.X}; it is resolved from RuleGo's [global] config, so connection secrets
// stay out of chain files. Reconnects are handled by lib/pq's Listener; notifications sent while the
// connection is down are lost (Postgres does not queue them), so chains that must not miss work
// should also have a periodic sweep.
package pgnotify

import (
	"context"
	"errors"
	"net/textproto"
	"strings"
	"sync"
	"time"

	"github.com/lib/pq"
	"github.com/rulego/rulego/api/types"
	endpointApi "github.com/rulego/rulego/api/types/endpoint"
	"github.com/rulego/rulego/endpoint"
	"github.com/rulego/rulego/endpoint/impl"
	"github.com/rulego/rulego/utils/maps"
	"github.com/rulego/rulego/utils/str"
)

// Type is the endpoint type used in chain DSL.
const Type = types.EndpointTypePrefix + "pgNotify"

var _ endpointApi.Endpoint = (*PgNotify)(nil)

func init() {
	_ = endpoint.Registry.Register(&PgNotify{})
}

// Config of the endpoint.
type Config struct {
	// Dsn is a lib/pq connection string or URL; ${global.X} is resolved from RuleGo's [global] config.
	Dsn string `json:"dsn" label:"DSN" desc:"Postgres connection string; may use ${global.X}" required:"true"`
}

// PgNotify listens on Postgres channels and routes each notification to its routers.
type PgNotify struct {
	impl.BaseEndpoint
	RuleConfig types.Config
	Config     Config
	dsn        string

	mu       sync.RWMutex
	routers  map[string][]endpointApi.Router // channel -> routers
	listener *pq.Listener
	stop     chan struct{}
}

func (x *PgNotify) Type() string { return Type }

func (x *PgNotify) Id() string { return x.dsn }

func (x *PgNotify) New() types.Node { return &PgNotify{} }

func (x *PgNotify) Def() types.ComponentForm {
	return types.ComponentForm{
		Desc: "Postgres LISTEN/NOTIFY endpoint: one rule message per notification",
		RouterForm: &types.RouterForm{From: &types.RouterFormField{Path: types.ComponentFormField{
			Name: "path", Type: "string", Label: "Channels", Desc: "Comma-separated channel names", Required: true}}},
	}
}

// Init reads the configuration and resolves ${global.X} in the DSN.
func (x *PgNotify) Init(ruleConfig types.Config, configuration types.Configuration) error {
	x.RuleConfig = ruleConfig
	if err := maps.Map2Struct(configuration, &x.Config); err != nil {
		return err
	}
	globals := map[string]string{}
	if ruleConfig.Properties != nil {
		globals = ruleConfig.Properties.Values()
	}
	x.dsn = str.ExecuteTemplate(x.Config.Dsn, map[string]interface{}{types.Global: globals})
	if strings.TrimSpace(x.dsn) == "" {
		return errors.New("pgNotify: dsn is required")
	}
	x.routers = map[string][]endpointApi.Router{}
	return nil
}

// Start opens the listener connection and LISTENs on every channel routed so far.
func (x *PgNotify) Start() error {
	x.mu.Lock()
	defer x.mu.Unlock()
	if x.listener != nil {
		return nil
	}
	x.listener = pq.NewListener(x.dsn, 2*time.Second, time.Minute, func(ev pq.ListenerEventType, err error) {
		if err != nil {
			x.Printf("pgNotify: listener event %d: %v", ev, err)
		}
	})
	for channel := range x.routers {
		if err := x.listener.Listen(channel); err != nil {
			return err
		}
	}
	x.stop = make(chan struct{})
	go x.loop(x.listener, x.stop)
	return nil
}

func (x *PgNotify) loop(l *pq.Listener, stop chan struct{}) {
	for {
		select {
		case <-stop:
			return
		case n := <-l.Notify:
			if n == nil { // reconnected: notifications in between are lost
				continue
			}
			x.handle(n.Channel, n.Extra)
		case <-time.After(90 * time.Second):
			go func() { _ = l.Ping() }()
		}
	}
}

func (x *PgNotify) handle(channel, payload string) {
	x.mu.RLock()
	routers := append([]endpointApi.Router(nil), x.routers[channel]...)
	x.mu.RUnlock()
	for _, router := range routers {
		exchange := &endpointApi.Exchange{
			In:  &Message{channel: channel, body: []byte(payload)},
			Out: &Message{channel: channel},
		}
		x.DoProcess(context.Background(), router, exchange)
	}
}

// AddRouter routes the channels in router.From (comma-separated) and LISTENs if already started.
func (x *PgNotify) AddRouter(router endpointApi.Router, params ...interface{}) (string, error) {
	if router == nil {
		return "", errors.New("router cannot be nil")
	}
	id := x.CheckAndSetRouterId(router)
	x.mu.Lock()
	defer x.mu.Unlock()
	for _, channel := range strings.Split(router.GetFrom().ToString(), ",") {
		channel = strings.TrimSpace(channel)
		if channel == "" {
			continue
		}
		_, known := x.routers[channel]
		x.routers[channel] = append(x.routers[channel], router)
		if !known && x.listener != nil {
			if err := x.listener.Listen(channel); err != nil {
				return id, err
			}
		}
	}
	return id, nil
}

// RemoveRouter drops a router and UNLISTENs channels nobody routes any more.
func (x *PgNotify) RemoveRouter(routerId string, params ...interface{}) error {
	x.mu.Lock()
	defer x.mu.Unlock()
	for channel, routers := range x.routers {
		kept := routers[:0]
		for _, r := range routers {
			if r.GetId() != routerId {
				kept = append(kept, r)
			}
		}
		if len(kept) > 0 {
			x.routers[channel] = kept
			continue
		}
		delete(x.routers, channel)
		if x.listener != nil {
			_ = x.listener.Unlisten(channel)
		}
	}
	return nil
}

// Destroy stops listening and closes the connection.
func (x *PgNotify) Destroy() {
	x.mu.Lock()
	if x.stop != nil {
		close(x.stop)
		x.stop = nil
	}
	if x.listener != nil {
		_ = x.listener.Close()
		x.listener = nil
	}
	x.mu.Unlock()
	x.BaseEndpoint.Destroy()
}

// Message is one notification (In) or the unused reply (Out).
type Message struct {
	channel string
	body    []byte
	msg     *types.RuleMsg
	err     error
}

func (m *Message) Body() []byte                  { return m.body }
func (m *Message) Headers() textproto.MIMEHeader { return textproto.MIMEHeader{} }
func (m *Message) From() string                  { return m.channel }
func (m *Message) GetParam(key string) string    { return "" }
func (m *Message) SetMsg(msg *types.RuleMsg)     { m.msg = msg }
func (m *Message) SetStatusCode(statusCode int)  {}
func (m *Message) SetBody(body []byte)           { m.body = body }
func (m *Message) SetError(err error)            { m.err = err }
func (m *Message) GetError() error               { return m.err }

// GetMsg builds the rule message: data is the payload (JSON), metadata "channel" the channel.
func (m *Message) GetMsg() *types.RuleMsg {
	if m.msg == nil {
		meta := types.NewMetadata()
		meta.PutValue("channel", m.channel)
		msg := types.NewMsg(0, m.channel, types.JSON, meta, string(m.body))
		m.msg = &msg
	}
	return m.msg
}
