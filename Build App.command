#!/bin/zsh
set -euo pipefail
cd "${0:A:h}"
if [[ ! -x .venv/bin/python ]]; then
  print 'Create the project .venv and install requirements-build.txt first.'
  exit 1
fi
export PYINSTALLER_CONFIG_DIR="$PWD/build/pyinstaller-cache"
export MACOSX_DEPLOYMENT_TARGET=13.0
.venv/bin/python -m pip install -r requirements-build.txt
.venv/bin/python packaging/make_icon.py
.venv/bin/python packaging/collect_licenses.py
.venv/bin/python -m PyInstaller --noconfirm --clean Frameglass.spec
/usr/bin/codesign --verify --deep --strict dist/Frameglass.app
print "Built $PWD/dist/Frameglass.app"
