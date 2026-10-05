# 自分の音声で試す

手元の録音から、文字起こし・日本語訳・要点を生成し、音声と一緒に画面で確認する手順です。**最初は2分以内の英語音声**で試します。マイクは使いません。音声はMac内で処理し、日本語訳と要点を生成する場合は認識したテキストをクラウドに送ります。

すべてのコマンドは、取得したリポジトリのディレクトリで実行してください。以下の `data/my-audio/` はgit管理対象外です。

## 1. 実行環境を準備する

Apple Silicon搭載Mac、Python 3.12以降、Apple Command Line ToolsのSwiftコンパイラが必要です。初回だけ実行します。

```sh
./setup.command
```

依存パッケージと音声認識モデルをダウンロードするので、ネット接続と時間が必要です。セットアップは録音・音声認識・有料APIを実行しません。音声変換用のデコーダーも同梱パッケージ `imageio-ffmpeg==0.6.0` として準備します。

すでにセットアップ済みの場合は、変換用パッケージだけ追加できます。モデルのセットアップをやり直す必要はありません。

```sh
.venv/bin/python -m pip install imageio-ffmpeg==0.6.0
```

## 2. 自分の音声を置く

```sh
mkdir -p data/my-audio
```

Finderで上のディレクトリに音声をコピーします。ここでは `input.m4a` という名前にしたものとして進めます。MP3・MP4などの音声付き動画・WAVも、以降のファイル名を置き換えれば同じ手順です。自分がこの用途に利用できる音声を選んでください。

**手動でWAVに変換する必要はありません。** アプリが必要な範囲を16 kHz・モノラル・16 bit PCMのWAVへ変換し、元ファイルをそのまま残します。複数の音声トラックがある場合は先頭のトラックを使います。入力はMac上の単一ファイルで、URLやプレイリストには対応していません。

## 3. 入力を確認する

```sh
.venv/bin/python audio-array/lecture_experiment.py check \
  "data/my-audio/input.m4a" --seconds 120 > data/my-audio/check.json
cat data/my-audio/check.json
```

エラーが出ずJSONが表示されたら、次を確認します。

| 項目 | 確認すること |
| --- | --- |
| `source.path` / `source.sha256` | 元ファイルの絶対パスと識別値 |
| `input.duration_seconds` | 120秒以内で、試したい音声の長さになっている |
| `input.path` | 認識に使うWAVの絶対パス。クラウド設定で使う |
| `input.sha256` | 認識に使うWAVを識別する64桁の値。クラウド設定で使う |

`--seconds 120` は先頭の最大120秒を選びます。`check` は必要な変換と入力の読み取りを一時ファイルで確認し、変換用の一時ファイルを削除します。変換が必要な場合、表示された `input.path` にWAVを保存するのは `run` の開始前検査後です。すでに適切な形式のWAVを全体利用する場合は、元のファイルをそのまま使います。認識・推論・API通信は行いません。上の `>` はチェック結果をローカルファイルに保存する指定です。モデル・APIキー・許可設定の有効性は、このチェックでは確認しません。

## 4. 文字起こしだけか、翻訳込みかを選ぶ

**AかBのどちらかを選びます。** 日本語訳・要点まで試す場合は、Aを実行せずBへ進めます。どちらの `run` も新しい音声認識を行うため、Aの後にBを実行すると認識もやり直します。

### A. 文字起こしだけ試す（APIキー不要）

```sh
.venv/bin/python audio-array/lecture_experiment.py run \
  "data/my-audio/input.m4a" --seconds 120 --label first-local \
  > data/my-audio/run-result.json
```

認識はMac上で行い、API料金は発生しません。再生画面には原文だけが出て、日本語訳・要点は空欄になります。終了したら、下の「実行完了を確認する」へ進みます。

### B. 日本語訳と要点も生成する（有料API）

