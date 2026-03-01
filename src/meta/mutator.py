"""Program mutator: generates new reasoning programs by mutating existing ones."""

from __future__ import annotations

import logging
import random
import re
from pathlib import Path

from src.models.base import ModelRoster
from src.models.router import ModelRouter
from src.programs.interface import ReasoningProgram
from src.utils.cost_tracker import CostTracker
from src.utils.sandbox import GENERATED_DIR, validate_program

logger = logging.getLogger(__name__)

# The two available mutation strategies
_STRATEGY_PROMPT_MUTATION = "prompt_mutation"
_STRATEGY_INJECTION = "strategy_injection"
_MUTATION_STRATEGIES = [_STRATEGY_PROMPT_MUTATION, _STRATEGY_INJECTION]

# System prompt for the code-generation LLM
_SYSTEM_PROMPT = (
    "You are an expert Python programmer specializing in LLM-based reasoning systems. "
    "You write clean, correct, standalone Python modules. "
    "Your output must be a complete Python module — nothing else."
)

# Template for prompt-mutation instruction
_PROMPT_MUTATION_INSTRUCTION = (
    "Your task is to improve the reasoning program below by rewriting its prompts "
    "to address the reported failure patterns.\n\n"
    "RULES:\n"
    "1. Keep the same overall class structure and solve() flow.\n"
    "2. Only change the prompt strings — make them more effective, clearer, or "
    "   more explicit about common mistakes.\n"
    "3. The output must be a complete, standalone Python module.\n"
    "4. The module must import from src.programs.interface and src.models.base / src.models.router.\n"
    "5. Define exactly ONE class that subclasses ReasoningProgram.\n"
    "6. That class must have: name (str), description (str), and "
    "   async def solve(self, problem, models, router) -> Solution.\n"
    "7. Output ONLY the Python code inside a ```python ... ``` block.\n"
)

# Template for strategy-injection instruction (takes {top_failure} as format argument)
_STRATEGY_INJECTION_INSTRUCTION = (
    "Your task is to improve the reasoning program below by adding new reasoning "
    "steps to address the top failure pattern: '{top_failure}'.\n\n"
    "Examples of what to add:\n"
    "  - arithmetic_error → add an explicit arithmetic verification step\n"
    "  - formatting_error → add an answer extraction / normalization step\n"
    "  - wrong_strategy   → add a problem-type detection step before solving\n"
    "  - misread_problem  → add a problem restatement step before solving\n"
    "  - wrong_decomposition → add a sub-problem validation step\n\n"
    "RULES:\n"
    "1. The output must be a complete, standalone Python module.\n"
    "2. The module must import from src.programs.interface and src.models.base / src.models.router.\n"
    "3. Define exactly ONE class that subclasses ReasoningProgram.\n"
    "4. That class must have: name (str), description (str), and "
    "   async def solve(self, problem, models, router) -> Solution.\n"
    "5. Output ONLY the Python code inside a ```python ... ``` block.\n"
)


def _extract_code_block(response: str) -> str:
    """
    Extract the first ```python ... ``` code block from the LLM response.

    Falls back to the entire response text if no fenced block is found.
    """
    pattern = r"```python\s*\n(.*?)```"
    match = re.search(pattern, response, re.DOTALL)
    if match:
        return match.group(1).strip()

    # Also try generic ``` ``` without the language tag
    pattern_generic = r"```\s*\n(.*?)```"
    match_generic = re.search(pattern_generic, response, re.DOTALL)
    if match_generic:
        return match_generic.group(1).strip()

    logger.debug("No fenced code block found; treating full response as code.")
    return response.strip()


def _build_mutation_prompt(
    parent_source: str,
    failure_summary: str,
    strategy: str,
    top_failure: str,
) -> str:
    """Compose the full mutation prompt for the LLM."""
    if strategy == _STRATEGY_PROMPT_MUTATION:
        instruction = _PROMPT_MUTATION_INSTRUCTION
    else:
        instruction = _STRATEGY_INJECTION_INSTRUCTION.format(top_failure=top_failure)

    return (
        f"{instruction}\n\n"
        f"FAILURE SUMMARY:\n{failure_summary}\n\n"
        f"PARENT PROGRAM SOURCE:\n```python\n{parent_source}\n```\n\n"
        "Now produce the improved program:"
    )


def _candidate_path(parent_name: str, n: int) -> Path:
    """Return the output path for the n-th generated candidate."""
    GENERATED_DIR.mkdir(parents=True, exist_ok=True)
    filename = f"{parent_name}_mut_{n}.py"
    return GENERATED_DIR / filename


