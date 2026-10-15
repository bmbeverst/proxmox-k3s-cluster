# Dev helpers for the repo root. The deploy tooling keeps its own Makefile
# (pyinfra/Makefile, ansible/Makefile).
#
# --only-group lint: the Pulumi SDKs are not needed to check formatting, and
# skipping them keeps the lint env small.
PY := uv run --only-group lint

.PHONY: hooks lint lint-check

# One-time per clone: `core.hooksPath` is local config, so a fresh clone has no
# hooks until this runs (see README).
hooks:
	git config core.hooksPath .githooks
	@echo "pre-commit hook enabled: black + isort check on every commit"

# Rewrite files in place.
lint:
	$(PY) isort .
	$(PY) black .

# The exact checks the pre-commit hook runs, without touching any file.
lint-check:
	$(PY) isort --check-only .
	$(PY) black --check .
