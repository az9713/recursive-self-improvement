"""Chain-of-thought baseline: single cheap LLM call with CoT system prompt."""

from __future__ import annotations

from src.models.base import ModelRoster
from src.models.router import ModelRouter
from src.programs.interface import ReasoningProgram, Solution


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


class ChainOfThoughtProgram(ReasoningProgram):
    """
    Chain-of-thought baseline.

    Uses a system prompt that instructs the model to reason step by step
    before stating its final answer.  Still a single cheap LLM call.
    """

    name = "chain_of_thought"
    description = (
        "Single cheap LLM call with a chain-of-thought system prompt. "
        "Encourages step-by-step reasoning before the final answer."
    )

    _SYSTEM = (
        "Think step by step. After your reasoning, write your final answer "
        "on a new line starting with 'ANSWER:'"
    )

    async def solve(
        self,
        problem: dict,
        models: ModelRoster,
        router: ModelRouter,
    ) -> Solution:
        question = problem["question"]

        config, provider = router.route("cheap")
        response, cost = await provider.complete(
            prompt=question,
            system=self._SYSTEM,
            temperature=0.0,
            model_name=config.name,
        )

        answer = _extract_answer(response)
        trace = _build_trace_entry(1, config.name, cost, question, response)

        return Solution(answer=answer, cost=cost, trace=trace)