def _pick_top_failure(failure_summary: str) -> str:
    """
    Extract the most prominent failure class name from the summary string.

    Scans for known class names ordered by their first appearance in the summary.
    Falls back to "wrong_strategy" if none are found.
    """
    from src.meta.analyzer import FAILURE_CLASSES

    summary_lower = failure_summary.lower()
    for cls in FAILURE_CLASSES:
        if cls in summary_lower:
            return cls
    return "wrong_strategy"


class ProgramMutator:
    """Generate new reasoning programs via LLM-driven mutation."""

    async def mutate(
        self,
        parent_program: ReasoningProgram,
        parent_source: str,
        failure_summary: str,
        models: ModelRoster,
        router: ModelRouter,
        n_candidates: int = 3,
        cost_tracker: CostTracker | None = None,
    ) -> list[Path]:
        """
        Generate n_candidates new program files by mutating the parent program.

        Two mutation strategies are used (chosen randomly per candidate):
        1. Prompt mutation: rewrite the prompts in the program based on failures.
        2. Strategy injection: add new reasoning steps to address failure patterns.

        Parameters
        ----------
        parent_program:
            The ReasoningProgram instance being mutated.
        parent_source:
            Full source code of the parent program as a string.
        failure_summary:
            Natural language summary from FailureAnalyzer.analyze().
        models:
            The full roster of available models.
        router:
            Cost-aware router used to select the strong LLM.
        n_candidates:
            Number of candidate programs to attempt to generate.

        Returns
        -------
        List of Paths to valid generated program files (may be shorter than
        n_candidates if some candidates fail validation).
        """
        parent_name = getattr(parent_program, "name", "program")
        # Sanitize the parent name to be a valid filename component
        safe_parent_name = re.sub(r"[^a-zA-Z0-9_]", "_", parent_name)

        top_failure = _pick_top_failure(failure_summary)
        logger.info(
            "ProgramMutator: mutating '%s', top_failure='%s', n_candidates=%d.",
            parent_name,
            top_failure,
            n_candidates,
        )

        valid_paths: list[Path] = []
        candidate_index = 0

        for i in range(n_candidates):
            strategy = random.choice(_MUTATION_STRATEGIES)
            logger.info(
                "ProgramMutator: candidate %d/%d using strategy '%s'.",
                i + 1,
                n_candidates,
                strategy,
            )

            prompt = _build_mutation_prompt(
                parent_source=parent_source,
                failure_summary=failure_summary,
                strategy=strategy,
                top_failure=top_failure,
            )

            # Call the strong LLM to generate the mutated program
            try:
                config, provider = router.route("strong")
                logger.info(
                    "ProgramMutator: calling model '%s' for candidate %d.",
                    config.name,
                    i + 1,
                )
                response, cost = await provider.complete(
                    prompt=prompt,
                    system=_SYSTEM_PROMPT,
                    temperature=0.7,
                    model_name=config.name,
                )
                if cost_tracker:
                    cost_tracker.add(cost, model_name=config.name)
                logger.info(
                    "ProgramMutator: generation call cost $%.6f.", cost
                )
            except Exception as exc:
                logger.error(
                    "ProgramMutator: LLM call failed for candidate %d: %s", i + 1, exc
                )
                continue

            # Extract the Python code from the response
            code = _extract_code_block(response)
            if not code:
                logger.warning(
                    "ProgramMutator: no code extracted from response for candidate %d.",
                    i + 1,
                )
                continue

            # Find a unique output path (avoid clobbering existing files)
            candidate_index += 1
            out_path = _candidate_path(safe_parent_name, candidate_index)
            # If the path already exists, increment until we find a free slot
            while out_path.exists():
                candidate_index += 1
                out_path = _candidate_path(safe_parent_name, candidate_index)

            # Write candidate to disk
            try:
                out_path.write_text(code, encoding="utf-8")
                logger.info("ProgramMutator: wrote candidate to %s.", out_path)
            except OSError as exc:
                logger.error(
                    "ProgramMutator: failed to write candidate %d to %s: %s",
                    i + 1,
                    out_path,
                    exc,
                )
                continue

            # Validate the written program
            valid, message = validate_program(out_path)
            if valid:
                logger.info(
                    "ProgramMutator: candidate %d passed validation: %s",
                    i + 1,
                    message,
                )
                valid_paths.append(out_path)
            else:
                logger.warning(
                    "ProgramMutator: candidate %d failed validation (%s). Discarding.",
                    i + 1,
                    message,
                )
                # Remove the invalid file to keep the generated directory clean
                try:
                    out_path.unlink()
                except OSError:
                    pass

        logger.info(
            "ProgramMutator: %d of %d candidates passed validation.",
            len(valid_paths),
            n_candidates,
        )
        return valid_paths
