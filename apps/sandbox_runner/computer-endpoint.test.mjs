import test from "node:test";
import assert from "node:assert/strict";

import { safeRelative, validatePayload } from "./computer-endpoint.mjs";


test("computer paths remain inside declared workspace roots", () => {
  assert.equal(safeRelative("report.pdf", "output"), "/workspace/output/report.pdf");
  assert.equal(safeRelative("nested/report.pdf", "output"), "/workspace/output/nested/report.pdf");
  assert.throws(() => safeRelative("../secret", "output"), /traversal/);
  assert.throws(() => safeRelative("/etc/passwd", "output"), /relative/);
});


test("python computer payload is bounded and decoded", () => {
  const payload = validatePayload({
    mode: "python",
    code: "print('ok')",
    inputs: [{ filename: "invoice.txt", contentBase64: Buffer.from("hello").toString("base64") }],
    outputPaths: ["report.json"],
    timeoutSeconds: 9999,
  });
  assert.equal(payload.mode, "python");
  assert.equal(payload.decoded[0].bytes.toString("utf8"), "hello");
  assert.equal(payload.outputs[0].target, "/workspace/output/report.json");
  assert.equal(payload.timeoutSeconds, 600);
});


test("command computer payload requires argv rather than a shell string", () => {
  assert.throws(() => validatePayload({ mode: "command", argv: [] }), /argv/);
  const payload = validatePayload({ mode: "command", argv: ["ffmpeg", "-version"] });
  assert.deepEqual(payload.argv, ["ffmpeg", "-version"]);
});
