import { FormEvent, ReactNode, useEffect, useMemo, useRef, useState } from "react";

import { api, ApiError } from "../api";
import { OperlyMark } from "../ui/OperlyMark";

const AUTH_FLOW_KEY = "operly:auth-flow";
const WORKSPACE_INVITE_KEY = "operly_workspace_invite";

type AuthFlow = { email?: string; challenge_id?: string; scope?: "personal" | "workspace" };
type AuthBootstrap = { google_client_id?: string | null; google_nonce?: string | null };
type AuthResponse = { scope?: "personal" | "workspace"; new_account?: boolean };

declare global {
  interface Window {
    google?: any;
  }
}

function readFlow(): AuthFlow {
  try { return JSON.parse(sessionStorage.getItem(AUTH_FLOW_KEY) || "{}"); } catch { return {}; }
}
function writeFlow(next: AuthFlow) { sessionStorage.setItem(AUTH_FLOW_KEY, JSON.stringify(next)); }
function linkToken() { return new URLSearchParams(window.location.hash.slice(1)).get("token") || ""; }
function inviteToken() {
  const hash = new URLSearchParams(window.location.hash.slice(1)).get("invite");
  if (hash) {
    sessionStorage.setItem(WORKSPACE_INVITE_KEY, hash);
    history.replaceState(history.state || {}, "", `${location.pathname}${location.search}`);
    return hash;
  }
  return sessionStorage.getItem(WORKSPACE_INVITE_KEY) || "";
}
function clearInvite() { sessionStorage.removeItem(WORKSPACE_INVITE_KEY); }
function go(path: string) { window.location.assign(path); }

async function enterAuthenticatedScope(preferred?: string | null) {
  if (preferred === "personal") { go("/channels/@me"); return; }
  if (preferred === "workspace") {
    try {
      const me = await api<{ tenant: { id: string } }>("/me");
      go(`/channels/${encodeURIComponent(me.tenant.id)}`);
      return;
    } catch { go("/channels/@me"); return; }
  }
  try {
    const me = await api<{ tenant: { id: string } }>("/me");
    go(`/channels/${encodeURIComponent(me.tenant.id)}`);
  } catch {
    await api("/auth/workspaces");
    go("/channels/@me");
  }
}

async function acceptInviteIfPresent() {
  const token = inviteToken();
  if (!token) return null;
  const result = await api<{ workspace_id?: string }>("/workspace-invitations/accept", { method: "POST", body: JSON.stringify({ token }) });
  clearInvite();
  return result;
}

async function enterAfterAuthentication(scope?: string | null) {
  const invitation = await acceptInviteIfPresent();
  if (invitation?.workspace_id) { go(`/channels/${encodeURIComponent(invitation.workspace_id)}`); return; }
  await enterAuthenticatedScope(scope);
}

function useGoogleButton(containerId: string, onCredential: (credential: string) => Promise<void>) {
  const [bootstrap, setBootstrap] = useState<AuthBootstrap | null>(null);
  useEffect(() => { api<AuthBootstrap>("/auth/bootstrap").then(setBootstrap).catch(() => setBootstrap({})); }, []);
  useEffect(() => {
    if (!bootstrap?.google_client_id) return;
    let cancelled = false;
    const render = () => {
      if (cancelled || !window.google?.accounts?.id) return;
      const target = document.getElementById(containerId);
      if (!target) return;
      window.google.accounts.id.initialize({
        client_id: bootstrap.google_client_id,
        nonce: bootstrap.google_nonce,
        callback: (result: any) => onCredential(String(result?.credential || "")),
        auto_select: false,
        cancel_on_tap_outside: true,
      });
      target.replaceChildren();
      window.google.accounts.id.renderButton(target, { theme: "outline", size: "large", text: "continue_with", width: 360 });
    };
    if (window.google?.accounts?.id) render();
    else {
      const existing = document.querySelector<HTMLScriptElement>('script[src="https://accounts.google.com/gsi/client"]');
      if (existing) existing.addEventListener("load", render, { once: true });
      else {
        const script = document.createElement("script");
        script.src = "https://accounts.google.com/gsi/client";
        script.async = true;
        script.defer = true;
        script.addEventListener("load", render, { once: true });
        document.head.appendChild(script);
      }
    }
    return () => { cancelled = true; };
  }, [bootstrap?.google_client_id, bootstrap?.google_nonce, containerId, onCredential]);
  return Boolean(bootstrap?.google_client_id);
}

