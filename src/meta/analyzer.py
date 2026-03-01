"""Failure analyzer: clusters failed evaluation results by failure pattern."""

from __future__ import annotations

import logging
import random
from collections import defaultdict

from src.evaluator.base import EvalResult
from src.models.base import ModelRoster
from src.models.router import ModelRouter
from src.utils.cost_tracker import CostTracker

logger = logging.getLogger(__name__)

# All recognized failure classes
FAILURE_CLASSES = [
    "arithmetic_error",
    "misread_problem",
    "wrong_decomposition",
    "hallucinated_constraint",
    "timeout",
    "code_error",
    "formatting_error",
    "wrong_strategy",
]

# Maximum number of failures to send to the LLM for categorization
_MAX_LLM_FAILURES = 20

# Substrings in the trace that indicate timeout or code errors (detected locally)
_TIMEOUT_MARKERS = ["timeout after", "timeouterror", "timed out"]
_CODE_ERROR_MARKERS = ["error:", "traceback (most recent call last)", "exception:"]


def _detect_trace_class(trace: str) -> str | None:
    """
    Detect timeout or code_error directly from the trace string without an LLM.

    Returns the failure class string if detected, or None if the trace needs LLM
    categorization.
    """
    lower = trace.lower()
    if any(marker in lower for marker in _TIMEOUT_MARKERS):
        return "timeout"
    if any(marker in lower for marker in _CODE_ERROR_MARKERS):
        return "code_error"
    return None


def _build_categorization_prompt(failures: list[EvalResult]) -> str:
    """Build the prompt sent to the strong LLM to categorize failures."""
    classes_list = "\n".join(f"  - {cls}" for cls in FAILURE_CLASSES)
    entries = []
    for i, result in enumerate(failures, start=1):
        trace_excerpt = result.trace[:500] if result.trace else "(no trace)"
        entries.append(
            f"[Failure {i}]\n"
            f"program_id: {result.program_id}\n"
            f"task_id: {result.task_id}\n"
            f"score: {result.score:.3f}\n"
            f"trace excerpt:\n{trace_excerpt}\n"
        )
    failures_block = "\n".join(entries)

    return (
        "You are analyzing failure cases from a reasoning program evaluation system.\n"
        "For each failure listed below, assign exactly one failure class from this list:\n"
        f"{classes_list}\n\n"
        "Definitions:\n"
        "  - arithmetic_error: the program made a wrong calculation or arithmetic mistake\n"
        "  - misread_problem: the program misunderstood or misread the question\n"
        "  - wrong_decomposition: the program broke the problem into incorrect sub-problems\n"
        "  - hallucinated_constraint: the program added a constraint not present in the problem\n"
        "  - timeout: the program exceeded the time limit\n"
        "  - code_error: the program code crashed with an exception\n"
        "  - formatting_error: the answer was correct but formatted incorrectly\n"
        "  - wrong_strategy: the program used a fundamentally wrong approach\n\n"
        "For each failure, respond with a line in exactly this format:\n"
        "  Failure N: <class>\n"
        "where N matches the [Failure N] number.\n\n"
        "After all individual classifications, add a summary paragraph starting with "
        "'SUMMARY:' that describes the dominant failure patterns in plain English.\n\n"
        f"Failures to classify:\n\n{failures_block}"
    )


def _parse_categorization_response(
    response: str,
    failures: list[EvalResult],
) -> tuple[dict[str, list[EvalResult]], str]:
    """
    Parse the LLM response into a class->failures dict and a summary string.

    Expected format per line: "Failure N: <class>"
    Falls back to "wrong_strategy" for any failure that cannot be parsed.
    """
    categorized: dict[str, list[EvalResult]] = defaultdict(list)
    summary = ""

    # Extract the SUMMARY section if present
    if "SUMMARY:" in response:
        summary_start = response.index("SUMMARY:")
        summary = response[summary_start + len("SUMMARY:"):].strip()

    # Parse per-failure lines
    lines = response.splitlines()
    for line in lines:
        line = line.strip()
        if not line.lower().startswith("failure "):
            continue
        # Expected: "Failure N: <class>"
        try:
            # Strip the "Failure " prefix and split on ":"
            rest = line[len("failure "):].strip()
            idx_str, class_str = rest.split(":", 1)
            idx = int(idx_str.strip()) - 1  # convert to 0-based
            assigned_class = class_str.strip().lower().replace(" ", "_")

            if assigned_class not in FAILURE_CLASSES:
                logger.debug(
                    "LLM returned unknown class '%s'; defaulting to 'wrong_strategy'",
                    assigned_class,
                )
                assigned_class = "wrong_strategy"

            if 0 <= idx < len(failures):
                categorized[assigned_class].append(failures[idx])
            else:
                logger.debug("Failure index %d out of range (have %d)", idx, len(failures))
        except (ValueError, IndexError) as exc:
            logger.debug("Could not parse categorization line '%s': %s", line, exc)

    # Any failure not assigned gets bucketed as wrong_strategy
    assigned_indices = set()
    for class_results in categorized.values():
        for result in class_results:
            assigned_indices.add(id(result))

    for result in failures:
        if id(result) not in assigned_indices:
            logger.debug(
                "Failure %s/%s was not assigned by LLM; defaulting to 'wrong_strategy'",
                result.program_id,
                result.task_id,
            )
            categorized["wrong_strategy"].append(result)

    return dict(categorized), summary


