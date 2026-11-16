# Review: S7 extended code and the M1 results write-up, 2026-11-16

An independent reviewer (a separate agent with no part in writing the code) read `spikes/s7_calibration/extended.py`, the saved results, and the raw S2L outputs (`spikes/s2l_models/out/`). It checked my draft conclusions against them. It didn't call Ollama or edit files. All 87 spike tests passed.

| # | Sev | Finding | Resolution |
|---|---|---|---|
| 1 | Low | No biasing bugs in `extended.py`: noncentral-t power, the t₃ rescale, AR(1), BH over estimable segments, power and other-region counting, `mde80`, and generator equivalence all check out. | — |
| 2 | Med | `region_sd` comes from the flat condition and is reused for every condition, so "d SDs" means flat-condition SDs. | The results now lead with MDE as a % drop and explain the unit. |
| 3 | Low | `estimable` adds a gate (comparison month > 0) that ARCH §6 doesn't have. | Noted as part of the log-level proposal. It goes into ARCH §6 only if you adopt log levels. |
| 4 | Low | `calib.bh` would spread a NaN p-value to every q. It can't happen at current sizes. | Not changed, because `calib.py` backs the frozen original. A finiteness check goes into the product's BH (M2+). |
| 5 | Info | `by_sd` and `by_pct` curves share seeds per point. | Not a bias; noted. |
| 6 | High | Log levels pass 15/19 conditions, not 13. Several results are borderline. | Corrected, with the borderline cases marked. |
| 7 | Med | "About 6 SDs with growth" is a units artifact. In % terms growth costs nothing; seasonality costs about 10 points. | Corrected. |
| 8 | Low | The "unreachable" argument is stronger from the uncorrected single-test bound (0.64–0.75 < 0.80). Cite the frozen log power of 0.487 too. | Added. |
| 9 | Med | The decline failure comes from log-concavity, not clipping (clipping touches 12% of final months). | Corrected. Untested compounding decline noted. |
| 10 | Low | Explain why the launch case is conservative. | Added (the step inflates s). |
| 11 | OK | S7 criteria tables match the JSON. | — |
| 12 | Med | The diagnostic re-score reproduces, but needs caveats: 36 sits exactly on the bar and is post hoc; qwen used `intent` deliberately; Y03 is ambiguous. | Added. |
| 13 | Med | The period-label defect is larger than stated, but S6 still fails for every model with it removed (12b 8–10/20, qwen 5–6/20). | Added the re-verify numbers. |
| 14 | Med | **Third harness defect:** `number` is offered but never accepted, and AOV effect fields aren't labelled as dollars. | Recorded as v3 fix candidates. The v2 result stands. |
| 15 | Low | The VERIFY "significant" check misses "significance" (too lenient). | Recorded for v3. No outcome changed. |
| 16 | Low | Repair rate 1.0 came partly from defect 13, so the 4b and qwen latency passes are pessimistic. | Added. |

No verdict changed. S7 (original) failed; S2L v2: no model passes; S6 fails for every model.
