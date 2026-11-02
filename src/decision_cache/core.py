"""Core of decision-cache: a guarded decision store.

record() stores an action under a context signature. lookup() returns the
stored action plus the outcome of every guard. A cached decision is reusable
only while all its guards still hold, so a changed environment forces a
fresh decision instead of a stale replay.

Guard evaluation is pure Python for file checks. The command guard never
runs free-form shell: it may only invoke the current interpreter with one
of a fixed set of read-only snippets, with the working directory pinned to
the cache root and every path argument confined inside that root.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

GUARD_KINDS = ("file_exists", "file_hash", "dir_listing", "command_exit_code")

# Fixed read-only snippets the command guard may execute.
ALLOWED_SNIPPETS = {
    "parses": "import ast, sys; ast.parse(open(sys.argv[1], encoding='utf-8').read())",
}
_COMMAND_TIMEOUT_S = 10


def signature(*parts: str) -> str:
    """Stable context signature: sha256 hex over the joined parts."""
    h = hashlib.sha256()
    h.update("\x1f".join(parts).encode("utf-8"))
    return h.hexdigest()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _normalize(text: str) -> str:
    # Guards compare content identity modulo line-ending style, so a file
    # written with CRLF on Windows still matches a decision pinned with LF.
    return text.replace("\r\n", "\n").replace("\r", "\n")


def sha256_text(text: str) -> str:
    return sha256_bytes(_normalize(text).encode("utf-8"))


def sha256_file(path: Path) -> str:
    return sha256_text(path.read_text(encoding="utf-8"))


def listing_digest(path: Path, pattern: str = "*") -> str:
    """Hash of the sorted names matching pattern under path."""
    names = sorted(p.name for p in Path(path).glob(pattern) if p.is_file())
    return sha256_text("\n".join(names))


@dataclass(frozen=True)
class Guard:
    """A predicate over the environment, evaluated at lookup time."""

    kind: str
    spec: dict

    @classmethod
    def file_exists(cls, path: str) -> "Guard":
        return cls("file_exists", {"path": path})

    @classmethod
    def file_hash(cls, path: str, digest: str) -> "Guard":
        return cls("file_hash", {"path": path, "sha256": digest})

    @classmethod
    def file_hash_of(cls, root: Path, path: str) -> "Guard":
        """Pin the current on-disk content of root/path."""
        return cls("file_hash", {"path": path, "sha256": sha256_file(Path(root) / path)})

    @classmethod
    def file_hash_text(cls, path: str, text: str) -> "Guard":
        """Pin root/path to exactly this text (a post-state guard)."""
        return cls("file_hash", {"path": path, "sha256": sha256_text(text)})

    @classmethod
    def dir_listing(
        cls, path: str, pattern: str = "*", digest: str | None = None
    ) -> "Guard":
        """Pin the set of names matching pattern under root/path."""
        return cls("dir_listing", {"path": path, "pattern": pattern, "sha256": digest or ""})

    @classmethod
    def dir_listing_of(cls, root: Path, path: str, pattern: str = "*") -> "Guard":
        """Pin the current set of names matching pattern under root/path."""
        return cls.dir_listing(path, pattern, listing_digest(Path(root) / path, pattern))

    @classmethod
    def command_exit_code(
        cls, snippet: str, args: list[str] | None = None, exit_code: int = 0
    ) -> "Guard":
        return cls(
            "command_exit_code",
            {"snippet": snippet, "args": list(args or []), "exit_code": exit_code},
        )


@dataclass(frozen=True)
class GuardOutcome:
    guard: Guard
    ok: bool
    detail: str


def _inside(root: Path, rel: str) -> Path | None:
    """Resolve rel against root, or None if it escapes the root."""
    root = root.resolve()
    p = (root / rel).resolve()
    if not p.is_relative_to(root):
        return None
    return p


def evaluate_guard(guard: Guard, root: Path) -> GuardOutcome:
    """Evaluate one guard against the environment rooted at root."""
    try:
        if guard.kind == "file_exists":
            p = _inside(root, guard.spec["path"])
            ok = bool(p and p.exists())
            return GuardOutcome(guard, ok, "exists" if ok else "missing or outside root")

        if guard.kind == "file_hash":
            p = _inside(root, guard.spec["path"])
            if p is None or not p.exists():
                return GuardOutcome(guard, False, "file missing or outside root")
            ok = sha256_file(p) == guard.spec["sha256"]
            return GuardOutcome(guard, ok, "hash matches" if ok else "hash differs")

        if guard.kind == "dir_listing":
            p = _inside(root, guard.spec["path"])
            if p is None or not p.is_dir():
                return GuardOutcome(guard, False, "directory missing or outside root")
            ok = listing_digest(p, guard.spec.get("pattern", "*")) == guard.spec.get("sha256", "")
            return GuardOutcome(guard, ok, "listing matches" if ok else "listing differs")

        if guard.kind == "command_exit_code":
            snippet = guard.spec["snippet"]
            if snippet not in ALLOWED_SNIPPETS:
                return GuardOutcome(guard, False, "snippet not in allowlist")
            argv = [sys.executable, "-c", ALLOWED_SNIPPETS[snippet]]
            for rel in guard.spec.get("args", []):
                p = _inside(root, rel)
                if p is None:
                    return GuardOutcome(guard, False, "argument outside root")
                argv.append(str(p))
            proc = subprocess.run(
                argv,
                cwd=str(root),
                capture_output=True,
                timeout=_COMMAND_TIMEOUT_S,
            )
            ok = proc.returncode == guard.spec.get("exit_code", 0)
            return GuardOutcome(guard, ok, f"exit code {proc.returncode}")

        return GuardOutcome(guard, False, f"unknown guard kind {guard.kind!r}")
    except Exception as exc:  # a failing guard must never crash the agent
        return GuardOutcome(guard, False, f"error: {exc}")


@dataclass
class Entry:
    action: dict
    guards: list[Guard]
    recorded_at: float = field(default_factory=time.time)


@dataclass
class LookupResult:
    signature: str
    action: dict
    outcomes: list[GuardOutcome]

    @property
    def reusable(self) -> bool:
        return all(o.ok for o in self.outcomes)

    @property
    def failed(self) -> list[GuardOutcome]:
        return [o for o in self.outcomes if not o.ok]


class DecisionCache:
    """Record decisions keyed by context signature, replay under guards.

    Persisted as a single JSON file so a cache survives across processes.
    """

    def __init__(self, root: Path, store: Path) -> None:
        self.root = Path(root)
        self.store = Path(store)
        self._entries: dict[str, Entry] = {}
        if self.store.exists():
            self._load()

    def __len__(self) -> int:
        return len(self._entries)

    def record(
        self, context_signature: str, action: dict, guards: list[Guard]
    ) -> None:
        self._entries[context_signature] = Entry(action=dict(action), guards=list(guards))
        self._save()

    def lookup(self, context_signature: str) -> LookupResult | None:
        """Return the stored action plus guard status, or None on a miss.

        Guards are evaluated at lookup time, not at record time, so the
        result reflects the environment right now.
        """
        entry = self._entries.get(context_signature)
        if entry is None:
            return None
        outcomes = [evaluate_guard(g, self.root) for g in entry.guards]
        return LookupResult(context_signature, entry.action, outcomes)

    def _save(self) -> None:
        data = {
            "version": 1,
            "entries": {
                sig: {
                    "action": e.action,
                    "guards": [{"kind": g.kind, "spec": g.spec} for g in e.guards],
                    "recorded_at": e.recorded_at,
                }
                for sig, e in self._entries.items()
            },
        }
        self.store.parent.mkdir(parents=True, exist_ok=True)
        self.store.write_text(json.dumps(data, indent=2), encoding="utf-8")

    def _load(self) -> None:
        try:
            data = json.loads(self.store.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return  # corrupt store behaves as an empty cache
        for sig, raw in data.get("entries", {}).items():
            guards = [
                Guard(g["kind"], g["spec"]) for g in raw.get("guards", [])
            ]
            self._entries[sig] = Entry(raw.get("action", {}), guards, raw.get("recorded_at", 0.0))
