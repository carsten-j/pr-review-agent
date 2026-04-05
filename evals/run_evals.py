"""Standalone script to run all eval suites and print reports.

Usage:
    uv run python evals/run_evals.py
"""

from __future__ import annotations

import asyncio

from evals.eval_review import review_dataset, review_task
from evals.eval_triage import triage_dataset, triage_task


async def main() -> None:
    print("=" * 60)
    print("TRIAGE EVALS")
    print("=" * 60)
    triage_report = await triage_dataset.evaluate(triage_task)
    triage_report.print(include_input=True, include_output=True, include_durations=True)

    print()
    print("=" * 60)
    print("REVIEW EVALS")
    print("=" * 60)
    review_report = await review_dataset.evaluate(review_task)
    review_report.print(include_input=True, include_output=True, include_durations=True)


if __name__ == "__main__":
    asyncio.run(main())