function Brand() {
  return <a className="public-brand" href="/"><OperlyMark className="public-brand-mark" /><span><strong>OPERLY</strong><small>AI operating layer</small></span></a>;
}

function PublicFooter() {
  return (
    <footer className="react-public-footer">
      <div><Brand /><p>Persistent context, governed capabilities, real operations.</p></div>
      <nav><a href="#capabilities">Capabilities</a><a href="#control">Control</a><a href="/privacy">Privacy</a><a href="/terms">Terms</a><a href="https://dragonzpyder.xyz/" target="_blank" rel="noreferrer">Dragonzpyder Industries</a></nav>
      <span>© 2026 OPERLY</span>
    </footer>
  );
}

function RuntimePreview() {
  return (
    <div className="runtime-card operly-runtime-preview">
      <div className="runtime-preview-head"><div><span className="runtime-pill"><i /> Operating</span><small>OPERLY runtime</small></div><span className="runtime-window-dots"><i /><i /><i /></span></div>
      <div className="runtime-request"><small>Request</small><p>“Find the leads that need follow-up, draft the messages, and show me anything that needs approval.”</p></div>
      <div className="runtime-workflow">
        <article><header><span>Business operation</span><small>authorized chain</small></header><div className="runtime-chain"><b>Context</b><i>→</i><b>CRM</b><i>→</i><b>Gmail</b><i>→</i><b>Approval</b></div></article>
        <article><header><span>Software construction</span><small>persistent lifecycle</small></header><div className="runtime-chain"><b>Prompt</b><i>→</i><b>Plan</b><i>→</i><b>Source</b><i>→</i><b>Preview</b></div></article>
      </div>
      <div className="runtime-status"><span><i>✓</i> Context loaded</span><span><i>✓</i> Authority resolved</span><span><i>◉</i> Approval boundary active</span></div>
    </div>
  );
}

const capabilityCards = [
  { icon: "↗", eyebrow: "01 / Connect", title: "Work across connected services", body: "Bring Gmail, Calendar, Discord, CRM, business records and other authorized services into one operating environment.", tags: ["Google Workspace", "Discord", "CRM", "Channels"] },
  { icon: "⌁", eyebrow: "02 / Context", title: "Persistent understanding", body: "Conversations, company state, projects and history become retrievable context instead of disappearing between prompts.", tags: ["Conversations", "History", "Projects", "Business state"] },
  { icon: "◆", eyebrow: "03 / Act", title: "Turn intent into operations", body: "Agents discover and invoke only the capabilities authorized for the current person, workspace and surface.", tags: ["Actions", "Approvals", "Capabilities", "Agent runtime"] },
  { icon: "◎", eyebrow: "04 / Access", title: "Use the same operating layer anywhere", body: "Reach the same governed capability layer from Operly, connected channels, APIs and compatible external AI clients.", tags: ["Operly AI", "MCP", "API", "External AI"] },
];

