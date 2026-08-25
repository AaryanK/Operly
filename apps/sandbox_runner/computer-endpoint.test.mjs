import test from "node:test";
import assert from "node:assert/strict";

import { cleanSessionId, safeRelative, validatePayload } from "./computer-endpoint.mjs";


test("computer paths remain inside declared workspace roots", () => {
  assert.equal(safeRelative("report.pdf", "output"), "/workspace/output/report.pdf");
  assert.equal(safeRelative("nested/report.pdf", "output"), "/workspace/output/nested/report.pdf");
  assert.throws(() => safeRelative("../secret", "output"), /traversal/);
  assert.throws(() => safeRelative("/etc/passwd", "output"), /relative/);
});


test("python computer payload is bounded and run-session aware", () => {
  const payload = validatePayload({
    mode: "python",
    code: "print('ok')",
    inputs: [{ filename: "invoice.txt", contentBase64: Buffer.from("hello").toString("base64") }],
    outputPaths: ["report.json"],
    timeoutSeconds: 9999,
    sandboxId: "sbx_test_1234",
    keepAlive: true,
  });
  assert.equal(payload.mode, "python");
  assert.equal(payload.decoded[0].bytes.toString("utf8"), "hello");
  assert.equal(payload.outputs[0].target, "/workspace/output/report.json");
  assert.equal(payload.timeoutSeconds, 600);
  assert.equal(payload.sandboxId, "sbx_test_1234");
  assert.equal(payload.keepAlive, true);
});


test("computer sandbox handles are opaque bounded identifiers", () => {
  assert.equal(cleanSessionId("sbx_abc-123"), "sbx_abc-123");
  assert.equal(cleanSessionId(""), null);
  assert.throws(() => cleanSessionId("../../other-run"), /invalid sandbox/);
  assert.throws(() => cleanSessionId("has spaces"), /invalid sandbox/);
});


test("command computer payload requires argv rather than a shell string", () => {
  assert.throws(() => validatePayload({ mode: "command", argv: [] }), /argv/);
  const payload = validatePayload({ mode: "command", argv: ["ffmpeg", "-version"] });
  assert.deepEqual(payload.argv, ["ffmpeg", "-version"]);
  assert.equal(payload.keepAlive, false);
});
