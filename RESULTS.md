# RESULTS

Measured on 2026-11-02 with `py -3.12 run_demo.py` on Windows 11. Backend: `llm`, model `glm-4.5-flash` on `https://api.z.ai/api/paas/v4`. Tokens are the sum of the `prompt_tokens` and `completion_tokens` usage fields reported by the API, taken from real calls only. The workload is the four-task repo maintenance fixture (sort imports in two modules, lint and wrap one module, regenerate the README module index), described in README.md. Raw numbers: `results/results.csv`.

| Run | Model calls | Tokens | Wall time | Guard misses |
| --- | --- | --- | --- | --- |
| Cold (fresh cache, every task decided by the model) | 4 | 1092 | 35.86 s | 0 |
| Warm (unchanged repo, cache active) | 0 | 0 | 0.08 s | 0 |

Guard drill, run immediately after the warm phase: `demo_pkg/utils.py` was edited by hand, then the sort-imports task ran twice. First run: the context signature hit, the guard comparing the file against the recorded decision output failed, so the miss correctly forced one fresh model call (293 tokens, 8.26 s) and the decision was re-recorded. Second run: guard held, decision reused, zero calls. Adding a new module to the package is covered equivalently by the `dir_listing` guard on the README task and is exercised in `tests/test_agent.py`.

## Where the analogy to HarnessMM holds and where it breaks

It holds on the core mechanism and the shape of the savings. Both systems store decisions that earlier feedback fixed, attach guards, and revalidate at reuse time instead of trusting the cache blindly, and both spend their savings on the same currency: paid model calls and tokens that simply never happen. In this measurement the warm run eliminated 4 of 4 model calls and 100 percent of tokens for an environment that provably had not changed, which is the same qualitative effect the paper reports on HumanEval (calls 954 to 415, tokens 96.3k to 11.6k per task). It breaks on scale and on ambition. HarnessMM builds executable decision graphs, applies them recursively so the harness improves its own success rate (71.4 to 85.7), and reuses decisions that tool feedback has verified across many levels. This project is a flat cache with one reuse level keyed by a heuristic context signature, evaluated on a four-task fixture that the author also wrote, so nothing here says anything about success-rate improvement, about code-generation benchmarks, or about behavior at the paper's scale.

## Limitations

The numbers describe one fixture workload with four small files, run once; variance in wall time and tokens across repeated runs is not characterized, and the warm wall time mostly measures guard evaluation, which is cheap local hashing and one `ast.parse`. Savings collapse whenever the environment drifts, because every guard miss costs a full fresh decision, and the guard set only pins file content, directory listings, and one allowlisted parse check: state the guards do not see (tool versions, environment variables, unrelated files) silently invalidates nothing. The cache has no opinion on decision quality: a wrong decision recorded with passing guards is replayed with confidence. Reuse validity also depends on post-state pinning, so cached tasks that write the same file supersede each other, which the example avoids by using one decision per target file rather than solving. No HumanEval, no multi-level reuse, no learned components; that is the point of calling this a simplification.
