"""Exact bridge contract for DragonZpyder's existing Hatchet task."""
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from apps.api.workflow_handoff import router
from packages.workflow.dispatch_driver import DriverUnavailable, DriverForbidden

app = FastAPI()
app.include_router(router)


def test_remote_dispatch_requires_service_proof_and_never_accepts_browser_credentials():
    with patch("apps.api.workflow_handoff.workflow_dispatch_driver.remote_tick",
               new_callable=AsyncMock) as tick:
        tick.return_value = {"state": "dispatched", "claimed_runs": 1, "event_runs_queued": 2}
        with TestClient(app) as c:
            assert c.post("/api/internal/dragonzpyder/workflow-pulse",
                          headers={"Origin": "https://malicious.example"}).status_code == 403
            assert c.post("/api/internal/dragonzpyder/workflow-pulse",
                          headers={"Cookie": "session=attacker"}).status_code == 403
            assert tick.await_count == 0
            response = c.post("/api/internal/dragonzpyder/workflow-pulse",
                              headers={"X-DragonZpyder-Workflow-Dispatch": "synthetic-proof"})
            assert response.status_code == 200
            assert response.json() == tick.return_value
            tick.assert_awaited_once_with("synthetic-proof")


def test_disabled_driver_and_wrong_proof_fail_closed():
    with patch("apps.api.workflow_handoff.workflow_dispatch_driver.remote_tick",
               new_callable=AsyncMock) as tick:
        with TestClient(app) as c:
            tick.side_effect = DriverForbidden()
            assert c.post("/api/internal/dragonzpyder/workflow-pulse").status_code == 403
            tick.side_effect = DriverUnavailable("remote_workflow_driver_inactive")
            assert c.post("/api/internal/dragonzpyder/workflow-pulse").status_code == 503
