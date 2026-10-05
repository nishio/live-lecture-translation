#!/bin/zsh
# Play the bundled Audrey recording and saved results; no model or API setup.
set -eu
export PATH="/opt/homebrew/bin:/usr/local/bin:${PATH}"
export PYTHONDONTWRITEBYTECODE=1
llt_demo_repo="${0:A:h}"
llt_demo_python="${llt_demo_repo}/.venv/bin/python"
if [[ ! -x "$llt_demo_python" ]]; then
  llt_demo_python="$(command -v python3)" || {
    print -u2 -- 'Python 3.9以上が必要です。デモにはsetup.commandやAPIキーは不要です。'
    exit 1
  }
fi
cd -- "$llt_demo_repo"
exec "$llt_demo_python" -S audio-array/lecture_demo.py \
  --session samples/audrey-plurality-seoul-2023/demo/session \
  --audio-file samples/audrey-plurality-seoul-2023/demo/audio.wav \
  --port 0 --open "$@"
