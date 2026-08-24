import crypto from "node:crypto";
import {
  hmacHex,
  safeEqual,
  sha256Hex,
  stableJson,
} from "./core.mjs";

const PORT = Number(process.env.PORT || 3000);
const TOKEN = String(process.env.OPERLY_RUNNER_TOKEN || "");

function fixture() {
  const frontend = `<!doctype html><html><head><meta charset="utf-8"><title>Operly Sandbox Smoke</title></head><body><main id="app">OPERLY_FULLSTACK_SMOKE_OK</main><button id="camera">Enable camera</button><script>document.getElementById('camera').onclick=()=>navigator.mediaDevices?.getUserMedia({video:true});</script></body></html>`;
  const backend = `import argparse\nfrom http.server import BaseHTTPRequestHandler, ThreadingHTTPServer\nfrom pathlib import Path\n\nclass Handler(BaseHTTPRequestHandler):\n    def do_GET(self):\n        if self.path == '/health':\n            body=b'{"status":"ok"}'\n            self.send_response(200); self.send_header('Content-Type','application/json')\n        elif self.path == '/':\n            body=Path('frontend/index.html').read_bytes()\n            self.send_response(200); self.send_header('Content-Type','text/html; charset=utf-8')\n        else:\n            body=b'not found'; self.send_response(404)\n        self.send_header('Content-Length',str(len(body))); self.end_headers(); self.wfile.write(body)\n    def log_message(self, *_args): pass\n\nif __name__ == '__main__':\n    p=argparse.ArgumentParser(); p.add_argument('--host',default='127.0.0.1'); p.add_argument('--port',type=int,default=8080); a=p.parse_args()\n    ThreadingHTTPServer((a.host,a.port),Handler).serve_forever()\n`;
  const test = `import unittest\nclass Smoke(unittest.TestCase):\n    def test_contract(self): self.assertEqual('clock-in'.replace('-', ' '), 'clock in')\nif __name__ == '__main__': unittest.main()\n`;
  const solutionManifest = {
    schemaVersion: "operly.solution/v1",
    runtime: "operly-fullstack-v1",
    runtimeVersion: 1,
    layout: { frontend: "frontend", backend: "backend", workers: "workers", tests: "tests", migrations: "migrations" },
    execution: { frontend: "static", backend: "python-cli", worker: "none", healthPath: "/health" },
    dependencies: [],
    bindings: [],
  };
  const files = [
    { path: "backend/app.py", content: backend, generatedBy: "production_smoke" },
    { path: "frontend/index.html", content: frontend, generatedBy: "production_smoke" },
    { path: "tests/test_app.py", content: test, generatedBy: "production_smoke" },
    { path: "operly.solution.json", content: JSON.stringify(solutionManifest), generatedBy: "production_smoke" },
  ];
  const rows = files.map((item) => ({
    path: item.path,
    bytes: Buffer.byteLength(item.content),
    digest: `sha256:${sha256Hex(Buffer.from(item.content))}`,
    generatedBy: item.generatedBy,
  })).sort((a, b) => a.path.localeCompare(b.path));
  const manifest = {
    schemaVersion: 1,
    workspaceId: "production-smoke-workspace",
    applicationId: "production-smoke-clock-in",
    planId: "production-smoke-plan",
    planVersion: 1,
    sourceVersion: 1,
    promptDigest: "camera-qr-clock-in-production-smoke",
    files: rows,
    totalBytes: rows.reduce((sum, row) => sum + row.bytes, 0),
  };
  const digest = `sha256:${sha256Hex(Buffer.from(stableJson(manifest)))}`;
  const submission = {
    workspaceId: manifest.workspaceId,
    applicationId: manifest.applicationId,
    planVersion: 1,
    sourceVersion: 1,
    stackId: "operly-fullstack-v1",
    stackVersion: 1,
    sourceBundleDigest: digest,
    dependencies: [],
    operations: ["stage_source", "static_analysis", "build", "test", "start", "health_check", "acceptance_test"],
    healthCheck: { path: "/health", expectedStatus: 200, timeoutSeconds: 30 },
    resources: { cpu: 1, memoryMb: 512, processes: 32, openFiles: 256, diskMb: 256, durationSeconds: 300, idleSeconds: 60, logBytes: 1000000, artifactBytes: 10000000, previewSeconds: 120 },
    installNetwork: { mode: "none", approvedHosts: [] },
    network: { mode: "none", approvedHosts: [] },
    serviceBindings: [],
    secretAliases: [],
    requiredPorts: [8080],
    artifactPaths: ["artifacts"],
    maxDurationSeconds: 300,
    idempotencyKey: `production-sandbox-smoke-${Date.now()}-${crypto.randomBytes(4).toString("hex")}`,
  };
  return { submission, bundle: { manifest, files } };
}

