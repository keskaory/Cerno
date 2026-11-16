"""Minimal Ollama client for the S2L benchmark: one call, one retry, everything measured.

Failure kinds are kept separate (structured output vs. semantics vs. transport), because
the S2L criteria report each dimension on its own.
"""

import json
import math
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from typing import Any

from pydantic import BaseModel, ValidationError

OLLAMA = "http://localhost:11434"
NUM_CTX = 8192
SETTINGS: dict[str, Any] = {"temperature": 0, "seed": 1, "num_ctx": NUM_CTX}
KEEP_ALIVE = "30m"


@dataclass
class Attempt:
    wall_s: float
    prompt_tokens: int
    out_tokens: int
    prompt_tok_s: float
    decode_tok_s: float
    done_reason: str
    failure: str | None  # transport | invalid_json | schema_violation | semantic_error |
    # context_overflow | truncated | None
    error: str = ""
    raw: str = ""


@dataclass
class CallRecord:
    stage: str
    attempts: list[Attempt] = field(default_factory=list)
    parsed: Any = None

    @property
    def ok(self) -> bool:
        return self.parsed is not None

    @property
    def wall_s(self) -> float:
        return sum(a.wall_s for a in self.attempts)

    def to_json(self) -> dict[str, Any]:
        p = self.parsed
        parsed = p.model_dump() if isinstance(p, BaseModel) else p
        return {
            "stage": self.stage,
            "parsed": parsed,
            "attempts": [asdict(a) for a in self.attempts],
        }