function Landing() {
  useEffect(() => {
    api("/auth/workspaces").then(() => enterAuthenticatedScope()).catch(() => undefined);
  }, []);

  return (
    <div className="react-public-page landing-page">
      <header className="react-public-header">
        <Brand />
        <nav><a href="#capabilities">Capabilities</a><a href="#studio">Studio</a><a href="#control">Control</a><a href="#use-cases">Use cases</a></nav>
        <div className="public-header-actions"><a className="secondary-button" href="/login">Sign in</a><a className="primary-button" href="/signup">Get started</a></div>
      </header>

      <main>
        <section className="react-hero">
          <div className="public-hero-copy">
            <span className="public-status-chip"><i /> Give AI somewhere to operate</span>
            <h1>AI shouldn’t just answer. <em>It should operate.</em></h1>
            <p>OPERLY gives AI persistent context, authorized capabilities, connected services and a real software workspace—so it can move from understanding work to getting it done.</p>
            <div className="hero-actions"><a className="primary-button hero-button" href="/signup">Start using OPERLY <span>→</span></a><a className="hero-text-link" href="#capabilities">Explore the operating layer</a></div>
            <div className="hero-proof"><span>Persistent context</span><span>Connected tools</span><span>Business operations</span><span>Software construction</span><span>Human approvals</span></div>
          </div>
          <RuntimePreview />
        </section>

        <section id="capabilities" className="public-section public-capability-section">
          <div className="public-section-intro"><span className="eyebrow">The capability layer</span><h2>Give AI more than a chat box. <em>Give it capabilities.</em></h2><p>Context, tools, business services and software projects sit behind one governed operating layer. The model can discover what is available while Operly keeps authority explicit.</p></div>
          <div className="public-grid capability-grid">
            {capabilityCards.map((card) => <article key={card.eyebrow}><span className="capability-icon">{card.icon}</span><b>{card.eyebrow}</b><h3>{card.title}</h3><p>{card.body}</p><div className="capability-tags">{card.tags.map((tag) => <span key={tag}>{tag}</span>)}</div></article>)}
          </div>
        </section>

        <section id="studio" className="public-operating-band">
          <div className="public-band-copy"><span className="eyebrow">OPERLY Studio</span><h2>Don’t just ask AI to write code. <em>Ask it to build the thing.</em></h2><p>Studio gives agents a persistent software workspace with real source, build evidence, repair loops and previewable outcomes instead of disposable code blocks.</p><div className="studio-proof"><span>Persistent source</span><span>Isolated builds</span><span>Validation</span><span>Repair</span></div></div>
          <div className="studio-flow">
            <article><span>01</span><div><strong>Plan & understand</strong><p>Turn requirements into bounded work and acceptance criteria.</p></div></article>
            <article><span>02</span><div><strong>Create real source</strong><p>Generate and edit durable project files, not throwaway snippets.</p></div></article>
            <article><span>03</span><div><strong>Run & validate</strong><p>Build and test through isolated runtime boundaries.</p></div></article>
            <article><span>04</span><div><strong>Repair & preview</strong><p>Use failure evidence to converge on a verified preview.</p></div></article>
          </div>
        </section>

        <section className="public-section context-section">
          <div className="public-section-intro"><span className="eyebrow">Persistent business context</span><h2>Your business becomes context. <em>Not another prompt.</em></h2><p>Operly can retrieve only the relevant authorized slices of the information that already matters to the work.</p></div>
          <div className="context-cloud"><span>Contacts</span><span>Leads</span><span>Products</span><span>Inventory</span><span>Orders</span><span>Quotations</span><span>Appointments</span><span>Documents</span><span>Conversations</span><span>Projects</span></div>
        </section>

        <section className="public-model-band">
          <div><span className="eyebrow">Model agnostic by design</span><h2>The model isn’t the operating system. <em>OPERLY is.</em></h2><p>Models can change while your context, permissions, capabilities and business state remain anchored to one operating layer.</p></div>
          <div className="model-role-grid"><span>Reasoning</span><span>Planning</span><span>Coding</span><span>Validation</span><span>Repair</span><span>Specialist work</span></div>
        </section>

        <section id="control" className="public-section control-section">
          <div className="public-section-intro"><span className="eyebrow">Explicit authority</span><h2>Powerful doesn’t mean <em>unrestricted.</em></h2><p>Operly separates what AI can understand from what it is permitted to execute, with authorization and approvals between reasoning and real-world effects.</p></div>
          <div className="public-grid control-grid">
            <article><span className="capability-icon">◎</span><b>Identity</b><h3>Scoped context</h3><p>Personal, workspace and channel identities keep authority attached to the correct operating context.</p></article>
            <article><span className="capability-icon">◇</span><b>Firewall</b><h3>Authorized capabilities</h3><p>Knowing a capability exists is separate from receiving permission to execute it.</p></article>
            <article><span className="capability-icon">✓</span><b>Approval</b><h3>Human control when needed</h3><p>Consequential actions can stop at an explicit approval boundary before execution.</p></article>
            <article><span className="capability-icon">⌁</span><b>Provenance</b><h3>Actions remain visible</h3><p>Operational activity stays attributable instead of disappearing into opaque model output.</p></article>
          </div>
        </section>

        <section id="use-cases" className="public-section use-case-section">
          <div className="public-section-intro"><span className="eyebrow">Think in jobs, not AI products</span><h2>Ask OPERLY to <em>do the work.</em></h2><p>Different jobs can reuse the same governed context and capability foundation instead of becoming separate AI products.</p></div>
          <div className="request-grid"><span>Check my calendar and email and tell me what needs attention.</span><span>Look through our leads and prepare the follow-ups.</span><span>Build a website for this business and let me keep editing it with you.</span><span>Read this attachment, understand the customer and update their record.</span><span>Use the tools available in this workspace to finish this task.</span><span>Take this software project, find the bug, fix it and validate the build.</span></div>
        </section>

        <section className="public-cta"><span className="eyebrow">One operating layer</span><h2>Stop giving AI isolated prompts.</h2><p>Bring together context, models, tools, approvals and business operations inside one authorized system.</p><div><a className="primary-button hero-button" href="/signup">Get started with OPERLY →</a><a className="secondary-button" href="/login">Sign in</a></div></section>
      </main>
      <PublicFooter />
    </div>
  );
}

