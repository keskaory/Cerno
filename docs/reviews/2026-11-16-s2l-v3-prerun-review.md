# Review: S2L v3 harness before any run, 2026-11-16

An independent reviewer (a separate agent with no part in writing the code) read the v3 draft (`4e82821`): `resolve.py`, `synth.py`, `run.py`, `test_s2l_harness.py` and `questions_v3.json`, against D13. It didn't call Ollama. No v3 output existed at review time.

| # | Sev | Finding | Resolution |
|---|---|---|---|
| 1 | Med | VERIFY's `\bsignific` check lets "insignificant" through. | Changed to a plain substring check. Test added. |
| 2 | Med | `q` (the BH-adjusted p-value) had no allowed format, so a model could still be asked to place it and always fail. | `q` maps to no format; RULES says it never goes in a slot. Test added. |
| 3 | Low | `render_value` silently returned text for an unknown format. | It raises `TypeError` now. |
| 4 | Med | The intent rule named `"explain_change"` only for "why" questions. Several held-out questions ask "how", "what happened" or "compare". | Rule text covers them, in both RESOLVE schemas. This is still the D13 fix (naming the value), worded so the rule matches the question set. |
| 5 | Med | Some held-out questions were near copies of v2 wording; a few had accepted answers that were too narrow for genuinely ambiguous wording (M02, M03, M04, U08). | Reworded R03, D01–D03, M01, M02, M04 and six more after a similarity scan; widened the accepted answers listed. |
| 6 | Low | `--smoke` without a model name crashed with an index error. | Argument guard added. |
| 7 | Info | R02 ("three months ago") is slightly harder than its v2 counterpart. RULES now points SYNTH at a specific month slot, which v2 didn't have. | Recorded; both are part of the v3 fixes, not hidden changes. |

**Remaining similarity.** After rewording, the closest v3 question is 0.79 similar to a v2 question (difflib ratio). The top cases (Y02–Y04, P02, P03) share a sentence template with v2 but use new contexts, months and metrics, so the expected answers differ. I left them: rewording them further would change difficulty more than it would reduce leakage.

Freeze: commit `ebf15a3`. Hashes are in the journal.