def _post(path: str, body: dict[str, Any], timeout: float = 900) -> dict[str, Any]:
    req = urllib.request.Request(
        f"{OLLAMA}{path}",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        out: dict[str, Any] = json.loads(r.read())
    return out


def inline_refs(schema: dict[str, Any]) -> dict[str, Any]:
    """Replace $ref with the referenced definition. Ollama's grammar converter is happiest
    with a self-contained schema, and this keeps what the model sees easy to read."""
    defs = schema.get("$defs", {})

    def walk(node: Any) -> Any:
        if isinstance(node, dict):
            if "$ref" in node:
                return walk(defs[node["$ref"].split("/")[-1]])
            return {k: walk(v) for k, v in node.items() if k != "$defs"}
        if isinstance(node, list):
            return [walk(x) for x in node]
        return node

    result: dict[str, Any] = walk(schema)
    return result


def call(
    model: str,
    stage: str,
    system: str,
    user: str,
    schema: type[BaseModel],
    num_predict: int,
    semantic_check: Callable[[Any], str | None] | None = None,
    think: bool = False,
) -> CallRecord:
    """One logical call: at most two attempts. The retry appends the validation error."""
    rec = CallRecord(stage)
    fmt = inline_refs(schema.model_json_schema())
    messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
    for _ in range(2):
        body = {
            "model": model,
            "messages": messages,
            "stream": False,
            "format": fmt,
            "options": {**SETTINGS, "num_predict": num_predict},
            "keep_alive": KEEP_ALIVE,
            "think": think,
        }
        t0 = time.perf_counter()
        try:
            r = _post("/api/chat", body)
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            rec.attempts.append(
                Attempt(time.perf_counter() - t0, 0, 0, 0, 0, "", "transport", str(e))
            )
            continue
        wall = time.perf_counter() - t0
        pt, ot = r.get("prompt_eval_count", 0), r.get("eval_count", 0)
        att = Attempt(
            wall_s=wall,
            prompt_tokens=pt,
            out_tokens=ot,
            prompt_tok_s=pt / max(r.get("prompt_eval_duration", 1), 1) * 1e9,
            decode_tok_s=ot / max(r.get("eval_duration", 1), 1) * 1e9,
            done_reason=r.get("done_reason", ""),
            failure=None,
            raw=r["message"]["content"],
        )
        rec.attempts.append(att)
        if pt + num_predict > NUM_CTX:
            att.failure, att.error = "context_overflow", f"{pt} prompt + {num_predict} > {NUM_CTX}"
            return rec  # a retry would only make the prompt longer
        try:
            obj = schema.model_validate_json(att.raw)
        except ValidationError as e:
            kind = (
                "invalid_json"
                if any(x["type"] == "json_invalid" for x in e.errors())
                else ("schema_violation")
            )
            att.failure = "truncated" if att.done_reason == "length" else kind
            att.error = str(e)[:800]
        else:
            problem = semantic_check(obj) if semantic_check else None
            if problem is None:
                rec.parsed = obj
                return rec
            att.failure, att.error = "semantic_error", problem
        messages = [
            *messages[:2],
            {"role": "assistant", "content": att.raw},
            {
                "role": "user",
                "content": (
                    f"That output was rejected: {att.error}\nReturn a corrected JSON object."
                ),
            },
        ]
    return rec


def unload_all() -> None:
    for m in _get("/api/ps").get("models", []):
        _post("/api/generate", {"model": m["name"], "keep_alive": 0})


def _get(path: str) -> dict[str, Any]:
    with urllib.request.urlopen(f"{OLLAMA}{path}", timeout=30) as r:
        out: dict[str, Any] = json.loads(r.read())
    return out


def cold_load(model: str) -> dict[str, Any]:
    """Time loading the model from disk with the benchmark's num_ctx, then report memory."""
    unload_all()
    free_before = free_ram_gb()
    t0 = time.perf_counter()
    r = _post(
        "/api/chat",
        {
            "model": model,
            "messages": [{"role": "user", "content": "Reply OK."}],
            "stream": False,
            "options": {**SETTINGS, "num_predict": 4},
            "keep_alive": KEEP_ALIVE,
            "think": False,
        },
    )
    wall = time.perf_counter() - t0
    loaded = next(m for m in _get("/api/ps")["models"] if m["name"] == model)
    show = _post("/api/show", {"model": model})
    return {
        "model": model,
        "digest": loaded["digest"],
        "quantization": show.get("details", {}).get("quantization_level"),
        "parameter_size": show.get("details", {}).get("parameter_size"),
        "remote_host": show.get("remote_host"),
        "ollama_version": _get("/api/version")["version"],
        "cold_load_s": round(wall, 2),
        "load_duration_s": round(r.get("load_duration", 0) / 1e9, 2),
        "size_gb": round(loaded["size"] / 1e9, 2),
        "vram_gb": round(loaded["size_vram"] / 1e9, 2),
        "vram_fraction": round(loaded["size_vram"] / loaded["size"], 3),
        "free_ram_gb_before": free_before,
        "free_ram_gb_after": free_ram_gb(),
        "gpu_mem_used_mb": gpu_mem_used_mb(),
    }


def free_ram_gb() -> float:
    try:  # Windows: GlobalMemoryStatusEx; elsewhere /proc/meminfo
        import ctypes

        class MS(ctypes.Structure):
            _fields_ = [
                ("len", ctypes.c_ulong),
                ("load", ctypes.c_ulong),
                ("total", ctypes.c_ulonglong),
                ("avail", ctypes.c_ulonglong),
                ("tp", ctypes.c_ulonglong),
                ("ap", ctypes.c_ulonglong),
                ("tv", ctypes.c_ulonglong),
                ("av", ctypes.c_ulonglong),
                ("ext", ctypes.c_ulonglong),
            ]

        ms = MS()
        ms.len = ctypes.sizeof(MS)
        ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(ms))
        return round(float(ms.avail) / 1e9, 2)
    except (AttributeError, OSError):
        with open("/proc/meminfo") as f:
            kb = next(int(line.split()[1]) for line in f if line.startswith("MemAvailable"))
        return round(kb / 1e6, 2)


def gpu_mem_used_mb() -> int | None:
    import subprocess

    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        return int(out.strip().splitlines()[0])
    except (OSError, subprocess.CalledProcessError, ValueError):
        return None


def driver_version() -> str | None:
    import subprocess

    try:
        return subprocess.run(
            ["nvidia-smi", "--query-gpu=driver_version", "--format=csv,noheader"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def pct(values: list[float], q: float) -> float:
    """Nearest-rank percentile; fine for the small samples here."""
    if not values:
        return math.nan
    s = sorted(values)
    return s[min(len(s) - 1, max(0, math.ceil(q * len(s)) - 1))]


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return (math.nan, math.nan)
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (round(c - h, 3), round(c + h, 3))
