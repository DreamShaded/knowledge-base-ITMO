"""Generator package: three-zone answer generation + AnswerEnvelope schema."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_DIR = Path(__file__).parent
_PKG = "app.services.generator"


def _load(logical: str, filename: str):
    """Load a kebab-case module file and register it in sys.modules."""
    fqname = f"{_PKG}.{logical}"
    spec = importlib.util.spec_from_file_location(fqname, _DIR / filename)
    mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    sys.modules[fqname] = mod
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


_envelope_mod = _load("_envelope", "answer-envelope.py")
_generator_mod = _load("_generator", "three-zone-generator.py")

AnswerEnvelope = _envelope_mod.AnswerEnvelope
Citation = _envelope_mod.Citation
Section = _envelope_mod.Section
ConfidenceSignal = _envelope_mod.ConfidenceSignal

generate = _generator_mod.generate
make_refusal_envelope = _generator_mod.make_refusal_envelope

__all__ = [
    "AnswerEnvelope",
    "Citation",
    "Section",
    "ConfidenceSignal",
    "generate",
    "make_refusal_envelope",
]
