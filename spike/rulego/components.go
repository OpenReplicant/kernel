// Components compiled into the server beyond RuleGo's standard set. Registering a
// component is a blank import; keep this list short and say why each one is here.
package main

import (
	// x/python: runs a Process(msg, metadata, msgType) function from a .py file in a
	// pre-started process, data over stdin/stdout, with a timeout. Step scripts use it
	// through pclib (docs/RULEGO_NOTES.md).
	_ "github.com/rulego/rulego-components/action/python"
)
