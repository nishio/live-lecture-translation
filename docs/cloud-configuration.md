# Cloud configuration

Cloud processing sends recognized speech and selected transcript context for translation and analysis. The normal microphone workflow does not send the recorded audio. A key alone is insufficient: the application also requires a local authorization file defining the permitted text scope and budget.

For your own recorded audio, use the complete [saved-audio walkthrough](audio-experiments.md). It includes a replay-only authorization file and key-file format. The `start.command --check --cloud` commands below check microphone startup and reject a zero microphone allowance; use `lecture_experiment.py` for recorded-audio experiments instead.

## Prepare your own authorization

Copy the deliberately disabled example:

```sh
cp config/authorization.example.json config/authorization.json
```

Review the JSON and set its fields for your own use. The example starts with `human_approved: false`, a `YYYY-MM-DD` date placeholder, and a zero budget. Set the allowed date, a daily USD limit, and a microphone-text allowance no greater than six hours. Set approval to true only after reviewing the transmission scope and limit. Do not reuse another person's working authorization.

For microphone use, replace all three `YYYY-MM-DD` placeholders with the same intended use date in Japan Standard Time: the entry in `allowed_dates`, `microphone_date`, and the key in `daily_budget_usd_by_date`. Keep `gpt-6.1-sol` in `allowed_models`; the microphone launcher selects that model.

For a saved-audio-only run, set `microphone_max_seconds` to `0` and list `input.path` and `input.sha256` from `lecture_experiment.py check` under `replay_sources`. These identify the actual PCM WAV used for recognition, including any internal conversion or selected prefix; `source` identifies the original file and is not the replay authorization target. Use the same source and `--seconds` value for `check` and `run`. A check does not save converted audio; a run verifies the converted input against authorization before inference. The application never edits authorization automatically. Zero disables every microphone-text request, including an empty one, while preserving explicitly authorized replay. Existing microphone reservations remain in the ledger when this allowance is disabled.

The authorization JSON is authoritative for the allowed daily budget. A day not explicitly authorized has a zero allowance. Day boundaries use Japan Standard Time. Text duration and USD budget are independent limits; having room in one does not override the other. The supported cloud model labels are `gpt-6-luna` and `gpt-6.1-sol`.

Keep the private authorization and accounting files out of commits. Changing authorization does not erase past use or unresolved reservations. If several intentional processes share one allowance, use one authoritative set of ledgers and a shared local-inference lock.

## Supply a key

The launcher accepts `OPENAI_API_KEY` from the environment. Alternatively, create `private.env` in the repository directory using an editor. Use this format, replacing the placeholder with your own key:

```dotenv
OPENAI_API_KEY=your-api-key
```

The file is ignored by Git. Select it explicitly when checking and starting the application:

```sh
./start.command --check --cloud --authorization config/authorization.json --key-file private.env
./start.command --cloud --authorization config/authorization.json --key-file private.env
```

The key file is private configuration and must not be committed. Do not put the key directly in a command argument or a public issue.

Without a key file, use:

```sh
./start.command --check --cloud --authorization config/authorization.json
./start.command --cloud --authorization config/authorization.json
```

`--check` inspects startup prerequisites and does not start microphone recording. The regular launch opens the local dashboard; recording still requires its start action.

<a id="lecture-settings"></a>
## 更新間隔を見積もって設定する

Translation and analysis intervals can be selected in an explicit JSON file. Estimate their cloud cost first; this setting is separate from authorization and the daily budget.

設定を指定しない場合は、翻訳60秒・要点整理120秒のままです。間隔は各処理を開始してから次の開始判定までの時間で、画面に結果が届くまでの時間ではありません。まず、標準設定と短い間隔の費用を比較します。

```sh
./start.command --estimate --hours 6
./start.command --estimate --settings config/lecture.example.json --hours 6
```

見積もりは保存済みの集計値を使うCPU計算で、API費用はUSD 0。Python 3があればセットアップ前でも実行でき、APIキー、音声認識モデル、モデル推論や録音は不要です。`--hours 6` は6時間分の費用計算であり、録音を6時間で止める設定ではありません。時間を省略すると1時間分を計算します。

例のJSONは次の内容です。

```json
{
  "schema_version": 1,
  "translation_interval_seconds": 30,
  "analysis_interval_seconds": 60
}
```

翻訳30秒・要点整理60秒は、費用を抑えつつ間隔を短くする設定例です。基本試算は約USD 2.34/時間、6時間でUSD 14.04。標準の60/120設定の同じ方法による試算はUSD 1.30/時間、6時間でUSD 7.81です。翻訳15秒・要点整理60秒も選べます。1回の要求量も変わらないと置く別試算も表示します。これらは保証範囲ではありません。[実費と計算の仮定](experiments/update-cadence-cost.md)を確認してください。

