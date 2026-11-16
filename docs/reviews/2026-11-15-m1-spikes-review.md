# Review: M1 spike code (S2L/S6 harness v1, S7, S3), 2026-11-15

An independent reviewer (a separate agent with no part in writing the code) read `spikes/s2l_models`, `spikes/s7_calibration` and `spikes/s3_repro` against `docs/M1_SPIKES.md` and ARCH §5–6. It ran only the unit tests. No model output or S7 result had been read when it reported.

**Effect on the S2L run.** The v1 harness was frozen in `1b96f3c`, and the v1 benchmark had been running for about 8 minutes, still on `gemma3:4b`. Several fixes change the retry and repair loop: a stricter check shows the model a new error and asks again. So those fixes can't simply be re-scored, and I stopped the v1 run. No v1 results were written or read. The v2 harness is frozen in the commit that follows this log, and all three models run on it. **No threshold changed.**

## Findings and resolutions

| # | Sev | Finding | Resolution |
|---|---|---|---|
| 1 | High | Substitutions were counted only when the status was `ok`. If the model invented a month for an unsupported period and it resolved to `partial_period` or `out_of_range`, it wasn't counted. | Fixed in `resolve.score`. For questions whose accepted answers are all refusals, any concrete status (`ok`, `partial_period`, `out_of_range`) counts as a substitution. Tested. |
| 2 | Med | The transcribe schema had no way to say "same month last year" (R05), so the model had to compute a date. | Added a `same_month_last_year` baseline form and a rule line. This is a prompt change, allowed because v2 re-runs everything. Tested. |
| 3 | Med | PLAN and ASSESS checks didn't enforce in-scope dimensions, didn't catch a segment-level `baseline_compare` the playbook already runs, and didn't catch a crosstab that repeats the planned pair. | All three are checked now. The ASSESS prompt no longer lists out-of-scope dimensions, and both rule texts say "only in-scope dimensions". Tested. |
| 4 | High | A claim could name the wrong segment in plain text ("East drove…" with North's number). | Segment names in template text are rejected; they must come from `label` slots. Driver wording requires every labelled segment to be flagged. Tested. |
| 5 | High | The direction check was skipped when a claim had both an up word and a down word, used only the first signed slot, and missed above/below and several other verbs. | Each signed slot is now checked against the nearest direction word, and the word lists are extended. Tested with swapped clauses and "above" on a negative value. |
| 6 | Med | A slot's format wasn't checked against its field (`unexpected_pct` as money). | `FIELD_FMTS` maps each field to its allowed formats. Tested. |
| 7 | Low | "behind the" matched as a substring, which rejected correct null reports. | Removed. Driver and causal words are matched on word boundaries. Tested. |
| 8 | Low | `invalid_ref_errors_first_try` counted coverage errors, and `repair_rate` missed repairs that failed to parse. | Both fixed. The repair rate now counts repair calls made. |
| 9 | Low | Picking the best schema by score could choose a schema that fails the gate over one that passes. | "Better" now means passes the gate first, then more correct, then fewer substitutions. Documented in `run.py`. |
| 10 | Low | `context_overflow` was flagged from `prompt + num_predict` rather than actual use, and a transport error used up the validation retry. | Overflow is now flagged only when `prompt + output ≥ num_ctx`. Transport errors have their own retry. S6 criteria 2 and 4 are true by construction after VERIFY. That's stated in the results, and the pre-VERIFY counts are reported next to them. |
| 11 | Med | Guided mode printed shares like "198.7% of the change" when movements offset, which contradicts ARCH §6. | Guided suppresses the share when gross/|net| > 3, and VERIFY rejects a `share_of_change` slot in that case. The SYNTH rules tell the model the same. Guided passes VERIFY on 20/20. |
| 12 | High | The S7 generator had no autocorrelation between months, so calibration would look better than it is. | Added `month_rho` (stationary AR(1) monthly shocks) and a reported-only `null_month_ar` scenario (ρ = 0.4, 500 seeds). It isn't gated, because adding a gate after the criteria were frozen needs your approval. |
| 13 | Med | The shift size used the SD of the prediction error, not the residual SD the spec names, which overstates power. | It now uses the in-sample residual SD (RMS over `null_flat` seeds), as specified. |
| 14 | Low | Criterion 4 is noisy (max of 12 rates at n ≈ 225), and power didn't check direction. | Wilson intervals are reported for every rate. Power counts a flag only when the observed value is below the prediction. |
| 15 | OK | The prediction-interval formula, BH, BY and k range are correct, and the formula check is valid. | — |
| 16 | Low | S3: rounding can still flip at a boundary, and sorting makes the hash ignore row order. | Both are already described in the learning guide. The order point is added to the S3 notes. |

## Found while fixing

- **The S7 log-scale option had its own bug.** The first `--log` run planted shifts in log units (the log residual SD), so power came out near 0. I fixed it so shifts are always sized in level units, then re-ran both scales. Found by checking the numbers before trusting them.
