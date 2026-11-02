# decision-cache

Guarded decision reuse for tool-using agents, a zero-training simplification of the idea in arXiv 2610.32813.

A tool-using agent often re-derives the same decision for the same situation: sorting the same import block, regenerating the same generated README section, applying the same lint fix. This library lets the agent store a decision once and replay it later, but only while guards that pin the relevant environment state still hold. When the environment drifts, the guard fails and the agent makes a fresh decision. No training, no learned components, one level of reuse.

Reference: "HarnessMM: Recursive Self-Improvement for Harness Token Efficiency" (arXiv 2610.32813, Tsinghua + Microsoft Research Asia), https://arxiv.org/abs/2610.32813

## How it relates to the paper

The paper reports that on HumanEval, reusing decisions that earlier tool feedback had already fixed raised success from 71.4 to 85.7 while cutting tokens per task from 96.3k to 11.6k and paid calls from 954 to 415, using executable decision graphs with guards and no retraining. This project borrows exactly one idea from that mechanism: cache a decision, validate reuse with guards, skip the model call when validation passes. It is not the paper's system:

- The paper stores executable decision graphs and applies them recursively (self-improvement across levels). This library is a flat key-value cache with a single reuse level.
- The paper's harness improves its own success rate over runs. This library cannot improve decision quality, it only avoids re-deriving a decision whose preconditions still hold.
- The paper evaluates on HumanEval coding agents. This repository evaluates on a small deterministic repo-maintenance fixture (import order, lint fixes, README regeneration). The numbers in RESULTS.md are from that fixture, not from HumanEval, and are not comparable to the paper's.

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

`record(context_signature, action, guards)` stores a decision. `lookup(context_signature)` returns the stored action plus a guard status evaluated at lookup time. A hit is reusable only if every guard holds; otherwise the caller decides fresh and re-records.

## Guards

| Guard | Pins | Check |
| --- | --- | --- |
| `file_exists(path)` | presence | path exists inside root |
| `file_hash(path, digest)` | content | sha256 of file matches (line endings normalized to LF) |
| `dir_listing(path, pattern)` | name set | hash of sorted matching names matches |
| `command_exit_code(snippet, args, exit_code)` | external validity | allowlisted command returns the stored exit code |

Safety model: guard evaluation is pure Python for all file checks. The command guard never runs free-form shell. It may only launch the current interpreter with one of a fixed set of read-only snippets (currently `parses`, an `ast.parse` of one file), with the working directory pinned to the cache root, every path argument resolved and required to stay inside that root, and a 10 second timeout. Non-whitelisted snippets and escaping paths fail closed. Guards compare text content with line endings normalized, so a CRLF checkout on Windows does not invalidate a decision pinned with LF.

Post-state pinning: a decision that writes a file pins the exact content it wrote, because replay is an idempotent write. Reuse is valid while the file still equals the decision output. If a human or a later task changes the file, the guard fails and the decision is re-derived. A consequence: two cached tasks writing the same file supersede each other, so the example workload uses one decision per target file.

## Example agent and measurement

`examples/repo_agent.py` runs a fixed maintenance workload over a fixture repo (sort imports in two modules, lint and wrap a third, regenerate a README module index). Each task goes through the cache. Two interchangeable backends produce decisions:

- `llm`: any OpenAI-compatible endpoint. Defaults to `glm-4.5-flash` on `https://api.z.ai/api/paas/v4` via `ZAI_API_KEY`.
- `scripted`: a deterministic offline policy, so the whole demo runs with no credentials. It reports zero tokens and zero model calls; token counts are never estimated.

```sh
py -3.12 run_demo.py                 # auto: probe llm, fall back to scripted
py -3.12 run_demo.py --backend scripted
py -3.12 run_demo.py --model glm-4.5-flash
py -3.12 -m pytest                   # 16 tests, offline
```

The demo runs the workload cold (fresh cache, every task decided by the backend), warm (unchanged repo, cache active), then a guard drill (a manual edit must invalidate exactly one cached decision). It prints the comparison and writes `results/results.csv`. Measured numbers live in RESULTS.md.

## Layout

```
src/decision_cache/   the library (core.py, stdlib only)
examples/repo_agent.py  fixture workspace, tasks, backends, agent loop
run_demo.py           cold/warm/drill measurement harness
tests/                pytest suite, offline
results/results.csv   raw numbers from the last demo run
```

## Limitations

See RESULTS.md for the measured limitations of this approach: a single fixture workload, reuse savings that depend on exact environment stability, no protection against a decision that was wrong when recorded, and guard evaluation that only covers file state and one allowlisted command.

MIT licensed, see LICENSE.
