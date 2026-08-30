#!/usr/bin/env bash
# Vendor the StratiGraph brand from `stratigraph-brand/` — the single source of
# truth. Same shape and same reasoning as EMStudio's `sync-datamodels.sh`: the
# server cannot import the brand at runtime, so it is COPIED in and committed.
#
#   ./sync-brand.sh                       # from the sibling checkout
#   ./sync-brand.sh ../stratigraph-brand  # from an explicit path
#
# Who wears it here: the two StratiGraph-native web faces this process serves —
# the room browser (`/rooms`) and the node console (`/admin`). NOT EMStudio and
# NOT EMtools: those are Extended Matrix tools and keep the EM look.
#
# Why a copy and not a CDN: a Field Computing Node is deployed where there may be
# no route to the internet at all, and a console that lost its typeface the day
# the uplink went down would lose it exactly when somebody is trying to find out
# why. Same-origin, always. Review the diff, commit it.
#
# Never edit `app/brand/` by hand: the next sync overwrites it.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DST="$HERE/app/brand"
SIBLING="$(cd "$HERE/.." && pwd)/stratigraph-brand"

has_theme() { [ -f "$1/stratigraph-theme.css" ]; }

SRC=""
if [ -n "${1:-}" ]; then
  for cand in "$1" "$1/stratigraph-brand"; do
    if has_theme "$cand"; then SRC="$cand"; break; fi
  done
  [ -n "$SRC" ] || { echo "no stratigraph-theme.css under '$1'" >&2; exit 1; }
fi
if [ -z "$SRC" ] && has_theme "$SIBLING"; then SRC="$SIBLING"; fi
[ -n "$SRC" ] || {
  echo "stratigraph-brand not found beside this repo — pass a path." >&2
  exit 1
}

mkdir -p "$DST/fonts" "$DST/logo"
cp "$SRC/stratigraph-theme.css" "$DST/"
cp "$SRC"/fonts/*.woff2 "$DST/fonts/"

# Only the logos these pages REACH. An unused colourway is weight nobody asked
# for. Two here: the hourglass on the light ground, and the off-white one for the
# dark scheme — the same pair the field assistant names.
for f in favicon-deep-charcoal.svg favicon-off-white.svg; do
  cp "$SRC/logo/$f" "$DST/logo/"
done

fonts=$(ls -1 "$DST/fonts" | wc -l | tr -d ' ')
bytes=$(du -sh "$DST" | cut -f1)
version=$(grep -oE '^- `[0-9]+\.[0-9]+\.[0-9]+`' "$SRC/README.md" | head -1 \
          | tr -d '`-' | tr -d ' ')
cat <<EOF
synced the brand from $SRC:
  theme            stratigraph-theme.css${version:+  (v$version)}
  fonts            $fonts woff2 (Erode · IBM Plex Sans · IBM Plex Mono)
  logo             $(ls -1 "$DST/logo" | wc -l | tr -d ' ') svg
  vendored size    $bytes
EOF
