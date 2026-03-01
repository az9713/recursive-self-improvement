"""Frozen evaluation layer for GSM8K and ARC-AGI benchmarks.

These evaluators are "frozen" — the meta-system never modifies them.
They load ground-truth tasks and check program outputs against those truths.

Usage::

    from src.evaluator import GSM8KEvaluator, ARCEvaluator

    gsm8k = GSM8KEvaluator()
    tasks = gsm8k.load_tasks(n_samples=100)

    arc = ARCEvaluator()
    arc_tasks = arc.load_tasks(n_samples=50)
"""

from src.evaluator.arc import ARCEvaluator
from src.evaluator.base import BenchmarkTask, EvalResult, Evaluator
from src.evaluator.gsm8k import GSM8KEvaluator

__all__ = [
    "EvalResult",
    "BenchmarkTask",
    "Evaluator",
    "GSM8KEvaluator",
    "ARCEvaluator",
]
