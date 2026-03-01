"""Direct baseline: single cheap LLM call with no special prompting."""

from __future__ import annotations

from src.models.base import ModelRoster
from src.models.router import ModelRouter
from src.programs.interface import ReasoningProgram, Solution


def _extract_answer(response: str) -> str:
    """Return text after 'ANSWER:' marker, or the last non-empty line."""
    if "ANSWER:" in response:
        return response.split("ANSWER:", 1)[1].strip()
    # Fallback: last non-empty line
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


class DirectProgram(ReasoningProgram):
    """
    Simplest possible baseline.

    Sends a single prompt to a cheap model and returns whatever it
    answers after the 'ANSWER:' marker.
    """

    name = "direct"
    description = (
        "Single cheap LLM call. No chain-of-thought or multi-step reasoning."
    )

    async def solve(
        self,
        problem: dict,
        models: ModelRoster,
        router: ModelRouter,
    ) -> Solution:
        question = problem["question"]
        prompt = (
            "Solve the following problem. "
            "Give your final answer after 'ANSWER:'.\n\n"
            f"{question}"
        )

        config, provider = router.route("cheap")
        response, cost = await provider.complete(
            prompt=prompt,
            system="",
            temperature=0.0,
            model_name=config.name,
        )

        answer = _extract_answer(response)
        trace = _build_trace_entry(1, config.name, cost, prompt, response)

        return Solution(answer=answer, cost=cost, trace=trace)
