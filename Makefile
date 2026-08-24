# Thin wrappers over the commands CONTRIBUTING documents. Nothing here is load
# bearing — every target is a uv invocation you can run by hand. It exists so
# `make check` is one thing to remember, and because pyproject.toml already
# referred to `make check-compat` before this file did.

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
	uv run pytest -m live

lint:  ## ruff
	uv run ruff check shipzil tests

types:  ## mypy
	uv run mypy shipzil

check: lint types test  ## lint + types + offline tests

check-compat:  ## Run the suite on every supported Python
	@for v in 3.9 3.10 3.11 3.12 3.13 3.14; do \
		printf "py%-5s " "$$v"; \
		uv run --python $$v --isolated --with pytest pytest -q 2>&1 \
			| grep -oE "[0-9]+ passed.*" | head -1; \
	done

docs:  ## Serve the docs at http://127.0.0.1:8000 with live reload
	uv run --group docs mkdocs serve

docs-build:  ## Build the docs; fails on a broken internal link
	uv run --group docs mkdocs build --strict

clean:  ## Remove build and docs output
	rm -rf site dist build .pytest_cache
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
