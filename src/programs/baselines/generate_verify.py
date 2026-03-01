"""Generate-verify baseline: iterative generation with cheap-model verification."""

from __future__ import annotations

from src.models.base import ModelRoster
from src.models.router import ModelRouter
from src.programs.interface import ReasoningProgram, Solution

_MAX_ATTEMPTS = 3


def _extract_answer(response: str) -> str:
    """Return text after 'ANSWER:' marker, or the last non-empty line."""
    if "ANSWER:" in response:
        return response.split("ANSWER:", 1)[1].strip()
    lines = [line.strip() for line in response.splitlines() if line.strip()]
    return lines[-1] if lines else response.strip()


def _build_trace_entry(call_num: int, model_name: str, cost: float,
                       prompt: str, response: str) -> str:
    """Format a single trace entry."""
    return (
        f"Call {call_num}: model={model_name}, cost=${cost:.6f}\n"
        f"Prompt: {prompt[:500]}\n"
        f"Response: {response[:2000]}\n"
        f"---\n"
    )


class GenerateVerifyProgram(ReasoningProgram):
    """
    Generate-verify-retry loop (up to 3 attempts).

    Each iteration:
    1. Generate a candidate answer from the cheap model.
    2. Ask the cheap model to verify whether the answer is correct.
    3. If the verifier says "NO", retry generation with the feedback.

    The last generated candidate is always returned regardless of the
    final verification result, so the program is bounded to at most
    3 generate + 3 verify = 6 LLM calls.
    """

    name = "generate_verify"
    description = (
        "Iterative generate-verify loop (up to 3 attempts) using the "
        "cheap model for both generation and verification."
    )

    async def solve(
        self,
        problem: dict,
        models: ModelRoster,
        router: ModelRouter,
    ) -> Solution:
        question = problem["question"]
        total_cost = 0.0
        trace = ""
        call_num = 0
        candidate = ""
        verification = ""

        for attempt in range(1, _MAX_ATTEMPTS + 1):
            # ----------------------------------------------------------------
            # Generate
            # ----------------------------------------------------------------
            if attempt == 1:
                gen_prompt = f"Solve this problem:\n{question}\nAnswer:"
            else:
                # Retry with verifier feedback attached
                gen_prompt = (
                    f"Previous attempt was wrong: {verification}\n"
                    f"Try again:\n{question}\nAnswer:"
                )

            call_num += 1
            config, provider = router.route("cheap")
            candidate, gen_cost = await provider.complete(
                prompt=gen_prompt,
                system="",
                temperature=0.0,
                model_name=config.name,
            )
            total_cost += gen_cost
            trace += _build_trace_entry(call_num, config.name,
                                        gen_cost, gen_prompt, candidate)

            # ----------------------------------------------------------------
            # Verify
            # ----------------------------------------------------------------
            verify_prompt = (
                "Here is a problem and a proposed answer. Check if the answer "
                "is correct. If incorrect, explain why.\n\n"
                f"Problem: {question}\n"
                f"Proposed answer: {candidate}\n\n"
                "Is this correct? Reply YES or NO, then explain."
            )

            call_num += 1
            config, provider = router.route("cheap")
            verification, verify_cost = await provider.complete(
                prompt=verify_prompt,
                system="",
                temperature=0.0,
                model_name=config.name,
            )
            total_cost += verify_cost
            trace += _build_trace_entry(call_num, config.name,
                                        verify_cost, verify_prompt, verification)

            # If verified as correct, stop early
            if "NO" not in verification.upper().split()[:3]:
                # First word(s) don't contain NO — treat as accepted
                break

        # The last generated candidate is the final answer
        answer = _extract_answer(candidate)

        return Solution(answer=answer, cost=total_cost, trace=trace)
