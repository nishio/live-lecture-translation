# 持ち込み音声で比較する

`audio-array/lecture_experiment.py` は、同じ音声を使って処理条件を比較する入口です。既存の再生処理を利用し、実行ごとに独立した保存先を作ります。既定ではローカル音声認識だけを実行します。マイクは使いません。

入力は **16 kHz・モノラル・16 bit PCMの非圧縮WAV** です。形式変換は行いません。公開してよい合成音声、または利用を許可された音声を用意してください。実行環境と認識モデルは事前に `./setup.command` で準備します。

## 単独講演の入力例

[Audrey Tang Remarks in Plurality Seoul, 2023-12-02](https://www.youtube.com/watch?v=4_tge6XJhGA) は、Code for Japanが公開したAudrey Tangによる約5分26秒のスピーチです。この音声から実際に生成した[原文・日本語訳・理解支援のサンプル](../samples/audrey-plurality-seoul-2023/README.md)を公開しています。サンプルを読むだけなら音声の取得や推論は不要です。

帰属は元動画のYouTube表示に従い **CC BY（Creative Commons Attribution）** と記載します。保存した動画メタデータには版番号がありません。[YouTube公式ヘルプ](https://support.google.com/youtube/answer/2797468?hl=en)が現在参照する[CC BY 4.0の説明](https://creativecommons.org/licenses/by/4.0/)は参考資料であり、元動画の版番号を確定する記録とは区別します。[公開告知](https://scrapbox.io/plurality-japanese/雑談ページ2)には同じ動画IDとCC0表記がありますが、本サンプルをCC0と表示する根拠には用いません。サンプルには元動画、投稿者、話者、ASR・翻訳・要約などの改変を明記しています。

同じ入力を用意する場合は、リポジトリのディレクトリで次を実行します。`yt-dlp` と `ffmpeg` は別途必要です。取得先はgit管理対象外の `data/public-audio/` です。取得コマンドはネットワーク通信とファイル保存を行いますが、ASRや翻訳APIは呼びません。

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

## まず確認する

リポジトリのディレクトリで実行します。

```sh
.venv/bin/python audio-array/lecture_experiment.py check /absolute/path/lecture.wav
```

`check` と `--help` は認識・推論・通信・録音・ファイル作成を行いません。`check` はWAVの形式、全フレームを読めること、長さ、ファイル全体のSHA256を確認し、JSONを表示します。実行環境や認可の有効性を確認した意味ではありません。通常の持ち込み音声では、ファイルのパスとハッシュも私的な情報として扱ってください。上記の公開サンプルでは、同じ入力を確認するためのハッシュだけを公開しています。

`cost.historical_sol_example_usd` は、公開済みの[過去の費用観測](experiments/development-handoff.md#partial-live-cost-observation)を音声の長さに比例させた参考値です。当時のSol・翻訳60秒／分析120秒という条件による例示で、現在の料金や、このファイルの見積保証ではありません。加速再生、モデルや更新間隔の変更、終了時の処理、失敗・再試行でも費用は変わります。ASRのみの場合は `planned_api_usd` が0、クラウドを使う場合は未確定のため `null` です。開発用AIと電力の費用は測定しません。

## 明示的にローカル認識を始める

```sh
.venv/bin/python audio-array/lecture_experiment.py run /absolute/path/lecture.wav --label baseline
```

`run` は新しいローカルASRを開始します。以前の文字起こしを再利用する操作ではありません。既定では原音の速度で再生し、15秒単位で認識します。ローカルLLMによる分析もクラウド送信も行いません。入力原本は書き換えません。

先頭部分を比較する例です。`--seconds` が音声全体より長い場合は、全体の長さまでに制限します。

```sh
.venv/bin/python audio-array/lecture_experiment.py check /absolute/path/lecture.wav --seconds 90
.venv/bin/python audio-array/lecture_experiment.py run /absolute/path/lecture.wav --seconds 90 --label chunks15
.venv/bin/python audio-array/lecture_experiment.py run /absolute/path/lecture.wav --seconds 90 --chunk-seconds 10 --label chunks10
```

認識だけを早く処理したい場合は `--pace accelerated` を指定できます。ただし、加速処理はライブの遅延測定になりません。クラウドを使う場合は実時間の更新スケジュールとの関係も変わるため、実時間再生の結果と混ぜないでください。

## 認識の区切りと更新用バッファを比較する

独立した区切りと更新可能な音声窓の比較は、[ASR比較ツールの手順](asr-probe.md)を参照してください。

## 必要な場合だけクラウド処理を加える

既存の[クラウド認可](cloud-configuration.md)を使います。`replay_sources` に入力ファイルの絶対パスと、`check` で確認したSHA256を明示してください。マイク用の許可だけでは持ち込み音声を送信できません。認可ファイルには許可日、モデル、支出上限、承認など既存の必須設定も必要です。認可ファイルは自動作成・変更しません。

持ち込み音声だけを許可する場合は、`microphone_max_seconds` を `0` にします。マイクの本文送信を許可せず、一覧にある音声だけを実行できます。

```json
"replay_sources": [
  {"path": "/absolute/path/lecture.wav", "sha256": "checkで確認した64桁のSHA256"}
]
```

次のコマンドは新しいローカルASRと、有料のクラウド翻訳・分析を開始します。録音音声は送信せず、既存アプリと同じテキスト処理を使います。

```sh
.venv/bin/python audio-array/lecture_experiment.py run /absolute/path/lecture.wav \
  --seconds 90 --label sol-baseline --cloud \
  --authorization config/authorization.json --model gpt-6.1-sol
```

APIキーは `OPENAI_API_KEY` または `--key-file /absolute/path/private.env` で指定します。対応モデルは `gpt-6.1-sol` と `gpt-6-luna` です。入力と出力の保存先は実行ごとに分かれますが、既存の推論ロック、送信範囲台帳、API費用台帳は共有します。別の台帳を作って予算を増やすことはありません。許可・予算は実際の送信前にも既存処理が再確認します。

比較するときは同じ入力、同じ範囲、同じ再生速度を保ち、モデル、チャンク長、更新間隔などを一度に変えないでください。`--translation-interval` と `--analysis-interval` は実行間隔の比較用で、既定値は60秒と120秒です。

## 結果と未完了を確認する

実行ごとに次のディレクトリを作ります。既存の録音アプリのポート8776や保存先は使いません。

- `data/audio-experiments/<run-id>/`：既存再生処理が保存する音声とセッションデータ。
- `results/audio-experiments/<run-id>/experiment.json`：入力ハッシュ・範囲・条件・経過時間・段階別の状態・費用記録への参照。
- 同じ結果ディレクトリ内の `Lecture-.../`：文字起こし、翻訳、分析、実行時ソースと既存の計測記録。
- `process.log`：プロセスの出力。認証付きローカルURLなども含み得るため非公開。

終了コード0と `completion_confirmed: true` は、必要な処理段階が完了し、入力の同一性を実行前後に確認できたことを表します。意味の正確さや聞き取り精度を証明するものではありません。失敗・中断・状態不明は終了コード2で、既存の結果と未確定の費用予約を残します。Ctrl+Cはこの実験のプロセスへ中断を伝えます。終了を確認できない場合は `child.shutdown_confirmed: false` とプロセスIDを記録します。保存された未完了データの再開や、過去のAPI要求の再試行を自動で行う入口ではありません。

入力の `sha256` と `selected_frames` が同じ実行を比較します。通常の実行出力はignored領域の私的な作業データであり、コミットしません。今回の[公開サンプル](../samples/audrey-plurality-seoul-2023/README.md)は、公開音声由来の生成結果について明示的に公開を許可された例外です。許可されたファイルと項目だけを抽出しており、実行ディレクトリ全体を公開する手順ではありません。ローカルモデルやプロンプトなどの実行時ソース記録も照合し、同じファイルでも内容を編集したものを同じ入力と扱わないでください。

保存済み音声の試験では、マイク収録や発話からブラウザ表示までの遅延は確認できません。内容は[読み返しの評価基準](../wiki/migration-follow-ups.md#field-feedback-explanation-granularity-and-speaker-claims)で確認し、ライブ中の注意負担は別の聴講課題として評価します。

## 保存結果を冒頭から実時間で観察する

`lecture_demo.py` は、既に生成した結果を当時の公開時刻に合わせて表示します。録音・ASR・翻訳を再実行せず、追加のAPIリクエストも送りません。実行結果に残っている `Lecture-.../` と、その実行に使ったWAVを指定します。

```sh
.venv/bin/python audio-array/lecture_demo.py \
  --session results/audio-experiments/実行ID/Lecture-セッションID \
  --audio-file data/public-audio/audrey-plurality-seoul-2023/audrey-plurality-seoul.wav \
  --port 0
```

表示されたローカルURLを開き、「再生」を押すと冒頭から1倍速で進みます。一時停止、先頭に戻る操作、スライダーでの移動ができます。音声付きでは音声の再生位置に画面を合わせ、音声が終わった後も最後の生成結果が届く時点まで進めます。巻き戻すと、その時点でまだ公開されていなかった原文・訳・理解支援は表示から外れます。

待ち時間の円グラフも再生位置に合わせて減り、一時停止中は止まります。「認識中」「生成中」「順番待ち」の灰色の円は残り時間を示しません。これらの処理の完了予定時間は表示していません。

`--audio-file` を省略すると音声なしで表示だけを再生します。`--check` を付けると保存記録と音声の形式を検証し、サーバーを起動しません。閲覧には認証付きのローカルサーバーを使い、指定した音声1ファイルだけを配信します。WAVや認証付きURLをリポジトリへ追加する必要はありません。

原文・分析は `measurements.jsonl`、継続翻訳は保存された公開記録に基づいて出します。発話の終了時刻で先回りして結果を見せるものではありません。公開時刻が不足・矛盾する記録は推定で埋めず、エラーにします。音声0秒を記録上のセッション開始に合わせますが、元の音声入力開始の厳密な時刻は未記録です。今回の分割記録からは約0.14秒のずれが推定されます。ブラウザや出力デバイスの遅延も含む厳密な同期精度や、会場の実音、新しい推論速度を計測する試験ではありません。加速実験の記録は音声付きの1倍速再現に使えません。

公開用の `samples/` は読むための抜粋であり、この再生に必要な生の計測記録は含みません。自分で取得した音声から実験を実行すると、その保存結果を上のコマンドで観察できます。
