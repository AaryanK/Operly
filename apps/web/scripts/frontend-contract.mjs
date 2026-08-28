import { readFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";

const here = dirname(fileURLToPath(import.meta.url));
const webRoot = resolve(here, "..");
const repoRoot = resolve(webRoot, "../..");

async function text(path) { return readFile(resolve(webRoot, path), "utf8"); }
async function repoText(path) { return readFile(resolve(repoRoot, path), "utf8"); }
function assert(condition, message) { if (!condition) throw new Error(message); }

const [rootApp, productApp, publicApp, adminPage, legalPage, main, publicStyles, reactPalette, liveStyles, convergence, theme, brand, legalLinks, personal, workspace, apiMain, dockerfile, emailBase, ...emailBodies] = await Promise.all([
  text("src/app/App.tsx"),
  text("src/app/ProductApp.tsx"),
  text("src/public/PublicApp.tsx"),
  text("src/admin/AdminPage.tsx"),
  text("src/legal/LegalPage.tsx"),
  text("src/main.tsx"),
  text("src/ui/public.css"),
  text("src/ui/react-public-admin-palette.css"),
  text("src/ui/react-public-live.css"),
  text("src/ui/convergence.css"),
  text("src/ui/theme.css"),
  text("src/ui/brand.css"),
  text("src/ui/legal-links.css"),
  text("src/account/PersonalHome.tsx"),
  text("src/workspace/WorkspaceShell.tsx"),
  repoText("apps/api/main.py"),
  repoText("Dockerfile"),
  repoText("packages/email/templates/base.html"),
  repoText("packages/email/templates/verify_email.html"),
  repoText("packages/email/templates/password_reset.html"),
  repoText("packages/email/templates/welcome.html"),
  repoText("packages/email/templates/password_changed.html"),
  repoText("packages/email/templates/security_alert.html"),
]);

assert(rootApp.includes('pathname === "/admin"'), "React root must own /admin");
assert(rootApp.includes('pathname === "/privacy"'), "React root must own /privacy");
assert(rootApp.includes('pathname === "/terms"'), "React root must own /terms");
assert(rootApp.includes('pathname.startsWith("/channels/")'), "React root must own authenticated /channels routes");
assert(rootApp.includes("<PublicApp pathname={pathname}"), "React root must own public/auth and unknown routes");

for (const route of ["/login", "/signup", "/verify-email", "/forgot-password", "/reset-password", "/onboarding"]) {
  assert(publicApp.includes(`pathname === "${route}"`), `React public app is missing ${route}`);
}
for (const contract of ["/auth/login", "/auth/signup", "/auth/google", "/auth/verify-email", "/auth/resend-verification", "/auth/forgot-password", "/auth/reset-password", "/workspace-invitations/accept", "/api/identities/discord/sign-in"]) {
  assert(publicApp.includes(contract), `React auth migration is missing ${contract}`);
}
assert(publicApp.includes('go("/channels/@me")'), "Personal auth handoff must target the canonical route");
assert(publicApp.includes("/channels/${encodeURIComponent"), "Workspace auth handoff must target the canonical route");
assert(publicApp.includes("workspace-invitations/inspect"), "Workspace invitation inspection must survive the migration");
assert(publicApp.includes("<RuntimePreview />"), "Public landing must keep the React runtime preview");
assert(publicApp.includes('id="studio"'), "Public landing must keep the React Studio product section");
assert(publicApp.includes("auth-visual-panel"), "Auth routes must keep the React visual context panel");
assert(publicApp.includes("public-model-band"), "Public landing must keep the model-agnostic operating-layer section");

for (const contract of ["/admin/session", "/admin/overview", "/admin/ai-usage?range=", "/admin/users?limit=500", "/admin/workspaces?limit=500"]) {
  assert(adminPage.includes(contract), `React admin migration is missing ${contract}`);
}
assert(adminPage.includes('type Tab = "overview" | "ai-usage" | "users" | "workspaces"'), "React admin must keep AI Usage as a first-class tab");
assert(adminPage.includes("admin-overview-grid"), "React admin must keep the rich overview composition");
assert(adminPage.includes("admin-health-ring"), "React admin must keep account-health visualization");
assert(adminPage.includes("admin-growth-panel"), "React admin must keep the 30-day growth chart");
assert(adminPage.includes("metrics.mau"), "React admin must keep MAU visibility");
assert(adminPage.includes("metrics.signups_today"), "React admin must keep today signup visibility");
assert(adminPage.includes("admin-token-chart"), "React admin must render token usage over time");
assert(adminPage.includes("admin-model-row"), "React admin must render per-model usage");
assert(adminPage.includes("admin-shell-orb"), "React admin must keep its canonical visual shell treatment");
assert(legalPage.includes("Privacy Policy"), "React Privacy Policy is missing");
assert(legalPage.includes("Terms of Service"), "React Terms of Service is missing");
assert(legalPage.includes("Google API Services User Data Policy"), "Google Limited Use disclosure must remain present");

assert(apiMain.includes("KNOWN_REACT_ROUTES"), "FastAPI must declare canonical React frontend routes");
assert(apiMain.includes("return react_shell(status_code=404)"), "Unknown frontend routes must render the React 404 shell");
assert(!apiMain.includes("WEB_STATIC"), "FastAPI must not depend on the removed static frontend");
assert(!apiMain.includes('app.mount("/static"'), "Legacy /static application mount must be retired");
assert(!dockerfile.includes("apps/web/static"), "Production image must not depend on apps/web/static");
assert(dockerfile.includes("apps/web/public/operly-logo.png"), "Production logo source must come from Vite public assets");

assert(main.includes('import "./ui/public.css"'), "React must load public/auth/admin/legal styles");
assert(main.includes('import "./ui/react-public-admin-palette.css"'), "React must load the public/admin palette convergence layer");
assert(main.includes('import "./ui/react-public-live.css"'), "React must load the live public/admin layer");
assert(main.indexOf('import "./ui/react-public-admin-palette.css"') > main.indexOf('import "./ui/public.css"'), "Public/admin palette convergence must load after public.css");
assert(main.indexOf('import "./ui/react-public-live.css"') > main.indexOf('import "./ui/react-public-admin-palette.css"'), "Live public/admin layer must load after the palette convergence layer");
assert(main.includes('import "./ui/legal-links.css"'), "React must load signed-in legal navigation styles");
assert(main.includes('import "./ui/convergence.css"'), "React must load the final convergence layer");
assert(publicStyles.includes(".react-auth-card"), "React auth card styling is missing");
assert(publicStyles.includes(".admin-react-shell"), "React admin styling is missing");
assert(publicStyles.includes(".react-legal-shell"), "React legal styling is missing");
assert(reactPalette.includes(".react-public-page"), "React public palette convergence is missing");
assert(reactPalette.includes(".admin-react-shell"), "React admin palette convergence is missing");
assert(reactPalette.includes(".admin-brand .operly-mark"), "React admin must explicitly bound the OperlyMark image size");
assert(reactPalette.includes(".operly-runtime-preview"), "React landing preview styling is missing");
assert(reactPalette.includes(".auth-visual-panel"), "React auth visual styling is missing");
assert(reactPalette.includes("var(--ui-canvas)"), "React public/admin convergence must use the canonical Operly canvas token");
assert(liveStyles.includes(".runtime-chain b"), "React landing runtime must keep visible live state motion");
assert(liveStyles.includes(".auth-visual-orb"), "React auth surface must keep ambient capability motion");
assert(liveStyles.includes(".admin-token-chart"), "React admin AI usage chart styling is missing");
assert(liveStyles.includes("@media (prefers-reduced-motion: reduce)"), "Public/admin live motion must respect reduced-motion preference");

assert(convergence.includes("@media (max-width: 680px)"), "Phone breakpoint contract is missing");
assert(convergence.includes("@media (max-width: 430px)"), "Small-phone breakpoint contract is missing");
assert(convergence.includes("100dvh"), "Mobile shell must use dynamic viewport units");
assert(convergence.includes("env(safe-area-inset-bottom)"), "Mobile shell must respect safe-area insets");
assert(convergence.includes(".workspace-mobile-nav"), "Mobile workspace navigation contract is missing");
assert(convergence.includes(".workspace-more-sheet"), "Mobile More sheet contract is missing");
assert(convergence.includes(".mobile-history-open .personal-history"), "Personal history drawer contract is missing");
assert(legalLinks.includes("position: static"), "Mobile legal links must leave the floating interaction layer");
assert(legalLinks.includes("env(safe-area-inset-bottom)"), "Signed-in legal links must respect phone safe areas");

for (const legacyPurple of ["#8173ff", "#7568e8", "#b9b0ff", "rgba(126, 104, 255", "rgba(129,115,255"]) {
  assert(!theme.toLowerCase().includes(legacyPurple.toLowerCase()), `Dark theme contains legacy purple brand accent: ${legacyPurple}`);
}
for (const legacyPurple of ["#7d6cff", "rgba(125,108,255", "rgba(111,92,255"]) {
  assert(!brand.toLowerCase().includes(legacyPurple.toLowerCase()), `Brand boot contains legacy purple accent: ${legacyPurple}`);
}

assert(workspace.includes('const mobilePrimarySections: WorkspaceSection[] = ["home", "operly", "activity", "solutions"]'), "Workspace phone navigation must stay intentionally small");
assert(workspace.includes("workspace-more-sheet"), "Workspace secondary destinations must remain reachable on phones");
assert(personal.includes("mobile-history-button"), "Personal conversation history must stay reachable on phones");
assert(personal.includes("history-mobile-close"), "Personal history drawer must have an explicit close control");
assert(productApp.includes("<ScopeRail"), "Authenticated product shell must keep the canonical scope rail");

for (const token of ["#f3f5f1", "#13231c", "#dfe6df", "#102f24"]) {
  assert(emailBase.toLowerCase().includes(token), `Transactional email shell is missing canonical Operly token: ${token}`);
}
for (const emailBody of emailBodies) {
  assert(emailBody.toLowerCase().includes("#185d43") || !emailBody.includes("$action_url"), "Transactional email CTA/link must use canonical Operly green #185d43");
  assert(!emailBody.toLowerCase().includes("#176c4a"), "Legacy email green #176c4a must not return");
}

console.log("React-only frontend contracts passed.");
