"""Example agent: repo maintenance on a fixture workspace.

The agent runs a fixed set of maintenance tasks. Every task is a decision:
given a context signature (task identity) the agent either replays a cached
action whose guards still hold, or asks a backend (an LLM, or a deterministic
scripted policy when no model is reachable) for a fresh decision.

Backends implement complete(prompt) -> Completion(text, prompt_tokens,
completion_tokens). Token counts are reported by the backend; the scripted
policy reports zeros, never invented numbers.
"""
from __future__ import annotations

import os
import re
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path

from decision_cache import DecisionCache, Guard, signature

README_START = "<!-- modules:start -->"
README_END = "<!-- modules:end -->"

# ---------------------------------------------------------------- fixture ---


def build_workspace(ws: Path) -> None:
    """(Re)create a deterministic fixture repo to run maintenance on."""
    if ws.exists():
        shutil.rmtree(ws)
    pkg = ws / "demo_pkg"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text('"""Demo package for the agent run."""\n', encoding="utf-8")
    (pkg / "utils.py").write_text(
        '''"""Small shared helpers."""

import sys
import re
import os
import json


def slugify(name):
    """Return a filesystem-safe slug."""
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def dump(obj, path):
    """Write obj as sorted JSON."""
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, indent=2, sort_keys=True)
''',
        encoding="utf-8",
    )
    (pkg / "api.py").write_text(
        '''"""Helpers for building maintenance api urls."""

import os
from demo_pkg.utils import slugify


def resource_path(kind, identifier):
    # kept on one line on purpose: the lint task has to wrap it
    return build_resource_url(slugify(kind), slugify(identifier), verbose=True, include_metadata=True, trace=False)


def build_resource_url(*args, **kwargs):
    """Pretend url builder used by the maintenance api."""
    return os.path.join("/api/v1", *args) + "?" + "&".join(sorted(kwargs.items()))
''',
        encoding="utf-8",
    )
    (pkg / "legacy.py").write_text(
        '''"""Legacy helpers kept for backward compatibility."""

import datetime
import os
import sys
from demo_pkg.utils import slugify
import re


def stamp(name):
    """Return a timestamped slug."""
    return f"{datetime.datetime.now().isoformat()}:{slugify(name)}"
''',
        encoding="utf-8",
    )
    (ws / "README.md").write_text(
        f"""# fixture repo

Internal docs for demo_pkg.

{README_START}
stale content that the agent must replace
{README_END}

Everything below the markers is managed by the agent.
""",
        encoding="utf-8",
    )


# ------------------------------------------------------- scripted policy ---


def sort_imports(text: str) -> str:
    """Sort the first import block (stdlib first, then first-party)."""
    lines = text.splitlines()
    start = next(
        (i for i, l in enumerate(lines) if l.startswith(("import ", "from "))), None
    )
    if start is None:
        return text
    end = start
    while end < len(lines):
        if lines[end].strip() == "":
            nxt = next(
                (l for l in lines[end:] if l.strip() != ""), None
            )
            if nxt is None or not nxt.startswith(("import ", "from ")):
                break
        elif not lines[end].startswith(("import ", "from ")):
            break
        end += 1
    imports = [l for l in lines[start:end] if l.startswith(("import ", "from "))]
    if not imports:
        return text
    std = sorted(l for l in imports if not l.startswith("from demo_pkg"))
    loc = sorted(l for l in imports if l.startswith("from demo_pkg"))
    rest = lines[end:]
    while rest and rest[0].strip() == "":
        rest.pop(0)
    prefix = lines[:start]
    out = prefix + std
    if loc:
        out.append("")
        out.extend(loc)
    out.append("")
    out.extend(rest)
    return "\n".join(out).rstrip("\n") + "\n"


def lint_fix(text: str) -> str:
    """Strip trailing whitespace, collapse 3+ blank lines, single final newline."""
    lines = [l.rstrip() for l in text.splitlines()]
    out: list[str] = []
    blanks = 0
    for l in lines:
        blanks = blanks + 1 if l == "" else 0
        if blanks <= 2:
            out.append(l)
    return "\n".join(out).rstrip("\n") + "\n"


def wrap_long_lines(text: str, limit: int = 88) -> str:
    """Wrap over-long call lines by splitting top-level comma arguments."""
    out: list[str] = []
    for line in text.splitlines():
        if len(line) <= limit or "(" not in line or not line.rstrip().endswith(")"):
            out.append(line)
            continue
        indent = re.match(r"\s*", line).group(0)
        m = re.match(r"(\s*(?:return |yield )?(.*?)\()(.*)\)\s*$", line)
        if not m:
            out.append(line)
            continue
        prefix, call, args_src = m.group(1), m.group(2), m.group(3)
        parts, depth, cur = [], 0, ""
        for ch in args_src:
            if ch in "([{":
                depth += 1
            elif ch in ")]}":
                depth -= 1
            if ch == "," and depth == 0:
                parts.append(cur)
                cur = ""
            else:
                cur += ch
        parts.append(cur)
        if any(len(p.strip()) + len(indent) + 8 > limit for p in parts):
            out.append(line)  # cannot wrap cleanly, leave for a human
            continue
        out.append(prefix.rstrip())
        for p in parts:
            out.append(f"{indent}    {p.strip()},")
        out.append(f"{indent})")
    return "\n".join(out) + "\n"


