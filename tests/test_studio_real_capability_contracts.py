import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

from packages.software_projects.coding.contract_guidance import generation_contract_packets
from packages.software_projects.coding.objective_audit import audit_generated_source
from packages.relational_data.contracts import InsertRequest, QueryRequest
from packages.workspace_entities.contracts import EntityList


OBJECTIVE = (
    "Employees should be able to clock in using their cameras, by scanning a QR code "
    "and clocking out by using another QR code. Attendance must persist and use canonical workspace employees."
)
PLAN = {
    "projectName": "Camera QR Attendance",
    "summary": OBJECTIVE,
    "provenance": {"originalPrompt": OBJECTIVE},
    "requirementLedger": [
        {
            "id": "R-001",
            "mandatory": True,
            "normalizedMeaning": "Employees use the browser camera to scan QR codes.",
            "acceptanceCriteria": ["The browser requests camera permission and decodes a QR code from the camera stream."],
        },
        {
            "id": "R-002",
            "mandatory": True,
            "normalizedMeaning": "One QR workflow clocks an employee in and another clocks the employee out.",
            "acceptanceCriteria": ["Decoded QR data drives distinct clock-in and clock-out operations."],
        },
        {
            "id": "R-003",
            "mandatory": True,
            "normalizedMeaning": "Attendance records are durable and reference canonical workspace employees.",
            "acceptanceCriteria": ["Attendance is stored with data.relational and employee IDs are verified through data.workspace_entities."],
        },
    ],
}