function AuthVisual() {
  return (
    <aside className="auth-visual-panel" aria-label="Operly capability overview">
      <div className="auth-visual-orb"><OperlyMark /><i /><i /></div>
      <span className="eyebrow">One identity. Governed action.</span>
      <h2>Your AI operating layer is waiting.</h2>
      <p>Sign in once to reach private Personal Operly and every workspace you are authorized to use.</p>
      <div className="auth-runtime-stack"><span><i>✓</i> Persistent context</span><span><i>✓</i> Permission-scoped capabilities</span><span><i>✓</i> Human approval boundaries</span></div>
      <div className="auth-mini-chain"><b>You</b><i>→</i><b>OPERLY</b><i>→</i><b>Authorized work</b></div>
    </aside>
  );
}

function AuthShell({ children }: { children: ReactNode }) {
  return (
    <div className="react-public-page auth-public-page">
      <header className="react-public-header auth-header"><Brand /><a className="auth-home-link" href="/">← Home</a></header>
      <main className="react-auth-shell"><AuthVisual /><div className="auth-form-stage">{children}</div></main>
      <PublicFooter />
    </div>
  );
}

function PasswordInput({ name, label, autoComplete }: { name: string; label: string; autoComplete: string }) {
  const [show, setShow] = useState(false);
  return <label>{label}<span className="react-password-field"><input name={name} type={show ? "text" : "password"} autoComplete={autoComplete} required minLength={12} /><button type="button" onClick={() => setShow(!show)}>{show ? "Hide" : "Show"}</button></span></label>;
}

function Login() {
  const [busy, setBusy] = useState(false); const [error, setError] = useState(""); const [invite, setInvite] = useState("");
  useEffect(() => { const token = inviteToken(); if (token) api<any>(`/workspace-invitations/inspect?token=${encodeURIComponent(token)}`).then((r) => setInvite(`You’ve been invited to ${r.workspace_name} as ${r.role}.`)).catch(() => { clearInvite(); setInvite("This workspace invitation is invalid or expired."); }); }, []);
  const google = async (credential: string) => { if (!credential) return; setBusy(true); setError(""); try { const r = await api<AuthResponse>("/auth/google", { method: "POST", body: JSON.stringify({ credential }) }); if (r.new_account) { writeFlow({ scope: r.scope }); go("/onboarding"); } else await enterAfterAuthentication(r.scope); } catch (e) { setError(e instanceof Error ? e.message : "Google sign-in failed"); } finally { setBusy(false); } };
  const googleEnabled = useGoogleButton("google-login-react", google);
  const submit = async (event: FormEvent<HTMLFormElement>) => { event.preventDefault(); setBusy(true); setError(""); const form = new FormData(event.currentTarget); try { const r = await api<AuthResponse>("/auth/login", { method: "POST", body: JSON.stringify({ email: form.get("email"), password: form.get("password") }) }); await enterAfterAuthentication(r.scope); } catch (e) { if (e instanceof ApiError && e.code === "EMAIL_NOT_VERIFIED") { writeFlow({ email: String(form.get("email") || "") }); go("/verify-email"); } else setError(e instanceof Error ? e.message : "Sign in failed"); } finally { setBusy(false); } };
  return <AuthShell><form className="react-auth-card" onSubmit={submit}><span className="eyebrow">Sign in</span><h1>Welcome back</h1><p>Open your Personal Operly and authorized workspaces.</p>{invite && <div className="auth-notice">{invite}</div>}{googleEnabled && <><div id="google-login-react" className="google-react-button"></div><div className="auth-divider"><span>or</span></div></>}<button type="button" className="discord-auth-button" onClick={() => go("/api/identities/discord/sign-in")}>Continue with Discord</button><label>Email<input name="email" type="email" autoComplete="username" required /></label><PasswordInput name="password" label="Password" autoComplete="current-password" />{error && <div className="inline-error">{error}</div>}<button className="primary-button full-button" disabled={busy}>{busy ? "Signing in…" : "Sign in"}</button><div className="auth-links"><a href="/forgot-password">Forgot password?</a><span>New here? <a href="/signup">Create an account</a></span></div></form></AuthShell>;
}

