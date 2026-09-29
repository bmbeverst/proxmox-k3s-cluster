#!/usr/bin/env bash
# Resolves the vints mod pins into $DATA_PATH/Mods from a content-addressed depot on the data PVC, so
# a steady-state boot (same pins, same game minor) needs no network at all.
#
#   --check   --mods <pins>                    CI: scratch data path; resolve, verify and stage the set
#   --promote --mods <pins>                    CI: rewrite promotable URL pins in place (idempotent)
#   --stage   --mods <pins> [--data-path <p>]  init container: fetch what is missing, then make
#                                              <p>/Mods exactly the pinned set
#
# Pins file: one "<key>: <version|url>" per line, "#" starts a comment.
#   key    a version pin's key is the mod's internal Mod ID (the game keys on the modid, not on the
#          filename) and is Renovate's depName; a URL pin's key is free-form and --promote replaces
#          it with the internal Mod ID. -> $DATA_PATH/Mods/<key>.zip
#   value  a released mod version, or a full https .zip URL used verbatim.
# $VS_VERSION fixes the game minor: the image bakes it, CI exports it from the same pin.
#
# Everything that cannot be resolved to verified bytes is fatal: the server must never start on a
# missing or partial mod set. The depot and catalog live outside Mods/, so the game only sees zips.
set -euo pipefail

API="https://mods.vintagestory.at/api/mod"
DOWNLOAD="https://mods.vintagestory.at/download"

MODE=""
PINS=""
DATA_PATH_ARG=""

fail() { echo "[mods] ERROR: $*" >&2; exit 1; }
log() { echo "[mods] $*"; }

