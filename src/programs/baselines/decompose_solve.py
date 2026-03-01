"""Decompose-then-solve baseline: two-stage cheap-model pipeline."""

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


class DecomposeSolveProgram(ReasoningProgram):
    """
    Two-stage decompose-then-solve pipeline.

    Stage 1 — Decompose: ask the model to break the problem into
    2–3 simpler sub-problems.

    Stage 2 — Solve: give the model the original question plus the
    sub-problems and ask it to solve everything and state a final answer.

    Both calls use the cheap tier.
    """

    name = "decompose_solve"
    description = (
        "Two-stage pipeline: decompose the problem into sub-problems, "
        "then solve them together to produce a final answer."
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

        # ----------------------------------------------------------------
        # Stage 1: Decompose into sub-problems
        # ----------------------------------------------------------------
        decompose_prompt = (
            f"Break this problem into 2-3 simpler sub-problems:\n{question}"
        )

        config, provider = router.route("cheap")
        sub_problems, cost1 = await provider.complete(
            prompt=decompose_prompt,
            system="",
            temperature=0.0,
            model_name=config.name,
        )
        total_cost += cost1
        trace += _build_trace_entry(1, config.name, cost1,
                                    decompose_prompt, sub_problems)

        # ----------------------------------------------------------------
        # Stage 2: Solve using original question + sub-problems
        # ----------------------------------------------------------------
        solve_prompt = (
            "Here is a problem and its sub-problems. Solve each sub-problem "
            "and combine into a final answer.\n\n"
            f"Original: {question}\n\n"
            f"Sub-problems:\n{sub_problems}\n\n"
            "Solve step by step. Write your final answer after 'ANSWER:'"
        )

        config, provider = router.route("cheap")
        final_response, cost2 = await provider.complete(
            prompt=solve_prompt,
            system="",
            temperature=0.0,
            model_name=config.name,
        )
        total_cost += cost2
        trace += _build_trace_entry(2, config.name, cost2,
                                    solve_prompt, final_response)

        answer = _extract_answer(final_response)

        return Solution(answer=answer, cost=total_cost, trace=trace)
