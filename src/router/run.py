"""Module entry point so `python -m src.router.run` works.

Delegates to scripts/run_batch.py (the canonical batch CLI). The demo
orchestrator invokes batches this way.
"""
from __future__ import annotations

import importlib.machinery
import importlib.util
import pathlib
import sys


def _load_script():
    script = pathlib.Path(__file__).resolve().parents[2] / "scripts" / "run_batch.py"
    loader = importlib.machinery.SourceFileLoader("run_batch_script", str(script))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    loader.exec_module(mod)
    return mod


if __name__ == "__main__":
    raise SystemExit(_load_script().main())