usage() {
  cat >&2 <<'EOF'
usage:
  resolve-vints-mods.sh --check   --mods <pins>
  resolve-vints-mods.sh --promote --mods <pins>
  resolve-vints-mods.sh --stage   --mods <pins> [--data-path <dir>]
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --check|--stage|--promote) MODE="$1"; shift ;;
    --mods) [[ $# -ge 2 ]] || fail "--mods needs a path"; PINS="$2"; shift 2 ;;
    --data-path) [[ $# -ge 2 ]] || fail "--data-path needs a path"; DATA_PATH_ARG="$2"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) usage; fail "unknown argument '$1'" ;;
  esac
done

[[ -n "$MODE" ]] || { usage; fail "one of --check, --stage or --promote is required"; }
[[ -n "$PINS" ]] || fail "--mods <pins> is required"
[[ -f "$PINS" ]] || fail "pins file '$PINS' not found"
[[ -n "${VS_VERSION:-}" ]] || fail "VS_VERSION is not set (the image bakes it, CI exports it)"
[[ "$VS_VERSION" =~ ^([0-9]+\.[0-9]+)\.[0-9]+$ ]] || fail "VS_VERSION '$VS_VERSION' is not X.Y.Z"
MINOR="${BASH_REMATCH[1]}"
MINOR_RE="^${MINOR//./\\.}\\.[0-9]+$"

# --- layout ------------------------------------------------------------------
# --check and --promote work in a throwaway data path, so all three modes resolve the pins the exact
# way --stage does.
if [[ "$MODE" == "--stage" ]]; then
  DATA_PATH="${DATA_PATH_ARG:-${DATA_PATH:-/data}}"
  # The init container's rootfs is read-only and has no /tmp: scratch lives on the data PVC.
  trap 'rm -rf "$WORK"' EXIT
else
  DATA_PATH="$(mktemp -d)"
  trap 'rm -rf "$DATA_PATH"' EXIT
fi
META="$DATA_PATH/.vints-mods"
DEPOT="$META/depot"
WORK="$META/tmp"
CATALOG="$META/catalog.json"
mkdir -p "$DATA_PATH/Mods" "$DEPOT" "$WORK"
[[ -f "$CATALOG" ]] || echo '{}' > "$CATALOG"

# --- depot + catalog ---------------------------------------------------------
# catalog.json maps "<key>:<version>:<minor>" and "url:<url>:<minor>" to the sha256 of the archive,
# plus "fileid:<fileid>:<minor>" so a URL pin promoted to a version pin reuses the depot bytes.

# The sha256 the catalog records for <key>, or "".
catalog_sha() { jq -r --arg k "$1" '.[$k] // empty' "$CATALOG"; }

catalog_put() {  # <key> <sha256>
  jq --arg k "$1" --arg s "$2" '.[$k] = $s' "$CATALOG" > "$WORK/catalog.new"
  mv "$WORK/catalog.new" "$CATALOG"
}

# Forgets <sha>'s depot file: the next attempt re-downloads and overwrites the stale catalog entry.
catalog_drop_sha() { rm -f "$DEPOT/$1.zip"; }

# 0 when the depot holds <sha> and the bytes still hash to it.
depot_has() {
  [[ -f "$DEPOT/$1.zip" ]] || return 1
  [[ "$(sha256sum "$DEPOT/$1.zip" | cut -d' ' -f1)" == "$1" ]]
}

# The Mod DB file id in a https://mods.vintagestory.at/download/<fileid>[/<filename>] URL, else "".
url_fileid() {
  if [[ "$1" =~ ^https://mods\.vintagestory\.at/download/([0-9]+)([/?].*)?$ ]]; then
    printf '%s' "${BASH_REMATCH[1]}"
  fi
}

# --- pins --------------------------------------------------------------------
# parse_pins fills the PIN_* arrays; the same index is the same pin line.

# The first duplicated input line, or "". awk, not head: head closing early SIGPIPEs the pipeline.
first_dup() { sort | uniq -d | awk 'NR == 1 { d = $0 } END { print d }'; }

# Reads the real pin lines into PIN_*; comments and blank lines are skipped.
parse_pins() {
  local lineno rest key value kind dup
  PIN_LINENO=() PIN_KEY=() PIN_KIND=() PIN_VALUE=()
  # One awk pass drops CRs, a trailing comment and the surrounding blanks, and numbers the lines so
  # a failure can name the line to edit.
  while IFS=$'\t' read -r lineno rest; do
    [[ "$rest" =~ ^([A-Za-z0-9_.-]+):[[:space:]]*(.*)$ ]] \
      || fail "$PINS:$lineno: not a '<key>: <version|url>' line: '$rest'"
    key="${BASH_REMATCH[1]}"
    value="${BASH_REMATCH[2]}"
    if [[ "$value" == https://* ]]; then
      kind="url"
    elif [[ "$value" =~ ^[0-9]+\.[0-9]+[^[:space:]]*$ ]]; then
      kind="version"
    else
      fail "$PINS:$lineno: value must be a version or a full https URL, got '$value'"
    fi
    PIN_LINENO+=("$lineno") PIN_KEY+=("$key") PIN_KIND+=("$kind") PIN_VALUE+=("$value")
  done < <(awk '{ sub(/\r$/, ""); sub(/(^|[ \t])#.*/, ""); gsub(/^[ \t]+|[ \t]+$/, "") } length { print NR "\t" $0 }' "$PINS")
  dup="$(printf '%s\n' "${PIN_KEY[@]}" | first_dup)"
  [[ -z "$dup" ]] || fail "$PINS: key '$dup' is pinned twice"
}
# --- Mod DB ------------------------------------------------------------------
# Body of GET /api/mod/<query>, or non-zero when the mod is not on the Mod DB. A bad urlalias
# answers 200 with a {"statuscode":"404"} body, so require .mod.modid instead of trusting the status.
api_mod() {
  local query="$1" json
  json="$(curl -fsSL --retry 3 "$API/$query")" || return 1
  jq -e '.mod.modid? // empty | tostring | length > 0' <<<"$json" >/dev/null || return 1
  printf '%s' "$json"
}

# Fatal resolution of a version pin: sets API_FILEID / API_MODIDSTR.
api_version_release() {
  local ctx="$1" key="$2" pin="$3" json rel
  json="$(api_mod "$key")" \
    || fail "$ctx: '$key' does not resolve on the Mod DB (a wrong key answers 200 with a statuscode body)"
  rel="$(printf '%s' "$json" | jq -c --arg pin "$pin" --arg re "$MINOR_RE" \
    '[.mod.releases[] | select(.modversion == $pin) | select([.tags[]? | select(test($re))] | length > 0)]
     | sort_by(.created) | last // empty')"
  if [[ -z "$rel" || "$rel" == "null" ]]; then
    if printf '%s' "$json" | jq -e --arg pin "$pin" 'any(.mod.releases[]; .modversion == $pin)' >/dev/null; then
      fail "$ctx: '$key' has no release of version '$pin' tagged for $MINOR"
    fi
    fail "$ctx: '$key' has no released version '$pin'"
  fi
  API_FILEID="$(jq -r '.fileid // empty' <<<"$rel")"
  API_MODIDSTR="$(jq -r '.modidstr // empty' <<<"$rel")"
  [[ -n "$API_FILEID" ]] || fail "$ctx: release '$pin' has no fileid"
  [[ "$API_MODIDSTR" == "$key" ]] \
    || fail "$ctx: release '$pin' carries internal Mod ID '$API_MODIDSTR', expected '$key'"
}

# --- artifacts ---------------------------------------------------------------
# Reads the zip's modinfo.json into MODINFO_MODID / MODINFO_VERSION and asserts what every artifact
# must satisfy: a plain modid, and, if it declares one, a game dependency the image can satisfy.
read_modinfo() {
  local ctx="$1" zip="$2" dep newest
  if ! unzip -p "$zip" modinfo.json > "$WORK/modinfo.json" 2>/dev/null || [[ ! -s "$WORK/modinfo.json" ]]; then
    rm -f "$WORK/modinfo.json"
    fail "$ctx: no readable modinfo.json at the zip root"
  fi
  MODINFO_MODID="$(jq -r '.modid // empty' "$WORK/modinfo.json")"
  MODINFO_VERSION="$(jq -r '.version // empty' "$WORK/modinfo.json")"
  dep="$(jq -r '.dependencies.game? // empty' "$WORK/modinfo.json")"
  rm -f "$WORK/modinfo.json"
  [[ -n "$MODINFO_MODID" ]] || fail "$ctx: modinfo.json carries no modid"
  [[ "$MODINFO_MODID" =~ ^[A-Za-z0-9_.-]+$ ]] \
    || fail "$ctx: modinfo.json modid '$MODINFO_MODID' is not a plain id"
  if [[ -n "$dep" ]]; then
    # dependencies.game is a SemVer minimum; an unsatisfied one means the game skips the mod.
    newest="$(printf '%s\n%s\n' "$dep" "$VS_VERSION" | sort -V | tail -1)"
    [[ "$newest" == "$VS_VERSION" ]] || fail "$ctx: needs game >= $dep, the image runs $VS_VERSION"
  fi
}

# Uses a verified depot artifact: sets the ART_* globals.
use_cached() {  # <ctx> <sha256>
  read_modinfo "$1" "$DEPOT/$2.zip"
  ART_SHA="$2"
  ART_MODID="$MODINFO_MODID"
  ART_VERSION="$MODINFO_VERSION"
  ART_SOURCE="depot"
}

# Makes sure the pinned artifact is in the depot, downloading it only when it is not there.
# Sets ART_SHA / ART_MODID / ART_VERSION / ART_SOURCE.
fetch_artifact() {  # <ctx> <kind> <key> <version|url>
  local ctx="$1" kind="$2" key="$3" value="$4"
  local catkey fileid url cached sha tmpfile
  if [[ "$kind" == version ]]; then catkey="$key:$value:$MINOR"; else catkey="url:$value:$MINOR"; fi

  # 1) already verified and cached: no API call, no download
  cached="$(catalog_sha "$catkey")"
  if [[ -n "$cached" ]]; then
    if depot_has "$cached"; then
      use_cached "$ctx" "$cached"
      return 0
    fi
    log "$ctx: depot/$cached.zip is missing or corrupt; dropping the entry and fetching again"
    catalog_drop_sha "$cached"
  fi

  # 2) the artifact's URL (a version pin needs the Mod DB for its fileid)
  if [[ "$kind" == version ]]; then
    api_version_release "$ctx" "$key" "$value"
    fileid="$API_FILEID"
    url="$DOWNLOAD/$fileid"
    # 3) the same bytes are often in the depot already (a URL pin promoted to this fileid)
    cached="$(catalog_sha "fileid:$fileid:$MINOR")"
    if [[ -n "$cached" ]] && depot_has "$cached"; then
      use_cached "$ctx" "$cached"
      catalog_put "$catkey" "$cached"
      return 0
    fi
  else
    url="$value"
    fileid="$(url_fileid "$value")"
  fi

  # 4) download, hash, verify, then move into the depot
  tmpfile="$WORK/download.$$.zip"
  log "$ctx: downloading $url"
  curl -fsSL --retry 3 --retry-delay 2 "$url" -o "$tmpfile" || fail "$ctx: download failed: $url"
  sha="$(sha256sum "$tmpfile" | cut -d' ' -f1)"
  read_modinfo "$ctx" "$tmpfile"
  ART_SHA="$sha"
  ART_MODID="$MODINFO_MODID"
  ART_VERSION="$MODINFO_VERSION"
  ART_SOURCE="downloaded"
  mv "$tmpfile" "$DEPOT/$sha.zip"
  catalog_put "$catkey" "$sha"
  if [[ -n "$fileid" ]]; then
    catalog_put "fileid:$fileid:$MINOR" "$sha"
  fi
}
# --- the pinned set ----------------------------------------------------------
# Phase 1: resolve every pin into the RES_* arrays; the same index is the same pin line. Anything
# that cannot be resolved to verified bytes fails before Mods/ is touched, so there is no partial set.
resolve_all() {
  local i ctx dup dup_keys=""
  RES_KEY=() RES_MODID=() RES_VERSION=() RES_SHA=()
  parse_pins
  for i in "${!PIN_KEY[@]}"; do
    ctx="$PINS:${PIN_LINENO[$i]} (${PIN_KEY[$i]})"
    fetch_artifact "$ctx" "${PIN_KIND[$i]}" "${PIN_KEY[$i]}" "${PIN_VALUE[$i]}"
    if [[ "${PIN_KIND[$i]}" == version ]]; then
      [[ "$ART_MODID" == "${PIN_KEY[$i]}" ]] \
        || fail "$ctx: the zip holds internal Mod ID '$ART_MODID', expected '${PIN_KEY[$i]}'"
      [[ "$ART_VERSION" == "${PIN_VALUE[$i]}" ]] \
        || fail "$ctx: the zip holds version '$ART_VERSION', expected '${PIN_VALUE[$i]}'"
    fi
    RES_KEY+=("${PIN_KEY[$i]}") RES_MODID+=("$ART_MODID") RES_VERSION+=("$ART_VERSION") RES_SHA+=("$ART_SHA")
    log "$ctx: ${PIN_KIND[$i]} ${PIN_VALUE[$i]} -> modid $ART_MODID, version $ART_VERSION, sha256 $ART_SHA ($ART_SOURCE)"
  done
  [[ ${#RES_KEY[@]} -gt 0 ]] || log "note: '$PINS' pins no mods"
  dup="$(printf '%s\n' "${RES_MODID[@]}" | first_dup)"
  if [[ -n "$dup" ]]; then
    for i in "${!RES_MODID[@]}"; do
      [[ "${RES_MODID[$i]}" == "$dup" ]] || continue
      dup_keys+="${dup_keys:+,}${RES_KEY[$i]}"
    done
    fail "internal Mod ID '$dup' is pinned more than once: $dup_keys"
  fi
  log "resolved every pin against Vintage Story $VS_VERSION (minor $MINOR)"
}

# --- stage (init container) --------------------------------------------------
# Every MODS_KEEP entry is a pinned <key>.zip, so anything else in Mods/ is a leftover: the
# deliberate, logged removal path.
declare -A MODS_KEEP=()

prune_mods() {
  local file base
  for file in "$DATA_PATH/Mods"/*; do
    [[ -e "$file" ]] || continue
    base="${file##*/}"
    case "$base" in
      *.zip|*.url)
        if [[ -n "${MODS_KEEP[$base]:-}" ]]; then continue; fi
        rm -f "$file"
        log "prune: removed $base (not in the pinned set)"
        ;;
      *) log "leave: $base (not a mod archive)" ;;
    esac
  done
}

# Phase 2: make Mods/ exactly the resolved set. Pure local I/O: phase 1 verified every sha256.
stage_apply() {
  local i
  for i in "${!RES_KEY[@]}"; do
    cp "$DEPOT/${RES_SHA[$i]}.zip" "$DATA_PATH/Mods/${RES_KEY[$i]}.zip"
    MODS_KEEP["${RES_KEY[$i]}.zip"]=1
    log "install: ${RES_KEY[$i]}.zip (modid ${RES_MODID[$i]}, version ${RES_VERSION[$i]}, sha256 ${RES_SHA[$i]})"
  done
  prune_mods
  log "depot $DEPOT: $(du -sh "$DEPOT" | cut -f1), $(ls -1 "$DEPOT" | wc -l) archive(s)"
}

# --- promote (URL pin -> tracked pin) ----------------------------------------
# A URL pin becomes "<internal modid>: <version>" only when that tracked pin would resolve to exactly
# the bytes the URL serves; every other URL pin stays one, with the reason logged.

untrack() { log "untracked: $1: $2; it stays a URL pin"; }  # <ctx> <reason>

promote_entry() {  # <index into the PIN_* arrays>
  local idx="$1" i ctx fileid json rel rel_fileid rel_modidstr newest other=""
  ctx="$PINS:${PIN_LINENO[$idx]} (${PIN_KEY[$idx]})"
  fileid="$(url_fileid "${PIN_VALUE[$idx]}")"
  if [[ -z "$fileid" ]]; then
    untrack "$ctx" "${PIN_VALUE[$idx]} is not a Mod DB download URL (Renovate never sees it)"
    return 0
  fi
  fetch_artifact "$ctx" url "${PIN_KEY[$idx]}" "${PIN_VALUE[$idx]}"
  if ! json="$(api_mod "$ART_MODID")"; then
    untrack "$ctx" "'$ART_MODID' is not on the Mod DB"
    return 0
  fi
  rel="$(printf '%s' "$json" | jq -c --arg v "$ART_VERSION" \
    '[.mod.releases[] | select(.modversion == $v)] | sort_by(.created) | last // empty')"
  if [[ -z "$rel" || "$rel" == "null" ]]; then
    untrack "$ctx" "'$ART_MODID' has no released version '$ART_VERSION'"
    return 0
  fi
  rel_fileid="$(jq -r '.fileid // empty' <<<"$rel")"
  rel_modidstr="$(jq -r '.modidstr // empty' <<<"$rel")"
  if [[ "$rel_fileid" != "$fileid" ]]; then
    untrack "$ctx" "upstream re-uploaded $ART_MODID $ART_VERSION as fileid $rel_fileid while the URL pins $fileid"
    return 0
  fi
  if [[ "$rel_modidstr" != "$ART_MODID" ]]; then
    untrack "$ctx" "release '$ART_VERSION' carries internal Mod ID '$rel_modidstr'"
    return 0
  fi
  if ! printf '%s' "$rel" | jq -e --arg re "$MINOR_RE" 'any(.tags[]?; test($re))' >/dev/null; then
    untrack "$ctx" "release '$ART_VERSION' has no tag for $MINOR"
    return 0
  fi
  newest="$(printf '%s' "$json" | jq -r --arg re "$MINOR_RE" \
    '[.mod.releases[] | select(any(.tags[]?; test($re)))] | sort_by(.created) | last | .modversion // empty')"
  if [[ "$newest" != "$ART_VERSION" ]]; then
    untrack "$ctx" "'$ART_VERSION' is older than $newest for $MINOR, so it stays a URL pin on purpose"
    return 0
  fi
  # Rewriting into a key another line already pins would write a pins file the resolver rejects.
  for i in "${!PIN_KEY[@]}"; do
    if [[ "$i" != "$idx" && "${PIN_KEY[$i]}" == "$ART_MODID" ]]; then other="1"; fi
  done
  if [[ -n "$other" ]]; then
    untrack "$ctx" "'$ART_MODID' is already pinned on another line"
    return 0
  fi
  REWRITE["${PIN_LINENO[$idx]}"]="$ART_MODID: $ART_VERSION"
  log "promote: $ctx -> $ART_MODID: $ART_VERSION (fileid $fileid, modidstr $rel_modidstr, sha256 $ART_SHA)"
}

promote_all() {
  local idx n=0 line
  REWRITE=()
  parse_pins
  for idx in "${!PIN_KEY[@]}"; do
    if [[ "${PIN_KIND[$idx]}" == url ]]; then promote_entry "$idx"; fi
  done
  if [[ ${#REWRITE[@]} -eq 0 ]]; then
    log "nothing to promote: no URL pin can be tracked byte-for-byte"
    return 0
  fi
  # Rewrites are keyed by line number, so comments, blanks and untouched pins survive verbatim.
  while IFS= read -r line || [[ -n "$line" ]]; do
    n=$((n + 1))
    if [[ -n "${REWRITE[$n]:-}" ]]; then
      printf '%s\n' "${REWRITE[$n]}"
    else
      printf '%s\n' "$line"
    fi
  done < "$PINS" > "$WORK/pins.new"
  mv "$WORK/pins.new" "$PINS"
  log "rewrote ${#REWRITE[@]} line(s) of $PINS"
}

case "$MODE" in
  --check|--stage)
    resolve_all
    stage_apply
    ;;
  --promote)
    promote_all
    ;;
esac
