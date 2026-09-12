"""EvalRun persistence round-trip."""

import datetime as dt

from opspilot.eval.runner import AggregateScores, EvalRun
from opspilot.eval.storage import load_run, save_run


def _sample_run() -> EvalRun:
    return EvalRun(
        run_label="sample",
        started_at=dt.datetime.now(dt.UTC),
        scenario_keys=["checkout-deploy-outage"],
        scenarios=[],
        aggregate=AggregateScores(
            scenario_count=1,
            completion_rate=1.0,
            policy_verdict_accuracy=1.0,
            recommended_action_exact_match_rate=1.0,
            mean_tool_selection_score=1.0,
            total_unnecessary_tool_calls=0,
            total_argument_errors=0,
            mean_steps_used=3.0,
            total_cost_usd=0.01,
            total_input_tokens=500,
            total_output_tokens=100,
            mean_latency_seconds=1.5,
        ),
    )


def test_save_and_load_round_trip(tmp_path):
    run = _sample_run()

    path = save_run(run, runs_dir=tmp_path)
    loaded = load_run(path)

    assert loaded == run


def test_save_run_creates_the_runs_directory(tmp_path):
    runs_dir = tmp_path / "nested" / "eval_runs"
    save_run(_sample_run(), runs_dir=runs_dir)
    assert runs_dir.exists()


def test_save_run_names_the_file_after_the_label(tmp_path):
    run = _sample_run()
    path = save_run(run, runs_dir=tmp_path)
    assert path.name == "sample.json"
