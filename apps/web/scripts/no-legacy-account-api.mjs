import { readFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";

const here = dirname(fileURLToPath(import.meta.url));
const source = await readFile(resolve(here, "../src/account/AccountSettings.tsx"), "utf8");
if (source.includes("/identities")) throw new Error("Current AccountSettings must not call the retired /identities API");
console.log("Legacy account API guard passed.");
