#!/bin/bash
# Kill any stuck Radio Meuh stream + menu-bar app, then relaunch the app.
# Useful when a modal error dialog freezes the menu and you can't Quit normally.
set -u

APP_DIR="$(cd "$(dirname "$0")" && pwd)"
APP="$APP_DIR/RadioMeuh.app"

echo "🔎  Looking for Radio Meuh processes…"

# 1. The audio stream players (ffplay/mpv/mplayer/cvlc pointed at the radiomeuh stream).
STREAM_PIDS=$(pgrep -f 'radiomeuh.*\.mp3' || true)

# 2. The menu-bar app itself (python -m radiomeuh.menubar).
APP_PIDS=$(pgrep -f 'radiomeuh.menubar' || true)

PIDS=$(printf '%s\n%s\n' "$STREAM_PIDS" "$APP_PIDS" | sort -u | grep -v '^$' || true)

if [ -z "$PIDS" ]; then
    echo "   nothing running."
else
    echo "   killing PIDs: $(echo "$PIDS" | tr '\n' ' ')"
    # Try graceful first, then force after a moment.
    echo "$PIDS" | xargs kill 2>/dev/null || true
    sleep 1
    echo "$PIDS" | xargs kill -9 2>/dev/null || true
fi

# Belt-and-suspenders: close any stray "Radio Meuh" app by name.
osascript -e 'tell application "System Events" to set app_running to (exists (processes whose name is "RadioMeuh"))' >/dev/null 2>&1 \
    && pkill -9 -x RadioMeuh 2>/dev/null || true

echo "🚀  Relaunching Radio Meuh…"
open "$APP"
echo "✅  Done."
