#!/usr/bin/env python3
"""Explicitly download the pinned ASR model; do not import MLX or run inference."""
from __future__ import annotations

import json
import os
from pathlib import Path

REPO = "mlx-community/whisper-large-v3-turbo"
REVISION = "a4aaeec0636e6fef84abdcbe3544cb2bf7e9f6fb"
ROOT = Path(__file__).resolve().parents[1]


def main():
    from huggingface_hub import snapshot_download

    cache = ROOT / "data/event-audio/model-cache"
    cache.mkdir(parents=True, exist_ok=True)
    # Do not copy another application's model symlinks or private runtime.
    os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
    local = Path(snapshot_download(repo_id=REPO, revision=REVISION,
                                  local_dir=cache / "whisper-turbo",
                                  allow_patterns=["*.json", "*.safetensors", "*.npz"]))
    if not (local / "config.json").is_file() or not any(
            (local / name).is_file() for name in ("weights.npz", "weights.safetensors")):
        raise SystemExit("Model download is incomplete; metadata was not updated.")
    metadata = {"repo": REPO, "revision": REVISION, "local_path": str(local.resolve())}
    pending = cache / "whisper-turbo.json.tmp"
    pending.write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
    pending.replace(cache / "whisper-turbo.json")
    print("Pinned ASR model is ready. No model inference was run.")


if __name__ == "__main__":
    main()
