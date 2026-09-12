"""Persists EvalRuns as JSON files — a lightweight, git-diffable record,
not a new database table. Eval output is a regenerable, offline analysis
artifact rather than something the live API serves, so it doesn't need a
migration or a model; a plain file per run (named by its label) is enough
to support the regression comparison this phase's "done when" calls for.
"""

from pathlib import Path

from opspilot.eval.runner import EvalRun

DEFAULT_RUNS_DIR = Path("eval_runs")


def save_run(run: EvalRun, *, runs_dir: Path = DEFAULT_RUNS_DIR) -> Path:
    runs_dir.mkdir(parents=True, exist_ok=True)
    path = runs_dir / f"{run.run_label}.json"
    path.write_text(run.model_dump_json(indent=2))
    return path


def load_run(path: Path) -> EvalRun:
    return EvalRun.model_validate_json(path.read_text())
