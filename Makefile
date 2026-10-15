# Dev helpers for the repo root. The deploy tooling keeps its own Makefile
# (pyinfra/Makefile, ansible/Makefile).
#
# --only-group lint: the Pulumi SDKs are not needed to run the hooks, and
# skipping them keeps the lint env small.
PY := uv run --only-group lint

.PHONY: hooks lint lint-ci

# One-time per clone. pre-commit writes .git/hooks/pre-commit itself, so unlike a
# core.hooksPath setup there is nothing of ours to keep in sync - but it is still
# local state, so a fresh clone needs this once.
hooks:
	$(PY) pre-commit install
	@echo "pre-commit hook installed; run 'make lint' any time"

# Apply the hooks to every file, not just the staged ones. Hooks fix files in
# place, so re-stage afterwards if this changed anything.
lint:
	$(PY) pre-commit run --all-files

# Exactly what CI runs. There is no read-only mode: pre-commit hooks fix and then
# fail, which is what makes them useful on an ephemeral CI checkout.
lint-ci:
	$(PY) pre-commit run --all-files --show-diff-on-failure
