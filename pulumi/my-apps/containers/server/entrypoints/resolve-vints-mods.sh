#!/usr/bin/env bash
# Resolves the vints mod pins into $DATA_PATH/Mods from a content-addressed depot on the data PVC, so
# a steady-state boot (same pins, same game minor) needs no network at all.
#
#   --check   --mods <pins>                  CI: scratch dir; resolve + verify every pin, print the set
#   --promote --mods <pins>                  CI: rewrite promotable URL pins in place (idempotent)
#   --stage   --mods <pins> [--data-path <p>]  init container: fetch what is missing, then make
#                                            <p>/Mods exactly the pinned set
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

if [[ "$MODE" == "--stage" ]]; then
  DATA_PATH="${DATA_PATH_ARG:-${DATA_PATH:-/data}}"
  META="$DATA_PATH/.vints-mods"
  # The init container's rootfs is read-only and has no /tmp: scratch lives on the data PVC.
  WORK="$META/tmp"
  mkdir -p "$DATA_PATH/Mods" "$META/depot" "$WORK"
else
  # --check and --promote never touch the data PVC.
  WORK="$(mktemp -d)"
  META="$WORK/data/.vints-mods"
  mkdir -p "$META/depot" "$META/tmp"
fi
DEPOT="$META/depot"
TMP="$META/tmp"
CATALOG="$META/catalog.json"
[[ -f "$CATALOG" ]] || echo '{}' > "$CATALOG"
trap 'rm -rf "$WORK"' EXIT

# --- depot + catalog ---------------------------------------------------------
# catalog.json: "<key>:<version>:<minor>" and "url:<url>:<minor>" -> {fileid, filename, sha256},
# plus "fileid:<fileid>:<minor>" -> sha256 so a promoted URL pin stages from the depot as well.

# The sha256 the catalog records for <key>, or "".
catalog_sha() {
  jq -r --arg k "$1" '.[$k] // empty | if type == "object" then (.sha256 // empty) else . end' "$CATALOG"
}

# A string field of a catalog entry, or "".
catalog_field() {
  jq -r --arg k "$1" --arg f "$2" '(.[$k] // {}) | if type == "object" then (.[$f] // empty) else empty end' \
    "$CATALOG"
}

catalog_put() {  # <key> <json value>
  jq --arg k "$1" --argjson v "$2" '.[$k] = $v' "$CATALOG" > "$TMP/catalog.new"
  mv "$TMP/catalog.new" "$CATALOG"
}