class FailureAnalyzer:
    """Analyze evaluation failures to identify structural patterns."""

    async def analyze(
        self,
        failures: list[EvalResult],
        models: ModelRoster,
        router: ModelRouter,
        cost_tracker: CostTracker | None = None,
    ) -> tuple[dict[str, list[EvalResult]], str]:
        """
        Categorize failures into structural classes using a strong LLM.

        Failure classes:
        - "arithmetic_error": wrong calculation
        - "misread_problem": misunderstood the question
        - "wrong_decomposition": broke problem down incorrectly
        - "hallucinated_constraint": added non-existent constraint
        - "timeout": program timed out
        - "code_error": program crashed
        - "formatting_error": answer was correct but badly formatted
        - "wrong_strategy": fundamentally wrong approach

        Parameters
        ----------
        failures:
            List of EvalResult instances where correct==False.
        models:
            The full roster of available models.
        router:
            Cost-aware router used to select the strong LLM.

        Returns
        -------
        A tuple of:
          - dict mapping failure_class -> list of EvalResults in that class
          - a natural language summary string describing the failure patterns
        """
        if not failures:
            logger.info("FailureAnalyzer: no failures to analyze.")
            return {}, "No failures to analyze."

        categorized: dict[str, list[EvalResult]] = defaultdict(list)

        # --- Step 1: detect timeout / code_error directly from traces ---
        needs_llm: list[EvalResult] = []
        for result in failures:
            detected = _detect_trace_class(result.trace)
            if detected:
                categorized[detected].append(result)
            else:
                needs_llm.append(result)

        logger.info(
            "FailureAnalyzer: %d failures auto-detected (timeout/code_error), "
            "%d require LLM categorization.",
            len(failures) - len(needs_llm),
            len(needs_llm),
        )

        summary = ""

        if not needs_llm:
            # Build a simple summary from the auto-detected classes
            class_counts = {cls: len(v) for cls, v in categorized.items()}
            summary = (
                f"All {len(failures)} failures were auto-detected. "
                + ", ".join(f"{cls}: {cnt}" for cls, cnt in sorted(class_counts.items()))
            )
            return dict(categorized), summary

        # --- Step 2: sample up to _MAX_LLM_FAILURES for LLM categorization ---
        sample = needs_llm
        if len(needs_llm) > _MAX_LLM_FAILURES:
            sample = random.sample(needs_llm, _MAX_LLM_FAILURES)
            logger.info(
                "FailureAnalyzer: sampled %d of %d failures for LLM categorization.",
                _MAX_LLM_FAILURES,
                len(needs_llm),
            )

        # --- Step 3: call a strong LLM to categorize the sample ---
        prompt = _build_categorization_prompt(sample)
        try:
            config, provider = router.route("strong")
            logger.info(
                "FailureAnalyzer: calling model '%s' for failure categorization.",
                config.name,
            )
            response, cost = await provider.complete(
                prompt=prompt,
                system=(
                    "You are an expert AI evaluator. Analyze program failures "
                    "and categorize them precisely."
                ),
                temperature=0.0,
                model_name=config.name,
            )
            if cost_tracker:
                cost_tracker.add(cost, model_name=config.name)
            logger.info(
                "FailureAnalyzer: categorization call cost $%.6f.", cost
            )
        except Exception as exc:
            logger.error(
                "FailureAnalyzer: LLM call failed (%s). "
                "All remaining failures will be classified as 'wrong_strategy'.",
                exc,
            )
            for result in needs_llm:
                categorized["wrong_strategy"].append(result)
            summary = (
                f"LLM categorization failed: {exc}. "
                f"{len(needs_llm)} failures defaulted to 'wrong_strategy'."
            )
            return dict(categorized), summary

        # --- Step 4: parse response and merge into categorized dict ---
        llm_categorized, summary = _parse_categorization_response(response, sample)
        for cls, results in llm_categorized.items():
            categorized[cls].extend(results)

        # Any needs_llm entries NOT in the sample also get assigned by majority class
        # (propagate the most common LLM-found class to unsampled failures)
        unsampled = [r for r in needs_llm if r not in sample]
        if unsampled:
            if llm_categorized:
                dominant_class = max(llm_categorized, key=lambda c: len(llm_categorized[c]))
            else:
                dominant_class = "wrong_strategy"
            logger.info(
                "FailureAnalyzer: assigning %d unsampled failures to dominant class '%s'.",
                len(unsampled),
                dominant_class,
            )
            categorized[dominant_class].extend(unsampled)

        # --- Step 5: compose a final summary ---
        class_counts = {cls: len(v) for cls, v in categorized.items() if v}
        total = sum(class_counts.values())
        counts_str = ", ".join(
            f"{cls}: {cnt}" for cls, cnt in sorted(class_counts.items(), key=lambda x: -x[1])
        )
        if not summary:
            summary = f"Analyzed {total} failures. Breakdown: {counts_str}."
        else:
            summary = f"{summary}\n\nBreakdown ({total} total): {counts_str}."

        logger.info("FailureAnalyzer: complete. %s", counts_str)
        return dict(categorized), summary
