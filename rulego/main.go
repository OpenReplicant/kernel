// RuleGo-Server entry point for this repo. Upstream's cmd/server/main.go (v0.38.0) without
// the AI-security hook; build-time additions (components, aspects, OTel) live in
// components.go and are the only reason this file exists. See ../README.md.
package main

import (
	"flag"
	"fmt"
	"os"

	"github.com/rulego/rulego/server/bootstrap"
)

var (
	configFile = flag.String("c", "config.conf", "config file path")
	version    = flag.Bool("v", false, "print version")
)

var (
	Version   = "dev"
	BuildTime = "unknown"
)

func main() {
	flag.Parse()
	if *version {
		fmt.Printf("kernel-rulego %s (built %s)\n", Version, BuildTime)
		return
	}
	if err := bootstrap.Run(bootstrap.DefaultApp(*configFile)); err != nil {
		fmt.Fprintf(os.Stderr, "server error: %v\n", err)
		os.Exit(1)
	}
}
