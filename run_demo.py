"""Run the cold-then-warm measurement demo.

Phases:
  1. cold:  fresh cache, every task is a fresh decision (backend call).
  2. warm:  same workspace, cache active, unchanged inputs -> pure reuse.
  3. drill: mutate one input file, rerun one task -> guard miss forces a
            fresh decision, which is then cached and reused again.

Results are printed and appended to results/results.csv. Backend is llm
(glm-4.5-flash on api.z.ai by default, --model to change) with automatic
fallback to the deterministic scripted policy when the API is unusable.
Token counts are taken from API usage fields; the scripted backend reports
zeros, never invented numbers.
"""
from __future__ import annotations

import argparse
import csv
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "examples"))

from repo_agent import (  # noqa: E402
    LLMBackend,
    RunStats,
    ScriptedBackend,
    TASKS,
    BackendUnavailable,
    build_workspace,
    run_task,
    run_workload,
)
from decision_cache import DecisionCache  # noqa: E402

DEFAULT_MODEL = "glm-4.5-flash"


def pick_backend(choice: str, model: str):
    if choice == "scripted":
        return ScriptedBackend(), "forced by --backend scripted"
    if choice == "llm":
        return LLMBackend.probe(model), "forced by --backend llm"
    try:
        return LLMBackend.probe(model), f"probe call on {model} succeeded"
    except Exception as exc:
        reason = str(exc).splitlines()[0][:100] if str(exc) else exc.__class__.__name__
        return ScriptedBackend(), f"llm probe failed ({reason}), falling back to scripted"


def fmt_row(label: str, s: RunStats, note: str) -> str:
    tokens = s.prompt_tokens + s.completion_tokens
    calls = s.model_calls if s.backend == "llm" else s.decision_calls
    kind = "model calls" if s.backend == "llm" else "decision calls (scripted)"
    return (
        f"{label:<16} {kind}: {calls:<3} tokens: {tokens:<6} "
        f"wall: {s.wall_seconds:6.2f}s guard misses: {s.guard_misses} "
        f"reused: {s.reused}/{s.tasks}  {note}"
    )


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--backend", choices=["auto", "llm", "scripted"], default="auto")
    ap.add_argument("--model", default=DEFAULT_MODEL, help=f"default {DEFAULT_MODEL}")
    args = ap.parse_args()

    backend, why = pick_backend(args.backend, args.model)
    print(f"backend: {backend.name} ({backend.model if hasattr(backend, 'model') else '-'})")
    print(f"reason:  {why}")

    ws = ROOT / "examples" / "workspace"
    store = ws / "cache.json"
    build_workspace(ws)

    # Phase 1: cold, fresh cache.
    if store.exists():
        store.unlink()
    cache = DecisionCache(root=ws, store=store)
    cold = run_workload(ws, cache, backend, TASKS, "cold")

    # Phase 2: warm, same cache, unchanged environment.
    warm = run_workload(ws, cache, backend, TASKS, "warm")

    # Phase 3: guard drill. A manual edit invalidates the cached decision
    # for demo_pkg/utils.py; the guard must catch it and force a fresh call.
    utils = ws / "demo_pkg" / "utils.py"
    utils.write_text(utils.read_text(encoding="utf-8") + "# manual edit after warm run\n", encoding="utf-8")
    drill = RunStats(label="drill", backend=backend.name)
    t = TASKS[0]
    _start = time.perf_counter()
    run_task(ws, cache, backend, t, drill)
    run_task(ws, cache, backend, t, drill)  # second pass must reuse again
    drill.wall_seconds = time.perf_counter() - _start
    print(f"  [drill] rerun after manual edit of {t.path}: "
          f"{drill.guard_misses} guard miss, {drill.decision_calls} fresh decision, "
          f"{drill.reused} reuse")

    print()
    print(fmt_row("cold", cold, "every task decided fresh"))
    print(fmt_row("warm", warm, "unchanged repo, cache active"))
    print(fmt_row("drill", drill, "1 mutated file, 2 runs of one task"))

    out = ROOT / "results" / "results.csv"
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(
            [
                "run", "backend", "model", "decision_calls", "model_calls",
                "prompt_tokens", "completion_tokens", "tokens", "wall_seconds",
                "guard_misses", "reused", "tasks",
            ]
        )
        for s in (cold, warm, drill):
            model = getattr(backend, "model", "-")
            w.writerow(
                [
                    s.label, s.backend, model, s.decision_calls, s.model_calls,
                    s.prompt_tokens, s.completion_tokens,
                    s.prompt_tokens + s.completion_tokens,
                    f"{s.wall_seconds:.3f}", s.guard_misses, s.reused, s.tasks,
                ]
            )
    print(f"\nwrote {out.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
