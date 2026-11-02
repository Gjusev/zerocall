# SPEC: zerocall

You are developing this project autonomously. Read this spec fully, make sensible decisions without asking questions, and record every non-obvious choice in DECISIONS.md. Do not modify anything outside this directory.

## What this is

A small Python library that lets a tool-using agent skip repeated model calls by reusing earlier decisions, with guards that check the reuse is still valid. It is a minimal, zero-training implementation of the idea in "HarnessMM: Recursive Self-Improvement for Harness Token Efficiency" (arXiv 2610.32813, Tsinghua + Microsoft Research Asia): https://arxiv.org/abs/2610.32813

Key numbers from that paper, for context only: on HumanEval, success rises from 71.4 to 85.7 while tokens per task fall from 96.3k to 11.6k (87.9 percent less) and paid model calls fall from 954 to 415. Their mechanism reuses decisions that earlier tool feedback has already fixed, stored as executable decision graphs with guards, with no retraining.

Your version is much simpler and must say so: a heuristic cache keyed by context signature, validated by guard expressions. No learned graphs, no recursive self-improvement loop beyond one reuse level.

## What to build

1. Core API:
   - `record(context_signature, action, guards)` stores a decision.
   - `lookup(context_signature)` returns the stored action plus guard status.
   - A guard is a small predicate over the environment, for example: file exists, file content hash unchanged, command exit code equals a stored value. Guards are evaluated with limited shell access in a sandboxed runner or pure-Python checks only (your choice, document it).
2. An example agent: a scripted or LLM-backed agent that performs a sequence of repo maintenance tasks (for example: regenerate a README section, fix import order, run lint and fix failures) where later runs hit the cache.
3. A measurement harness that runs the same workload twice, once cold (every decision calls the model) and once warm (cache active), and reports: model calls, tokens, wall time, and any guard misses that correctly forced a fresh decision.

## Model calls

OpenAI-compatible endpoint, cheap flash-tier model. Get credentials however is available on this machine (env vars, local hermes proxy, hermes secrets). Hard budget: 2 USD equivalent. If no credentials are found, implement a deterministic scripted agent so the whole demo and measurement still run offline, and label those numbers as scripted-agent numbers, not model numbers. Never invent token counts.

## Layout

```
README.md
SPEC.md (this file, keep unchanged)
DECISIONS.md
src/zerocall/
examples/repo_agent.py
results/results.csv
RESULTS.md
run_demo.py
tests/
LICENSE (MIT)
```

## RESULTS.md

One table: cold run vs warm run, columns = model calls, tokens, wall time, guard misses. One short paragraph on where the analogy to HarnessMM holds and where it breaks. One paragraph on limitations.

## Style rules

- README opens with: "Guarded decision reuse for tool-using agents, a zero-training simplification of the idea in arXiv 2610.32813."
- Cite the paper, state the differences honestly.
- No em dashes anywhere. English only. Engineer tone, no marketing.
- Python 3.11+, stdlib plus openai client plus pytest.

## Definition of done

- `python run_demo.py` runs the cold then warm workload end to end.
- RESULTS.md has a measured table (scripted numbers allowed if labeled as such).
- `pytest` passes.
- git repo initialized with meaningful commits.
