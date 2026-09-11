"""Every protected route must reject a missing or wrong API key, and accept
the correct one — this is the concrete check behind v0.1 Phase 1's security
requirement, not just a claim in the blueprint.
"""

import pytest

PROTECTED_PATHS = [
    "/api/v1/services",
    "/api/v1/scenarios",
    "/api/v1/scenarios/checkout-deploy-outage",
]


def test_health_requires_no_auth(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


@pytest.mark.parametrize("path", PROTECTED_PATHS)
def test_protected_route_rejects_missing_key(client, path):
    resp = client.get(path)
    assert resp.status_code == 401


@pytest.mark.parametrize("path", PROTECTED_PATHS)
def test_protected_route_rejects_wrong_key(client, path):
    resp = client.get(path, headers={"X-API-Key": "definitely-not-the-key"})
    assert resp.status_code == 401


@pytest.mark.parametrize("path", PROTECTED_PATHS)
def test_protected_route_accepts_correct_key(client, path, auth_headers):
    resp = client.get(path, headers=auth_headers)
    assert resp.status_code == 200
