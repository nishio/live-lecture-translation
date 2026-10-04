#!/bin/zsh
# Explicit installation only: never capture audio or call a paid inference API.
set -eu
export PATH="/opt/homebrew/bin:/usr/local/bin:${PATH}"
llt_repo="${0:A:h}"
cd -- "$llt_repo"
if [[ "$(uname -s)" != Darwin || "$(uname -m)" != arm64 ]]; then
  print -u2 -- 'v0.9 requires macOS on Apple Silicon for native capture and MLX ASR.'
  exit 1
fi
command -v python3 >/dev/null || { print -u2 -- 'Install Python 3.12 or newer first.'; exit 1; }
command -v swiftc >/dev/null || { print -u2 -- 'Install Apple Command Line Tools first.'; exit 1; }
python3 -c 'import sys; assert sys.version_info >= (3, 12), "Python 3.12 or newer required"'
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
print -- 'Downloading the pinned local ASR model into this repository. No audio is captured or sent.'
.venv/bin/python scripts/prepare_model.py
print -- 'Setup complete. See README.md for text-only cloud authorization and starting the app.'
