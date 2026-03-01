"""ARC-AGI benchmark evaluator.

Loads Abstraction and Reasoning Corpus (ARC) tasks from a cached local copy,
downloading them from GitHub if the cache is not present.

Each ARC task consists of:
- Several "train" pairs: (input_grid, output_grid) demonstrations
- One "test" pair: input_grid only (the program must predict the output_grid)

Grids are 2-D lists of integers (0-9 representing colours).
"""

from __future__ import annotations

import json
import logging
import os
import re
import urllib.request
import zipfile
from pathlib import Path

from src.evaluator.base import BenchmarkTask, Evaluator

logger = logging.getLogger(__name__)

# Fixed seed for reproducible sampling
_SAMPLE_SEED = 42

# GitHub ZIP archive for the full ARC-AGI repository
_ARC_ZIP_URL = "https://github.com/fchollet/ARC-AGI/archive/refs/heads/master.zip"

# Relative path inside the ZIP where training task JSON files live
_ZIP_TASKS_PREFIX = "ARC-AGI-master/data/training/"

# Default local cache directory (relative to repo root; resolved at runtime)
_DEFAULT_CACHE_DIR = Path(__file__).resolve().parents[2] / "data" / "arc"

Grid = list[list[int]]


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------


def _format_grid(grid: Grid) -> str:
    """Render a 2-D grid as a compact multi-line string.

    Each row is space-separated integers on its own line, making it easy for
    LLMs to parse visually.

    Example output::

        0 1 2
        3 4 5
        6 7 8
    """
    return "\n".join(" ".join(str(cell) for cell in row) for row in grid)


def _format_task_for_llm(train_pairs: list[dict], test_input: Grid) -> str:
    """Serialise an ARC task into a human-readable prompt string.

    Format::

        You are solving an ARC (Abstraction and Reasoning Corpus) task.
        Each task shows input/output grid pairs as demonstrations.
        Your job is to figure out the transformation rule and apply it to the
        test input.

        --- Training Example 1 ---
        Input:
        0 1
        2 3
        Output:
        3 2
        1 0

        ...

        --- Test Input ---
        0 2
        1 3

        Respond with ONLY the output grid, one row per line, values
        space-separated. Do not include any explanation.
    """
    lines: list[str] = [
        "You are solving an ARC (Abstraction and Reasoning Corpus) task.",
        "Each task shows input/output grid pairs as demonstrations.",
        "Your job is to figure out the transformation rule and apply it to the test input.",
        "",
    ]

    for i, pair in enumerate(train_pairs, start=1):
        lines.append(f"--- Training Example {i} ---")
        lines.append("Input:")
        lines.append(_format_grid(pair["input"]))
        lines.append("Output:")
        lines.append(_format_grid(pair["output"]))
        lines.append("")

    lines.append("--- Test Input ---")
    lines.append(_format_grid(test_input))
    lines.append("")
    lines.append(
        "Respond with ONLY the output grid, one row per line, "
        "values space-separated. Do not include any explanation."
    )

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Answer parsing helpers
# ---------------------------------------------------------------------------


def _find_json_arrays(text: str) -> list[str]:
    """Extract all balanced bracket substrings starting with '[' from text.

    Uses a bracket-balance counter so nested arrays are captured in full,
    e.g. '[[1,2],[3,4]]' is returned as a single candidate string.

    Parameters
    ----------
    text:
        Source string to scan.

    Returns
    -------
    List of candidate substrings, each starting with '['.
    """
    candidates: list[str] = []
    i = 0
    while i < len(text):
        if text[i] == "[":
            depth = 0
            for j in range(i, len(text)):
                if text[j] == "[":
                    depth += 1
                elif text[j] == "]":
                    depth -= 1
                    if depth == 0:
                        candidates.append(text[i : j + 1])
                        i = j  # advance past this closing bracket
                        break
            else:
                # No matching close bracket found; skip
                pass
        i += 1
    return candidates


def _parse_grid_from_json(text: str) -> Grid | None:
    """Try to parse a grid from JSON embedded in the text.

    Scans for all balanced bracket substrings and tries to parse each as a
    valid 2-D integer grid.  Returns the first match found.

    Parameters
    ----------
    text:
        Raw string that may contain a JSON grid somewhere inside it.

    Returns
    -------
    Parsed grid, or None if no valid grid found.
    """
    # Try the whole text first (model may return pure JSON)
    try:
        parsed = json.loads(text.strip())
        if _is_valid_grid(parsed):
            return parsed
    except (json.JSONDecodeError, TypeError, ValueError):
        pass

    # Scan for balanced bracket substrings and try each as JSON
    for candidate in _find_json_arrays(text):
        try:
            parsed = json.loads(candidate)
            if _is_valid_grid(parsed):
                return parsed
        except (json.JSONDecodeError, TypeError, ValueError):
            continue

    return None


