import asyncio
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import packages.coding_harness.execution_loop as execution_loop
from packages.coding_harness.objective_audit import audit_generated_source
from packages.coding_harness.opencode_agent import CapabilityCodingAgent
from packages.coding_harness.runtime_resolution import validate_source_files
from packages.coding_harness.studio_controller import source_scoped_idempotency_key


OBJECTIVE = (
    "Build an equipment checkout application where employees scan equipment asset codes to check items out "
    "and return them, persist checkout history across reloads, prevent double checkout and invalid returns, "
    "and give managers a dashboard of current equipment status."
)
PLAN = {
    "projectName": "Equipment Checkout",
    "summary": OBJECTIVE,
    "provenance": {"originalPrompt": OBJECTIVE},
    "requirementLedger": [
        {
            "id": "R-001",
            "mandatory": True,
            "normalizedMeaning": "Employees scan equipment asset codes to checkout and return equipment.",
            "acceptanceCriteria": ["A scanned asset code drives a real checkout or return operation for a canonical employee."],
        },
        {
            "id": "R-002",
            "mandatory": True,
            "normalizedMeaning": "Checkout history is persistent and survives browser reload.",
            "acceptanceCriteria": ["Reloading reads durable checkout history instead of resetting in-memory state."],
        },
        {
            "id": "R-003",
            "mandatory": True,
            "normalizedMeaning": "Prevent double checkout and reject invalid returns.",
            "acceptanceCriteria": ["An already checked-out asset cannot be checked out again and an available asset cannot be returned."],
        },
        {
            "id": "R-004",
            "mandatory": True,
            "normalizedMeaning": "Managers have a dashboard showing current equipment status and history.",
            "acceptanceCriteria": ["The dashboard reloads current persistent records."],
        },
    ],
}


