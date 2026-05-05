"""Router package: rule-based scorer + LLM fallback + RouterDecision schema."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_DIR = Path(__file__).parent
_PKG = "app.services.router"


def _load(logical: str, filename: str):
    """Load a kebab-case module file and register it in sys.modules."""
    fqname = f"{_PKG}.{logical}"
    spec = importlib.util.spec_from_file_location(fqname, _DIR / filename)
    mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    sys.modules[fqname] = mod
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


_decision_mod = _load("_decision", "router-decision.py")
_scorer_mod = _load("_scorer", "rule-scorer.py")
_llm_mod = _load("_llm", "llm-router.py")

RouterDecision = _decision_mod.RouterDecision
RetrievalContext = _decision_mod.RetrievalContext
rule_score = _scorer_mod.score
best_intent = _scorer_mod.best_intent
route_with_llm = _llm_mod.route_with_llm

__all__ = [
    "RouterDecision",
    "RetrievalContext",
    "rule_score",
    "best_intent",
    "route_with_llm",
]
