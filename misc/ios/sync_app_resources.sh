#!/usr/bin/env bash

set -u -o pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
[ "$#" -eq 1 ] \
    || { echo "usage: $0 path/to/xr_3da.app" >&2; exit 2; }
APP="$1"
[ -d "$APP" ] \
    || { echo "FAIL: app bundle not found: $APP" >&2; exit 1; }

mkdir -p "$APP/gamedata" \
    || { echo "FAIL: could not create app gamedata directory" >&2; exit 1; }
rsync -a --delete "$REPO_ROOT/res/gamedata/" "$APP/gamedata/" \
    || { echo "FAIL: could not synchronize bundled gamedata" >&2; exit 1; }
cp "$REPO_ROOT/res/fsgame.ltx" "$APP/fsgame.ltx" \
    || { echo "FAIL: could not synchronize fsgame.ltx" >&2; exit 1; }

echo "resources synced: $APP"
