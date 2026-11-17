"""Spike S2L + S6: run the frozen benchmark against local models. Criteria: docs/M1_SPIKES.md.

Usage (from backend/):  uv run python ../spikes/s2l_models/run.py gemma3:4b qwen3:8b gemma3:12b
Results: results/v3/<model>.json (committed), out/v3/<model>.json (full raw, ignored).
Smoke run (no scoring, old v2 questions only):  python run.py --smoke gemma3:4b
Talks only to the local Ollama server. Costs nothing.
"""

import functools
import hashlib
import json
import sys
import time
from pathlib import Path
from typing import Any

import client as c
import plan as pl
import resolve as rv
import synth as sy

HERE = Path(__file__).parent
OUT, RESULTS = HERE / "out" / "v3", HERE / "results" / "v3"  # v2 results: results/v2/
NUM_PREDICT = {"resolve": 256, "plan": 768, "assess": 768, "synth": 1536}
SCENARIOS = (
    [("a", s) for s in range(7)]
    + [("b", 10 + s) for s in range(7)]
    + [("c", 20 + s) for s in range(6)]
)


def frozen_hashes() -> dict[str, str]:
    names = ["questions_v3.json", "resolve.py", "plan.py", "synth.py", "client.py", "run.py"]
    return {n: hashlib.sha256((HERE / n).read_bytes()).hexdigest()[:16] for n in names}


def failure_counts(records: list[c.CallRecord]) -> dict[str, int]:
    out: dict[str, int] = {}
    for r in records:
        for a in r.attempts:
            if a.failure:
                out[a.failure] = out.get(a.failure, 0) + 1
    return out


def structured(records: list[c.CallRecord]) -> dict[str, Any]:
    first = [r.attempts[0] for r in records if r.attempts]
    return {
        "calls": len(records),
        "parsed_json_first_try": sum(
            a.failure not in ("invalid_json", "transport", "truncated") for a in first
        ),
        "schema_valid_first_try": sum(
            a.failure not in ("invalid_json", "schema_violation", "transport", "truncated")
            for a in first
        ),
        "valid_after_retry": sum(r.ok for r in records),
        "retried": sum(len(r.attempts) > 1 for r in records),
    }


def timing(records: list[c.CallRecord]) -> dict[str, Any]:
    walls = [r.wall_s for r in records]
    atts = [a for r in records for a in r.attempts if a.failure != "transport"]
    return {
        "p50_s": round(c.pct(walls, 0.5), 2),
        "p90_s": round(c.pct(walls, 0.9), 2),
        "max_s": round(max(walls), 2) if walls else None,
        "prompt_tokens_p50": c.pct([a.prompt_tokens for a in atts], 0.5),
        "out_tokens_p50": c.pct([a.out_tokens for a in atts], 0.5),
        "prompt_tok_s_p50": round(c.pct([a.prompt_tok_s for a in atts], 0.5), 1),
        "decode_tok_s_p50": round(c.pct([a.decode_tok_s for a in atts], 0.5), 1),
    }


