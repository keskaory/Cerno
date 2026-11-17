# Review: D12 criterion 3a result, 2026-11-16

An independent reviewer checked the D12 section of `M1_SPIKES.md` against `results/d12_check.json` and the frozen scripts. Its diagnostics used seed root 99, never the 1216 stream.

| # | Sev | Finding | Resolution |
|---|---|---|---|
| 1 | Med | My guessed second cause ("pooled SD unit") was wrong. The unit ratio is 1.000. The cause is that subtracting a fixed amount from the noisy last month inflates its log-scale noise by 1/(1 − x). With concavity, that reproduces the published rates. | Replaced with the identified mechanism. I reproduced it independently in `spikes/s7_calibration/d12_diagnostic.py`. |
| 2 | Med | A single "effective d" misdescribes a shift plus extra variance, and it has sampling error. | Reworded: kept only as an inversion, with its limits stated. |
| 3 | Low | The "about as ideal" claim has stronger direct evidence: on a pure log shift, raw-p power matches the analytic test within 0.005. | Cited (0.006 in my run). |
| 4 | Low | Ranges: BH adds 0.00–0.02, not 0.01–0.02; planted drops are 14–37%, not 14–35%. | Corrected. |
| 5 | Low | Changing the unit alone wouldn't make the M3 gate like-for-like; the drop must also be planted multiplicatively. | Proposal updated. Still needs your approval. |
| 6 | Info | JSON records commit 7c39f01, the doc says 76e163a; the scripts are identical between them. | Stated. |
| 7 | Info | The pre-run review underestimated the effect. | Note added to that review. |

Integrity: all numbers, hashes and the frozen scripts check out. **The 3a PASS stands as pre-registered.**
