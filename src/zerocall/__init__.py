"""Guarded decision reuse for tool-using agents, stdlib only.

A zero-training simplification of the decision reuse idea in arXiv
2610.32813 (HarnessMM). See README.md for the honest differences.
"""
from .core import (
    ALLOWED_SNIPPETS,
    DecisionCache,
    Entry,
    Guard,
    GuardOutcome,
    LookupResult,
    evaluate_guard,
    sha256_file,
    sha256_text,
    signature,
)

__all__ = [
    "ALLOWED_SNIPPETS",
    "DecisionCache",
    "Entry",
    "Guard",
    "GuardOutcome",
    "LookupResult",
    "evaluate_guard",
    "sha256_file",
    "sha256_text",
    "signature",
]
