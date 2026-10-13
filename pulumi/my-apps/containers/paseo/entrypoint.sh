#!/usr/bin/env bash
# Seeds runtime config into the Paseo home volume, then runs the base entrypoint.
set -euo pipefail

export HOME="${HOME:-/home/paseo}"
export PASEO_HOME="${PASEO_HOME:-${HOME}/.paseo}"
export CLINE_CONFIG_DIR="${CLINE_CONFIG_DIR:-${HOME}/.cline}"

SEED="${CLINE_PROVIDERS_SEED:-/etc/paseo-secrets/cline-providers.json}"
GUARD_DIR="${CLINE_GUARD_DIR:-/opt/dangerous-command-guard}"
PANEL_DIR="${OPS_PANEL_DIR:-/opt/paseo-ops-panel}"

IS_ROOT=false
if [ "$(id -u)" = "0" ]; then IS_ROOT=true; fi
as_paseo() { if [ "$IS_ROOT" = true ]; then gosu paseo "$@"; else "$@"; fi; }

echo "[paseo-ops] preflight: uid=$(id -u) home=$HOME cline_dir=$CLINE_CONFIG_DIR"

# --- 1. Chown first: `install -d -o X` skips parents, so install fails EACCES.
mkdir -p "$PASEO_HOME" "$CLINE_CONFIG_DIR/data/settings" /workspace
if [ "$IS_ROOT" = true ]; then
  chown -R paseo:paseo "$HOME" /workspace
fi
echo "[paseo-ops] ownership set: $(stat -c '%n %u:%g %a' "$CLINE_CONFIG_DIR" "$CLINE_CONFIG_DIR/data/settings")"

# --- 2. Daemon config: plugins, web UI, Cline ACP provider. Rewritten when it differs.
# Model list comes from the installed CLI, so a bump cannot leave it stale.
# Canonical <provider>/ ids only: the catalog also carries aliases.
LLMS_MODELS="${CLINE_MODELS_MODULE:-/usr/local/lib/node_modules/cline/node_modules/@cline/llms/dist/models.js}"
MODEL_PROVIDER="${CLINE_MODELS_PROVIDER:-cline-pass}"
DEFAULT_MODEL="${CLINE_DEFAULT_MODEL:-cline-pass/deepseek-v4.1-flash}"
if [ ! -f "$LLMS_MODELS" ]; then
  echo "[paseo-ops] FATAL: no Cline model catalog at $LLMS_MODELS" >&2
  exit 1
fi
ADDITIONAL_MODELS="$(node -e '
  const m = require(process.argv[1]);
  const generated = m.getGeneratedModelsForProvider(process.argv[2]);
  const models = Array.isArray(generated) ? generated : Object.values(generated || {});
  const prefix = process.argv[2] + "/";
  const list = models
    .filter((e) => e && typeof e.id === "string" && e.id.startsWith(prefix))
    .map((e) => ({ id: e.id, label: e.name || e.id }));
  if (list.length === 0) { console.error("no models under " + prefix); process.exit(1); }
  if (process.argv[3] && !list.some((e) => e.id === process.argv[3])) {
    list.push({ id: process.argv[3], label: process.argv[3].slice(prefix.length) });
  }
  for (const entry of list) if (entry.id === process.argv[3]) entry.isDefault = true;
  console.log(JSON.stringify(list));
' "$LLMS_MODELS" "$MODEL_PROVIDER" "$DEFAULT_MODEL")" || {
  echo "[paseo-ops] FATAL: could not list models for $MODEL_PROVIDER" >&2
  exit 1
}
echo "[paseo-ops] models: $(printf '%s' "$ADDITIONAL_MODELS" | jq 'length') for $MODEL_PROVIDER"

cfg="$(mktemp)"
cat > "$cfg" <<JSON
{
  "version": 1,
  "pluginsEnabled": true,
  "plugins": {
    "paseo-ops-panel": { "source": "directory", "path": "${PANEL_DIR}", "enabled": true }
  },
  "features": { "webUi": { "enabled": true } },
  "daemon": { "listen": "0.0.0.0:6767", "relay": { "enabled": false } },
  "agents": {
    "providers": {
      "cline": {
        "extends": "acp",
        "label": "ClinePass",
        "command": ["/usr/local/bin/cline", "--config", "${CLINE_CONFIG_DIR}", "--acp"],
        "additionalModels": ${ADDITIONAL_MODELS}
      }
    }
  }
}
JSON
if ! cmp -s "$cfg" "$PASEO_HOME/config.json"; then
  install -m 0644 -o paseo -g paseo "$cfg" "$PASEO_HOME/config.json" 2>/dev/null \
    || cp "$cfg" "$PASEO_HOME/config.json"
  echo "[paseo-ops] wrote $PASEO_HOME/config.json"
else
  echo "[paseo-ops] $PASEO_HOME/config.json unchanged"
fi
rm -f "$cfg"

# --- 3. Credentials from the Secret; a missing seed is fatal, not a warning.
if [ -f "$SEED" ]; then
  install -m 0600 -o paseo -g paseo "$SEED" "$CLINE_CONFIG_DIR/data/settings/providers.json" 2>/dev/null \
    || { cp "$SEED" "$CLINE_CONFIG_DIR/data/settings/providers.json"; chmod 0600 "$CLINE_CONFIG_DIR/data/settings/providers.json"; }
  echo "[paseo-ops] seeded Cline credentials from $SEED"
else
  echo "[paseo-ops] FATAL: no Cline seed at $SEED (mount the ClinePass Secret there; CLINE_PROVIDERS_SEED overrides)" >&2
  exit 1
fi

# --- 4. Guard plugin (as paseo) + git identity/governance.
as_paseo env HOME="$HOME" /usr/local/bin/cline plugin install "$GUARD_DIR" --force
as_paseo git config --global user.name "${GIT_AUTHOR_NAME:-paseo}"
as_paseo git config --global user.email "${GIT_AUTHOR_EMAIL:-paseo@localhost}"
as_paseo git config --global --add safe.directory /workspace

echo "[paseo-ops] handing off to the base entrypoint"
exec /usr/local/bin/paseo-docker-entrypoint
