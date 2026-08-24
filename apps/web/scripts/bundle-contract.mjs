import { readdir, stat } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";

const here = dirname(fileURLToPath(import.meta.url));
const assets = resolve(here, "../dist/assets");

function assert(condition, message) {
  if (!condition) throw new Error(message);
}

const files = await readdir(assets);
const javascript = files.filter((name) => name.endsWith(".js"));

assert(javascript.length >= 6, `Expected route-level JavaScript splitting; found only ${javascript.length} chunks`);
for (const expected of ["PersonalHome-", "WorkspaceShell-", "AccountSettings-"]) {
  assert(javascript.some((name) => name.startsWith(expected)), `Missing lazy authenticated chunk: ${expected}*`);
}

const sizes = await Promise.all(javascript.map(async (name) => ({ name, size: (await stat(resolve(assets, name))).size })));
const largest = sizes.reduce((left, right) => left.size >= right.size ? left : right);
assert(largest.size < 350 * 1024, `Largest JavaScript chunk is unexpectedly large: ${largest.name} (${largest.size} bytes)`);

console.log(`Bundle split contract passed with ${javascript.length} JavaScript chunks; largest is ${largest.name} (${largest.size} bytes).`);