REAL_FILES = {
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
        {
            "schemaVersion": "operly.workspace-entities/v1",
            "entities": [{"kind": "employee", "semanticName": "employee"}],
        }
    ),
    "migrations/0001_attendance.json": json.dumps(
        {
            "schemaVersion": "operly.relational.migration/v1",
            "version": 1,
            "name": "attendance events",
            "operations": [
                {
                    "op": "create_table",
                    "table": "attendance_events",
                    "columns": [
                        {"name": "id", "type": "uuid", "primaryKey": True},
                        {"name": "employee_id", "type": "string", "nullable": False},
                        {"name": "event_type", "type": "string", "nullable": False},
                        {"name": "qr_nonce", "type": "string", "nullable": False},
                        {"name": "occurred_at", "type": "datetime", "nullable": False},
                    ],
                }
            ],
        }
    ),
    "backend/app.py": r'''import argparse
import datetime
import json
import os
import uuid
from pathlib import Path
from urllib.request import Request, urlopen

_GATEWAY_OVERRIDE = None


def _bindings():
    path = os.environ.get("OPERLY_BINDINGS_FILE")
    if not path:
        raise RuntimeError("OPERLY_BINDINGS_FILE is required")
    rows = json.loads(Path(path).read_text(encoding="utf-8"))
    return rows.get("bindings", rows) if isinstance(rows, dict) else rows


def _endpoint(semantic_name):
    for item in _bindings():
        if item.get("semanticName") == semantic_name:
            endpoint = str(item.get("endpoint") or "").rstrip("/")
            if endpoint:
                return endpoint
    raise RuntimeError("Missing Operly binding: " + semantic_name)


def _binding_post(semantic_name, operation, payload):
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


def canonical_employee(employee_id):
    result = _binding_post("employee", "/list", {"kind": "employee", "status": "active", "limit": 500, "offset": 0})
    rows = result.get("entities") or result.get("rows") or result.get("items") or []
    return next((row for row in rows if str(row.get("id")) == str(employee_id)), None)


def record_attendance(employee_id, event_type, qr_nonce):
    if event_type not in {"clock_in", "clock_out"}:
        raise ValueError("invalid attendance event")
    employee = canonical_employee(employee_id)
    if not employee:
        raise ValueError("employee not found")
    previous = _binding_post(
        "data",
        "/query",
        {
            "table": "attendance_events",
            "filters": [{"column": "employee_id", "op": "eq", "value": employee_id}],
            "orderBy": [{"column": "occurred_at", "direction": "desc"}],
            "limit": 1,
        },
    )
    rows = previous.get("rows") or []
    previous_type = rows[0].get("event_type") if rows else None
    if event_type == "clock_in" and previous_type == "clock_in":
        raise ValueError("already clocked in")
    if event_type == "clock_out" and previous_type != "clock_in":
        raise ValueError("not clocked in")
    record = {
        "id": str(uuid.uuid4()),
        "employee_id": employee_id,
        "event_type": event_type,
        "qr_nonce": qr_nonce,
        "occurred_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }
    _binding_post("data", "/insert", {"table": "attendance_events", "values": record})
    return record


def clock_in(employee_id, qr_nonce):
    return record_attendance(employee_id, "clock_in", qr_nonce)


def clock_out(employee_id, qr_nonce):
    return record_attendance(employee_id, "clock_out", qr_nonce)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8080)
    parser.parse_args()


if __name__ == "__main__":
    main()

# /health /clock-in /clock-out are served by the generated HTTP adapter in the full app.
''',
    "frontend/index.html": '''<!doctype html><html><body><main><h1>Camera QR attendance</h1><video id="camera-preview" autoplay playsinline></video><p id="scan-status" aria-live="polite">Ready to scan</p><button data-operly-interaction="start-camera" id="start-camera">Start camera</button></main><script src="app.js"></script></body></html>''',
    "frontend/app.js": r'''function parseAttendanceQr(rawValue) {
  const decoded = String(rawValue || '').trim();
  const match = /^operly-attendance:(clock_in|clock_out):([a-zA-Z0-9-]{3,80}):([a-zA-Z0-9-]{6,120})$/.exec(decoded);
  if (!match) throw new Error('Invalid attendance QR code');
  return {eventType: match[1], employeeId: match[2], nonce: match[3]};
}

async function createQrDetector(BarcodeDetectorClass = globalThis.BarcodeDetector) {
  if (!BarcodeDetectorClass) throw new Error('QR scanning is not supported by this browser');
  return new BarcodeDetectorClass({formats: ['qr_code']});
}

async function startCamera(mediaDevices = navigator.mediaDevices, video = document.getElementById('camera-preview')) {
  if (!mediaDevices || !mediaDevices.getUserMedia) throw new Error('Camera API unavailable');
  const stream = await mediaDevices.getUserMedia({video: {facingMode: {ideal: 'environment'}}, audio: false});
  video.srcObject = stream;
  await video.play?.();
  return stream;
}

async function submitDecodedQr(decoded, api = fetch) {
  const event = parseAttendanceQr(decoded);
  const path = event.eventType === 'clock_in' ? '/clock-in' : '/clock-out';
  const response = await api(path, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({employee_id: event.employeeId, qr_nonce: event.nonce})});
  const body = await response.json();
  if (!response.ok) throw new Error(body.error || 'Attendance operation failed');
  return body;
}

async function scanFrame(detector, video, api = fetch) {
  const codes = await detector.detect(video);
  if (!codes.length || !codes[0].rawValue) return null;
  return submitDecodedQr(codes[0].rawValue, api);
}

async function handleStartCamera() {
  const status = document.getElementById('scan-status');
  try {
    await startCamera();
    status.textContent = 'Camera active — point it at an attendance QR code';
  } catch (error) {
    status.textContent = 'Camera unavailable: ' + error.message;
    throw error;
  }
}

if (typeof document !== 'undefined') document.getElementById('start-camera').addEventListener('click', handleStartCamera);
if (typeof module !== 'undefined') module.exports = {parseAttendanceQr, createQrDetector, startCamera, submitDecodedQr, scanFrame};
''',
    "frontend/operly.interactions.json": json.dumps(
        {
            "schemaVersion": 1,
            "interactions": [
                {
                    "id": "start-camera",
                    "control": "button",
                    "event": "click",
                    "handler": "handleStartCamera",
                    "operation": "startCamera",
                    "success": "Camera stream started",
                    "rejection": "Camera permission or API failure is shown",
                    "stateChange": "camera stream becomes active",
                    "stateProbe": "startCamera",
                    "uiEvidence": "scan-status",
                    "uiProjection": "handleStartCamera",
                    "persistence": "not_applicable",
                    "reloadOperation": "startCamera",
                    "testId": "interaction_r_001",
                    "requirementIds": ["R-001"],
                }
            ],
        }
    ),
    "tests/qr.test.js": r'''const test = require('node:test');
const assert = require('node:assert/strict');
const app = require('../frontend/app.js');

test('camera permission is requested and attached to video', async () => {
  const stream = {id: 'camera-stream'};
  const mediaDevices = {getUserMedia: async options => { assert.equal(options.video.facingMode.ideal, 'environment'); return stream; }};
  const video = {srcObject: null, play: async () => {}};
  assert.equal(await app.startCamera(mediaDevices, video), stream);
  assert.equal(video.srcObject, stream);
});

test('BarcodeDetector is configured for qr_code and decoded scan drives clock-in', async () => {
  let formats = null;
  class Detector { constructor(options) { formats = options.formats; } async detect() { return [{rawValue: 'operly-attendance:clock_in:employee-9:nonce-123456'}]; } }
  const detector = await app.createQrDetector(Detector);
  const calls = [];
  const api = async (path, options) => ({ok: true, json: async () => { calls.push([path, JSON.parse(options.body)]); return {ok: true}; }});
  await app.scanFrame(detector, {}, api);
  assert.deepEqual(formats, ['qr_code']);
  assert.equal(calls[0][0], '/clock-in');
  assert.equal(calls[0][1].employee_id, 'employee-9');
});

test('clock-out QR drives distinct operation and invalid QR is rejected', async () => {
  const calls = [];
  const api = async (path, options) => ({ok: true, json: async () => { calls.push([path, JSON.parse(options.body)]); return {ok: true}; }});
  await app.submitDecodedQr('operly-attendance:clock_out:employee-9:nonce-654321', api);
  assert.equal(calls[0][0], '/clock-out');
  await assert.rejects(() => app.submitDecodedQr('not-a-qr', api), /Invalid attendance QR/);
});
''',
    "tests/test_attendance.py": r'''import json
import os
import tempfile
import unittest

from backend import app


class AttendanceTests(unittest.TestCase):
    def setUp(self):
        self.calls = []
        self.rows = []
        handle = tempfile.NamedTemporaryFile("w", delete=False)
        json.dump([
            {"semanticName": "data", "capabilityId": "data.relational", "endpoint": "http://127.0.0.1:8083"},
            {"semanticName": "employee", "capabilityId": "data.workspace_entities", "endpoint": "http://127.0.0.1:8084"},
        ], handle)
        handle.close()
        self.binding_path = handle.name
        os.environ["OPERLY_BINDINGS_FILE"] = self.binding_path

        def gateway(semantic, operation, payload):
            self.calls.append((semantic, operation, payload))
            if semantic == "employee":
                return {"entities": [{"id": "employee-9", "status": "active"}]}
            if operation == "/query":
                return {"rows": list(reversed(self.rows[-1:]))}
            if operation == "/insert":
                self.rows.append(payload["values"])
                return {"row": payload["values"]}
            raise AssertionError((semantic, operation, payload))
        app._GATEWAY_OVERRIDE = gateway

    def tearDown(self):
        app._GATEWAY_OVERRIDE = None
        os.unlink(self.binding_path)

    def test_clock_in_and_out_use_canonical_employee_and_relational_data(self):
        app.clock_in("employee-9", "nonce-123456")
        app.clock_out("employee-9", "nonce-654321")
        self.assertEqual([row["event_type"] for row in self.rows], ["clock_in", "clock_out"])
        self.assertTrue(any(call[0] == "employee" and call[1] == "/list" for call in self.calls))
        self.assertTrue(any(call[0] == "data" and call[1] == "/query" for call in self.calls))
        self.assertEqual(sum(1 for call in self.calls if call[0] == "data" and call[1] == "/insert"), 2)

    def test_invalid_state_transitions_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "not clocked in"):
            app.clock_out("employee-9", "nonce-out000")
        app.clock_in("employee-9", "nonce-in0000")
        with self.assertRaisesRegex(ValueError, "already clocked in"):
            app.clock_in("employee-9", "nonce-in0001")


if __name__ == '__main__':
    unittest.main()
''',
    "backend/__init__.py": "",
}


