// Command kk-rulego is RuleGo-Server v0.38.0 with the components the Knowledge Kernel needs:
// the x/python node from rulego-components (D-33) and this repository's own components
// (components/...). It is RuleGo-Server's own main; components register themselves in init().
package main

import (
	"flag"
	"fmt"
	"os"

	"github.com/rulego/rulego/server/bootstrap"

	_ "github.com/openreplicant/kernel/modules/chain-runner-rulego/components/pgnotify"
	_ "github.com/rulego/rulego-components/action/python"
)

var (
	configFile = flag.String("c", "config.conf", "config file")
	version    = flag.Bool("v", false, "print the version")

	Version   = "dev"
	BuildTime = "unknown"
)

func main() {
	flag.Parse()
	if *version {
		fmt.Printf("kk-rulego %s (RuleGo-Server v0.38.0; built %s)\n", Version, BuildTime)
		return
	}
	if err := bootstrap.Run(bootstrap.DefaultApp(*configFile)); err != nil {
		fmt.Fprintf(os.Stderr, "server error: %v\n", err)
		os.Exit(1)
	}
}
