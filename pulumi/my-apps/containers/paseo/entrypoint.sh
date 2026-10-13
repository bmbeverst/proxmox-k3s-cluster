#!/usr/bin/env bash
# Seeds runtime config into the Paseo home volume, then runs the base entrypoint.
set -euo pipefail

export HOME="${HOME:-/home/paseo}"
export PASEO_HOME="${PASEO_HOME:-${HOME}/.paseo}"
export CLINE_CONFIG_DIR="${CLINE_CONFIG_DIR:-${HOME}/.cline}"

SEED="${CLINE_PROVIDERS_SEED:-/run/secrets/cline-providers.json}"
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
        "additionalModels": [
          { "id": "cline-pass/glm-5.3", "label": "ClinePass GLM-5.3" },
          { "id": "cline-pass/glm-5.3-flash", "label": "ClinePass GLM-5.3 Flash" },
          { "id": "cline-pass/kimi-k3", "label": "ClinePass Kimi K3" },
          { "id": "cline-pass/deepseek-v4-pro", "label": "ClinePass DeepSeek V4 Pro" },
          { "id": "cline-pass/deepseek-v4.1-flash", "label": "ClinePass DeepSeek V4.1 Flash", "isDefault": true },
          { "id": "cline-pass/mimo-v2.5", "label": "ClinePass MiMo-V2.5" },
          { "id": "cline-pass/mimo-v2.5-pro", "label": "ClinePass MiMo-V2.5-Pro" },
          { "id": "cline-pass/minimax-m3", "label": "ClinePass MiniMax M3" },
          { "id": "cline-pass/muse-spark-1.3-contributor", "label": "ClinePass Muse Spark 1.3 Contributor" },
          { "id": "cline-pass/qwen3.8-max", "label": "ClinePass Qwen3.8 Max" },
          { "id": "cline-pass/qwen3.7-max", "label": "ClinePass Qwen3.7 Max" },
          { "id": "cline-pass/qwen3.7-plus", "label": "ClinePass Qwen3.7 Plus" }
        ]
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