def source(files):
    return SimpleNamespace(source_version=1, files_json=json.dumps([
        {"path": path, "content": content, "generatedBy": "ci"} for path, content in files.items()
    ]))


def test_real_camera_qr_app_consumes_operly_services_and_passes_objective_audit():
    audit = audit_generated_source(PLAN, source(REAL_FILES))
    assert audit["verified"], audit
    assert not audit["behaviorGaps"]
    assert not audit["capabilityUsageGaps"]
    assert not audit["authorityGaps"]


def test_real_app_tests_execute_and_payloads_match_canonical_capability_contracts(tmp_path):
    for path, content in REAL_FILES.items():
        target = tmp_path / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    py = subprocess.run(
        [sys.executable, "-m", "unittest", "tests.test_attendance"],
        cwd=tmp_path,
        text=True,
        capture_output=True,
        timeout=20,
    )
    assert py.returncode == 0, py.stdout + py.stderr
    node = subprocess.run(
        ["node", "--test", "tests/qr.test.js"],
        cwd=tmp_path,
        text=True,
        capture_output=True,
        timeout=20,
    )
    assert node.returncode == 0, node.stdout + node.stderr

    QueryRequest.model_validate({
        "table": "attendance_events",
        "filters": [{"column": "employee_id", "op": "eq", "value": "employee-9"}],
        "orderBy": [{"column": "occurred_at", "direction": "desc"}],
        "limit": 1,
    })
    InsertRequest.model_validate({
        "table": "attendance_events",
        "values": {"id": "id-1", "employee_id": "employee-9", "event_type": "clock_in", "qr_nonce": "nonce-1", "occurred_at": "2026-01-01T00:00:00Z"},
    })
    EntityList.model_validate({"kind": "employee", "status": "active", "limit": 500, "offset": 0})


