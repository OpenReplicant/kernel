package pgnotify

import (
	"database/sql"
	"os"
	"testing"
	"time"

	"github.com/rulego/rulego"
	endpointApi "github.com/rulego/rulego/api/types/endpoint"
	"github.com/rulego/rulego/endpoint"
)

// Needs a Postgres the test may connect to, e.g.
// KK_TEST_PG_DSN=postgres://postgres:postgres@localhost:5432/postgres?sslmode=disable go test ./...
func TestNotificationsReachTheRouterUntilRemoved(t *testing.T) {
	dsn := os.Getenv("KK_TEST_PG_DSN")
	if dsn == "" {
		t.Skip("KK_TEST_PG_DSN not set")
	}
	config := rulego.NewConfig()
	config.Properties.PutValue("PG_DSN", dsn)

	ep := &PgNotify{}
	if err := ep.Init(config, map[string]interface{}{"dsn": "${global.PG_DSN}"}); err != nil {
		t.Fatal(err)
	}
	got := make(chan [2]string, 4)
	router := endpoint.NewRouter().From("kk_pgnotify_test").Process(func(_ endpointApi.Router, ex *endpointApi.Exchange) bool {
		msg := ex.In.GetMsg()
		got <- [2]string{msg.Metadata.GetValue("channel"), msg.GetData()}
		return true
	}).End()
	id, err := ep.AddRouter(router)
	if err != nil {
		t.Fatal(err)
	}
	if err := ep.Start(); err != nil {
		t.Fatal(err)
	}
	defer ep.Destroy()

	db, err := sql.Open("postgres", dsn)
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()
	notify := func(payload string) {
		if _, err := db.Exec("select pg_notify('kk_pgnotify_test', $1)", payload); err != nil {
			t.Fatal(err)
		}
	}

	time.Sleep(500 * time.Millisecond) // LISTEN is issued asynchronously by the listener
	notify(`{"id":"cs-1","status":"applied"}`)
	select {
	case m := <-got:
		if m[0] != "kk_pgnotify_test" || m[1] != `{"id":"cs-1","status":"applied"}` {
			t.Fatalf("unexpected message %v", m)
		}
	case <-time.After(5 * time.Second):
		t.Fatal("notification not delivered")
	}

	if err := ep.RemoveRouter(id); err != nil {
		t.Fatal(err)
	}
	notify(`{"id":"cs-2"}`)
	select {
	case m := <-got:
		t.Fatalf("delivered after the router was removed: %v", m)
	case <-time.After(time.Second):
	}
}

func TestInitRequiresDsn(t *testing.T) {
	if err := (&PgNotify{}).Init(rulego.NewConfig(), map[string]interface{}{}); err == nil {
		t.Fatal("expected an error without a dsn")
	}
}
