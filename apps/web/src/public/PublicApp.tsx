import { FormEvent, useEffect, useMemo, useRef, useState } from "react";

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
  return <a className="public-brand" href="/"><OperlyMark className="public-brand-mark" /><strong>OPERLY</strong></a>;
}

function PublicFooter() {
  return <footer className="react-public-footer"><span>© 2026 OPERLY</span><nav><a href="/privacy">Privacy</a><a href="/terms">Terms</a><a href="https://dragonzpyder.xyz/" target="_blank" rel="noreferrer">Dragonzpyder Industries</a></nav></footer>;
}

function Landing() {
  useEffect(() => {
    api("/auth/workspaces").then(() => enterAuthenticatedScope()).catch(() => undefined);
  }, []);
  return <div className="react-public-page landing-page">
    <header className="react-public-header"><Brand /><nav><a href="#capabilities">Capabilities</a><a href="#control">Control</a><a href="#use-cases">Use cases</a></nav><div><a className="secondary-button" href="/login">Sign in</a><a className="primary-button" href="/signup">Get started</a></div></header>
    <main>
      <section className="react-hero"><div><span className="eyebrow">AI that can actually operate</span><h1>Give AI somewhere to <em>operate.</em></h1><p>OPERLY connects persistent context, authorized capabilities, business data and human approvals so AI can move from answering questions to getting work done.</p><div className="hero-actions"><a className="primary-button hero-button" href="/signup">Start using OPERLY →</a><span>Start privately. Add workspaces when you need shared context.</span></div></div><div className="runtime-card"><span className="runtime-pill">● Operating</span><h3>One request. Real work.</h3><p>“Find the leads that need follow-up, draft the messages, and show me anything that needs approval.”</p><div className="runtime-chain"><b>Context</b><i>→</i><b>Capabilities</b><i>→</i><b>Actions</b><i>→</i><b>Approval</b></div></div></section>
      <section id="capabilities" className="public-section"><span className="eyebrow">Capability layer</span><h2>More than a chat box.</h2><div className="public-grid"><article><b>Connect</b><h3>Work across services</h3><p>Gmail, Calendar, Discord, CRM, business records and other authorized services can participate in one operating environment.</p></article><article><b>Context</b><h3>Persistent understanding</h3><p>Conversations, projects, company state and history become retrievable context instead of being lost between prompts.</p></article><article><b>Act</b><h3>Turn intent into operations</h3><p>Agents can discover and invoke only the capabilities authorized for the current person or workspace.</p></article><article><b>Access</b><h3>Use it from anywhere</h3><p>The same operating layer can be reached from Operly, connected channels, APIs and compatible external AI clients.</p></article></div></section>
      <section id="control" className="public-section dark-band"><span className="eyebrow">Explicit authority</span><h2>Powerful does not mean unrestricted.</h2><div className="public-grid"><article><b>Identity</b><p>Personal and workspace scopes keep authority attached to the correct person and context.</p></article><article><b>Capability firewall</b><p>Knowing a capability exists is separate from being allowed to execute it.</p></article><article><b>Approval</b><p>Consequential actions can require explicit human approval before execution.</p></article><article><b>Provenance</b><p>Operational activity remains visible and attributable instead of disappearing into opaque agent output.</p></article></div></section>
      <section id="use-cases" className="public-section"><span className="eyebrow">Think in jobs</span><h2>Ask OPERLY to do the work.</h2><div className="request-grid"><span>Check my calendar and email and tell me what needs attention.</span><span>Look through our leads and prepare the follow-ups.</span><span>Use the tools available in this workspace to finish this task.</span><span>Read this attachment, understand the customer and update their record.</span></div></section>
      <section className="public-cta"><h2>Stop giving AI isolated prompts.</h2><p>Bring together context, models, tools and business operations inside one authorized AI operating layer.</p><a className="primary-button hero-button" href="/signup">Get started with OPERLY →</a></section>
    </main><PublicFooter />
  </div>;
}

function AuthShell({ children }: { children: React.ReactNode }) {
  return <div className="react-public-page auth-public-page"><header className="react-public-header auth-header"><Brand /><a href="/">← Home</a></header><main className="react-auth-shell">{children}</main><PublicFooter /></div>;
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

function NotFound() { return <div className="react-public-page"><header className="react-public-header"><Brand /><a href="/">Home</a></header><main className="not-found-react"><span>404</span><h1>That page does not exist.</h1><p>The route may have moved as Operly evolved.</p><a className="primary-button" href="/">Return home</a></main><PublicFooter /></div>; }

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