def test_comments_mocks_and_hardcoded_ids_do_not_fake_camera_qr_or_capability_usage():
    fake = dict(REAL_FILES)
    fake["frontend/index.html"] = "<button>Camera QR Clock In</button><button>QR Clock Out</button>"
    fake["frontend/app.js"] = "// camera qr scanner\nfetch('/clock-in',{body:JSON.stringify({employee_id:'emp1'})}); fetch('/clock-out');"
    fake["backend/app.py"] = '''import os\nattendance_events=[]\nallowed_user_ids={'emp1','emp2'}\n# mock relational and mock workspace implementations demonstrate consumption\nassert isinstance(attendance_events,list)\nOPERLY_BINDINGS_FILE=os.getenv("OPERLY_BINDINGS_FILE")\n# endpoint /query /insert /list -- comments only\n# --host --port /health /clock-in /clock-out\n'''
    audit = audit_generated_source(PLAN, source(fake))
    assert not audit["verified"]
    assert audit["behaviorGaps"]
    assert audit["capabilityUsageGaps"]
    assert audit["authorityGaps"]


def test_workspace_entity_relational_query_is_not_accepted_as_entity_consumption():
    broken = dict(REAL_FILES)
    broken["backend/app.py"] = broken["backend/app.py"].replace(
        '_binding_post("employee", "/list", {"kind": "employee", "status": "active", "limit": 500, "offset": 0})',
        '_binding_post("employee", "/query", {"id": employee_id})',
    )
    audit = audit_generated_source(PLAN, source(broken))
    assert not audit["verified"]
    assert any(gap["capabilityId"] == "data.workspace_entities" for gap in audit["capabilityUsageGaps"])


def test_migrated_relational_table_cannot_be_shadowed_by_in_memory_authority():
    broken = dict(REAL_FILES)
    broken["backend/app.py"] = "attendance_events = []\n" + broken["backend/app.py"]
    audit = audit_generated_source(PLAN, source(broken))
    assert not audit["verified"]
    assert any("attendance_events" in gap for gap in audit["authorityGaps"])


def test_generation_contracts_tell_the_agent_how_the_real_runner_injects_capabilities():
    packets = generation_contract_packets()
    bindings = packets["operly.runtime_bindings"]
    assert bindings["environmentVariable"] == "OPERLY_BINDINGS_FILE"
    assert "endpoint" in bindings["fileShape"][0]
    assert "/list" in bindings["operations"]["data.workspace_entities"]["methods"]
    assert "/query" in bindings["operations"]["data.relational"]["methods"]
    assert "getUserMedia" in " ".join(packets["browser.device_requirements"]["camera"]["requiredEvidence"])