APP_FILES = {
    "operly.solution.json": json.dumps(
        {
            "schemaVersion": "operly.solution/v1",
            "runtime": "operly-fullstack-v1",
            "runtimeVersion": 1,
            "execution": {"frontend": "static", "backend": "python-cli", "worker": "none", "healthPath": "/health"},
            "bindings": [
                {"semanticName": "data", "capabilityId": "data.relational"},
                {"semanticName": "employee", "capabilityId": "data.workspace_entities"},
            ],
        }
    ),
    "operly.entities.json": json.dumps(
        {"schemaVersion": "operly.workspace-entities/v1", "entities": [{"kind": "employee", "semanticName": "employee"}]}
    ),
    "migrations/0001_checkout.json": json.dumps(
        {
            "version": 1,
            "name": "equipment checkout records",
            "schemaVersion": "operly.relational.migration/v1",
            "operations": [
                {
                    "op": "create_table",
                    "table": "equipment_checkouts",
                    "columns": [
                        {"name": "id", "type": "uuid", "primaryKey": True},
                        {"name": "asset_code", "type": "string", "nullable": False},
                        {"name": "employee_id", "type": "string", "nullable": False},
                        {"name": "status", "type": "string", "nullable": False},
                        {"name": "occurred_at", "type": "datetime", "nullable": False},
                    ],
                },
                {"op": "create_index", "table": "equipment_checkouts", "columns": ["asset_code"], "name": "idx_equipment_asset"},
            ],
        }
    ),
    "backend/__init__.py": "",
    "backend/build.py": "def build():\n    return {'ok': True}\n",
    "backend/app.py": r'''import argparse
import datetime
import json
import os
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.request import Request, urlopen

_GATEWAY_OVERRIDE = None


def _bindings():
    path = os.environ.get("OPERLY_BINDINGS_FILE")
    if not path:
        raise RuntimeError("OPERLY_BINDINGS_FILE is required")
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _endpoint(semantic_name):
    rows = _bindings().get("bindings", _bindings())
    if isinstance(rows, dict):
        item = rows.get(semantic_name) or {}
        return str(item.get("endpoint") or item.get("url") or "").rstrip("/")
    for item in rows:
        if item.get("semanticName") == semantic_name:
            return str(item.get("endpoint") or item.get("url") or "").rstrip("/")
    raise RuntimeError(f"Missing binding: {semantic_name}")


def _binding_call(semantic_name, operation, payload):
    if _GATEWAY_OVERRIDE is not None:
        return _GATEWAY_OVERRIDE(semantic_name, operation, payload)
    request = Request(
        _endpoint(semantic_name) + operation,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urlopen(request, timeout=10) as response:
        return json.loads(response.read().decode("utf-8"))


def _active_checkout(asset_code):
    result = _binding_call("data", "/query", {"table": "equipment_checkouts", "filters": {"asset_code": asset_code, "status": "checked_out"}})
    return (result.get("rows") or [None])[0]


def checkout_equipment(employee_id, asset_code):
    employee = _binding_call("employee", "/query", {"id": employee_id})
    if not employee or employee.get("found") is False:
        raise ValueError("employee not found")
    if _active_checkout(asset_code):
        raise ValueError("equipment already checked out")
    record = {
        "id": str(uuid.uuid4()),
        "asset_code": asset_code,
        "employee_id": employee_id,
        "status": "checked_out",
        "occurred_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }
    _binding_call("data", "/insert", {"table": "equipment_checkouts", "values": record})
    return record


def return_equipment(employee_id, asset_code):
    active = _active_checkout(asset_code)
    if not active:
        raise ValueError("equipment is not checked out")
    record = {
        "id": str(uuid.uuid4()),
        "asset_code": asset_code,
        "employee_id": employee_id,
        "status": "returned",
        "occurred_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }
    _binding_call("data", "/update", {"table": "equipment_checkouts", "filters": {"id": active["id"]}, "values": {"status": "returned"}})
    _binding_call("data", "/insert", {"table": "equipment_checkouts", "values": record})
    return record


def checkout_history():
    return _binding_call("data", "/query", {"table": "equipment_checkouts", "orderBy": [{"field": "occurred_at", "direction": "desc"}]})


class Handler(BaseHTTPRequestHandler):
    def _json(self, status, payload):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/health":
            return self._json(200, {"status": "ok"})
        if self.path == "/api/history":
            return self._json(200, checkout_history())
        if self.path == "/":
            body = Path("frontend/index.html").read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self._json(404, {"error": "not found"})

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        payload = json.loads(self.rfile.read(length) or b"{}")
        try:
            if self.path == "/api/checkout":
                return self._json(200, checkout_equipment(payload["employee_id"], payload["asset_code"]))
            if self.path == "/api/return":
                return self._json(200, return_equipment(payload["employee_id"], payload["asset_code"]))
            self._json(404, {"error": "not found"})
        except (KeyError, ValueError) as error:
            self._json(409, {"error": str(error)})


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8080)
    args = parser.parse_args()
    ThreadingHTTPServer((args.host, args.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
''',
    "frontend/index.html": '''<!doctype html><html><head><meta name="viewport" content="width=device-width,initial-scale=1"><title>Equipment Checkout</title></head><body><main><h1>Equipment Checkout</h1><label>Employee <input id="employee-id" value="emp-1"></label><label>Scan asset code <input id="asset-code" data-operly-interaction="asset-code-input" autocomplete="off"></label><button id="checkout" data-operly-interaction="checkout-button">Check out</button><button id="return" data-operly-interaction="return-button">Return</button><section><h2>Manager dashboard</h2><div id="dashboard" aria-live="polite"></div></section></main><script src="app.js"></script></body></html>''',
    "frontend/app.js": r'''function scanAssetCode(raw) {
  const value = String(raw || '').trim().toUpperCase();
  if (!/^EQ-[A-Z0-9-]{2,32}$/.test(value)) throw new Error('Invalid equipment asset code');
  return value;
}

async function operation(path, employeeId, rawCode, api = fetch) {
  const asset_code = scanAssetCode(rawCode);
  const response = await api(path, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({employee_id: employeeId, asset_code})});
  const body = await response.json();
  if (!response.ok) throw new Error(body.error || 'Equipment operation failed');
  return body;
}

const checkoutEquipment = (employeeId, rawCode, api) => operation('/api/checkout', employeeId, rawCode, api);
const returnEquipment = (employeeId, rawCode, api) => operation('/api/return', employeeId, rawCode, api);

async function loadHistory(api = fetch) {
  const response = await api('/api/history');
  if (!response.ok) throw new Error('Unable to reload persistent checkout history');
  const body = await response.json();
  return body.rows || [];
}

function renderDashboard(records, root) {
  const node = root || document.getElementById('dashboard');
  node.textContent = records.length ? records.map(row => `${row.asset_code}: ${row.status}`).join(' | ') : 'No equipment activity yet';
  return node.textContent;
}

async function reloadDashboard(api = fetch, root) {
  const records = await loadHistory(api);
  renderDashboard(records, root);
  return records;
}

async function handleCheckout() {
  await checkoutEquipment(document.getElementById('employee-id').value, document.getElementById('asset-code').value);
  return reloadDashboard();
}
async function handleReturn() {
  await returnEquipment(document.getElementById('employee-id').value, document.getElementById('asset-code').value);
  return reloadDashboard();
}
function handleAssetScan() { return scanAssetCode(document.getElementById('asset-code').value); }

if (typeof document !== 'undefined') {
  document.getElementById('checkout').addEventListener('click', handleCheckout);
  document.getElementById('return').addEventListener('click', handleReturn);
  document.getElementById('asset-code').addEventListener('change', handleAssetScan);
  reloadDashboard().catch(error => { document.getElementById('dashboard').textContent = error.message; });
}
if (typeof module !== 'undefined') module.exports = {scanAssetCode, checkoutEquipment, returnEquipment, loadHistory, renderDashboard, reloadDashboard};
''',
    "frontend/operly.interactions.json": json.dumps(
        {
            "schemaVersion": 1,
            "interactions": [
                {"id": "asset-code-input", "control": "input", "event": "change", "handler": "handleAssetScan", "operation": "scanAssetCode", "success": "Asset scanned", "rejection": "Invalid asset code", "stateChange": "validated asset code", "stateProbe": "scanAssetCode", "uiEvidence": "asset-code", "uiProjection": "renderDashboard", "persistence": "not_applicable", "reloadOperation": "reloadDashboard", "testId": "interaction_r_001", "requirementIds": ["R-001"]},
                {"id": "checkout-button", "control": "button", "event": "click", "handler": "handleCheckout", "operation": "checkoutEquipment", "success": "Equipment checked out", "rejection": "Checkout rejected", "stateChange": "persistent checkout record", "stateProbe": "loadHistory", "uiEvidence": "dashboard", "uiProjection": "renderDashboard", "persistence": "reload_preserved", "reloadOperation": "reloadDashboard", "testId": "interaction_r_002", "requirementIds": ["R-001", "R-002", "R-003"]},
                {"id": "return-button", "control": "button", "event": "click", "handler": "handleReturn", "operation": "returnEquipment", "success": "Equipment returned", "rejection": "Return rejected", "stateChange": "persistent return record", "stateProbe": "loadHistory", "uiEvidence": "dashboard", "uiProjection": "renderDashboard", "persistence": "reload_preserved", "reloadOperation": "reloadDashboard", "testId": "interaction_r_003", "requirementIds": ["R-001", "R-002", "R-003", "R-004"]},
            ],
        }
    ),
    "tests/interaction.test.js": r'''const test = require('node:test');
const assert = require('node:assert/strict');
const app = require('../frontend/app.js');

test('scan drives checkout and return operations', async () => {
  const calls = [];
  const api = async (path, options) => ({ok: true, json: async () => { calls.push([path, options && JSON.parse(options.body)]); return path === '/api/history' ? {rows: [{asset_code: 'EQ-42', status: 'checked_out'}]} : {status: 'ok'}; }});
  await app.checkoutEquipment('emp-9', 'eq-42', api);
  await app.returnEquipment('emp-9', 'EQ-42', api);
  assert.equal(calls[0][0], '/api/checkout');
  assert.equal(calls[0][1].asset_code, 'EQ-42');
  assert.equal(calls[1][0], '/api/return');
});

test('invalid scanned asset is rejected before network', async () => {
  let called = false;
  await assert.rejects(() => app.checkoutEquipment('emp-9', 'bad', async () => { called = true; }), /Invalid equipment asset code/);
  assert.equal(called, false);
});

test('dashboard reload reads persistent history and projects status', async () => {
  const api = async () => ({ok: true, json: async () => ({rows: [{asset_code: 'EQ-42', status: 'checked_out'}]})});
  const root = {textContent: ''};
  const rows = await app.reloadDashboard(api, root);
  assert.equal(rows.length, 1);
  assert.match(root.textContent, /EQ-42: checked_out/);
});
''',
    "tests/test_backend.py": r'''import unittest
from backend import app


class Gateway:
    def __init__(self):
        self.active = {}
        self.history = []

    def __call__(self, semantic, operation, payload):
        if semantic == 'employee':
            return {'found': payload.get('id') == 'emp-9'}
        if operation == '/query':
            filters = payload.get('filters') or {}
            if filters.get('status') == 'checked_out':
                row = self.active.get(filters.get('asset_code'))
                return {'rows': [row] if row else []}
            return {'rows': list(self.history)}
        if operation == '/insert':
            row = dict(payload['values'])
            self.history.append(row)
            if row['status'] == 'checked_out': self.active[row['asset_code']] = row
            return {'row': row}
        if operation == '/update':
            for code, row in list(self.active.items()):
                if row['id'] == payload['filters']['id']:
                    self.active.pop(code)
            return {'updated': 1}
        raise AssertionError((semantic, operation, payload))


class EquipmentDomainTests(unittest.TestCase):
    def setUp(self):
        self.gateway = Gateway()
        app._GATEWAY_OVERRIDE = self.gateway

    def tearDown(self):
        app._GATEWAY_OVERRIDE = None

    def test_persistent_checkout_return_invariants(self):
        app.checkout_equipment('emp-9', 'EQ-42')
        with self.assertRaisesRegex(ValueError, 'already checked out'):
            app.checkout_equipment('emp-9', 'EQ-42')
        app.return_equipment('emp-9', 'EQ-42')
        with self.assertRaisesRegex(ValueError, 'not checked out'):
            app.return_equipment('emp-9', 'EQ-42')
        self.assertEqual([row['status'] for row in self.gateway.history], ['checked_out', 'returned'])


if __name__ == '__main__':
    unittest.main()
''',
}


