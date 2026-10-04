# Additional development handoff measurements

These are selected numeric aggregates from existing private development records. No recording, recognized speech, generated lecture content, ledger, or API payload is included. They were not rerun while consolidating the repository and are not the final results of the event.

## Older pre-event audit

The audit ran against an earlier application checkout, before the continuous-translation worktree was extracted as v0.9.

| Recorded check | Result |
| --- | ---: |
| Audio test suite | 517 passed |
| Original project baseline tests | 17 passed |
| Local recognition smoke input | 90 seconds |
| Input chunks / recognized lines | 6 / 16 |
| Storage and provenance checks | 230 |
| Failed recognition chunks | 0 |
| Additional API cost | USD 0 |

The saved result reported capture and recognition complete, storage integrity verified, and semantic quality unverified. These counts describe the historical configuration; they must not be substituted for the standalone release's own test count. The underlying source speech remains private.

## Partial live cost observation

A live continuous-translation session had run for approximately 1.006 hours when the following snapshot was collected. Historical translation and analysis intervals were 60 and 120 seconds. All 89 live requests had usage-confirmed costs, and a separate recomputation matched the saved ledger aggregate.

| Observed quantity | Recorded value |
| --- | ---: |
| Completed live requests | 89 |
| Input / output tokens | 245,687 / 51,904 |
| Confirmed live API cost | USD 1.131179 |
| Live unresolved reservations | USD 0 |
| Recent 30-minute live cost | USD 0.630875 |
| Recent 30-minute cost converted to hourly rate | USD 1.26175 / hour |
| Shared daily confirmed cost, including other work | USD 1.9365511 |
| Shared daily unresolved reservations | USD 0.187 |

The daily total includes other development work. A live session's cost and an account's shared daily expenditure answer different questions; neither should overwrite the other.

The saved forecast had about 4.023 hours remaining. Continuing the recent 30-minute rate gave a daily total of about USD 7.20, including existing reservations. A planning scenario used the maximum of the full-session, recent 15-minute, and recent 30-minute rates, multiplied future cost by 1.3, and added USD 0.20 for final processing. That scenario gave about USD 9.27. These were unexecuted projections at that observation point, not final charges, current pricing, or a guarantee of completion within a budget.

Reusable method: report measured cost, unknown-usage reservations, the sampling window, remaining duration, and assumptions separately. Configured budget, granted allowance, and limits already loaded by a running process may differ; changing a configuration document alone does not establish that a process adopted the new limit.

Consolidating these records made no additional application API request. Development-assistant usage and electricity were not measured.