def _parse_grid_from_plain_text(text: str) -> Grid | None:
    """Try to parse a grid from space/comma-separated rows of integers.

    Handles formats like::

        0 1 2
        3 4 5

    or::

        0, 1, 2
        3, 4, 5

    Parameters
    ----------
    text:
        Raw string that may contain a grid as plain text rows.

    Returns
    -------
    Parsed grid, or None if no consistent grid-like structure found.
    """
    lines = text.strip().splitlines()
    grid: Grid = []

    for line in lines:
        line = line.strip()
        if not line:
            continue
        # Replace commas and extra spaces
        normalized = re.sub(r"[,\t]+", " ", line)
        tokens = normalized.split()
        try:
            row = [int(t) for t in tokens]
        except ValueError:
            # Line contains non-integer tokens; stop accumulating
            if grid:
                break
            continue
        grid.append(row)

    if not grid:
        return None

    # Validate consistent row lengths
    row_len = len(grid[0])
    if not all(len(row) == row_len for row in grid):
        return None

    return grid


def _is_valid_grid(obj: object) -> bool:
    """Return True if obj looks like a non-empty 2-D grid of integers."""
    if not isinstance(obj, list) or not obj:
        return False
    if not isinstance(obj[0], list):
        return False
    row_len = len(obj[0])
    if row_len == 0:
        return False
    for row in obj:
        if not isinstance(row, list) or len(row) != row_len:
            return False
        if not all(isinstance(cell, int) for cell in row):
            return False
    return True


def _extract_grid(text: str) -> Grid | None:
    """Attempt to extract a 2-D grid from free-form predicted text.

    Tries JSON parsing first, then falls back to plain-text row parsing.

    Parameters
    ----------
    text:
        Raw output from the reasoning program.

    Returns
    -------
    Extracted grid, or None if extraction failed.
    """
    grid = _parse_grid_from_json(text)
    if grid is not None:
        return grid
    return _parse_grid_from_plain_text(text)


# ---------------------------------------------------------------------------
# Data loading helpers
# ---------------------------------------------------------------------------


def _download_arc_dataset(cache_dir: Path) -> None:
    """Download the ARC-AGI dataset ZIP from GitHub and extract training tasks.

    The ZIP is downloaded once and then deleted; only the extracted JSON files
    are kept in ``cache_dir``.

    Parameters
    ----------
    cache_dir:
        Directory where individual task JSON files will be stored.
    """
    cache_dir.mkdir(parents=True, exist_ok=True)
    zip_path = cache_dir / "arc_master.zip"

    logger.info("Downloading ARC-AGI dataset from GitHub...")
    try:
        urllib.request.urlretrieve(_ARC_ZIP_URL, zip_path)
    except Exception as exc:
        raise RuntimeError(
            f"Failed to download ARC-AGI dataset from {_ARC_ZIP_URL}: {exc}"
        ) from exc

    logger.info("Extracting ARC training tasks...")
    extracted_count = 0
    with zipfile.ZipFile(zip_path, "r") as zf:
        for member in zf.namelist():
            if member.startswith(_ZIP_TASKS_PREFIX) and member.endswith(".json"):
                # Extract just the filename, store flat in cache_dir
                filename = os.path.basename(member)
                if not filename:
                    continue
                dest = cache_dir / filename
                with zf.open(member) as src, open(dest, "wb") as dst:
                    dst.write(src.read())
                extracted_count += 1

    zip_path.unlink()  # Remove the ZIP to save space
    logger.info("Extracted %d ARC training tasks to %s", extracted_count, cache_dir)


def _load_task_files(cache_dir: Path) -> list[Path]:
    """Return all JSON task files found in cache_dir, sorted by name."""
    return sorted(cache_dir.glob("*.json"))


# ---------------------------------------------------------------------------
# Evaluator class
# ---------------------------------------------------------------------------