def regenerate_readme(text: str, listing: list[tuple[str, str]]) -> str:
    """Replace the managed section between the markers from the listing."""
    lines = [f"- `{path}`: {doc}" for path, doc in listing]
    section = "\n".join([README_START, *lines, README_END])
    pat = re.compile(re.escape(README_START) + r".*?" + re.escape(README_END), re.S)
    if not pat.search(text):
        return text
    return pat.sub(lambda _: section, text)


# ---------------------------------------------------------------- tasks ---


@dataclass
class Task:
    task_id: str
    path: str
    instruction: str
    extra_inputs: list[str] = field(default_factory=list)
    command_guard: tuple[str, list[str]] | None = None
    listing_guard: str | None = None


TASKS = [
    Task(
        "sort_imports",
        "demo_pkg/utils.py",
        "sort the import block",
        command_guard=("parses", ["demo_pkg/utils.py"]),
    ),
    Task("sort_imports", "demo_pkg/legacy.py", "sort the import block"),
    Task(
        "lint_fix",
        "demo_pkg/api.py",
        "strip trailing whitespace and wrap lines over 88 chars",
    ),
    Task(
        "regen_readme",
        "README.md",
        "regenerate the module index between the markers from the listing",
        extra_inputs=["demo_pkg/utils.py", "demo_pkg/api.py", "demo_pkg/legacy.py"],
        listing_guard="demo_pkg",
    ),
]


def task_signature(task: Task) -> str:
    return signature(task.task_id, task.path, task.instruction)


def module_listing(ws: Path) -> list[tuple[str, str]]:
    """Sorted (path, first docstring line) for every module in demo_pkg."""
    items = []
    for p in sorted((ws / "demo_pkg").glob("*.py")):
        doc = ""
        m = re.search(r'"""(.*)', p.read_text(encoding="utf-8"))
        if m:
            doc = m.group(1).strip().strip('"')
        items.append((p.relative_to(ws).as_posix(), doc))
    return items


def build_prompt(ws: Path, task: Task) -> tuple[str, str]:
    """Return (prompt, current_target_content) for a task."""
    current = (ws / task.path).read_text(encoding="utf-8")
    if task.task_id == "regen_readme":
        listing = "\n".join(f"{path} :: {doc}" for path, doc in module_listing(ws))
        prompt = (
            "You are a repo maintenance agent. Apply this task to a file and\n"
            "return the complete new file content.\n\n"
            f"TASK {task.task_id} {task.path}\n"
            f"INSTRUCTION {task.instruction}\n\n"
            f"Module listing (path :: description):\n{listing}\n\n"
            f"Current content of {task.path}:\n"
            f"```text\n{current}```\n\n"
            "Return only the complete new file content inside one fenced code\n"
            "block. Keep everything outside the modules:start/modules:end\n"
            "markers unchanged."
        )
    else:
        lang = "text" if task.path.endswith(".md") else "python"
        prompt = (
            "You are a repo maintenance agent. Apply this task to a file and\n"
            "return the complete new file content.\n\n"
            f"TASK {task.task_id} {task.path}\n"
            f"INSTRUCTION {task.instruction}\n\n"
            f"Current content of {task.path}:\n"
            f"```{lang}\n{current}```\n\n"
            "Return only the complete new file content inside one fenced code\n"
            "block. Do not add commentary."
        )
    return prompt, current


# -------------------------------------------------------------- backends ---


@dataclass
class Completion:
    text: str
    prompt_tokens: int = 0
    completion_tokens: int = 0


class BackendUnavailable(Exception):
    """Raised when no model backend can be used at all."""


class ScriptedBackend:
    """Deterministic offline policy. Reports zero tokens, never invented."""

    name = "scripted"

    def complete(self, prompt: str) -> Completion:
        m = re.search(r"^TASK (\S+) (\S+)$", prompt, re.M)
        if not m:
            raise ValueError("prompt without TASK header")
        task_id, _path = m.group(1), m.group(2)
        blocks = re.findall(r"```[a-z]*\n(.*?)```", prompt, re.S)
        if not blocks:
            raise ValueError("prompt without target content")
        current = blocks[-1]
        if task_id == "sort_imports":
            new = sort_imports(current)
        elif task_id == "lint_fix":
            new = lint_fix(wrap_long_lines(current))
        elif task_id == "regen_readme":
            listing = [
                tuple(line.split(" :: ", 1))
                for line in re.search(r"\n\n(.*?)\n\nCurrent content", prompt, re.S).group(1).splitlines()
                if " :: " in line
            ]
            new = regenerate_readme(current, listing)
        else:
            raise ValueError(f"unknown task {task_id}")
        return Completion(new, 0, 0)