この経路はOpenAIのAPIキーとAPI利用枠を使います。[課金方式の説明](cloud-configuration.md#api-billing-and-codex-subscription-access)も確認してください。

エディタで **`config/experiment.local.json`** を作り、次の全体を保存します。既存の `config/authorization.json` を上書きする必要はありません。

```json
{
  "human_approved": false,
  "destination": "https://api.openai.com/v1/responses",
  "raw_audio_allowed": false,
  "allowed_dates": ["YYYY-MM-DD"],
  "allowed_models": ["gpt-6.1-sol"],
  "microphone_date": "YYYY-MM-DD",
  "microphone_max_seconds": 0,
  "daily_budget_usd_by_date": {"YYYY-MM-DD": 0},
  "replay_sources": [
    {"path": "ここをinput.pathの絶対パスに置換", "sha256": "ここをinput.sha256の64桁に置換"}
  ]
}
```

以下を編集します。

- 3か所の `YYYY-MM-DD`：実行する日付（日本時間）。`microphone_date` も同じ日付にします。マイクを使わなくても現在の設定形式では必須です。
- `daily_budget_usd_by_date` の `0`：自分が許容する1日あたりの上限金額（米ドル）。例として `1.0` と設定できますが、これは費用見積や完了保証ではありません。
- `replay_sources` の `path` と `sha256`：手順3の **`input.path` と `input.sha256`** をそのまま使います。元ファイルを示す `source` の値とは異なります。
- 送信対象と金額を確認後、`human_approved` を `true` にします。`raw_audio_allowed: false` と `microphone_max_seconds: 0` はそのままです。

次に、エディタで **`config/experiment.local.env`** を作り、自分のキーを保存します。

```dotenv
OPENAI_API_KEY=ここに自分のAPIキー
```

この2ファイルはgit管理対象外です。キーをターミナルのコマンド行へ直接書く必要はありません。

```sh
.venv/bin/python audio-array/lecture_experiment.py run \
  "data/my-audio/input.m4a" --seconds 120 --label first-cloud --cloud \
  --authorization config/experiment.local.json \
  --key-file config/experiment.local.env --model gpt-6.1-sol \
  > data/my-audio/run-result.json
```

実行直前にモデルファイル・変換した対象音声・日付・許可・キーの有無・残予算を検査します。`check` と `run` には同じ元ファイルと `--seconds` を指定してください。元ファイルや変換条件を変えた場合は、チェックと許可設定を更新します。アプリが許可設定を書き換えることはありません。API側でキーが有効か、利用枠があるかは実際の送信時に判明します。この音声実験では `start.command --check --cloud` は使いません。そのコマンドはマイク利用向けなので、上の音声専用設定ではマイク許可なしとして止まります。

実行ごとに出力は分かれますが、送信範囲・費用台帳と推論ロックは既存のものを共有します。別の設定ファイルを作っても過去の使用量や未確定予約は消えません。

### 実行完了を確認する

**2分の音声なら少なくとも2分と残り処理の時間**がかかります。終了までターミナルに進捗が出ないのは現在の動作です。音声は自動でスピーカー再生されません。画面と音声を観察するのは次の手順です。

コマンドが終了して入力待ちに戻ったら、確認します。

```sh
cat data/my-audio/run-result.json
```

`status` が `completed`、`completion_confirmed` が `true` なら処理完了です。Bでは `cost_report.confirmed_api_usd` が使用量を確認できたAPI費用、`cost_report.retained_reservation_usd` が未確定予約です。予約額は確定料金と区別してください。AのAPI費用は `cost_report.additional_api_usd: 0` で確認できます。ローカル処理の電力や開発用AIの費用は未測定です。

ファイルが空なら起動前の検査で失敗した可能性があるため、ターミナルのエラーを確認します。`incomplete` なら保存結果とエラーを確認し、完了扱いにはしません。[つまずいたとき](#つまずいたとき)も参照してください。

## 保存結果を冒頭から実時間で観察する

次のブロックは、今回の `run-result.json` から保存先と入力WAVを取り出してビューアを開きます。実行IDを探して書き写す必要はありません。録音・認識・翻訳を再実行せず、追加APIリクエストも送りません。

```sh
.venv/bin/python - <<'PY'
import json
from pathlib import Path
import subprocess
import sys

result_path = Path("data/my-audio/run-result.json")
if not result_path.exists() or not result_path.read_text().strip():
    raise SystemExit("実行結果がありません。runの終了とターミナルのエラーを確認してください。")
result = json.loads(result_path.read_text())
if not result.get("completion_confirmed"):
    raise SystemExit("処理は未完了です。run-result.jsonと保存された記録を確認してください。")
run = json.loads(Path(result["manifest"]).read_text())
subprocess.run([
    sys.executable, "audio-array/lecture_demo.py",
    "--session", str(Path(run["state_path"]).parent),
    "--audio-file", run["input"]["path"], "--port", "0",
], check=True)
PY
```

表示されたローカルURLを開き、「再生」を押すと冒頭から1倍速で進みます。一時停止、先頭に戻る操作、スライダーでの移動ができます。音声付きでは音声の再生位置に画面を合わせ、音声が終わった後も最後の生成結果が届く時点まで進めます。巻き戻すと、その時点でまだ公開されていなかった速報・原文・訳・理解支援は表示から外れます。速報も実際の公開時刻で再生します。古い保存結果に速報が後付けされることはありません。

閲覧を終えるときは、このコマンドを実行したターミナルでCtrl+Cを押します。保存結果は残ります。

待ち時間の円グラフも再生位置に合わせて減り、一時停止中は止まります。「認識中」「生成中」「順番待ち」の灰色の円は残り時間を示しません。これらの処理の完了予定時間は表示していません。

`--audio-file` を省略すると音声なしで表示だけを再生します。`--check` を付けると保存記録と音声の形式を検証し、サーバーを起動しません。閲覧には認証付きのローカルサーバーを使い、指定した音声1ファイルだけを配信します。WAVや認証付きURLをリポジトリへ追加する必要はありません。

原文・分析は `measurements.jsonl`、継続翻訳は保存された公開記録に基づいて出します。発話の終了時刻で先回りして結果を見せるものではありません。公開時刻が不足・矛盾する記録は推定で埋めず、エラーにします。音声0秒を記録上のセッション開始に合わせますが、元の音声入力開始の厳密な時刻は未記録です。公開Audreyサンプルを生成した過去の実行では、分割記録から約0.14秒のずれが推定されています。自分の音声の実行に同じ値が当てはまるとは限りません。ブラウザや出力デバイスの遅延も含む厳密な同期精度や、会場の実音、新しい推論速度を計測する試験ではありません。加速実験の記録は音声付きの1倍速再現に使えません。

公開用の `samples/` は読むための抜粋であり、この再生に必要な生の計測記録は含みません。自分で取得した音声から実験を実行すると、その保存結果を上のコマンドで観察できます。

## 条件を変えて比較する

一度表示まで確認できたら、`check` と `run` の `--seconds 120` を外して全体を使ったり、`--chunk-seconds 10` で蓄積する原文の認識単位を変えたりできます。原文の既定は15秒、翻訳の開始間隔は60秒、整理は120秒です。速報は別に3秒ごとに直近最大15秒を認識し直します。`--provisional-refresh-seconds` と `--provisional-window-seconds` で変更でき、更新間隔に `0` を指定すると速報を無効にします。初期化や処理待ちにより、実際の表示は指定間隔より遅れる場合があります。英語以外には `--language auto` または `--language ja` を指定します。日本語の原文は日本語翻訳の対象にはなりません。

毎回の `run` は独立した実験を作ります。比較する実行は `--label` と結果JSONの保存名を変えて記録し、同じ入力・同じ範囲から一つずつ条件を変えます。入力のパスや内容を変更した場合は、`check` とクラウド許可のパス・ハッシュも更新してください。

`--seconds 90` などで範囲を指定した場合も、その範囲だけのWAVを自動で用意します。上の再生コマンドは実行記録の `input.path` を使うので、部分実行でも音声の長さが一致します。ビューアに元のM4Aや全長WAVを渡す必要はありません。

`--pace accelerated` は認識を速く進める比較用です。加速実験の記録は音声付きの1倍速再生には使えません。表示だけなら、次の直接起動コマンドから `--audio-file` を外します。

保存先を直接指定する場合は、`run-result.json` の `manifest` にあるJSONを開き、その `state_path` の親ディレクトリを `--session` に渡します。

```sh
.venv/bin/python audio-array/lecture_demo.py \
  --session "results/audio-experiments/実行ID/Lecture-セッションID" \
  --audio-file "実行記録のinput.pathにあるWAVの絶対パス" --port 0
```

独立した区切りと更新可能な音声窓の比較は、[ASR比較ツールの手順](asr-probe.md)を参照してください。この別ツールの入力は引き続き16 kHz・モノラル・PCM16 WAVです。

## つまずいたとき

| 状態・エラー | 確認すること |
| --- | --- |
| `.venv/bin/python` がない／モデル未準備 | リポジトリ内で `./setup.command` が完了したか確認する |
| 音声デコーダーが見つからない | `.venv/bin/python -m pip install imageio-ffmpeg==0.6.0` で変換用パッケージを追加する |
| 変換エラー／音声トラックがない | 音声を含むローカルファイルか確認する。対応外の形式や破損ファイルは認識を始めずに停止する |
| `Experiment not started` | ターミナルに出た理由を確認する。`check` の成功はモデルやクラウド設定の成功を意味しない |
| 許可日・モデル・音声ハッシュが違う | 許可設定と今回の `check` の値を照合する。日付の区切りは日本時間 |
| APIキー／利用枠／予算のエラー | 自分のAPI設定と上限を確認する。未確定予約や台帳を削除しない |
| 日本語訳・要点が空欄 | Aでは原文だけを表示する。Bでも最初の生成結果が届くまで待ち時間がある |
| 再生音声の長さが一致しない | 上の再生コマンドで、実行記録の `input.path` にあるWAVを使う |
| 音声付き再生でpaceのエラー | 加速実験の結果を使っていないか確認する。最初の手順は既定の実時間で実行する |

`check` の `cost.historical_sol_example_usd` は[過去の費用観測](experiments/development-handoff.md#partial-live-cost-observation)からの参考値です。今回の実測料金や見積保証ではありません。ASRのみの `planned_api_usd` は0、クラウド利用時は未確定のため `null` です。今回の費用は終了後の `cost_report` で確認します。

## 結果と未完了を確認する

実行時には次の場所を使います。実験ごとの結果を分け、同じ変換済みWAVは共有します。既存の録音アプリのポート8776や保存先は使いません。

- `data/audio-experiments/<run-id>/`：既存再生処理が保存する音声とセッションデータ。
- `data/audio-imports/<WAVのSHA256>/audio.wav`：変換や切り出しで作った認識・再生用WAV。開始前検査を通った `run` が保存する。すでに指定形式のWAV全体を使う場合は元ファイルを使う。
- `results/audio-experiments/<run-id>/experiment.json`：入力ハッシュ・範囲・条件・経過時間・段階別の状態・費用記録への参照。
- 同じ結果ディレクトリ内の `Lecture-.../`：文字起こし、翻訳、分析、実行時ソースと既存の計測記録。
- `process.log`：プロセスの出力。認証付きローカルURLなども含み得るため非公開。

終了コード0と `completion_confirmed: true` は、必要な処理段階が完了し、入力の同一性を実行前後に確認できたことを表します。意味の正確さや聞き取り精度を証明するものではありません。失敗・中断・状態不明は終了コード2で、既存の結果と未確定の費用予約を残します。Ctrl+Cはこの実験のプロセスへ中断を伝えます。終了を確認できない場合は `child.shutdown_confirmed: false` とプロセスIDを記録します。保存された未完了データの再開や、過去のAPI要求の再試行を自動で行う入口ではありません。

元ファイルの識別情報は `source`、実際に認識したWAVの情報は `input` に残ります。入力の `sha256` と `selected_frames` が同じ実行を比較します。通常の実行出力はignored領域の私的な作業データであり、コミットしません。今回の[公開サンプル](../samples/audrey-plurality-seoul-2023/README.md)は、公開音声由来の生成結果について明示的に公開を許可された例外です。許可されたファイルと項目だけを抽出しており、実行ディレクトリ全体を公開する手順ではありません。ローカルモデルやプロンプトなどの実行時ソース記録も照合し、同じファイルでも内容を編集したものを同じ入力と扱わないでください。

保存済み音声の試験では、マイク収録や発話からブラウザ表示までの遅延は確認できません。内容は[読み返しの評価基準](../wiki/migration-follow-ups.md#field-feedback-explanation-granularity-and-speaker-claims)で確認し、ライブ中の注意負担は別の聴講課題として評価します。

## 単独講演の入力例

[Audrey Tang Remarks in Plurality Seoul, 2023-12-02](https://www.youtube.com/watch?v=4_tge6XJhGA) は、Code for Japanが公開したAudrey Tangによる約5分26秒のスピーチです。この音声から実際に生成した[原文・日本語訳・理解支援のサンプル](../samples/audrey-plurality-seoul-2023/README.md)を公開しています。サンプルを読むだけなら音声の取得や推論は不要です。

帰属は元動画のYouTube表示に従い **CC BY（Creative Commons Attribution）** と記載します。保存した動画メタデータには版番号がありません。[YouTube公式ヘルプ](https://support.google.com/youtube/answer/2797468?hl=en)が現在参照する[CC BY 4.0の説明](https://creativecommons.org/licenses/by/4.0/)は参考資料であり、元動画の版番号を確定する記録とは区別します。[公開告知](https://scrapbox.io/plurality-japanese/雑談ページ2)には同じ動画IDとCC0表記がありますが、本サンプルをCC0と表示する根拠には用いません。サンプルには元動画、投稿者、話者、ASR・翻訳・要約などの改変を明記しています。

過去の公開サンプルと同じ入力を再現する場合は、リポジトリのディレクトリで次を実行します。この再現手順には `yt-dlp` と `ffmpeg` が別途必要です。新しい実験では取得したM4Aを手順3の `check` と `run` に直接渡せるので、手動変換は不要です。取得先はgit管理対象外の `data/public-audio/` です。取得コマンドはネットワーク通信とファイル保存を行いますが、ASRや翻訳APIは呼びません。

```sh
mkdir -p data/public-audio/audrey-plurality-seoul-2023
yt-dlp --ignore-config --no-playlist --no-overwrites -f 140 --write-info-json \
  -o 'data/public-audio/audrey-plurality-seoul-2023/%(id)s.%(ext)s' \
  'https://www.youtube.com/watch?v=4_tge6XJhGA'
ffmpeg -nostdin -n -xerror \
  -i data/public-audio/audrey-plurality-seoul-2023/4_tge6XJhGA.m4a \
  -ar 16000 -ac 1 -c:a pcm_s16le \
  data/public-audio/audrey-plurality-seoul-2023/audrey-plurality-seoul.wav
.venv/bin/python audio-array/lecture_experiment.py check \
  data/public-audio/audrey-plurality-seoul-2023/audrey-plurality-seoul.wav
```

`-f 140` は今回使用したAAC音声の形式IDです。利用できなければ別形式へ自動で切り替えず、取得条件を確認してください。`ffmpeg -xerror` はデコードエラー時に失敗させ、`-n` は既存WAVの上書きを防ぎます。今回の変換結果は325.567秒、5,209,072フレームでした。再取得した入力は `check` の長さ・SHA256をサンプルの[provenance.json](../samples/audrey-plurality-seoul-2023/provenance.json)と照合し、一致しない入力を同一条件と扱わないでください。

**音声・動画・配布字幕・取得メタデータ原本はリポジトリに含めません。** 公開英語・日本語字幕を別途取得して比較する場合もignored領域に置き、アプリのASR・翻訳出力と区別します。上の準備と入力チェックは、認識や翻訳の実行・精度検証ではありません。
