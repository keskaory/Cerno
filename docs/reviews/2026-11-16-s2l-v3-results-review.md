# Review: S2L v3 results write-up, 2026-11-16

An independent reviewer (a separate agent with no part in writing the code) checked the v3 results section of `M1_SPIKES.md` against `results/v3/`, the raw outputs in `out/v3/`, the frozen harness and the question set. It didn't call Ollama or edit files.

| # | Sev | Finding | Resolution |
|---|---|---|---|
| 1 | Info | Integrity holds: frozen M1_SPIKES lines unchanged (`31be6498…`), no harness change after `ebf15a3`, all hashes in the results match the journal. | — |
| 2 | Med | "420 calls" was copied from v2; v3 RESOLVE+PLAN+ASSESS is 300 first calls (335 attempts). | Corrected. |
| 3 | Low | "%" leniency counts included repairs but were labelled first-try. | Now 20/121 and 12/133 first-try, with the all-attempts counts given too. |
| 4 | Med | Undisclosed VERIFY leniency: a signed slot after "fell" passes and renders "fell by -$110,283". In 3 of 12b's 8 and 1 of qwen's 4 completed reports. | Disclosed; strict reading 5/20 and 3/20; "completed" defined as "passed VERIFY". Not changed in the frozen harness. |
| 5 | Low | v3 VERIFY is stricter than v2 on `q`, which affects the v2→v3 S6 comparison (12b 9, qwen 6 without it). | Disclosed. |
| 6 | Low | "S6 improved" overclaimed for 12b (8/20 both runs). | Reworded. |
| 7 | Low | Substitutions aren't all periods (4b: 3 metric; qwen: 1 partial month). | Split by type. |
| 8 | Info | RESOLVE scoring sample is correct; accept lists for Y03, D02 and D04 are fair. | — |
| 9 | Low | Refusal-vs-refusal answers (U03, U06) are scored wrong; crediting them gives 12b 37 and qwen 33, with no gate change. | Sensitivity note added. |
| 10 | Info | Sampled S6 rejections are genuine rule breaches; no new instrument defect found. | — |
| 11 | Med | The failure list was incomplete (the user asked for all model failures). | Added an S6 rejection table by category and a list of RESOLVE error types. |
| 12 | Info | Truncation counts confirmed; the text before each cut already breaks rules. | Noted. |
| 13 | Low | "Memory the same as v2" was only true for VRAM and GPU memory. | Narrowed; free-RAM variability noted. |
| 14 | Low | Thinking-on was worse on PLAN, not "no better"; qwen's run time includes it. | Reworded. |
| 15 | Low | The v2→v3 RESOLVE gain mixes the fix with a new question set. | Caveat added. |
| 16 | Info | All other numbers are verified, including the qwen latency projection. | — |
| 17 | Info | Raw outputs are gitignored, so the hand reads can only be re-checked on this machine. | Stated in the write-up. |

No gate outcome changes. Every model fails S2L v3, and Guided mode stays the default.
