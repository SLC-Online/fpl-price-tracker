#!/bin/bash
# Build FPL Optimiser.app and install it in /Applications.
#
#   ./mac_app/build.sh            build + install
#   ./mac_app/build.sh --no-install
#
# Needs Xcode (for actool, to compile the Icon Composer icon) and swiftc.
# The app runs the engine from this project folder, so rebuild if the folder moves.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
PROJECT="$(cd "$HERE/.." && pwd)"
NAME="FPL Optimiser"
BUILD="$HERE/build"
APP="$BUILD/$NAME.app"
export DEVELOPER_DIR="${DEVELOPER_DIR:-/Applications/Xcode.app/Contents/Developer}"

rm -rf "$BUILD"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"

echo "• Compiling icon"
xcrun actool "$HERE/AppIcon.icon" --compile "$APP/Contents/Resources" \
  --platform macosx --minimum-deployment-target 13.0 --app-icon AppIcon \
  --output-partial-info-plist "$BUILD/icon-partial.plist" --errors --warnings >/dev/null

echo "• Compiling app"
xcrun swiftc -O -swift-version 5 -target arm64-apple-macos13.0 \
  -framework Cocoa -framework WebKit \
  "$HERE/FPLOptimiser.swift" -o "$APP/Contents/MacOS/$NAME"

sed "s|__PROJECT_DIR__|$PROJECT|" "$HERE/Info.plist" > "$APP/Contents/Info.plist"
plutil -lint -s "$APP/Contents/Info.plist"

echo "• Signing (ad hoc)"
codesign --force --deep --sign - "$APP"

if [[ "${1:-}" == "--no-install" ]]; then
  echo "Built $APP"
  exit 0
fi

echo "• Installing to /Applications"
osascript -e "tell application \"$NAME\" to quit" >/dev/null 2>&1 || true
sleep 1
rm -rf "/Applications/$NAME.app"
ditto "$APP" "/Applications/$NAME.app"
# Make Finder/Dock pick up the new icon straight away.
/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister -f "/Applications/$NAME.app"
touch "/Applications/$NAME.app"
echo "Installed /Applications/$NAME.app"