function Signup() {
  const [busy, setBusy] = useState(false); const [error, setError] = useState("");
  const google = async (credential: string) => { if (!credential) return; setBusy(true); setError(""); try { const r = await api<AuthResponse>("/auth/google", { method: "POST", body: JSON.stringify({ credential }) }); if (r.new_account) { writeFlow({ scope: r.scope }); go("/onboarding"); } else await enterAfterAuthentication(r.scope); } catch (e) { setError(e instanceof Error ? e.message : "Google sign-in failed"); } finally { setBusy(false); } };
  const googleEnabled = useGoogleButton("google-signup-react", google);
  const submit = async (event: FormEvent<HTMLFormElement>) => { event.preventDefault(); const form = new FormData(event.currentTarget); if (form.get("password") !== form.get("confirm")) { setError("Passwords do not match"); return; } setBusy(true); setError(""); try { const r = await api<any>("/auth/signup", { method: "POST", body: JSON.stringify({ display_name: form.get("name"), email: form.get("email"), password: form.get("password") }) }); writeFlow({ email: r.email, challenge_id: r.challenge_id }); go("/verify-email"); } catch (e) { setError(e instanceof Error ? e.message : "Account creation failed"); } finally { setBusy(false); } };
  return <AuthShell><form className="react-auth-card" onSubmit={submit}><span className="eyebrow">Get started</span><h1>Create your Operly account</h1><p>Start with a private Personal Operly. Create or join workspaces whenever you need them.</p>{googleEnabled && <><div id="google-signup-react" className="google-react-button"></div><div className="auth-divider"><span>or</span></div></>}<button type="button" className="discord-auth-button" onClick={() => go("/api/identities/discord/sign-in")}>Continue with Discord</button><label>Name<input name="name" autoComplete="name" required maxLength={200} /></label><label>Email<input name="email" type="email" autoComplete="email" required /></label><PasswordInput name="password" label="Password" autoComplete="new-password" /><PasswordInput name="confirm" label="Confirm password" autoComplete="new-password" />{error && <div className="inline-error">{error}</div>}<button className="primary-button full-button" disabled={busy}>{busy ? "Creating account…" : "Create account"}</button><div className="auth-links"><span>Already have an account? <a href="/login">Sign in</a></span></div></form></AuthShell>;
}

function VerifyEmail() {
  const flow = useMemo(readFlow, []); const token = useMemo(linkToken, []); const [busy, setBusy] = useState(false); const [error, setError] = useState(""); const [status, setStatus] = useState(flow.email ? `We sent a code to ${flow.email}.` : token ? "Verifying your email link…" : "Enter the verification code from your email."); const autoTried = useRef(false);
  const verify = async (code?: string) => { setBusy(true); setError(""); try { const payload = token ? { token } : { challenge_id: flow.challenge_id, email: flow.email, code }; const r = await api<AuthResponse>("/auth/verify-email", { method: "POST", body: JSON.stringify(payload) }); writeFlow({ ...flow, scope: r.scope }); if (inviteToken()) await enterAfterAuthentication(r.scope); else go("/onboarding"); } catch (e) { setError(e instanceof Error ? e.message : "Verification failed"); } finally { setBusy(false); } };
  useEffect(() => { if (token && !autoTried.current) { autoTried.current = true; verify(); } }, [token]);
  const submit = (e: FormEvent<HTMLFormElement>) => { e.preventDefault(); verify(String(new FormData(e.currentTarget).get("code") || "")); };
  const resend = async () => { if (!flow.email) { setError("Return to sign up and enter your email again."); return; } setBusy(true); setError(""); try { const r = await api<any>("/auth/resend-verification", { method: "POST", body: JSON.stringify({ email: flow.email }) }); writeFlow({ ...flow, challenge_id: r.challenge_id || flow.challenge_id }); setStatus(r.message || "A new verification email is on its way."); } catch (e) { setError(e instanceof Error ? e.message : "Could not resend verification"); } finally { setBusy(false); } };
  return <AuthShell><form className="react-auth-card" onSubmit={submit}><span className="eyebrow">Verify email</span><h1>Check your inbox</h1><p>{status}</p>{!token && <label>Verification code<input name="code" inputMode="numeric" autoComplete="one-time-code" required /></label>}{error && <div className="inline-error">{error}</div>}{!token && <button className="primary-button full-button" disabled={busy}>{busy ? "Verifying…" : "Verify email"}</button>}<button className="secondary-button full-button" type="button" disabled={busy} onClick={resend}>Resend verification</button></form></AuthShell>;
}

