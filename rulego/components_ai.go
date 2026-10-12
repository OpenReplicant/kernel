//go:build with_ai

// RuleGo's AI components, compiled in only with `-tags with_ai` (KERNEL_RULEGO_AI=1
// tools/dev.sh up). Only two packages, not rulego-components-ai/all: ai/agent's tools (bash,
// read, write, browser) run on the host, outside the sandbox. Not used by the kernel's
// modules: ai/llm returns only the reply text, so calls that must be recorded go through the
// model_call node (docs/HARNESSES.md, docs/SPIKE_NOTES.md S5).
package main

import (
	// ai/llm (OpenAI-compatible chat), ai/createImage
	_ "github.com/rulego/rulego-components-ai/action"
	// ai/mcpClient
	_ "github.com/rulego/rulego-components-ai/mcp"
)