クラウド翻訳と分析はそれぞれ1件ずつ同時に実行できます。初回だけ、訳の公開または最初の試行の失敗を待ってから自動分析を開始します（翻訳対象なし・手動分析は例外）。表示する所要時間対間隔の参考比は翻訳・分析を別々に計算し、両者を足して15秒設定を不可とは判断しません。要求回数・トークン量などが同じなら費用試算の式は変わりませんが、実際には開始時刻の変化で文脈や要求回数も変わり得ます。並列実行と新しい間隔での実費・到着時間・品質は未測定です。

見積もりを確認してから自分用の設定を作ります。

```sh
cp config/lecture.example.json config/lecture.json
```

`config/lecture.json` をエディタで開き、必要なら数値を変えて、もう一度見積もります。このファイルはGitの対象外です。上の3キーが必須で、`schema_version` は1、間隔は1〜3600秒の正整数で指定します。未知のキーや重複キー、小数、文字列は受け付けません。

```sh
./start.command --estimate --settings config/lecture.json --hours 6
```

適用するには、次に新しくアプリを起動するとき、設定ファイルを明示します。

```sh
./start.command --cloud --authorization config/authorization.json --settings config/lecture.json
```

キーをファイルで渡す場合は末尾に `--key-file private.env` を追加します。通常のクラウド起動でも、有効な間隔とその費用試算を表示します。`config/lecture.json` が存在するだけでは読み込まず、`--settings` の指定が必要です。実行中のアプリを再表示してもこのJSONは適用されず、編集しても実行中の設定は変わりません。現在のセッションを終えてから次回起動に適用してください。

間隔を変えても送信許可や日次予算は増えません。見積額は費用の承認ではなく、既存の使用額・未確定予約も保持されます。保存済みAudrey再生を含む過去の結果は、その実行時の間隔と公開時刻のまま表示します。

## API billing and Codex subscription access

The application calls the OpenAI API through its existing API-key adapter. A ChatGPT subscription does not change that adapter's billing. Codex separately supports ChatGPT sign-in for subscription access and API-key sign-in for usage-based access; see the official [authentication guide](https://learn.chatgpt.com/docs/auth).

Supported programmatic interfaces include: [Codex non-interactive mode](https://learn.chatgpt.com/docs/non-interactive-mode) reuses saved CLI authentication, the [SDK](https://learn.chatgpt.com/docs/codex-sdk) controls local agents, and [app-server](https://learn.chatgpt.com/docs/app-server) supports custom clients. These documents were checked on 2026-10-05. For personal local use, authenticate through the supported Codex client; do not substitute a ChatGPT credential into this application's API-key field.

The live application has no Codex adapter. The route has now been evaluated in an isolated v0.9.0-based experiment and is **not recommended as the current pipeline replacement**: Sol was slower than API on matching translation inputs, while Luna reduced time but introduced rejected translations and semantic/source-reference errors. Read the [measured results and adoption decision](../wiki/live-and-review-pipeline.md#subscription-provider-decision) before proposing or repeating this approach. Reconsider only with a concrete changed factor and quality/latency acceptance criteria. Included allowance is not unlimited; full-event capacity and savings remain unmeasured. Keep subscription usage separate from the USD API ledger. No authentication, billing or live model settings have been changed.

## Read cost state accurately

Usage-confirmed cost and unresolved reservations are different. A request that fails without a final usage report can leave a reservation. Preserve that uncertainty until reconciliation; do not treat it as a proven charge or silently remove it.

The displayed accounting may include reservations in its committed total. Read the field meanings before subtracting a reservation again. A session's cost is also distinct from total daily spending across all work that shares its ledger.

Admission checks include a conservative reservation for the next request in addition to existing commitments. A new request may therefore be blocked before usage-confirmed expense reaches the configured limit. Preserve unresolved reservations; they are not spare budget. For session attribution and forecasting, see the [cost-accounting method](../wiki/engineering-decisions.md#attribute-and-forecast-cost-without-changing-the-running-session).

The [published experiments](experiments/README.md) report historical costs under their recorded methods and request sizes. They are not current pricing documentation or a promise that another session will fit the same amount. Model capability, available quota, and pricing must be checked for the account used to run a new experiment.

Make configuration changes between sessions and check that the runtime and authorization agree before capture begins. Retain the source session when cloud processing fails; local audio and recognition can still be valuable.
