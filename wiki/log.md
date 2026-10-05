# Wiki log

## [2026-10-05] experiment | Codex subscription from stable v0.9.0

Created the user-requested isolated `experiment/codex-subscription` worktree from
v0.9.0 and added an opt-in synthetic-text probe. Reused baseline translation and
analysis contracts through a ChatGPT-authenticated, isolated Codex CLI adapter.
Preserved the concurrent development checkout, live runtime, API authorization,
USD ledger, and baseline tag.

Observed two accepted translations at 11.009–11.378 seconds and two accepted
analyses at 22.878–23.673 seconds. Recorded three earlier failed workloads and
four unstarted workloads without relabeling them as completed. Fixed reserved
provider configuration incompatibility, narrow startup-advisory handling, and
structured-schema adaptation. Kept all prompts, outputs, events and state under
ignored results; published only methodology and content-free measurements.

Model-assisted spot checks retained the main translation facts/qualifiers;
analysis still showed small interpretation shifts. This does not establish
live latency, lecture comprehension, quota capacity, or savings. No API-key
calls were requested. Codex reported 53,706 input and 1,974 output tokens across
five invocations, including one rejected result; two failed invocations lacked
usage. API charge reconciliation, subscription quota/fee allocation, development
usage and electricity remain unmeasured.

Validation in the worktree's own test environment ran 345 CPU tests: 343 passed,
two optional native capture tests skipped. Both Node UI checks passed. No routine
test performed capture, ASR or real model inference. Updated the topic and
canonical experiment/configuration/architecture documents, the index, then this
log. The live provider remains unchanged; later integration is separate work.

## [2026-10-04] file back | Standalone 0.9 engineering baseline

Created [engineering decisions](engineering-decisions.md) as a new distillation for the standalone repository. Preserved three reusable themes: capture evidence independently of interpretation, separate complete translation from selective understanding support, and validate distinct timing and quality claims separately. Added the operational consequences for shared accounting, process state, and browser authentication.

Added links to the architecture, release boundary, and content-free historical experiment summaries. Existing private wiki pages and session artifacts were not copied. Markdown is the authority; no database-backed wiki migration was introduced. This documentation work performed no new capture, ASR inference, or API experiment.
