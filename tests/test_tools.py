"""Unit tests for each read-only tool, including that a tool bound to one
scenario can never see another scenario's data — that boundary is enforced
in ToolContext/the query filters, not by the model choosing to behave.
"""

from opspilot.agent.tools import (
    GetDependencyStatusArgs,
    GetLogsArgs,
    GetMetricsArgs,
    GetRecentDeploymentsArgs,
    GetRunbookArgs,
    ToolContext,
    get_dependency_status,
    get_logs,
    get_metrics,
    get_recent_deployments,
    get_runbook,
)


def test_get_metrics_returns_checkout_scoped_data(db_session, checkout_scenario):
    ctx = ToolContext(session=db_session, scenario=checkout_scenario)
    result = get_metrics(ctx, GetMetricsArgs(since_minutes=60))
    assert result
    assert {r["metric_name"] for r in result} <= {"error_rate", "latency_ms"}


def test_get_metrics_filters_by_metric_name(db_session, checkout_scenario):
    ctx = ToolContext(session=db_session, scenario=checkout_scenario)
    result = get_metrics(ctx, GetMetricsArgs(metric_name="error_rate", since_minutes=60))
    assert result
    assert all(r["metric_name"] == "error_rate" for r in result)


def test_get_logs_filters_by_level(db_session, checkout_scenario):
    ctx = ToolContext(session=db_session, scenario=checkout_scenario)
    result = get_logs(ctx, GetLogsArgs(level="error", since_minutes=60, limit=20))
    assert result
    assert all(r["level"] == "error" for r in result)
    assert any("connection pool exhausted" in r["message"] for r in result)


def test_get_recent_deployments_finds_the_causal_deploy(db_session, checkout_scenario):
    ctx = ToolContext(session=db_session, scenario=checkout_scenario)
    result = get_recent_deployments(ctx, GetRecentDeploymentsArgs(since_minutes=60))
    versions = {r["version"] for r in result}
    assert "v2.8" in versions


def test_get_dependency_status_checkout_is_healthy(db_session, checkout_scenario):
    ctx = ToolContext(session=db_session, scenario=checkout_scenario)
    result = get_dependency_status(ctx, GetDependencyStatusArgs())
    assert any(r["dependency_name"] == "postgres-checkout" and r["status"] == "healthy" for r in result)


def test_get_dependency_status_payments_is_degraded(db_session, payments_scenario):
    ctx = ToolContext(session=db_session, scenario=payments_scenario)
    result = get_dependency_status(ctx, GetDependencyStatusArgs())
    assert any(r["dependency_name"] == "postgres-payments" and r["status"] == "degraded" for r in result)


def test_get_runbook_matches_on_keyword(db_session, checkout_scenario):
    ctx = ToolContext(session=db_session, scenario=checkout_scenario)
    result = get_runbook(ctx, GetRunbookArgs(symptom_keyword="connection pool"))
    assert result["found"] is True
    assert "rollback" in result["content"].lower()


def test_get_runbook_no_match_returns_not_found(db_session, checkout_scenario):
    ctx = ToolContext(session=db_session, scenario=checkout_scenario)
    result = get_runbook(ctx, GetRunbookArgs(symptom_keyword="totally unrelated symptom"))
    assert result["found"] is False
    assert result["content"] is None


def test_tools_are_scoped_and_cannot_see_other_scenarios(db_session, checkout_scenario, payments_scenario):
    checkout_ctx = ToolContext(session=db_session, scenario=checkout_scenario)
    payments_ctx = ToolContext(session=db_session, scenario=payments_scenario)

    checkout_logs = get_logs(checkout_ctx, GetLogsArgs(since_minutes=120, limit=50))
    payments_logs = get_logs(payments_ctx, GetLogsArgs(since_minutes=120, limit=50))

    checkout_messages = {r["message"] for r in checkout_logs}
    payments_messages = {r["message"] for r in payments_logs}

    assert checkout_messages.isdisjoint(payments_messages)
    assert any("pool exhausted" in m for m in checkout_messages)
    assert any("Slow query" in m for m in payments_messages)