class LLMBackend:
    """OpenAI-compatible chat backend. Token counts come from API usage."""

    name = "llm"
    base_url = "https://api.z.ai/api/paas/v4"

    def __init__(self, model: str = "glm-4.5-flash", api_key: str | None = None) -> None:
        api_key = api_key or os.environ.get("ZAI_API_KEY")
        if not api_key:
            raise BackendUnavailable("ZAI_API_KEY not set")
        from openai import OpenAI

        self.model = model
        self.client = OpenAI(api_key=api_key, base_url=self.base_url, timeout=90.0)

    @classmethod
    def probe(cls, model: str) -> "LLMBackend":
        """One tiny billed call to check the model actually answers."""
        backend = cls(model)
        backend.complete("Reply with the single word: ready", max_tokens=16)
        return backend

    def complete(self, prompt: str, max_tokens: int = 2048) -> Completion:
        try:
            return self._call(prompt, max_tokens, thinking=False)
        except Exception as exc:
            if "thinking" in str(exc).lower() or "parameter" in str(exc).lower():
                return self._call(prompt, max_tokens, thinking=True)
            raise

    def _call(self, prompt: str, max_tokens: int, thinking: bool) -> Completion:
        extra = {} if thinking else {"thinking": {"type": "disabled"}}
        resp = self.client.chat.completions.create(
            model=self.model,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=max_tokens,
            extra_body=extra,
        )
        content = resp.choices[0].message.content or ""
        blocks = re.findall(r"```[a-z]*\n(.*?)```", content, re.S)
        text = blocks[-1] if blocks else content
        usage = getattr(resp, "usage", None)
        return Completion(
            text,
            getattr(usage, "prompt_tokens", 0) or 0,
            getattr(usage, "completion_tokens", 0) or 0,
        )


# ------------------------------------------------------------- agent run ---


@dataclass
class RunStats:
    label: str
    backend: str
    tasks: int = 0
    decision_calls: int = 0
    model_calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    reused: int = 0
    guard_misses: int = 0
    wall_seconds: float = 0.0


def apply_action(ws: Path, action: dict) -> None:
    if action.get("op") != "write_file":
        raise ValueError(f"unsupported action op {action.get('op')!r}")
    target = ws / action["path"]
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(action["content"], encoding="utf-8", newline="\n")


def guards_for(task: Task, new_content: str, ws: Path) -> list[Guard]:
    """Guards pinning the reuse contract of a write decision.

    The target file guard pins the post-state (the exact content the action
    writes), because the decision is an idempotent write: reuse is valid
    while the file is still exactly what the action produced. Input files
    beyond the target are pinned at their current (pre-action) content.
    """
    guards = [Guard.file_hash_text(task.path, new_content)]
    for rel in task.extra_inputs:
        guards.append(Guard.file_hash_of(ws, rel))
    if task.listing_guard:
        guards.append(Guard.dir_listing_of(ws, task.listing_guard, "*.py"))
    if task.command_guard:
        snippet, args = task.command_guard
        guards.append(Guard.command_exit_code(snippet, args))
    return guards


def run_task(
    ws: Path, cache: DecisionCache, backend, task: Task, stats: RunStats
) -> str:
    """Run one task through the cache. Returns reused|fresh|hit_stale."""
    stats.tasks += 1
    sig = task_signature(task)
    found = cache.lookup(sig)
    if found is not None and found.reusable:
        apply_action(ws, found.action)
        stats.reused += 1
        return "reused"
    if found is not None:
        stats.guard_misses += 1  # stale hit: guards correctly forced a fresh decision

    prompt, _current = build_prompt(ws, task)
    completion = backend.complete(prompt)
    stats.decision_calls += 1
    if backend.name == "llm":
        stats.model_calls += 1
        stats.prompt_tokens += completion.prompt_tokens
        stats.completion_tokens += completion.completion_tokens

    action = {"op": "write_file", "path": task.path, "content": completion.text}
    apply_action(ws, action)
    cache.record(sig, action, guards_for(task, completion.text, ws))
    return "fresh"


def run_workload(
    ws: Path, cache: DecisionCache, backend, tasks: list[Task], label: str
) -> RunStats:
    stats = RunStats(label=label, backend=backend.name)
    start = time.perf_counter()
    for task in tasks:
        outcome = run_task(ws, cache, backend, task, stats)
        print(f"  [{label}] {task.task_id} {task.path}: {outcome}")
    stats.wall_seconds = time.perf_counter() - start
    return stats
