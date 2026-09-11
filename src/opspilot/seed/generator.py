"""Turns the ScenarioSpec definitions into either a pure payload (for the
determinism test) or actual database rows (for real seeding).

`build_seed_payload()` has no side effects and touches no database — it's the
thing tests/test_seed_determinism.py calls twice and diffs. `seed_all()` is
the only function in this module that talks to Postgres.
"""

import datetime as dt
from dataclasses import asdict

from sqlalchemy.orm import Session

from opspilot.models import (
    DependencyStatus,
    Deployment,
    LogEntry,
    MetricPoint,
    Runbook,
    Scenario,
    Service,
)
from opspilot.seed.scenarios import ALL_SCENARIOS, ScenarioSpec


def _iso(value: dt.datetime) -> str:
    return value.astimezone(dt.UTC).isoformat()


def build_seed_payload() -> list[dict]:
    """Pure function: ScenarioSpec objects -> plain, JSON-serializable dicts.

    Calling this twice must produce an identical result — that's the whole
    determinism guarantee the seed generator exists to provide.
    """
    payload = []
    for spec in ALL_SCENARIOS:
        raw = asdict(spec)
        raw["incident_started_at"] = _iso(spec.incident_started_at)
        for dep in raw["deployments"]:
            dep["deployed_at"] = _iso(dep["deployed_at"])
        for series in raw["metric_series"]:
            series["start"] = _iso(series["start"])
        for log in raw["logs"]:
            log["timestamp"] = _iso(log["timestamp"])
        for dep_status in raw["dependency_statuses"]:
            dep_status["checked_at"] = _iso(dep_status["checked_at"])
        payload.append(raw)
    return payload


def _get_or_create_service(session: Session, name: str, description: str) -> Service:
    existing = session.query(Service).filter_by(name=name).one_or_none()
    if existing is not None:
        return existing
    service = Service(name=name, description=description)
    session.add(service)
    session.flush()
    return service


def _seed_scenario(session: Session, spec: ScenarioSpec) -> Scenario:
    existing = session.query(Scenario).filter_by(key=spec.key).one_or_none()
    if existing is not None:
        return existing  # idempotent: re-running seed does not duplicate rows

    service = _get_or_create_service(session, spec.service_name, spec.service_description)

    scenario = Scenario(
        key=spec.key,
        service_id=service.id,
        title=spec.title,
        description=spec.description,
        injected_cause=spec.injected_cause,
        incident_started_at=spec.incident_started_at,
        ground_truth={
            "expected_evidence": list(spec.ground_truth.expected_evidence),
            "expected_diagnosis": spec.ground_truth.expected_diagnosis,
            "expected_action": spec.ground_truth.expected_action,
            "expected_policy_verdict": spec.ground_truth.expected_policy_verdict,
            "notes": spec.ground_truth.notes,
        },
    )
    session.add(scenario)
    session.flush()

    for dep in spec.deployments:
        session.add(
            Deployment(
                scenario_id=scenario.id,
                service_id=service.id,
                version=dep.version,
                deployed_at=dep.deployed_at,
                diff_summary=dep.diff_summary,
            )
        )

    for series in spec.metric_series:
        for i, value in enumerate(series.values):
            timestamp = series.start + dt.timedelta(minutes=series.step_minutes * i)
            session.add(
                MetricPoint(
                    scenario_id=scenario.id,
                    service_id=service.id,
                    metric_name=series.metric_name,
                    timestamp=timestamp,
                    value=value,
                )
            )

    for log in spec.logs:
        session.add(
            LogEntry(
                scenario_id=scenario.id,
                service_id=service.id,
                timestamp=log.timestamp,
                level=log.level,
                message=log.message,
            )
        )

    for dep_status in spec.dependency_statuses:
        session.add(
            DependencyStatus(
                scenario_id=scenario.id,
                dependency_name=dep_status.dependency_name,
                status=dep_status.status,
                checked_at=dep_status.checked_at,
            )
        )

    for runbook in spec.runbooks:
        exists = (
            session.query(Runbook)
            .filter_by(service_id=service.id, symptom_keyword=runbook.symptom_keyword)
            .one_or_none()
        )
        if exists is None:
            session.add(
                Runbook(
                    service_id=service.id,
                    symptom_keyword=runbook.symptom_keyword,
                    content=runbook.content,
                )
            )

    return scenario


def seed_all(session: Session) -> int:
    """Idempotent: safe to call on every app startup. Returns count of scenarios seeded."""
    seeded = 0
    for spec in ALL_SCENARIOS:
        before = session.query(Scenario).filter_by(key=spec.key).count()
        _seed_scenario(session, spec)
        if before == 0:
            seeded += 1
    session.commit()
    return seeded