function ForgotPassword() {
  const [busy, setBusy] = useState(false); const [error, setError] = useState(""); const [status, setStatus] = useState("");
  const submit = async (e: FormEvent<HTMLFormElement>) => { e.preventDefault(); const email = String(new FormData(e.currentTarget).get("email") || ""); setBusy(true); setError(""); try { const r = await api<any>("/auth/forgot-password", { method: "POST", body: JSON.stringify({ email }) }); writeFlow({ email }); setStatus(r.message); } catch (err) { setError(err instanceof Error ? err.message : "Could not send reset instructions"); } finally { setBusy(false); } };
  return <AuthShell><form className="react-auth-card" onSubmit={submit}><span className="eyebrow">Password recovery</span><h1>Reset your password</h1><p>Enter your account email and we’ll send a reset link and code.</p><label>Email<input name="email" type="email" autoComplete="email" required /></label>{status && <div className="auth-success">{status}</div>}{error && <div className="inline-error">{error}</div>}<button className="primary-button full-button" disabled={busy}>{busy ? "Sending…" : "Send reset instructions"}</button><a href="/reset-password">I already have a reset code</a></form></AuthShell>;
}

function ResetPassword() {
  const flow = useMemo(readFlow, []); const token = useMemo(linkToken, []); const [busy, setBusy] = useState(false); const [error, setError] = useState("");
  const submit = async (e: FormEvent<HTMLFormElement>) => { e.preventDefault(); const form = new FormData(e.currentTarget); if (form.get("password") !== form.get("confirm")) { setError("Passwords do not match"); return; } const proof = token ? { token } : { email: form.get("email"), code: form.get("code") }; setBusy(true); setError(""); try { const r = await api<AuthResponse>("/auth/reset-password", { method: "POST", body: JSON.stringify({ ...proof, password: form.get("password") }) }); await enterAfterAuthentication(r.scope); } catch (err) { setError(err instanceof Error ? err.message : "Password reset failed"); } finally { setBusy(false); } };
  return <AuthShell><form className="react-auth-card" onSubmit={submit}><span className="eyebrow">Set new password</span><h1>Choose a new password</h1>{!token && <><label>Email<input name="email" type="email" defaultValue={flow.email || ""} required /></label><label>Reset code<input name="code" autoComplete="one-time-code" required /></label></>}<PasswordInput name="password" label="New password" autoComplete="new-password" /><PasswordInput name="confirm" label="Confirm new password" autoComplete="new-password" />{error && <div className="inline-error">{error}</div>}<button className="primary-button full-button" disabled={busy}>{busy ? "Resetting…" : "Reset password"}</button></form></AuthShell>;
}

function Onboarding() {
  const flow = useMemo(readFlow, []);
  return <AuthShell><section className="react-auth-card onboarding-react"><span className="onboarding-check">✓</span><span className="eyebrow">You’re ready</span><h1>Welcome to OPERLY</h1><p>Your account is ready. Start in private Personal Operly; create or join workspaces whenever you choose.</p><button className="primary-button full-button" onClick={() => enterAfterAuthentication(flow.scope)}>Open Operly</button></section></AuthShell>;
}

function NotFound() {
  return <div className="react-public-page"><header className="react-public-header"><Brand /><a href="/">Home</a></header><main className="not-found-react"><div className="not-found-orbit"><OperlyMark /><i /><i /></div><span>404 / ROUTE NOT FOUND</span><h1>This part of the operating layer isn’t here.</h1><p>The route may have moved as Operly evolved. Return to the main surface and continue from there.</p><a className="primary-button" href="/">Return home →</a></main><PublicFooter /></div>;
}

export function PublicApp({ pathname }: { pathname: string }) {
  useEffect(() => { document.title = pathname === "/" ? "OPERLY — AI that can operate" : `${pathname.slice(1).replaceAll("-", " ") || "Operly"} · OPERLY`; }, [pathname]);
  if (pathname === "/") return <Landing />;
  if (pathname === "/login" || pathname === "/join") return <Login />;
  if (pathname === "/signup") return <Signup />;
  if (pathname === "/verify-email") return <VerifyEmail />;
  if (pathname === "/forgot-password") return <ForgotPassword />;
  if (pathname === "/reset-password") return <ResetPassword />;
  if (pathname === "/onboarding") return <Onboarding />;
  if (pathname === "/personal") { go("/channels/@me"); return null; }
  if (pathname === "/app") { enterAuthenticatedScope().catch(() => go("/login")); return null; }
  return <NotFound />;
}