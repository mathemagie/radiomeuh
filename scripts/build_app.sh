#!/bin/bash
# Builds RadioMeuh.app — a menu-bar-only macOS app that launches the
# rumps menu bar app from this repo's virtualenv.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
APP="$ROOT/RadioMeuh.app"
VENV_PY="$ROOT/.venv/bin/python"

if [ ! -x "$VENV_PY" ]; then
  echo "Missing venv. Run:" >&2
  echo "  python3 -m venv .venv && ./.venv/bin/pip install -e '.[menubar]'" >&2
  exit 1
fi

rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"

# Launcher: run the installed package's menu bar entry point.
cat > "$APP/Contents/MacOS/RadioMeuh" <<EOF
#!/bin/bash
exec "$VENV_PY" -m radiomeuh.menubar
EOF
chmod +x "$APP/Contents/MacOS/RadioMeuh"

# Info.plist — LSUIElement=1 => menu-bar-only, no Dock icon.
cat > "$APP/Contents/Info.plist" <<'EOF'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleName</key>            <string>Radio Meuh</string>
    <key>CFBundleDisplayName</key>     <string>Radio Meuh</string>
    <key>CFBundleIdentifier</key>      <string>com.radiomeuh.menubar</string>
    <key>CFBundleVersion</key>         <string>1.0</string>
    <key>CFBundleShortVersionString</key> <string>1.0</string>
    <key>CFBundlePackageType</key>     <string>APPL</string>
    <key>CFBundleExecutable</key>      <string>RadioMeuh</string>
    <key>LSUIElement</key>             <true/>
    <key>LSMinimumSystemVersion</key>  <string>10.13</string>
    <key>NSHighResolutionCapable</key> <true/>
</dict>
</plist>
EOF

echo "Built: $APP"