class ARCEvaluator(Evaluator):
    """Evaluator for the ARC-AGI (Abstraction and Reasoning Corpus) benchmark.

    Downloads the full training split from GitHub on first use and caches
    individual task files locally. Tasks are formatted as few-shot demonstration
    prompts for LLMs.
    """

    def __init__(self, cache_dir: Path | str | None = None) -> None:
        """Initialise the evaluator with an optional custom cache directory.

        Parameters
        ----------
        cache_dir:
            Directory where ARC JSON files are stored. Defaults to
            ``<repo_root>/data/arc/``.
        """
        self._cache_dir = Path(cache_dir) if cache_dir else _DEFAULT_CACHE_DIR

    @property
    def name(self) -> str:
        return "arc_agi"

    def _ensure_dataset(self) -> None:
        """Download the dataset if the cache directory is empty."""
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        existing = _load_task_files(self._cache_dir)
        if not existing:
            logger.info(
                "ARC cache at %s is empty — downloading dataset...", self._cache_dir
            )
            _download_arc_dataset(self._cache_dir)
        else:
            logger.debug(
                "ARC cache at %s has %d task files", self._cache_dir, len(existing)
            )

    def load_tasks(self, n_samples: int = 50) -> list[BenchmarkTask]:
        """Load ARC training tasks from the local cache (downloading if needed).

        Each task is formatted as a few-shot prompt: training examples are
        shown as demonstrations, and the test input is presented last.

        Only tasks with exactly one test pair are included (the standard ARC
        training format).

        Parameters
        ----------
        n_samples:
            Maximum number of tasks to load. Uses a fixed seed for
            reproducibility.

        Returns
        -------
        List of BenchmarkTask where:
        - ``question`` is a formatted LLM prompt with demo pairs + test input
        - ``expected_answer`` is the JSON-serialised test output grid
        - ``context`` contains ``{"train": [...], "test_input": [...]}``
        """
        self._ensure_dataset()

        task_files = _load_task_files(self._cache_dir)
        if not task_files:
            raise RuntimeError(
                f"No ARC task files found in {self._cache_dir} after download attempt."
            )

        # Deterministic shuffle using fixed seed
        import random  # noqa: PLC0415

        rng = random.Random(_SAMPLE_SEED)
        shuffled = list(task_files)
        rng.shuffle(shuffled)

        tasks: list[BenchmarkTask] = []
        for path in shuffled:
            if len(tasks) >= n_samples:
                break

            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
            except (json.JSONDecodeError, OSError) as exc:
                logger.warning("Skipping malformed ARC task file %s: %s", path.name, exc)
                continue

            train_pairs = data.get("train", [])
            test_pairs = data.get("test", [])

            # Standard ARC tasks have exactly one test pair
            if len(test_pairs) != 1:
                logger.debug(
                    "Skipping %s: expected 1 test pair, got %d",
                    path.name,
                    len(test_pairs),
                )
                continue

            test_input: Grid = test_pairs[0]["input"]
            test_output: Grid = test_pairs[0]["output"]

            question = _format_task_for_llm(train_pairs, test_input)
            expected_answer = json.dumps(test_output)

            task_id = f"arc_{path.stem}"

            tasks.append(
                BenchmarkTask(
                    task_id=task_id,
                    question=question,
                    expected_answer=expected_answer,
                    context={
                        "train": train_pairs,
                        "test_input": test_input,
                    },
                )
            )

        logger.info("Loaded %d ARC tasks (requested %d)", len(tasks), n_samples)
        return tasks

    def check_answer(self, predicted: str, expected: str) -> bool:
        """Check whether the predicted grid exactly matches the expected grid.

        The predicted string may contain surrounding explanation text; the
        evaluator attempts to extract a valid grid from it. The expected string
        is always a JSON-serialised grid (as produced by ``load_tasks``).

        Parameters
        ----------
        predicted:
            Free-form output from the reasoning program.
        expected:
            JSON-serialised 2-D grid (list of lists of integers).

        Returns
        -------
        True only if the grids match exactly (dimensions and all cell values).
        """
        # Parse expected grid (always valid JSON from load_tasks)
        try:
            expected_grid: Grid = json.loads(expected)
        except json.JSONDecodeError:
            logger.error("ARC check_answer: could not parse expected grid: %r", expected[:80])
            return False

        # Try to extract a grid from the prediction
        predicted_grid = _extract_grid(predicted)
        if predicted_grid is None:
            logger.debug("ARC check_answer: could not extract grid from prediction")
            return False

        # Exact match comparison
        return predicted_grid == expected_grid
