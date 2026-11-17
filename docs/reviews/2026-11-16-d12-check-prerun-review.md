# Review: D12 criterion 3a check before its run, 2026-11-16

An independent reviewer read `spikes/s7_calibration/d12_check.py` against D12, `extended.py`, `calib.py` and `run.py`. It didn't draw from seed root 1216. It confirmed: no off-by-one in k (the analytic test and `baseline_compare` use the same fit and k − 2 df); the d unit matches earlier runs; the stream is separate; requiring the planted direction costs nothing (wrong-tail power ≤ 5e-6).

| # | Sev | Finding | Resolution |
|---|---|---|---|
| 1 | Med | The BH gate is lenient: the planted drop also flags the total and channels, loosening BH's threshold for the region, while the ideal is held at α/8. Stream 7 already beat the ideal by 0.04–0.14. | Gate kept exactly as D12 states. Added a reported-only like-for-like check (raw p < α/8). The write-up says the gate is lenient. |
| 2 | Med | The freeze covered only the script, not its dependencies or environment. | Committed before running (`76e163a`). Output records the commit, hashes of all three files and the library versions. Hashes are in M1_SPIKES before the run. |
| 3 | Low | The docstring said run.py uses roots 1–3; it's 1–6. | Fixed. |
| 4 | Low | Pre-registration didn't say: one run only, where `region_sd` comes from, unrounded comparison, outcome if a d fails. | All four added. |
| 5 | Info | d is in level-SD units but the test is on logs; the effect is a few percent, slightly in the method's favour. | Mentioned in the write-up. |
| 6 | Info | Runtime about 20–25 s. | — |

Verdict: safe to freeze and run after #2; #1's reported check added.
