"""Verify a saved local lecture URL before reopening an already running app.

This module never starts or stops a server, microphone, model, or API request.
Only authenticated GETs to the fixed loopback origin are permitted.
"""
from __future__ import annotations

import argparse
import http.client
import json
from pathlib import Path
import re
import subprocess
from urllib.parse import parse_qs, urlsplit


class LauncherError(RuntimeError):
    pass


def read_saved_url(path: Path) -> tuple[str, str]:
    try:
        with path.open("rb") as saved:
            raw = saved.read(2049)
        if len(raw) > 2048:
            raise ValueError("too long")
        url = raw.decode("utf-8").strip()
        parts = urlsplit(url)
        params = parse_qs(parts.query, strict_parsing=True, keep_blank_values=True)
        tokens = params.get("token", [])
        if (parts.scheme != "http" or parts.hostname != "127.0.0.1"
                or parts.port != 8776 or parts.netloc != "127.0.0.1:8776"
                or parts.path != "/" or parts.fragment or set(params) != {"token"}
                or len(tokens) != 1 or not re.fullmatch(r"[A-Za-z0-9_-]{20,200}", tokens[0])
                or any(c.isspace() for c in url)):
            raise ValueError("unexpected URL")
        return url, tokens[0]
    except (OSError, UnicodeError, ValueError):
        raise LauncherError("保存された起動URLを確認できません。既存アプリのターミナルを確認してください。") from None


def get_json(path: str, token: str, *, connection_factory=http.client.HTTPConnection) -> dict:
    # HTTPConnection has no proxy or redirect support. Never send this cookie elsewhere.
    connection = connection_factory("127.0.0.1", 8776, timeout=4)
    try:
        # Credentials go only to the independently configured loopback app.
        connection.request("GET", path, headers={"Cookie": f"lecture_session_8776={token}", "Accept": "application/json"})
        response = connection.getresponse()
        if response.status != 200:
            raise LauncherError("起動中アプリの認証・識別を確認できません。古いURLまたは別のアプリの可能性があります。")
        raw = response.read(16 * 1024 * 1024 + 1)
        if len(raw) > 16 * 1024 * 1024:
            raise LauncherError("起動中アプリの応答が想定より大きいため、再表示を中止しました。")
        result = json.loads(raw)
        if not isinstance(result, dict):
            raise ValueError("not an object")
        return result
    except LauncherError:
        raise
    except (OSError, ValueError, http.client.HTTPException):
        raise LauncherError("起動中アプリの状態を確認できません。録音を止めずに、既存の画面・ターミナルを確認してください。") from None
    finally:
        connection.close()


def verify_existing(path: Path, *, getter=get_json) -> tuple[str, dict]:
    url, token = read_saved_url(path)
    identity = getter("/api/identity", token)
    if (identity.get("app_id") != "live-lecture-translation"
            or type(identity.get("schema_version")) is not int or identity["schema_version"] != 1
            or type(identity.get("cloud_enabled")) is not bool):
        raise LauncherError("ポート8776のアプリを講演ライブノートとして識別できません。自動停止・再起動は行いません。")
    state = getter("/api/state", token)
    capabilities = state.get("capabilities")
    if (type(state.get("schema_version")) is not int or state["schema_version"] != 1
            or not isinstance(state.get("capture"), dict)
            or not isinstance(capabilities, dict)
            or capabilities.get("cloud_enabled") is not identity["cloud_enabled"]):
        raise LauncherError("起動中アプリの認証済み状態が一致しません。再表示を中止しました。")
    return url, identity


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url-file", type=Path, required=True)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--cloud", action="store_true")
    args = parser.parse_args(argv)
    try:
        url, identity = verify_existing(args.url_file)
        print("起動済みの講演ライブノートと認証を確認しました。録音・設定を変更せず同じ画面を使います。")
        if args.cloud and not identity["cloud_enabled"]:
            print("既存アプリはローカル構成です。OpenAIを使う場合は画面で停止・処理完了を確認し、元のターミナルを終了してからクラウド入口を開いてください。")
        elif not args.cloud and identity["cloud_enabled"]:
            print("既存アプリは承認済みのOpenAI選択が可能な構成です。画面の設定を確認してください。")
        if not args.check:
            try:
                subprocess.run(["/usr/bin/open", url], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            except (OSError, subprocess.CalledProcessError):
                raise LauncherError("アプリは確認できましたがブラウザを開けません。既存の講演ライブノートのタブを開いてください。") from None
        return 0
    except LauncherError as exc:
        print(str(exc))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