# Forgets every entry that holds <sha>, and the depot file itself: the next attempt re-downloads.
catalog_drop_sha() {
  jq --arg s "$1" \
    'with_entries(select((if (.value | type) == "object" then (.value.sha256 // "") else .value end) != $s))' \
    "$CATALOG" > "$TMP/catalog.new"
  mv "$TMP/catalog.new" "$CATALOG"
  rm -f "$DEPOT/$1.zip"
}

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
# "<lineno>\t<key>\t<version|url>\t<value>" for every pin line; comments and blank lines are skipped.
parse_pins() {
  local lineno=0 line key value dup
  : > "$WORK/pins.tsv"
  while IFS= read -r line || [[ -n "$line" ]]; do
    lineno=$((lineno + 1))
    line="${line%$'\r'}"
    if [[ -z "$line" || "$line" =~ ^[[:space:]]*$ ]]; then continue; fi
    if [[ "$line" =~ ^[[:space:]]*# ]]; then continue; fi
    [[ "$line" =~ ^[[:space:]]*([A-Za-z0-9_.-]+):[[:space:]]*(.*)$ ]] \
      || fail "$PINS:$lineno: not a '<key>: <version|url>' line: '$line'"
    key="${BASH_REMATCH[1]}"
    value="${BASH_REMATCH[2]}"
    value="${value%%[[:space:]]#*}"                 # drop a trailing "# comment"
    value="${value%"${value##*[![:space:]]}"}"      # trim
    value="${value#"${value%%[![:space:]]*}"}"
    if [[ "$value" == https://* ]]; then
      printf '%s\t%s\turl\t%s\n' "$lineno" "$key" "$value" >> "$WORK/pins.tsv"
    elif [[ "$value" =~ ^[0-9]+\.[0-9]+[^[:space:]]*$ ]]; then
      printf '%s\t%s\tversion\t%s\n' "$lineno" "$key" "$value" >> "$WORK/pins.tsv"
    else
      fail "$PINS:$lineno: value must be a version or a full https URL, got '$value'"
    fi
  done < "$PINS"
  dup="$(cut -f2 "$WORK/pins.tsv" | sort | uniq -d | awk 'NR == 1 { d = $0 } END { print d }')"
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

# Fatal resolution of a version pin: sets API_FILEID / API_FILENAME / API_MODIDSTR.
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
  API_FILENAME="$(jq -r '.filename // empty' <<<"$rel")"
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
  if ! unzip -p "$zip" modinfo.json > "$TMP/modinfo.json" 2>/dev/null || [[ ! -s "$TMP/modinfo.json" ]]; then
    rm -f "$TMP/modinfo.json"
    fail "$ctx: no readable modinfo.json at the zip root"
  fi
  MODINFO_MODID="$(jq -r '.modid // empty' "$TMP/modinfo.json")"
  MODINFO_VERSION="$(jq -r '.version // empty' "$TMP/modinfo.json")"
  dep="$(jq -r '.dependencies.game? // empty' "$TMP/modinfo.json")"
  rm -f "$TMP/modinfo.json"
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
use_cached() {  # <ctx> <sha> <fileid>
  read_modinfo "$1" "$DEPOT/$2.zip"
  ART_SHA="$2"
  ART_FILEID="$3"
  ART_MODID="$MODINFO_MODID"
  ART_VERSION="$MODINFO_VERSION"
  ART_SOURCE="depot"
}


# Makes sure the pinned artifact is in the depot, downloading it only when it is not there.
# Sets ART_SHA / ART_FILEID / ART_MODID / ART_VERSION / ART_SOURCE.
fetch_artifact() {
  local ctx="$1" kind="$2" key="$3" value="$4"
  local catkey url fileid cached sha tmpfile
  if [[ "$kind" == version ]]; then catkey="$key:$value:$MINOR"; else catkey="url:$value:$MINOR"; fi

  # 1) already verified and cached: no API call, no download
  cached="$(catalog_sha "$catkey")"
  if [[ -n "$cached" ]]; then
    if depot_has "$cached"; then
      use_cached "$ctx" "$cached" "$(catalog_field "$catkey" fileid)"
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
    # 3) the same bytes are often in the depot already (e.g. a URL pin promoted to this fileid)
    cached="$(catalog_sha "fileid:$fileid:$MINOR")"
    if [[ -n "$cached" ]] && depot_has "$cached"; then
      use_cached "$ctx" "$cached" "$fileid"
      catalog_put "$catkey" "$(jq -nc --arg f "$fileid" --arg n "$API_FILENAME" --arg s "$cached" \
        '{fileid: $f, filename: $n, sha256: $s}')"
      return 0
    fi
  else
    url="$value"
    fileid="$(url_fileid "$value")"
  fi

  # 4) download, hash, verify, then move into the depot
  tmpfile="$TMP/download.$$.zip"
  log "$ctx: downloading $url"
  curl -fsSL --retry 3 --retry-delay 2 "$url" -o "$tmpfile" || fail "$ctx: download failed: $url"
  sha="$(sha256sum "$tmpfile" | cut -d' ' -f1)"
  read_modinfo "$ctx" "$tmpfile"
  ART_SHA="$sha"
  ART_FILEID="$fileid"
  ART_MODID="$MODINFO_MODID"
  ART_VERSION="$MODINFO_VERSION"
  ART_SOURCE="downloaded"
  mv "$tmpfile" "$DEPOT/$sha.zip"
  catalog_put "$catkey" "$(jq -nc --arg f "$fileid" --arg n "${API_FILENAME:-}" --arg s "$sha" \
    '{fileid: $f, filename: $n, sha256: $s}')"
  if [[ -n "$fileid" ]]; then
    catalog_put "fileid:$fileid:$MINOR" "$(jq -nc --arg s "$sha" '$s')"
  fi
}

# --- the pinned set ----------------------------------------------------------
# Resolves every pin and appends "key kind value modid version fileid sha256" to the plan.
resolve_all() {
  local lineno key kind value dup ctx
  : > "$WORK/plan.tsv"
  : > "$WORK/modids.tsv"
  parse_pins
  while IFS=$'\t' read -r lineno key kind value; do
    ctx="$PINS:$lineno ($key)"
    fetch_artifact "$ctx" "$kind" "$key" "$value"
    if [[ "$kind" == version ]]; then
      [[ "$ART_MODID" == "$key" ]] || fail "$ctx: the zip holds internal Mod ID '$ART_MODID', expected '$key'"
      [[ "$ART_VERSION" == "$value" ]] || fail "$ctx: the zip holds version '$ART_VERSION', expected '$value'"
    fi
    printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
      "$key" "$kind" "$value" "$ART_MODID" "$ART_VERSION" "${ART_FILEID:--}" "$ART_SHA" >> "$WORK/plan.tsv"
    printf '%s\t%s\n' "$key" "$ART_MODID" >> "$WORK/modids.tsv"
    log "$ctx: $kind $value -> modid $ART_MODID, version $ART_VERSION, fileid ${ART_FILEID:--}, sha256 $ART_SHA ($ART_SOURCE)"
  done < "$WORK/pins.tsv"
  [[ -s "$WORK/plan.tsv" ]] || log "note: '$PINS' pins no mods"
  dup="$(cut -f2 "$WORK/modids.tsv" | sort | uniq -d | awk 'NR == 1 { d = $0 } END { print d }')"
  [[ -z "$dup" ]] || fail "internal Mod ID '$dup' is pinned more than once: $(awk -F'\t' -v m="$dup" \
    '$2 == m { print $1 }' "$WORK/modids.tsv" | paste -sd, -)"
}

# --- stage (init container) --------------------------------------------------
# Anything in Mods/ that is not a pinned <key>.zip goes: the deliberate, logged removal path.
prune_mods() {
  local file base stem
  for file in "$DATA_PATH/Mods"/*; do
    [[ -e "$file" ]] || continue
    base="$(basename "$file")"
    stem="${base%.*}"
    case "$base" in
      *.zip|*.url)
        if awk -F'\t' -v k="$stem" '$1 == k { pinned = 1 } END { exit (pinned ? 0 : 1) }' "$WORK/plan.tsv"; then
          if [[ "$base" != *.zip ]]; then
            rm -f "$file"
            log "prune: removed $base (legacy URL stamp)"
          fi
          continue
        fi
        rm -f "$file"
        log "prune: removed $base (not in the pinned set)"
        ;;
      *) log "leave: $base (not a mod archive)" ;;
    esac
  done
}

# Phase 2: verify the depot bytes, then make Mods/ exactly the pinned set. Pure local I/O.
stage_apply() {
  local key kind value modid version fileid sha file actual
  while IFS=$'\t' read -r key kind value modid version fileid sha; do
    file="$DEPOT/$sha.zip"
    [[ -f "$file" ]] || fail "key '$key': depot/$sha.zip vanished after it was staged"
    actual="$(sha256sum "$file" | cut -d' ' -f1)"
    if [[ "$actual" != "$sha" ]]; then
      catalog_drop_sha "$sha"
      fail "key '$key': depot/$sha.zip hashes to $actual, expected $sha; dropped it, the next attempt re-downloads"
    fi
    cp "$file" "$DATA_PATH/Mods/$key.zip"
    log "install: $key.zip (modid $modid, version $version, fileid $fileid, sha256 $sha)"
  done < "$WORK/plan.tsv"
  prune_mods
  log "applied set:"
  while IFS=$'\t' read -r key kind value modid version fileid sha; do
    log "  $key  version $version  modid $modid  fileid $fileid  sha256 $sha"
  done < "$WORK/plan.tsv"
  log "depot $DEPOT: $(du -sh "$DEPOT" | cut -f1), $(ls -1 "$DEPOT" | wc -l) archive(s)"
}



# --- promote (URL pin -> tracked pin) ----------------------------------------
# A URL pin becomes "<internal modid>: <version>" only when that tracked pin would resolve to exactly
# the bytes the URL serves; every other URL pin stays one, with the reason logged.
promote_entry() {
  local lineno="$1" key="$2" value="$3" ctx fileid json rel rel_fileid rel_modidstr newest
  ctx="$PINS:$lineno ($key)"
  fileid="$(url_fileid "$value")"
  if [[ -z "$fileid" ]]; then
    log "untracked: $ctx is not a Mod DB download URL; it stays a URL pin (Renovate never sees it)"
    return
  fi
  fetch_artifact "$ctx" url "$key" "$value"
  if ! json="$(api_mod "$ART_MODID")"; then
    log "untracked: $ctx: '$ART_MODID' is not on the Mod DB; it stays a URL pin"
    return
  fi
  rel="$(printf '%s' "$json" | jq -c --arg v "$ART_VERSION" \
    '[.mod.releases[] | select(.modversion == $v)] | sort_by(.created) | last // empty')"
  if [[ -z "$rel" || "$rel" == "null" ]]; then
    log "untracked: $ctx: '$ART_MODID' has no released version '$ART_VERSION'; it stays a URL pin"
    return
  fi
  rel_fileid="$(jq -r '.fileid // empty' <<<"$rel")"
  rel_modidstr="$(jq -r '.modidstr // empty' <<<"$rel")"
  if [[ "$rel_fileid" != "$fileid" ]]; then
    log "untracked: $ctx: upstream re-uploaded $ART_MODID $ART_VERSION as fileid $rel_fileid while the URL pins $fileid; it stays a URL pin"
    return
  fi
  if [[ "$rel_modidstr" != "$ART_MODID" ]]; then
    log "untracked: $ctx: release '$ART_VERSION' carries internal Mod ID '$rel_modidstr'; it stays a URL pin"
    return
  fi
  if ! printf '%s' "$rel" | jq -e --arg re "$MINOR_RE" 'any(.tags[]?; test($re))' >/dev/null; then
    log "untracked: $ctx: release '$ART_VERSION' has no tag for $MINOR; it stays a URL pin"
    return
  fi
  newest="$(printf '%s' "$json" | jq -r --arg re "$MINOR_RE" \
    '[.mod.releases[] | select(any(.tags[]?; test($re)))] | sort_by(.created) | last | .modversion // empty')"
  if [[ "$newest" != "$ART_VERSION" ]]; then
    log "untracked: $ctx: '$ART_VERSION' is older than $newest for $MINOR, so it stays a URL pin on purpose"
    return
  fi
  # Rewriting into a key another line already pins would write a pins file the resolver rejects.
  if awk -F'\t' -v n="$lineno" -v k="$ART_MODID" '$1 != n && $2 == k { other = 1 } END { exit (other ? 0 : 1) }' \
    "$WORK/pins.tsv"; then
    log "untracked: $ctx: '$ART_MODID' is already pinned on another line; it stays a URL pin"
    return
  fi
  printf '%s\t%s: %s\n' "$lineno" "$ART_MODID" "$ART_VERSION" >> "$WORK/rewrites.tsv"
  log "promote: $ctx -> $ART_MODID: $ART_VERSION (fileid $fileid, modidstr $rel_modidstr, sha256 $ART_SHA)"
}

promote_all() {
  local lineno key kind value
  : > "$WORK/rewrites.tsv"
  parse_pins
  while IFS=$'\t' read -r lineno key kind value; do
    if [[ "$kind" != url ]]; then continue; fi
    promote_entry "$lineno" "$key" "$value"
  done < "$WORK/pins.tsv"
  if [[ ! -s "$WORK/rewrites.tsv" ]]; then
    log "nothing to promote: no URL pin can be tracked byte-for-byte"
    return 0
  fi
  awk -F'\t' 'NR == FNR { rep[$1] = $2; next } { print ((FNR in rep) ? rep[FNR] : $0) }' \
    "$WORK/rewrites.tsv" "$PINS" > "$WORK/pins.new"
  mv "$WORK/pins.new" "$PINS"
  log "rewrote $(wc -l < "$WORK/rewrites.tsv") line(s) of $PINS"
}

case "$MODE" in
  --check)
    resolve_all
    log "resolved every pin against Vintage Story $VS_VERSION (minor $MINOR)"
    ;;
  --stage)
    resolve_all
    stage_apply
    ;;
  --promote)
    promote_all
    ;;
esac