class ScriptedCodingClient:
    """Deterministic CI stand-in for any coding model; real workspace tools still run."""

    def __init__(self):
        self.called = False

    async def chat(self, messages, tools):
        assert not self.called
        self.called = True
        calls = []
        for index, (path, content) in enumerate(APP_FILES.items(), 1):
            calls.append(
                {
                    "id": f"write-{index}",
                    "type": "function",
                    "function": {"name": "write", "arguments": json.dumps({"path": path, "content": content})},
                }
            )
        calls.append(
            {
                "id": "finish",
                "type": "function",
                "function": {
                    "name": "finish",
                    "arguments": json.dumps(
                        {
                            "summary": "Built a persistent employee equipment scan checkout/return application with manager dashboard.",
                            "verification": ["Run Python checkout invariants", "Run Node scan/reload interaction tests"],
                        }
                    ),
                },
            }
        )
        return {"role": "assistant", "content": "", "tool_calls": calls}


def _source_record(result):
    return SimpleNamespace(
        id="source-1",
        source_version=1,
        bundle_digest="sha256:" + "a" * 64,
        files_json=json.dumps(
            [{"path": item.path, "content": item.content.decode("utf-8"), "generatedBy": item.generated_by} for item in result.files]
        ),
    )


def test_arbitrary_sophisticated_app_is_built_through_real_workspace_tools_and_runs(tmp_path):
    agent = CapabilityCodingAgent(client=ScriptedCodingClient(), max_steps=4)
    result = asyncio.run(agent.build(json.dumps(PLAN)))

    paths = {item.path for item in result.files}
    assert {"backend/app.py", "frontend/app.js", "frontend/index.html", "migrations/0001_checkout.json", "tests/test_backend.py", "tests/interaction.test.js"}.issubset(paths)
    assert validate_source_files(result.files) == "operly-fullstack-v1"

    audit = audit_generated_source(PLAN, _source_record(result))
    assert audit["verified"], audit
    assert not audit["capabilityUsageGaps"]
    assert not audit["runtimeContractGaps"]

    for item in result.files:
        destination = tmp_path / item.path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(item.content)

    python_run = subprocess.run(
        [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-p", "test_backend.py"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert python_run.returncode == 0, python_run.stdout + python_run.stderr

    node_run = subprocess.run(
        ["node", "--test", "tests/interaction.test.js"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert node_run.returncode == 0, node_run.stdout + node_run.stderr


def test_objective_audit_rejects_runner_green_toy_that_drops_camera_qr_and_bindings():
    plan = {
        "summary": "Employees scan QR codes with their cameras to clock in and clock out with persistent attendance.",
        "requirementLedger": [
            {
                "id": "R-001",
                "mandatory": True,
                "normalizedMeaning": "Employees use a camera to scan QR codes for clock in and clock out with persistent attendance.",
            }
        ],
    }
    toy = SimpleNamespace(
        source_version=16,
        files_json=json.dumps(
            [
                {
                    "path": "operly.solution.json",
                    "content": json.dumps(
                        {
                            "runtime": "operly-fullstack-v1",
                            "execution": {"healthPath": "/health"},
                            "bindings": [
                                {"semanticName": "data", "capabilityId": "data.relational"},
                                {"semanticName": "employee", "capabilityId": "data.workspace_entities"},
                            ],
                        }
                    ),
                },
                {"path": "backend/app.py", "content": "attendance=[]\ndef health(): return 'OK'\napp.run(host='0.0.0.0', port=5000)"},
                {"path": "frontend/app.js", "content": "fetch('/clock-in',{body:JSON.stringify({employee_id:'emp1'})})"},
                {"path": "frontend/index.html", "content": "<button>Clock In</button><button>Clock Out</button>"},
                {"path": "tests/test_attendance.py", "content": "def test_clock_in(): assert True"},
            ]
        ),
    )
    audit = audit_generated_source(plan, toy)
    assert not audit["verified"]
    assert audit["unmetRequirements"]
    assert audit["capabilityUsageGaps"]
    assert audit["runtimeContractGaps"]


def test_source_scoped_runner_key_survives_restart_without_rebinding_new_source():
    first = SimpleNamespace(source_version=7, bundle_digest="sha256:" + "1" * 64, id="s1")
    same = SimpleNamespace(source_version=7, bundle_digest="sha256:" + "1" * 64, id="s1")
    repaired = SimpleNamespace(source_version=8, bundle_digest="sha256:" + "2" * 64, id="s2")
    base = "solution:abc:generated-build:3"
    assert source_scoped_idempotency_key(base, first) == source_scoped_idempotency_key(base, same)
    assert source_scoped_idempotency_key(base, first) != source_scoped_idempotency_key(base, repaired)


def test_solution_keys_route_through_shared_durable_studio_controller(monkeypatch):
    import packages.coding_harness.studio_controller as studio

    expected = (SimpleNamespace(state="preview_ready"), SimpleNamespace(source_version=1), [])
    called = {}

    async def fake_controller(*args, **kwargs):
        called["metadata"] = kwargs["metadata"]
        return expected

    monkeypatch.setattr(studio, "run_studio_generation", fake_controller)
    actual = asyncio.run(
        execution_loop.build_with_repair(
            SimpleNamespace(),
            "tenant",
            "user",
            SimpleNamespace(id="plan-1", approved_version=1),
            PLAN,
            "solution:solution-123:generated-build:9",
        )
    )
    assert actual == expected
    assert called["metadata"]["runtime_run_id"] == "solution:solution-123:attempt:9"
    assert called["metadata"]["surface"] == "solution_generation"
