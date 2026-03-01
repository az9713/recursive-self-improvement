"""Base abstractions for benchmark evaluators."""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class EvalResult:
    """Result of evaluating one program output against one benchmark task."""

    program_id: str
    task_id: str
    correct: bool
    score: float
    cost: float
    trace: str


@dataclass
class BenchmarkTask:
    """A single benchmark task to be solved by a reasoning program."""

    task_id: str
    question: str
    expected_answer: str
    context: dict | None = field(default=None)


class Evaluator(ABC):
    """Abstract base class for frozen benchmark evaluators.

    Evaluators load tasks from a fixed dataset and check program outputs against
    ground truth. They never call LLMs — they only provide tasks and verify answers.
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Human-readable name for this benchmark."""

    @abstractmethod
    def load_tasks(self, n_samples: int = 100) -> list[BenchmarkTask]:
        """Load a sample of tasks from the benchmark dataset.

        Parameters
        ----------
        n_samples:
            Maximum number of tasks to return. Uses a fixed seed so the same
            tasks are returned for reproducibility.

        Returns
        -------
        A list of BenchmarkTask instances ready to be passed to programs.
        """

    @abstractmethod
    def check_answer(self, predicted: str, expected: str) -> bool:
        """Check whether a predicted answer matches the expected answer.

        Parameters
        ----------
        predicted:
            The raw string output produced by the reasoning program.
        expected:
            The ground-truth answer string from the dataset.

        Returns
        -------
        True if the answer is considered correct, False otherwise.
        """
