"""Ensemble-vote baseline: 3 parallel cheap-model calls with majority voting."""

from __future__ import annotations

import asyncio

from src.models.base import ModelRoster
from src.models.router import ModelRouter
from src.programs.interface import ReasoningProgram, Solution

_NUM_SAMPLES = 3


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


def _majority_vote(answers: list[str]) -> str:
    """
    Return the answer held by the majority (2+ out of 3).

    Comparison is case-insensitive and strips surrounding whitespace.
    If no majority exists, return the first answer.
    """
    normalised = [a.strip().lower() for a in answers]
    for candidate in normalised:
        if normalised.count(candidate) >= 2:
            # Return the original (non-lowercased) version of the first match
            idx = normalised.index(candidate)
            return answers[idx]
    # No majority — fall back to first response
    return answers[0]


class EnsembleVoteProgram(ReasoningProgram):
    """
    Ensemble-vote baseline.

    Fires 3 cheap-model calls in parallel (temperature=0.7 for diversity),
    extracts each answer, and picks the majority answer.  If no two answers
    agree, the first response is used.
    """

    name = "ensemble_vote"
    description = (
        "Three parallel cheap-model samples with temperature=0.7, "
        "resolved by majority voting."
    )

    async def _single_call(
        self,
        prompt: str,
        router: ModelRouter,
    ) -> tuple[str, float, str]:
        """Run one sampled completion.  Returns (response, cost, model_name)."""
        config, provider = router.route("cheap")
        response, cost = await provider.complete(
            prompt=prompt,
            system="",
            temperature=0.7,
            model_name=config.name,
        )
        return response, cost, config.name

    async def solve(
        self,
        problem: dict,
        models: ModelRoster,
        router: ModelRouter,
    ) -> Solution:
        question = problem["question"]
        prompt = f"Solve: {question}\nFinal answer:"

        # Launch all 3 calls concurrently
        results = await asyncio.gather(
            *[self._single_call(prompt, router) for _ in range(_NUM_SAMPLES)]
        )

        total_cost = 0.0
        trace = ""
        raw_answers: list[str] = []

        for call_num, (response, cost, model_name) in enumerate(results, start=1):
            total_cost += cost
            trace += _build_trace_entry(call_num, model_name, cost,
                                        prompt, response)
            raw_answers.append(_extract_answer(response))

        answer = _majority_vote(raw_answers)

        return Solution(answer=answer, cost=total_cost, trace=trace)
