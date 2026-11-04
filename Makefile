# Dev helpers; deploy tooling keeps its own Makefile
#
# --only-group lint: Pulumi SDKs unneeded for hooks
PY := uv run --only-group lint

.PHONY: hooks lint lint-ci

# One-time per clone; pre-push keeps CI lint weekly.
hooks:
	$(PY) pre-commit install
	$(PY) pre-commit install --hook-type pre-push
	@echo "pre-commit hook installed; run 'make lint' any time"

# Lints all files; re-stage if hooks changed anything.
lint:
	$(PY) pre-commit run --all-files

# Exactly what CI runs; hooks fix then fail.
lint-ci:
	$(PY) pre-commit run --all-files --show-diff-on-failure
