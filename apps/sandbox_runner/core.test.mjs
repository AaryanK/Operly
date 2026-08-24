import test from "node:test";
import assert from "node:assert/strict";
import {
  hmacHex,
  rebuildBundle,
  safeEqual,
  sha256Hex,
  stableJson,
  validateFullstackSource,
} from "./core.mjs";

function fixture() {
  const files = [
    { path: "backend/app.py", content: "print('ok')\n", generatedBy: "test" },
    { path: "frontend/index.html", content: "<!doctype html><title>ok</title>", generatedBy: "test" },
    { path: "tests/test_app.py", content: "import unittest\nclass T(unittest.TestCase):\n def test_ok(self): self.assertTrue(True)\n", generatedBy: "test" },
    {
      path: "operly.solution.json",
      content: JSON.stringify({
        schemaVersion: "operly.solution/v1",
        runtime: "operly-fullstack-v1",
        runtimeVersion: 1,
        layout: { frontend: "frontend", backend: "backend", workers: "workers", tests: "tests", migrations: "migrations" },
        execution: { frontend: "static", backend: "python-cli", worker: "none", healthPath: "/health" },
        dependencies: [],
        bindings: [],
      }),
      generatedBy: "test",
    },
  ];
  const rows = files.map((item) => {
    const bytes = Buffer.byteLength(item.content);
    return { path: item.path, bytes, digest: `sha256:${sha256Hex(Buffer.from(item.content))}`, generatedBy: item.generatedBy };
  }).sort((a,b) => (a.path < b.path ? -1 : a.path > b.path ? 1 : 0));
  const manifest = {
    schemaVersion: 1,
    workspaceId: "workspace",
    applicationId: "app",
    planId: "plan",
    planVersion: 1,
    sourceVersion: 1,
    promptDigest: "prompt",
    files: rows,
    totalBytes: rows.reduce((sum, row) => sum + row.bytes, 0),
  };
  const digest = `sha256:${sha256Hex(Buffer.from(stableJson(manifest)))}`;
  const submission = {
    workspaceId: "workspace",
    applicationId: "app",
    planVersion: 1,
    sourceVersion: 1,
    stackId: "operly-fullstack-v1",
    stackVersion: 1,
    sourceBundleDigest: digest,
    dependencies: [],
    operations: ["stage_source","static_analysis","build","test","start","health_check","acceptance_test"],
    healthCheck: { path: "/health", expectedStatus: 200, timeoutSeconds: 30 },
    serviceBindings: [],
    idempotencyKey: "idempotency-key",
  };
  return { submission, bundle: { manifest, files } };
}

test("rebuilds and validates an immutable full-stack bundle", () => {
  const { submission, bundle } = fixture();
  const rebuilt = rebuildBundle(submission, bundle);
  const manifest = validateFullstackSource(submission, rebuilt);
  assert.equal(manifest.runtime, "operly-fullstack-v1");
  assert.equal(rebuilt.digest, submission.sourceBundleDigest);
});

test("rejects traversal and digest ambiguity", () => {
  const { submission, bundle } = fixture();
  bundle.files[0] = { ...bundle.files[0], path: "../backend/app.py" };
  assert.throws(() => rebuildBundle(submission, bundle), /safe relative POSIX|traversal/);
});

test("runner HMAC comparison is constant-length safe", () => {
  const token = "x".repeat(64);
  const body = Buffer.from('{"ok":true}');
  const signature = hmacHex(token, body);
  assert.equal(safeEqual(signature, hmacHex(token, body)), true);
  assert.equal(safeEqual(signature, "bad"), false);
});
