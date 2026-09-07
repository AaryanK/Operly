import { readFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";

const here = dirname(fileURLToPath(import.meta.url));
const webRoot = resolve(here, "..");
const repoRoot = resolve(webRoot, "../..");
const web = (path) => readFile(resolve(webRoot, path), "utf8");
const repo = (path) => readFile(resolve(repoRoot, path), "utf8");
const assert = (condition, message) => { if (!condition) throw new Error(message); };

const [brand, main, googleRouter] = await Promise.all([
  web("src/ui/brand.css"),
  web("src/main.tsx"),
  repo("apps/api/personal_connectors_router.py"),
]);

assert(
  brand.includes(':root,\nhtml[data-theme="dark"]') &&
  brand.includes('--ui-accent: #9b72ff') &&
  brand.includes('--ui-accent-strong: #c4a7ff') &&
  brand.includes('--ui-accent-gradient: linear-gradient(135deg, #8d63f4'),
  "Operly brand accents must remain purple in both light and dark appearances",
);
assert(
  !brand.includes('#79c99d') && !brand.includes('#b9ee72'),
  "Legacy green/lime brand accents must not return in the Operly brand layer",
);
assert(
  main.indexOf('./ui/theme.css') < main.indexOf('./ui/brand.css'),
  "The brand layer must load after generic theme values so purple remains authoritative",
);
assert(
  googleRouter.includes('def google_oauth_configuration()') &&
  googleRouter.includes('GOOGLE_OAUTH_CLIENT_ID') &&
  googleRouter.includes('GOOGLE_OAUTH_CLIENT_SECRET') &&
  googleRouter.includes('GOOGLE_OAUTH_REDIRECT_URI') &&
  googleRouter.includes('raise HTTPException(\n            503'),
  "Personal Google OAuth must validate its complete server configuration before redirecting the browser",
);
assert(
  googleRouter.includes('access_token = str(tokens.get("access_token")') &&
  googleRouter.includes('if not access_token:') &&
  googleRouter.includes('if not row and not tokens.get("refresh_token"):'),
  "Google connector persistence must never initialize from an absent access or offline refresh token",
);
assert(
  googleRouter.includes('prompt": "consent"') && googleRouter.includes('access_type": "offline"'),
  "Google tools must explicitly request offline consent for durable Personal Operly access",
);

console.log("Google OAuth initialization and Operly purple brand contracts passed.");
