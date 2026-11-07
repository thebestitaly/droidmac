#!/bin/bash
# Crea DroidMac.app (default: ~/Applications). Uso: scripts/build-app.sh [cartella]
# L'app è creata localmente, quindi macOS non la mette in quarantena.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DEST="${1:-$HOME/Applications}"
APP="$DEST/DroidMac.app"
VERSION="$(sed -n 's/^VERSION = "\(.*\)"/\1/p' "$ROOT/droidmac/__init__.py")"

mkdir -p "$DEST"
if [ -d "$APP" ]; then
  rm -rf "${APP:?}"
fi
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"

# codice dell'app
cp -R "$ROOT/droidmac" "$APP/Contents/Resources/droidmac"
find "$APP/Contents/Resources" -name "__pycache__" -type d -prune -exec rm -rf {} +
cp "$ROOT/assets/DroidMac.icns" "$APP/Contents/Resources/DroidMac.icns"

cat > "$APP/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleName</key><string>DroidMac</string>
  <key>CFBundleDisplayName</key><string>DroidMac</string>
  <key>CFBundleIdentifier</key><string>it.ileven.droidmac</string>
  <key>CFBundleVersion</key><string>$VERSION</string>
  <key>CFBundleShortVersionString</key><string>$VERSION</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleExecutable</key><string>DroidMac</string>
  <key>CFBundleIconFile</key><string>DroidMac</string>
  <key>LSMinimumSystemVersion</key><string>11.0</string>
  <key>LSUIElement</key><true/>
  <key>NSHumanReadableCopyright</key><string>DroidMac</string>
</dict>
</plist>
PLIST

cat > "$APP/Contents/MacOS/DroidMac" <<'LAUNCHER'
#!/bin/bash
# Avvia il server locale e apre DroidMac nel browser; se è già aperto, porta in primo piano la pagina.
RES="$(cd "$(dirname "$0")/../Resources" && pwd)"
LOG="$HOME/Library/Logs/DroidMac.log"
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:$PATH"
PY="$(command -v python3 || true)"
if [ -z "$PY" ]; then
  osascript -e 'display alert "DroidMac" message "Serve Python 3. Apri il Terminale ed esegui: xcode-select --install"' >/dev/null
  exit 1
fi
cd "$RES" || exit 1
exec "$PY" -m droidmac --app >>"$LOG" 2>&1
LAUNCHER
chmod +x "$APP/Contents/MacOS/DroidMac"
# se il progetto arriva da uno zip scaricato, macOS lo marca "in quarantena": l'app non deve esserlo
xattr -dr com.apple.quarantine "$APP" 2>/dev/null || true
touch "$APP"

echo "✓ Creata $APP"
command -v adb >/dev/null 2>&1 || [ -x /opt/homebrew/bin/adb ] || [ -x /usr/local/bin/adb ] || \
  echo "! adb non trovato: installalo con  brew install android-platform-tools"
