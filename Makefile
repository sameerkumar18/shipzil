# Thin wrappers over the commands CONTRIBUTING documents. The Python package and
# the Fumadocs site have separate toolchains; the docs build never enters the
# runtime dependency graph.

.DEFAULT_GOAL := help
.PHONY: help sync test test-live lint types check check-compat docs docs-build clean

help:  ## Show this help
	@grep -E '^[a-z-]+:.*?## ' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}'

sync:  ## Install the dev toolchain, pinned by uv.lock
	uv sync

test:  ## Offline tests
	uv run pytest -m "not live"

test-live:  ## Tests that hit real provider sandboxes; needs credentials in .env
	@if [ -f .env ]; then set -a; . ./.env; set +a; fi; \
	uv run pytest -m live

lint:  ## ruff
	uv run ruff check shipzil tests

types:  ## mypy
	uv run mypy shipzil

check: lint types test  ## lint + types + offline tests

check-compat:  ## Run the suite on every supported Python
	@failed=0; for v in 3.10 3.11 3.12 3.13 3.14; do \
		echo "=== Python $$v ==="; \
		uv run --python $$v --isolated --with pytest pytest -q -rA -m "not live" || failed=1; \
	done; exit $$failed

docs:  ## Serve the docs at http://127.0.0.1:3000 with live reload
	@test -d docs-site/node_modules || npm --prefix docs-site ci
	npm --prefix docs-site run dev

docs-build:  ## Build the docs; fails on a broken internal link
	@test -d docs-site/node_modules || npm --prefix docs-site ci
	npm --prefix docs-site run types:check
	npm --prefix docs-site run build
	npm --prefix docs-site run check:agent

clean:  ## Remove build and docs output
	rm -rf docs-site/.next docs-site/out docs-site/generated docs-site/content/docs/api dist build .pytest_cache
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
