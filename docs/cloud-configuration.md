# Cloud configuration

Cloud processing sends recognized speech and selected transcript context for translation and analysis. The normal microphone workflow does not send the recorded audio. A key alone is insufficient: the application also requires a local authorization file defining the permitted text scope and budget.

## Prepare your own authorization

Copy the deliberately disabled example:

```sh
cp config/authorization.example.json config/authorization.json
```

Review the JSON and set its fields for your own use. The example starts with `human_approved: false`, a `YYYY-MM-DD` date placeholder, and a zero budget. Set the allowed date, a daily USD limit, and a microphone-text allowance no greater than six hours. Set approval to true only after reviewing the transmission scope and limit. Do not reuse another person's working authorization.

For a saved-audio-only run, set `microphone_max_seconds` to `0` and list the exact input path and SHA256 under `replay_sources`. Zero disables every microphone-text request, including an empty one, while preserving explicitly authorized replay. Existing microphone reservations remain in the ledger when this allowance is disabled.

The authorization JSON is authoritative for the allowed daily budget. A day not explicitly authorized has a zero allowance. Day boundaries use Japan Standard Time. Text duration and USD budget are independent limits; having room in one does not override the other. The supported cloud model labels are `gpt-6-luna` and `gpt-6.1-sol`.

Keep the private authorization and accounting files out of commits. Changing authorization does not erase past use or unresolved reservations. If several intentional processes share one allowance, use one authoritative set of ledgers and a shared inference lock.

## Supply a key

The launcher accepts `OPENAI_API_KEY` from the environment. Alternatively, select a private key file explicitly:

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

## API billing and Codex subscription access

Version 0.9 calls the OpenAI API through its existing API-key adapter. A ChatGPT subscription does not change that adapter's billing. Codex separately supports ChatGPT sign-in for subscription access and API-key sign-in for usage-based access; see the official [authentication guide](https://learn.chatgpt.com/docs/auth).

There is a supported programmatic route to evaluate: [Codex non-interactive mode](https://learn.chatgpt.com/docs/non-interactive-mode) reuses saved CLI authentication, the [SDK](https://learn.chatgpt.com/docs/codex-sdk) controls local agents, and [app-server](https://learn.chatgpt.com/docs/app-server) supports custom clients. These documents were checked on 2026-10-05. For personal local use, authenticate through the supported Codex client; do not substitute a ChatGPT credential into this application's API-key field.

The lecture application has no Codex adapter yet. Subscription-backed processing would require a separate integration and checks for model availability, remaining allowance, structured output, latency, source provenance, cancellation, and recovery. Included allowance is not unlimited, and no full-event capacity or savings amount has been established here. Keep subscription usage separate from the USD API ledger. See the [proposed comparison](../wiki/live-and-review-pipeline.md#subscription-access-as-a-provider-candidate), starting with post-lecture text processing. No authentication or billing settings have been changed.

## Read cost state accurately

Usage-confirmed cost and unresolved reservations are different. A request that fails without a final usage report can leave a reservation. Preserve that uncertainty until reconciliation; do not treat it as a proven charge or silently remove it.

The displayed accounting may include reservations in its committed total. Read the field meanings before subtracting a reservation again. A session's cost is also distinct from total daily spending across all work that shares its ledger.

Admission checks include a conservative reservation for the next request in addition to existing commitments. A new request may therefore be blocked before usage-confirmed expense reaches the configured limit. Preserve unresolved reservations; they are not spare budget. For session attribution and forecasting, see the [cost-accounting method](../wiki/engineering-decisions.md#attribute-and-forecast-cost-without-changing-the-running-session).

The [published experiments](experiments/README.md) report historical costs under their recorded methods and request sizes. They are not current pricing documentation or a promise that another session will fit the same amount. Model capability, available quota, and pricing must be checked for the account used to run a new experiment.

Make configuration changes between sessions and check that the runtime and authorization agree before capture begins. Retain the source session when cloud processing fails; local audio and recognition can still be valuable.