def run_resolve(model: str, data: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for schema_name, schema in (("months", rv.MonthsOut), ("transcribe", rv.TranscribeOut)):
        items, records = [], []
        for q in data["questions"]:
            ctx = data["contexts"][q["ctx"]]
            rec = c.call(
                model,
                "resolve",
                rv.system_prompt(ctx, schema_name),
                q["q"],
                schema,
                NUM_PREDICT["resolve"],
                rv.semantic_problem,
            )
            records.append(rec)
            result = rv.resolve(rec.parsed, ctx) if rec.ok else {"status": "call_failed"}
            sc = rv.score(result, q["accept"])
            items.append(
                {
                    "id": q["id"],
                    "cat": q["cat"],
                    "result": result,
                    **sc,
                    "raw": rec.parsed.model_dump() if rec.ok else None,
                }
            )
        k = sum(i["correct"] for i in items)
        by_cat: dict[str, list[int]] = {}
        for i in items:
            by_cat.setdefault(i["cat"], [0, 0])
            by_cat[i["cat"]][0] += i["correct"]
            by_cat[i["cat"]][1] += 1
        out[schema_name] = {
            "correct": k,
            "n": len(items),
            "wilson95": c.wilson(k, len(items)),
            "substitutions": sum(i["substitution"] for i in items),
            "by_category": {cat: f"{v[0]}/{v[1]}" for cat, v in by_cat.items()},
            "structured": structured(records),
            "failures": failure_counts(records),
            "timing": timing(records),
            "wrong": [i for i in items if not i["correct"]],
            "_records": [r.to_json() for r in records],
        }
    # "Better" means: passes the RESOLVE gate (>= 36 correct, 0 substitutions) first, then more
    # correct, then fewer substitutions. A schema that passes beats one that scores higher.
    best = max(
        ("months", "transcribe"),
        key=lambda s: (
            out[s]["correct"] >= 36 and out[s]["substitutions"] == 0,
            out[s]["correct"],
            -out[s]["substitutions"],
        ),
    )
    out["best_schema"] = best
    return out


def run_plan_assess(model: str, think: bool = False) -> dict[str, Any]:
    res: dict[str, Any] = {}
    for stage in ("plan", "assess"):
        records, items = [], []
        for ctx in pl.contexts():
            plan_m, assess_m = pl.models_for(ctx)
            if stage == "plan":
                sysp, user = pl.plan_prompt(ctx)
                rec = c.call(
                    model,
                    "plan",
                    sysp,
                    user,
                    plan_m,
                    NUM_PREDICT["plan"],
                    functools.partial(pl.plan_problem, ctx),
                    think=think,
                )
            else:
                sysp, user = pl.assess_prompt(ctx)
                rec = c.call(
                    model,
                    "assess",
                    sysp,
                    user,
                    assess_m,
                    NUM_PREDICT["assess"],
                    functools.partial(pl.assess_problem, ctx),
                    think=think,
                )
            records.append(rec)
            hot = min(ctx.profile[ctx.in_scope[0]].items(), key=lambda kv: kv[1])  # informational
            parsed = rec.parsed.model_dump() if rec.ok else None
            items.append(
                {
                    "ctx": ctx.name,
                    "enum_values": ctx.enum_count(),
                    "ok": rec.ok,
                    "parsed": parsed,
                    "hot_segment": hot[0],
                }
            )
        res[stage] = {
            "valid": sum(r.ok for r in records),
            "n": len(records),
            "structured": structured(records),
            "failures": failure_counts(records),
            "timing": timing(records),
            "items": items,
            "_records": [r.to_json() for r in records],
        }
    return res


def run_synth(model: str, scenarios: list[tuple[str, int]], think: bool = False) -> dict[str, Any]:
    reports, records = [], []
    for kind, seed in scenarios:
        sc = sy.scenario(kind, seed)
        sysp, user = sy.prompt(sc)
        rec = c.call(model, "synth", sysp, user, sy.SynthOut, NUM_PREDICT["synth"], think=think)
        records.append(rec)
        rep: dict[str, Any] = {"scenario": kind, "seed": seed, "status": "failed", "attempts": []}
        out = rec.parsed
        for attempt in range(2):  # first try + one repair
            if out is None:
                break
            errors = sy.verify(out, sc["evidence"])
            rep["attempts"].append(
                {
                    "errors": errors,
                    "driver_claims": sy.driver_claims(out),
                    "digits": sy.has_digits(out),
                    "output": out.model_dump(),
                }
            )
            if not errors:
                rep["status"] = "completed"
                rep["repaired"] = attempt == 1
                rep["rendered"] = [
                    sy.render(cl, sc["evidence"]) for cl in [out.headline, *out.claims]
                ]
                rep["covers_planted"] = (
                    sy.covers_planted(out, sc["planted"]) if sc["planted"] else None
                )
                break
            if attempt == 0:
                fix = (
                    user
                    + "\n\nYour previous output:\n"
                    + json.dumps(out.model_dump())
                    + "\n\nVERIFY rejected it:\n- "
                    + "\n- ".join(errors)
                    + "\nReturn a corrected report."
                )
                rec2 = c.call(
                    model, "synth_repair", sysp, fix, sy.SynthOut, NUM_PREDICT["synth"], think=think
                )
                records.append(rec2)
                out = rec2.parsed
        reports.append(rep)
    first = [r["attempts"][0] for r in reports if r["attempts"]]
    n_claims = sum(len(a["output"]["claims"]) + 1 for a in first)
    ref_error = ("bad reference syntax", "no evidence", "has no row", ": no field")
    bad_refs = sum(sum(1 for e in a["errors"] if any(x in e for x in ref_error)) for a in first)
    completed = [r for r in reports if r["status"] == "completed"]
    null_reports = [r for r in reports if r["scenario"] == "c"]
    a_done = [r for r in completed if r["scenario"] == "a"]
    return {
        "completed": len(completed),
        "n": len(reports),
        "repaired": sum(bool(r.get("repaired")) for r in completed),
        # Share of reports that needed a repair call, whether or not the repair parsed.
        "repair_rate": round(sum(r.stage == "synth_repair" for r in records) / len(reports), 3),
        "null_driver_claims_pre_verify": sum(
            r["attempts"][0]["driver_claims"] for r in null_reports if r["attempts"]
        ),
        "null_driver_claims_after_verify": sum(
            r["attempts"][-1]["driver_claims"] for r in null_reports if r["status"] == "completed"
        ),
        "planted_covered": f"{sum(bool(r.get('covers_planted')) for r in a_done)}/{len(a_done)}",
        "digits_pre_verify": sum(a["digits"] for a in first),
        "digits_in_completed": sum(r["attempts"][-1]["digits"] for r in completed),
        "invalid_ref_errors_first_try": bad_refs,
        "claims_first_try": n_claims,
        "structured": structured(records),
        "failures": failure_counts(records),
        "timing": timing([r for r in records if r.stage == "synth"]),
        "reports": reports,
        "_records": [r.to_json() for r in records],
    }


def guided_check() -> dict[str, Any]:
    results = []
    for kind, seed in SCENARIOS:
        sc = sy.scenario(kind, seed)
        out = sy.guided(sc)
        errors = sy.verify(out, sc["evidence"])
        results.append(
            {
                "scenario": kind,
                "seed": seed,
                "errors": errors,
                "rendered": [sy.render(cl, sc["evidence"]) for cl in [out.headline, *out.claims]]
                if not errors
                else None,
            }
        )
    return {"passed": sum(not r["errors"] for r in results), "n": len(results), "reports": results}


def criteria(r: dict[str, Any]) -> dict[str, Any]:
    best = r["resolve"][r["resolve"]["best_schema"]]
    proj = {}
    for key in ("p50_s", "p90_s"):
        proj[key] = round(
            r["plan_assess"]["plan"]["timing"][key]
            + 2 * r["plan_assess"]["assess"]["timing"][key]
            + r["synth"]["timing"][key] * (1 + r["synth"]["repair_rate"]),
            1,
        )
    s6 = r["synth"]
    s6_pass = (
        s6["completed"] >= 18
        and s6["null_driver_claims_after_verify"] == 0
        and s6["planted_covered"].split("/")[0] == s6["planted_covered"].split("/")[1]
        and s6["digits_in_completed"] == 0
    )
    crit = {
        "1_resolve": best["correct"] >= 36 and best["substitutions"] == 0,
        "2_schema": r["plan_assess"]["plan"]["valid"] == 10
        and r["plan_assess"]["assess"]["valid"] == 10,
        "3_latency": proj["p50_s"] <= 180 and proj["p90_s"] <= 300,
        "4_s6": s6_pass,
    }
    return {"criteria": crit, "pass": all(crit.values()), "projected_llm_s": proj}


def strip_private(d: Any) -> Any:
    if isinstance(d, dict):
        return {k: strip_private(v) for k, v in d.items() if not k.startswith("_")}
    if isinstance(d, list):
        return [strip_private(x) for x in d]
    return d


SMOKE_QUESTIONS = ("C01", "R05", "U01", "P02")  # from the frozen v2 set, never the held-out set


def smoke(model: str) -> None:
    """A few calls per stage on already-seen inputs, printed for reading by hand. It checks the
    harness (prompts, schemas, VERIFY) end to end and scores nothing, so it can't bias v3."""
    old = rv.load(rv.QUESTIONS_V2)
    rows: list[dict[str, Any]] = []
    for q in (q for q in old["questions"] if q["id"] in SMOKE_QUESTIONS):
        ctx = old["contexts"][q["ctx"]]
        for name, schema in (("months", rv.MonthsOut), ("transcribe", rv.TranscribeOut)):
            rec = c.call(
                model,
                "resolve",
                rv.system_prompt(ctx, name),
                q["q"],
                schema,
                NUM_PREDICT["resolve"],
                rv.semantic_problem,
            )
            res = rv.resolve(rec.parsed, ctx) if rec.parsed is not None else None
            rows.append({"stage": f"resolve/{name}", "q": q["q"], "raw": rec.to_json(), "res": res})
    for ctx in (pl.contexts()[0], pl.contexts()[6]):
        plan_m, assess_m = pl.models_for(ctx)
        for stage, (sysp, user), m, check in (
            ("plan", pl.plan_prompt(ctx), plan_m, functools.partial(pl.plan_problem, ctx)),
            ("assess", pl.assess_prompt(ctx), assess_m, functools.partial(pl.assess_problem, ctx)),
        ):
            rec = c.call(model, stage, sysp, user, m, NUM_PREDICT[stage], check)
            rows.append({"stage": stage, "ctx": ctx.name, "raw": rec.to_json()})
    for kind, seed in (("a", 0), ("b", 10), ("c", 20)):
        sc = sy.scenario(kind, seed)
        sysp, user = sy.prompt(sc)
        rec = c.call(model, "synth", sysp, user, sy.SynthOut, NUM_PREDICT["synth"])
        errs = sy.verify(rec.parsed, sc["evidence"]) if rec.parsed is not None else ["no parse"]
        rows.append({"stage": "synth", "scenario": kind, "raw": rec.to_json(), "verify": errs})
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / f"smoke_{model.replace(':', '_')}.json"
    path.write_text(json.dumps(rows, indent=1, default=str), encoding="utf-8")
    print(f"wrote {path}")


def main() -> None:
    if sys.argv[1:2] == ["--smoke"]:
        if len(sys.argv) != 3:
            raise SystemExit("usage: run.py --smoke MODEL")
        smoke(sys.argv[2])
        return
    OUT.mkdir(parents=True, exist_ok=True)
    RESULTS.mkdir(parents=True, exist_ok=True)
    data = rv.load()
    for model in sys.argv[1:]:
        t0 = time.time()
        env = c.cold_load(model)
        if env["remote_host"] or model.endswith("-cloud"):
            raise SystemExit(f"{model} is not a local model; refusing to benchmark it")
        r: dict[str, Any] = {
            "env": {
                **env,
                "nvidia_driver": c.driver_version(),
                "settings": c.SETTINGS,
                "keep_alive": c.KEEP_ALIVE,
                "num_predict": NUM_PREDICT,
                "think": False,
                "frozen_hashes": frozen_hashes(),
            },
            "resolve": run_resolve(model, data),
            "plan_assess": run_plan_assess(model),
            "synth": run_synth(model, SCENARIOS),
        }
        if model.startswith("qwen3"):
            r["thinking_on"] = {
                "plan_assess": run_plan_assess(model, think=True),
                "synth": run_synth(model, SCENARIOS[:3] + SCENARIOS[14:17], think=True),
            }
        r["env"]["memory_after_run"] = {
            "gpu_mem_used_mb": c.gpu_mem_used_mb(),
            "free_ram_gb": c.free_ram_gb(),
        }
        r["summary"] = criteria(r)
        r["guided"] = guided_check()
        r["elapsed_min"] = round((time.time() - t0) / 60, 1)
        name = model.replace(":", "_")
        (OUT / f"{name}.json").write_text(json.dumps(r, indent=1, default=str))
        (RESULTS / f"{name}.json").write_text(json.dumps(strip_private(r), indent=1, default=str))
        print(
            json.dumps({"model": model, **r["summary"], "elapsed_min": r["elapsed_min"]}),
            flush=True,
        )


if __name__ == "__main__":
    main()
