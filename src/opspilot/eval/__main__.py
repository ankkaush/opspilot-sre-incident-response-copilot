"""Run with: python -m opspilot.eval [--scenarios key1,key2] [--label LABEL]
[--no-judge] [--compare-to LABEL]

Requires ANTHROPIC_API_KEY — this runs the real agent (and, unless
--no-judge, a real judge call) against the golden dataset. Scope which
scenarios run with --scenarios to control cost; a full 17-scenario run with
judging costs roughly the same order of magnitude as two dozen ordinary
investigations.
"""

import argparse

from opspilot.agent.loop import get_chat_fn
from opspilot.db import SessionLocal
from opspilot.eval.regression import compare_runs, format_regression_report
from opspilot.eval.runner import run_eval
from opspilot.eval.scorecard import print_scorecard
from opspilot.eval.storage import DEFAULT_RUNS_DIR, load_run, save_run


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the OpsPilot golden-dataset evaluation suite.")
    parser.add_argument("--scenarios", help="Comma-separated scenario keys (default: all).")
    parser.add_argument("--label", default="eval", help="Label for this run (also the output filename).")
    parser.add_argument(
        "--no-judge", action="store_true", help="Skip LLM-as-judge scoring (deterministic checks only)."
    )
    parser.add_argument("--compare-to", help="Label of a previously saved run to compare against.")
    args = parser.parse_args()

    scenario_keys = args.scenarios.split(",") if args.scenarios else None
    chat_fn = get_chat_fn()

    session = SessionLocal()
    try:
        run = run_eval(
            session,
            agent_chat_fn=chat_fn,
            judge_chat_fn=None if args.no_judge else chat_fn,
            scenario_keys=scenario_keys,
            run_label=args.label,
        )
    finally:
        session.close()

    print_scorecard(run)
    path = save_run(run)
    print(f"\nSaved to {path}")

    if args.compare_to:
        baseline = load_run(DEFAULT_RUNS_DIR / f"{args.compare_to}.json")
        print()
        print(format_regression_report(compare_runs(baseline, run)))


if __name__ == "__main__":
    main()
