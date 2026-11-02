"""End-to-end agent tests with the offline scripted backend."""
from pathlib import Path

from decision_cache import DecisionCache
from repo_agent import (
    TASKS,
    ScriptedBackend,
    build_workspace,
    lint_fix,
    regenerate_readme,
    run_task,
    run_workload,
    sort_imports,
    wrap_long_lines,
)


def fresh(tmp_path: Path):
    ws = tmp_path / "ws"
    build_workspace(ws)
    cache = DecisionCache(root=ws, store=ws / "cache.json")
    return ws, cache


def test_scripted_policies():
    text = '"""Doc."""\n\nimport sys\nimport os\n\n\nimport json\n\n\ndef f():\n    pass\n'
    out = sort_imports(text)
    assert out.index("import json") < out.index("import os") < out.index("import sys")
    assert out.startswith('"""Doc."""')

    dirty = "a = 1   \n\n\n\n\nb = 2\t\n"
    assert lint_fix(dirty) == "a = 1\n\n\nb = 2\n"

    long_line = " " * 4 + "f(" + ", ".join(f"arg{i}=True" for i in range(12)) + ")"
    assert max(len(l) for l in wrap_long_lines("x = 1\n" + long_line + "\n")) <= 88

    readme = "pre\n<!-- modules:start -->old\n<!-- modules:end -->\npost\n"
    regen = regenerate_readme(readme, [("pkg/a.py", "does a")])
    assert "old" not in regen
    assert "- `pkg/a.py`: does a" in regen
    assert regen.startswith("pre") and regen.endswith("post\n")


def test_cold_then_warm_reuse(tmp_path):
    ws, cache = fresh(tmp_path)
    backend = ScriptedBackend()

    cold = run_workload(ws, cache, backend, TASKS, "cold")
    assert cold.decision_calls == len(TASKS)
    assert cold.reused == 0 and cold.guard_misses == 0

    warm = run_workload(ws, cache, backend, TASKS, "warm")
    assert warm.decision_calls == 0
    assert warm.reused == len(TASKS)
    assert warm.guard_misses == 0


def test_tasks_actually_change_the_repo(tmp_path):
    ws, cache = fresh(tmp_path)
    run_workload(ws, cache, ScriptedBackend(), TASKS, "cold")
    utils = (ws / "demo_pkg" / "utils.py").read_text(encoding="utf-8")
    imports = [l for l in utils.splitlines() if l.startswith("import ")]
    assert imports == sorted(imports)
    api = (ws / "demo_pkg" / "api.py").read_text(encoding="utf-8")
    assert max(len(l) for l in api.splitlines()) <= 88
    readme = (ws / "README.md").read_text(encoding="utf-8")
    assert "demo_pkg/utils.py" in readme and "stale" not in readme
    import ast

    for mod in sorted((ws / "demo_pkg").glob("*.py")):
        ast.parse(mod.read_text(encoding="utf-8"))  # transforms keep files valid


def test_mutation_forces_guard_miss_then_recache(tmp_path):
    ws, cache = fresh(tmp_path)
    run_workload(ws, cache, ScriptedBackend(), TASKS, "cold")

    utils = ws / "demo_pkg" / "utils.py"
    utils.write_text(utils.read_text(encoding="utf-8") + "# manual edit\n", encoding="utf-8")

    from repo_agent import RunStats, task_signature

    stats = RunStats(label="drill", backend="scripted")
    t = TASKS[0]
    assert run_task(ws, cache, ScriptedBackend(), t, stats) == "fresh"
    assert stats.guard_misses == 1 and stats.decision_calls == 1
    assert run_task(ws, cache, ScriptedBackend(), t, stats) == "reused"
    assert stats.guard_misses == 1 and stats.decision_calls == 1


def test_readme_guard_catches_input_drift(tmp_path):
    ws, cache = fresh(tmp_path)
    run_workload(ws, cache, ScriptedBackend(), TASKS, "cold")
    readme_task = next(t for t in TASKS if t.task_id == "regen_readme")

    # a new module invalidates the listing input even though README itself
    # still matches the recorded post-state
    (ws / "demo_pkg" / "extra.py").write_text('"""Extra module."""\n', encoding="utf-8")
    from repo_agent import RunStats

    stats = RunStats(label="drift", backend="scripted")
    assert run_task(ws, cache, ScriptedBackend(), readme_task, stats) == "fresh"
    assert stats.guard_misses == 1
    readme = (ws / "README.md").read_text(encoding="utf-8")
    assert "demo_pkg/extra.py" in readme
