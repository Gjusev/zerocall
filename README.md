# decision-cache

Cache the decision, guard the environment: on the demo fixture a cold run makes 4 model calls for 1092 tokens in 35.86 s, and the warm rerun of the same workload makes 0 calls for 0 tokens in 0.08 s with 4 of 4 decisions reused.

## Measured

Run on 2026-11-02 with `py -3.12 run_demo.py` on Windows 11, backend `llm`, model `glm-4.5-flash` on `https://api.z.ai/api/paas/v4`. Tokens are the sum of the `prompt_tokens` and `completion_tokens` usage fields reported by the API, real calls only. Raw numbers: `results/results.csv`.

| Run | Model calls | Tokens | Wall time | Guard misses |
| --- | --- | --- | --- | --- |
| Cold (fresh cache, every task decided by the model) | 4 | 1092 | 35.86 s | 0 |
| Warm (unchanged repo, cache active) | 0 | 0 | 0.08 s | 0 |

The warm phase reused 4 of 4 cached decisions. Guard drill, run immediately after the warm phase: `demo_pkg/utils.py` was edited by hand and the sort-imports task ran again. The context signature still hit, the file hash guard failed, and the miss forced exactly 1 fresh decision (293 tokens, 8.26 s), which was re-recorded; the next run reused it with zero calls.

## What it is

decision-cache is a small Python library that gives a tool-using agent a one-level heuristic cache with guards: a decision is recorded under a context signature and replayed only while every guard still holds, so environment drift forces a fresh decision instead of a stale replay. It is a zero-training simplification of the decision reuse mechanism in "HarnessMM: Recursive Self-Improvement for Harness Token Efficiency" (arXiv 2610.32813, Tsinghua + Microsoft Research Asia); it has no decision graphs and no recursive self-improvement, and it claims no novelty.

## Quickstart

Python 3.11+, no install step, run from the repo root.

```sh
python run_demo.py --backend auto      # probe the model endpoint, fall back to scripted
python run_demo.py --backend scripted  # deterministic offline policy, no credentials
python run_demo.py --model glm-4.5-flash
python -m pytest                       # 16 tests, offline
```

The `llm` backend speaks the OpenAI-compatible chat API and needs `ZAI_API_KEY`; the scripted backend answers every task deterministically and reports zero model calls and zero tokens, never estimates. The demo runs the workload cold, then warm, then the guard drill, and writes `results/results.csv`.

## API

```python
from pathlib import Path
from decision_cache import DecisionCache, Guard, signature

ws = Path("repo")
cache = DecisionCache(root=ws, store=ws / ".decision_cache.json")

sig = signature("sort_imports", "pkg/utils.py", "sort the import block")
hit = cache.lookup(sig)
if hit is not None and hit.reusable:
    replay(hit.action)            # no model call
else:
    action = decide_with_model()  # fresh decision
    apply(action)
    cache.record(sig, action, guards=[
        Guard.file_hash_text("pkg/utils.py", action["content"]),  # post-state
        Guard.command_exit_code("parses", ["pkg/utils.py"]),      # still valid python
    ])
```

`record(context_signature, action, guards)` stores a decision. `lookup(context_signature)` evaluates every guard against the environment at lookup time and returns the stored action plus per-guard outcomes; `result.reusable` is true only when all guards hold, otherwise the caller decides fresh and records again.

| Guard | Pins | Check |
| --- | --- | --- |
| `file_exists(path)` | presence | path exists inside root |
| `file_hash(path, digest)` | content | sha256 of file matches (line endings normalized to LF) |
| `dir_listing(path, pattern)` | name set | hash of sorted matching names matches |
| `command_exit_code(snippet, args, exit_code)` | external validity | allowlisted command returns the stored exit code |

Guards hash text content with line endings normalized to LF, so a CRLF checkout on Windows does not invalidate a decision pinned with LF.

## Safety

- File guards (`file_exists`, `file_hash`, `dir_listing`) are pure stdlib Python and never spawn a process.
- The command guard never runs free-form shell. It may only launch the current interpreter with a snippet from a fixed in-code allowlist, currently only `parses`, an `ast.parse` of one file.
- That subprocess runs with the working directory pinned to the cache root, every path argument resolved and required to stay inside the root, and a 10 second timeout.
- It fails closed: a snippet outside the allowlist, a path that escapes the root, an unknown guard kind, or any exception during evaluation is reported as a failed guard, never raised into the agent loop.

## Limitations

- The numbers describe one four-task fixture workload, run once; variance in wall time and tokens across repeated runs is not characterized, and they say nothing about HumanEval or any other benchmark, so they are not comparable to the paper's numbers.
- Savings collapse whenever the environment drifts, because each guard miss costs a full fresh decision, and guards only pin file content, directory listings, and one allowlisted parse check: state they cannot see (tool versions, environment variables, unrelated files) invalidates nothing.
- The cache has no opinion on decision quality: a wrong decision recorded with passing guards is replayed with confidence.
- Reuse validity rests on post-state pinning, so two cached tasks that write the same file supersede each other; the example workload uses one decision per target file.

## Layout

```
src/decision_cache/     the library (core.py, stdlib only)
examples/repo_agent.py  fixture workspace, tasks, backends, agent loop
run_demo.py             cold/warm/drill measurement harness
tests/                  pytest suite, offline
results/results.csv     raw numbers from the last demo run
```

## Citation

Source paper:

```bibtex
@misc{harnessmm2026,
  title         = {HarnessMM: Recursive Self-Improvement for Harness Token Efficiency},
  author        = {{Tsinghua University and Microsoft Research Asia}},
  year          = {2026},
  eprint        = {2610.32813},
  archivePrefix = {arXiv},
  url           = {https://arxiv.org/abs/2610.32813}
}
```

This repository:

```bibtex
@misc{decisioncache2026,
  title  = {decision-cache: guarded decision reuse for tool-using agents},
  author = {{decision-cache contributors}},
  year   = {2026},
  note   = {This repository}
}
```

MIT licensed, see LICENSE.