async function signedRequest(method, path, payload = null) {
  const body = payload === null ? Buffer.alloc(0) : Buffer.from(JSON.stringify(payload));
  const response = await fetch(`http://127.0.0.1:${PORT}${path}`, {
    method,
    headers: {
      Authorization: `Bearer ${TOKEN}`,
      "X-Operly-Signature": hmacHex(TOKEN, body),
      ...(payload === null ? {} : { "Content-Type": "application/json" }),
    },
    body: payload === null ? undefined : body,
  });
  const raw = Buffer.from(await response.arrayBuffer());
  const signature = String(response.headers.get("x-operly-signature") || "");
  if (!signature || !safeEqual(signature, hmacHex(TOKEN, raw))) {
    throw new Error(`runner response signature invalid for ${method} ${path}`);
  }
  let data = {};
  try { data = JSON.parse(raw.toString("utf8")); } catch {}
  if (!response.ok) throw new Error(`runner ${method} ${path} failed: ${response.status} ${raw.toString("utf8").slice(0, 500)}`);
  return data;
}

async function waitForHealth() {
  const deadline = Date.now() + 30_000;
  while (Date.now() < deadline) {
    try {
      const response = await fetch(`http://127.0.0.1:${PORT}/health`);
      if (response.ok) return;
    } catch {}
    await new Promise((resolve) => setTimeout(resolve, 250));
  }
  throw new Error("local runner health did not become ready");
}

export async function runFullstackSmoke() {
  if (process.env.OPERLY_RUNNER_FULLSTACK_SMOKE !== "1") return;
  if (TOKEN.length < 32) throw new Error("OPERLY_RUNNER_TOKEN is required for full-stack smoke");
  await waitForHealth();
  const payload = fixture();
  let jobId = null;
  try {
    const queued = await signedRequest("POST", "/v1/builds", payload);
    jobId = queued.jobId;
    if (!jobId) throw new Error("runner did not return a job id");
    const deadline = Date.now() + 240_000;
    let final = queued;
    while (Date.now() < deadline) {
      final = await signedRequest("GET", `/v1/builds/${jobId}`);
      if (["preview_ready", "failed", "cancelled", "cleaned"].includes(final.state)) break;
      await new Promise((resolve) => setTimeout(resolve, 1000));
    }
    if (final.state !== "preview_ready") {
      throw new Error(`full-stack runner smoke did not become preview_ready: ${JSON.stringify(final).slice(0, 1000)}`);
    }
    if (!final.result?.buildSuccess || !final.result?.testSuccess || !final.result?.processStartSuccess || !final.result?.healthCheckSuccess || !final.result?.acceptanceCheckSuccess || !final.result?.previewAvailable) {
      throw new Error("full-stack runner smoke missing verified quality gates");
    }
    const preview = new URL(final.preview.targetUrl);
    const localPreview = await fetch(`http://127.0.0.1:${PORT}${preview.pathname}`);
    const html = await localPreview.text();
    if (!localPreview.ok || !html.includes("OPERLY_FULLSTACK_SMOKE_OK")) {
      throw new Error(`preview proxy smoke failed: ${localPreview.status}`);
    }
    console.log(`OPERLY_FULLSTACK_SMOKE_OK job=${jobId} sandbox=${final.result?.resourceUsage?.sandboxId || "unknown"}`);
  } finally {
    if (jobId) {
      try {
        await signedRequest("POST", `/v1/builds/${jobId}/cleanup`);
        console.log(`OPERLY_FULLSTACK_SMOKE_CLEANED job=${jobId}`);
      } catch (error) {
        console.error("OPERLY_FULLSTACK_SMOKE_CLEANUP_FAILED", error?.message || error);
        throw error;
      }
    }
  }
}
