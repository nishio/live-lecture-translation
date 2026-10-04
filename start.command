#!/bin/zsh
# The launcher prepares the app; recording begins only after the user presses Start.
set -eu
export PATH="/opt/homebrew/bin:/usr/local/bin:${PATH}"
export HF_HUB_OFFLINE=1
export HF_HUB_DISABLE_TELEMETRY=1
llt_repo="${0:A:h}"
llt_python="${llt_repo}/.venv/bin/python"
llt_cloud=0
llt_check=0
llt_authorization=''
llt_keyfile=''
while (( $# )); do
  case "$1" in
    --cloud) llt_cloud=1; shift ;;
    --check) llt_check=1; shift ;;
    --authorization|--key-file)
      (( $# >= 2 )) || { print -u2 -- "Missing value for $1"; exit 1; }
      if [[ "$1" == --authorization ]]; then llt_authorization="$2"; else llt_keyfile="$2"; fi
      shift 2 ;;
    *) print -u2 -- 'Usage: ./start.command [--check] [--cloud --authorization config/authorization.json [--key-file private.env]]'; exit 1 ;;
  esac
done
cd -- "$llt_repo"
[[ -x "$llt_python" ]] || { print -u2 -- 'Run ./setup.command first.'; exit 1; }
if [[ "$llt_cloud" -eq 1 && -z "$llt_authorization" ]]; then
  print -u2 -- '--cloud requires --authorization with your explicit text scope and daily budget.'
  exit 1
fi
if /usr/sbin/lsof -nP -iTCP:8776 -sTCP:LISTEN -t >/dev/null 2>&1; then
  llt_reuse=(--url-file "${llt_repo}/results/event-audio/mac-live-continuous/url.txt")
  [[ "$llt_cloud" -eq 1 ]] && llt_reuse+=(--cloud)
  [[ "$llt_check" -eq 1 ]] && llt_reuse+=(--check)
  exec "$llt_python" audio-array/lecture_launcher.py "${llt_reuse[@]}"
fi
"$llt_python" - "$llt_cloud" "$llt_authorization" "$llt_keyfile" <<'PY'
import importlib.util
import json
import os
from pathlib import Path
import sys

if sys.version_info < (3, 12):
    raise SystemExit('Python 3.12 or newer required by the pinned dependencies.')
for name in ('numpy', 'mlx_whisper'):
    if importlib.util.find_spec(name) is None:
        raise SystemExit(f'Missing {name}; run setup.command first.')
try:
    metadata = json.loads(Path('data/event-audio/model-cache/whisper-turbo.json').read_text())
    model = Path(metadata['local_path'])
    if not (model / 'config.json').is_file() or not any(
            (model / name).is_file() for name in ('weights.npz', 'weights.safetensors')):
        raise ValueError('missing model files')
except (OSError, ValueError, KeyError, TypeError):
    raise SystemExit('Pinned ASR model is missing. Run setup.command explicitly; startup never downloads models.')
sys.path.insert(0, str(Path('audio-array').resolve()))
from lecture_capture import ensure_native_helper
ensure_native_helper()
if sys.argv[1] == '1':
    from lecture_cloud_scope import CloudScope
    import event_insights_cloud as cloud
    scope = CloudScope(sys.argv[2])
    allowance = scope.status()
    if not allowance['authorized_today'] or allowance['remaining_seconds'] <= 0:
        raise SystemExit('No microphone text allowance remains for today (Asia/Tokyo).')
    if 'gpt-6.1-sol' not in allowance['allowed_models']:
        raise SystemExit('The launcher default model gpt-6.1-sol is not authorized.')
    cloud.configure_budget_authorization(sys.argv[2])
    if sys.argv[3]:
        os.environ.pop('OPENAI_API_KEY', None)
        cloud.DOTENV_PATH = Path(sys.argv[3]).expanduser().resolve()
    if not cloud.has_api_key():
        raise SystemExit('Set OPENAI_API_KEY or provide --key-file. Key contents are never printed.')
    budget = cloud.budget_status()
    if budget['budget_usd'] <= budget['spent_usd']:
        raise SystemExit('No API budget remains for today (Asia/Tokyo).')
print('Preparation checked. No microphone, inference, or paid API request was started.')
PY
if [[ "$llt_check" -eq 1 ]]; then exit 0; fi
llt_options=(--port 8776 --open --continuous-translation --translation-interval 60 --analysis-interval 120 --language en)
if [[ "$llt_cloud" -eq 1 ]]; then
  llt_options+=(--allow-cloud --cloud-authorization "$llt_authorization" --model gpt-6.1-sol)
  [[ -n "$llt_keyfile" ]] && llt_options+=(--key-file "$llt_keyfile")
fi
print -- 'The app opens on port 8776. Choose the microphone and press Start when ready.'
print -- 'Keep the Mac awake and this terminal open. Stop in the UI, wait for saving and pending work, then exit.'
exec "$llt_python" -u audio-array/lecture_live.py "${llt_options[@]}"
