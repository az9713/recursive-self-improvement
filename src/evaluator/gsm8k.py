"""GSM8K math reasoning benchmark evaluator."""

from __future__ import annotations

import logging
import re

from src.evaluator.base import BenchmarkTask, Evaluator

logger = logging.getLogger(__name__)

# Fixed seed for reproducible sampling across runs
_SAMPLE_SEED = 42


def _extract_gsm8k_answer(answer_text: str) -> str:
    """Extract the numeric answer that follows '####' in a GSM8K ground-truth string.

    GSM8K ground truth format: '...reasoning steps...\\n#### {number}'

    Parameters
    ----------
    answer_text:
        The raw answer field from the GSM8K dataset.

    Returns
    -------
    The number string after '####', stripped of whitespace and commas.
    """
    match = re.search(r"####\s*([\d,.\-]+)", answer_text)
    if match:
        return match.group(1).replace(",", "").strip()
    # Fallback: return the whole string stripped
    logger.warning("GSM8K: could not find '####' marker in answer: %r", answer_text[:80])
    return answer_text.strip()


def _extract_predicted_number(predicted: str) -> str | None:
    """Extract the final numeric answer from a program's free-form output.

    Strategy: find the last number in the string (integers, decimals, negatives).
    Handles commas as thousands separators (e.g. "1,234" → "1234").

    Parameters
    ----------
    predicted:
        Raw output from the reasoning program.

    Returns
    -------
    Normalised number string, or None if no number could be found.
    """
    # Remove commas used as thousands separators before matching
    cleaned = predicted.replace(",", "")

    # Match integers and decimals, optionally negative
    # Looks for: optional minus, digits, optional decimal point + digits
    numbers = re.findall(r"-?\d+(?:\.\d+)?", cleaned)

    if not numbers:
        return None

    # Return the last number found — LLMs typically put the final answer last
    return numbers[-1]


class GSM8KEvaluator(Evaluator):
    """Evaluator for the GSM8K grade-school math benchmark.

    Loads 8.5k test problems from HuggingFace and checks numeric answers.
    Each problem requires multi-step arithmetic reasoning.
    """

    @property
    def name(self) -> str:
        return "gsm8k"

    def load_tasks(self, n_samples: int = 100) -> list[BenchmarkTask]:
        """Load tasks from the GSM8K test split via HuggingFace datasets.

        Parameters
        ----------
        n_samples:
            Number of tasks to sample. Capped at dataset size.

        Returns
        -------
        List of BenchmarkTask with numeric expected answers.
        """
        try:
            import datasets  # noqa: PLC0415
        except ImportError as exc:
            raise ImportError(
                "The 'datasets' package is required for GSM8KEvaluator. "
                "Install it with: pip install datasets"
            ) from exc

        logger.info("Loading GSM8K test split from HuggingFace...")
        dataset = datasets.load_dataset("gsm8k", "main", split="test")

        total = len(dataset)
        n_samples = min(n_samples, total)
        logger.info("GSM8K test set has %d examples; sampling %d", total, n_samples)

        # Fixed-seed shuffle for reproducibility
        shuffled = dataset.shuffle(seed=_SAMPLE_SEED)
        subset = shuffled.select(range(n_samples))

        tasks: list[BenchmarkTask] = []
        for example in subset:
            task_id = f"gsm8k_{example['question'][:40].replace(' ', '_')}"
            # Sanitise task_id to safe characters
            task_id = re.sub(r"[^\w]", "_", task_id)

            expected = _extract_gsm8k_answer(example["answer"])

            tasks.append(
                BenchmarkTask(
                    task_id=task_id,
                    question=example["question"],
                    expected_answer=expected,
                    context=None,
                )
            )

        logger.info("Loaded %d GSM8K tasks", len(tasks))
        return tasks

    def check_answer(self, predicted: str, expected: str) -> bool:
        """Compare a predicted answer to the ground-truth numeric answer.

        Both values are normalised before comparison:
        - Commas removed (thousands separators)
        - Leading/trailing whitespace stripped
        - Decimal trailing zeros normalised (e.g. "2.0" == "2")

        Parameters
        ----------
        predicted:
            Free-form output from the reasoning program.
        expected:
            Numeric string extracted from GSM8K ground truth (no commas).

        Returns
        -------
        True if the extracted number matches expected.
        """
        predicted_num = _extract_predicted_number(predicted)
        if predicted_num is None:
            logger.debug("GSM8K check_answer: no number found in predicted output")
            return False

        # Normalise expected (strip commas, whitespace)
        expected_norm = expected.replace(",", "").strip()

        # Try numeric comparison first to handle "2.0" == "2" cases
        try:
            predicted_float = float(predicted_num)
            expected_float = float(expected_norm)
            if predicted_float == expected_float:
                return True
        except ValueError:
            pass

        # Fallback: string comparison
        return predicted_num == expected_norm
