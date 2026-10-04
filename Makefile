# World Model Kernel. Run `make help` for the targets.
COMPOSE ?= docker compose
WMK_DB_PORT ?= 5432
WMK_DB_PASSWORD ?= postgres
export WMK_ADMIN_DSN ?= postgresql://postgres:$(WMK_DB_PASSWORD)@localhost:$(WMK_DB_PORT)/postgres
export WMK_DATABASE ?= wmk

.PHONY: help up up-otel down db test replay eval seed live lint fmt

help:
	@echo "make up       start db + gateway (http://localhost:8000/mcp)"
	@echo "make up-otel  also start the OTel Collector and Jaeger (http://localhost:16686)"
	@echo "make down     stop the stack and remove volumes"
	@echo "make test     unit, SQL and regression tests (starts db)"
	@echo "make eval     run the eval fixtures through the eval profile"
	@echo "make seed     write the eval fixtures into the running stack through its gateway"
	@echo "make live     a real harness (headless Claude Code) maps a conversation; needs the claude CLI"
	@echo "make replay   rebuild the graph from the log of \$$WMK_DATABASE and diff against the live graph"
	@echo "make lint     ruff check and format check"

up:
	$(COMPOSE) up -d --build --wait

up-otel:
	OTEL_EXPORTER_OTLP_ENDPOINT=http://otel-collector:4318 $(COMPOSE) --profile otel up -d --build --wait

down:
	$(COMPOSE) --profile otel down -v

db:
	$(COMPOSE) up -d --build --wait db

test: db
	uv run pytest

eval: db
	uv run python -m evals.run
	uv run python -m evals.resolution

seed:
	uv run python -m evals.seed --url http://localhost:$${WMK_GATEWAY_PORT:-8000}/mcp

live:
	uv run python -m evals.live --url http://localhost:$${WMK_GATEWAY_PORT:-8000}/mcp --database $(WMK_DATABASE)

replay:
	uv run python -m evals.replay --database $(WMK_DATABASE)

lint:
	uv run ruff check .
	uv run ruff format --check .

fmt:
	uv run ruff check --fix .
	uv run ruff format .
