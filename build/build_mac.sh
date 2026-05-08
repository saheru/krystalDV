#!/usr/bin/env bash
# ============================================================
#  Krystal Data Vision — one-click macOS build
#  Run from the project root:
#      bash build/build_mac.sh
#  Requires: Python 3.11+, Xcode CLT (for iconutil; pre-installed).
# ============================================================
set -euo pipefail

cd "$(dirname "$0")/.."

echo "[1/5] Creating virtualenv if missing..."
if [ ! -d ".venv" ]; then
    python3 -m venv .venv
fi
# shellcheck disable=SC1091
source .venv/bin/activate

echo "[2/5] Installing dependencies..."
python -m pip install --upgrade pip >/dev/null
python -m pip install -e ".[dev]"
python -m pip install pyinstaller pillow >/dev/null

echo "[3/5] (Re)generating icons..."
python build/make_icon.py

echo "[4/5] Running tests..."
python -m pytest -q

echo "[5/5] Building .app with PyInstaller..."
rm -rf build/build dist/kdv dist/kdv.app dist/kdv-mac.zip
pyinstaller build/kdv.spec --clean --noconfirm

if [ ! -d "dist/kdv.app" ]; then
    echo "*** Build failed: dist/kdv.app missing"
    exit 1
fi

echo "[6/6] Zipping kdv.app for distribution..."
( cd dist && zip -qr kdv-mac.zip kdv.app )

cat <<EOF

=====================================================
 Build success!
 - Run locally   :  open dist/kdv.app
 - Distribute    :  dist/kdv-mac.zip   (send to users)

 Note for Mac users: first launch may show
 "App can't be opened because Apple cannot check it for malicious software".
 Right-click the app → Open → confirm. Only required once per Mac.
 Or remove quarantine attribute beforehand:
     xattr -dr com.apple.quarantine /path/to/kdv.app
=====================================================
EOF
