"""Simulated, parameterized remediation actions against the synthetic
environment. Every action is schema-validated the same way the read-only
tools are — never a free-text or shell command.

Deliberately NOT exposed to the model as callable tools during
`gather_context`. The read-only tool set stays read-only, exactly as it was
in v0.1 (see that phase's pushback #5: v0.1 is read-only "by construction,"
not by convention). The LLM proposes an action *type* as part of its
diagnosis (`SubmitDiagnosisArgs.recommended_action`, a closed enum); it
never constructs or invokes a remediation call itself. Only deterministic
code — `opspilot.agent.nodes.evaluate_policy`, after a policy verdict of
EXECUTE — ever calls one of these. That's a stronger property than "the
policy engine gates what the LLM calls": the LLM has no path to calling a
remediation tool at all.
"""

from dataclasses import dataclass

from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from opspilot.models import Scenario


@dataclass(frozen=True)
class RemediationContext:
    session: Session
    scenario: Scenario


class RollbackDeploymentArgs(BaseModel):
    target_version: str = Field(min_length=1, description="Deployment version to roll back to.")


class RestartServiceArgs(BaseModel):
    pass


class ScaleServiceArgs(BaseModel):
    replicas: int = Field(default=1, ge=1, le=50)


class ToggleFeatureFlagArgs(BaseModel):
    flag_name: str = Field(min_length=1)
    enabled: bool = False


def rollback_deployment(ctx: RemediationContext, args: RollbackDeploymentArgs) -> dict:
    return {
        "simulated": True,
        "action": "rollback_deployment",
        "service": ctx.scenario.service.name,
        "target_version": args.target_version,
        "outcome": "rollback executed against the synthetic environment",
    }


def restart_service(ctx: RemediationContext, _args: RestartServiceArgs) -> dict:
    return {
        "simulated": True,
        "action": "restart_service",
        "service": ctx.scenario.service.name,
        "outcome": "service restarted in the synthetic environment",
    }


def scale_service(ctx: RemediationContext, args: ScaleServiceArgs) -> dict:
    return {
        "simulated": True,
        "action": "scale_service",
        "service": ctx.scenario.service.name,
        "replicas": args.replicas,
        "outcome": "service scaled in the synthetic environment",
    }


def toggle_feature_flag(ctx: RemediationContext, args: ToggleFeatureFlagArgs) -> dict:
    return {
        "simulated": True,
        "action": "toggle_feature_flag",
        "service": ctx.scenario.service.name,
        "flag_name": args.flag_name,
        "enabled": args.enabled,
        "outcome": "feature flag toggled in the synthetic environment",
    }
