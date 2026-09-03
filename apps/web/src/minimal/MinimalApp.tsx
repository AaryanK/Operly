import { FormEvent, ReactNode, useEffect, useMemo, useRef, useState } from "react";

import { api, ApiError } from "../api";
import { OperlyMark } from "../ui/OperlyMark";

const AUTH_FLOW_KEY = "operly:minimal-auth-flow";
const INVITE_FLOW_KEY = "operly:workspace-invite";

type AuthFlow = { email?: string; challenge_id?: string };
type AuthBootstrap = { google_client_id?: string | null; google_nonce?: string | null };
type Workspace = { id: string; name: string; role: string; current: boolean };
type InviteInfo = { invitation_id: string; workspace_id: string; workspace_name: string; role: string; targeted: boolean; expires_at: string; status: string };
type InviteAccept = { ok: boolean; workspace_id: string; workspace_name: string; role: string; next: string };

declare global {
  interface Window { google?: any; }
}

function readFlow(): AuthFlow {
  try { return JSON.parse(sessionStorage.getItem(AUTH_FLOW_KEY) || "{}"); } catch { return {}; }
}
function writeFlow(flow: AuthFlow) { sessionStorage.setItem(AUTH_FLOW_KEY, JSON.stringify(flow)); }
function go(path: string) { window.location.assign(path); }
function hashParam(name: string) { return new URLSearchParams(window.location.hash.slice(1)).get(name) || ""; }
function linkToken() { return hashParam("token"); }
function captureInvite(): string {
  const fromHash = hashParam("invite");
  if (fromHash) sessionStorage.setItem(INVITE_FLOW_KEY, fromHash);
  return fromHash || sessionStorage.getItem(INVITE_FLOW_KEY) || "";
}
function pendingInvite() { return sessionStorage.getItem(INVITE_FLOW_KEY) || ""; }
async function acceptPendingInvite(): Promise<string | null> {
  const token = pendingInvite();
  if (!token) return null;
  const result = await api<InviteAccept>("/workspace-os/invitation/accept", { method: "POST", body: JSON.stringify({ token }) });
  sessionStorage.removeItem(INVITE_FLOW_KEY);
  return result.next;
}
async function bootstrapAuth<T = unknown>() { return api<T>("/auth/bootstrap"); }
function errorMessage(error: unknown, fallback: string) { return error instanceof Error ? error.message : fallback; }

function Brand() {
  return <a className="minimal-brand" href="/"><OperlyMark className="minimal-brand-mark" /><span>OPERLY</span></a>;
}
function Page({ children }: { children: ReactNode }) {
  return <div className="minimal-page"><header className="minimal-header"><Brand /><nav><a href="/login">Sign in</a><a className="minimal-button minimal-button-primary" href="/signup">Create account</a></nav></header>{children}<footer className="minimal-footer"><span>© 2026 OPERLY</span><nav><a href="/privacy">Privacy</a><a href="/terms">Terms</a></nav></footer></div>;
}
function AuthPage({ children }: { children: ReactNode }) {
  return <div className="minimal-page minimal-auth-page"><header className="minimal-header"><Brand /><a href="/">Home</a></header><main className="minimal-auth-main">{children}</main><footer className="minimal-footer"><span>© 2026 OPERLY</span><nav><a href="/privacy">Privacy</a><a href="/terms">Terms</a></nav></footer></div>;
}
function PasswordField({ name, label, autoComplete }: { name: string; label: string; autoComplete: string }) {
  const [visible, setVisible] = useState(false);
  return <label className="minimal-field"><span>{label}</span><div className="minimal-password-wrap"><input name={name} type={visible ? "text" : "password"} autoComplete={autoComplete} minLength={12} required /><button type="button" onClick={() => setVisible((value) => !value)}>{visible ? "Hide" : "Show"}</button></div></label>;
}

function GoogleSignIn({ busy, setBusy, setError }: { busy: boolean; setBusy: (value: boolean) => void; setError: (value: string) => void }) {
  const [bootstrap, setBootstrap] = useState<AuthBootstrap | null>(null);

  useEffect(() => {
    bootstrapAuth<AuthBootstrap>().then(setBootstrap).catch(() => setBootstrap({}));
  }, []);

  useEffect(() => {
    if (!bootstrap?.google_client_id) return;
    let cancelled = false;
    const render = () => {
      if (cancelled || !window.google?.accounts?.id) return;
      const target = document.getElementById("minimal-google-auth");
      if (!target) return;
      window.google.accounts.id.initialize({
        client_id: bootstrap.google_client_id,
        nonce: bootstrap.google_nonce,
        auto_select: false,
        cancel_on_tap_outside: true,
        callback: async (result: any) => {
          const credential = String(result?.credential || "");
          if (!credential) return;
          setBusy(true); setError("");
          try {
            await api("/auth/google", { method: "POST", body: JSON.stringify({ credential }) });
            const next = await acceptPendingInvite();
            go(next || "/account");
          } catch (caught) {
            setError(errorMessage(caught, "Google sign-in failed"));
            setBusy(false);
          }
        },
      });
      target.replaceChildren();
      window.google.accounts.id.renderButton(target, {
        theme: "outline",
        size: "large",
        text: "continue_with",
        shape: "rectangular",
        width: Math.max(260, Math.min(420, Math.floor(target.clientWidth || 420))),
      });
    };

    if (window.google?.accounts?.id) render();
    else {
      const src = "https://accounts.google.com/gsi/client";
      const existing = document.querySelector<HTMLScriptElement>(`script[src="${src}"]`);
      if (existing) existing.addEventListener("load", render, { once: true });
      else {
        const script = document.createElement("script");
        script.src = src;
        script.async = true;
        script.defer = true;
        script.addEventListener("load", render, { once: true });
        document.head.appendChild(script);
      }
    }
    return () => { cancelled = true; };
  }, [bootstrap?.google_client_id, bootstrap?.google_nonce, setBusy, setError]);

  if (!bootstrap?.google_client_id) return null;
  return <><div id="minimal-google-auth" style={{ width: "100%", minHeight: 44, display: "grid", placeItems: "center", opacity: busy ? 0.65 : 1, pointerEvents: busy ? "none" : "auto" }} /><div style={{ display: "flex", alignItems: "center", gap: 12, margin: "18px 0", color: "#8f879d", fontSize: 12 }}><span style={{ height: 1, background: "rgba(255,255,255,0.1)", flex: 1 }} /><span>or continue with email</span><span style={{ height: 1, background: "rgba(255,255,255,0.1)", flex: 1 }} /></div></>;
}

function Landing() {
  const [signedIn, setSignedIn] = useState(false);
  useEffect(() => { api<Workspace[]>("/auth/workspaces").then(() => setSignedIn(true)).catch(() => setSignedIn(false)); }, []);
  return <Page><main className="minimal-landing"><section className="minimal-hero"><div className="minimal-hero-mark"><OperlyMark /></div><span className="minimal-kicker">OPERLY</span><h1>Your organization, operated from one workspace.</h1><p>Sign in to manage your workspaces, business modules, members and operating data.</p><div className="minimal-actions">{signedIn ? <a className="minimal-button minimal-button-primary" href="/account">Open Operly</a> : <><a className="minimal-button minimal-button-primary" href="/signup">Create account</a><a className="minimal-button" href="/login">Sign in</a></>}</div></section><section className="minimal-info-grid"><article><strong>Workspace first</strong><p>Each workspace has its own members, roles, modules and records.</p></article><article><strong>Business operating suite</strong><p>CRM, sales, finance, projects, operations, research and more share one authority model.</p></article><article><strong>Invite your team</strong><p>Workspace links carry access cleanly through sign-up and sign-in.</p></article></section></main></Page>;
}

function JoinWorkspace() {
  const [token] = useState(() => captureInvite());
  const [info, setInfo] = useState<InviteInfo | null>(null);
  const [signedIn, setSignedIn] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => {
    if (!token) { setError("This invitation link is missing its invitation token."); return; }
    Promise.all([
      api<InviteInfo>(`/workspace-os/invitation/inspect?token=${encodeURIComponent(token)}`),
      api<Workspace[]>("/auth/workspaces").then(() => true).catch(() => false),
    ]).then(([invite, authenticated]) => { setInfo(invite); setSignedIn(authenticated); }).catch((caught) => setError(errorMessage(caught, "This invitation is unavailable or expired.")));
  }, [token]);
  const accept = async () => {
    setBusy(true); setError("");
    try { const next = await acceptPendingInvite(); go(next || "/account"); }
    catch (caught) { setError(errorMessage(caught, "Could not join this workspace")); setBusy(false); }
  };
  const inviteHash = token ? `#invite=${encodeURIComponent(token)}` : "";
  return <AuthPage><section className="minimal-card"><span className="minimal-kicker">WORKSPACE INVITATION</span><h1>{info ? `Join ${info.workspace_name}` : "Join workspace"}</h1>{info && <><p>You’ve been invited as <strong>{info.role.replaceAll("-", " ")}</strong>.{info.targeted ? " This link is restricted to the invited email address." : " This is a one-use workspace invitation link."}</p><div className="minimal-account-meta"><span>{info.workspace_name}</span><span>Expires {new Date(info.expires_at).toLocaleString()}</span></div></>}{error && <div className="minimal-error">{error}</div>}{info && signedIn && <button className="minimal-button minimal-button-primary minimal-full" disabled={busy} onClick={() => void accept()}>{busy ? "Joining…" : `Join ${info.workspace_name}`}</button>}{info && !signedIn && <div className="minimal-actions"><a className="minimal-button minimal-button-primary" href={`/signup${inviteHash}`}>Create account & join</a><a className="minimal-button" href={`/login${inviteHash}`}>Sign in & join</a></div>}</section></AuthPage>;
}

function Login() {
  const [busy, setBusy] = useState(false); const [error, setError] = useState("");
  useEffect(() => { captureInvite(); }, []);
  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault(); const form = new FormData(event.currentTarget); setBusy(true); setError("");
    try {
      await bootstrapAuth();
      await api("/auth/login", { method: "POST", body: JSON.stringify({ email: form.get("email"), password: form.get("password") }) });
      const next = await acceptPendingInvite(); go(next || "/account");
    } catch (caught) {
      if (caught instanceof ApiError && caught.code === "EMAIL_NOT_VERIFIED") { writeFlow({ email: String(form.get("email") || "") }); go("/verify-email"); return; }
      setError(errorMessage(caught, "Sign in failed"));
    } finally { setBusy(false); }
  };
  return <AuthPage><form className="minimal-card" onSubmit={submit}><span className="minimal-kicker">SIGN IN</span><h1>Welcome back</h1><p>{pendingInvite() ? "Sign in to accept your workspace invitation." : "Sign in to your Operly account."}</p><GoogleSignIn busy={busy} setBusy={setBusy} setError={setError} /><label className="minimal-field"><span>Email</span><input name="email" type="email" autoComplete="username" required /></label><PasswordField name="password" label="Password" autoComplete="current-password" />{error && <div className="minimal-error">{error}</div>}<button className="minimal-button minimal-button-primary minimal-full" disabled={busy}>{busy ? "Signing in…" : "Sign in"}</button><div className="minimal-card-links"><a href="/forgot-password">Forgot password?</a><span>New here? <a href={pendingInvite() ? `/signup#invite=${encodeURIComponent(pendingInvite())}` : "/signup"}>Create an account</a></span></div></form></AuthPage>;
}

function Signup() {
  const [busy, setBusy] = useState(false); const [error, setError] = useState("");
  useEffect(() => { captureInvite(); }, []);
  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault(); const form = new FormData(event.currentTarget);
    if (form.get("password") !== form.get("confirm")) { setError("Passwords do not match"); return; }
    setBusy(true); setError("");
    try {
      await bootstrapAuth();
      const result = await api<{ email: string; challenge_id: string }>("/auth/signup", { method: "POST", body: JSON.stringify({ display_name: form.get("name"), email: form.get("email"), password: form.get("password") }) });
      writeFlow({ email: result.email, challenge_id: result.challenge_id }); go("/verify-email");
    } catch (caught) { setError(errorMessage(caught, "Account creation failed")); } finally { setBusy(false); }
  };
  return <AuthPage><form className="minimal-card" onSubmit={submit}><span className="minimal-kicker">CREATE ACCOUNT</span><h1>{pendingInvite() ? "Create your account & join" : "Join Operly"}</h1><p>{pendingInvite() ? "Your workspace invitation will be accepted after email verification." : "Create an Operly account to start or join workspaces."}</p><GoogleSignIn busy={busy} setBusy={setBusy} setError={setError} /><label className="minimal-field"><span>Name</span><input name="name" autoComplete="name" maxLength={200} required /></label><label className="minimal-field"><span>Email</span><input name="email" type="email" autoComplete="email" required /></label><PasswordField name="password" label="Password" autoComplete="new-password" /><PasswordField name="confirm" label="Confirm password" autoComplete="new-password" />{error && <div className="minimal-error">{error}</div>}<button className="minimal-button minimal-button-primary minimal-full" disabled={busy}>{busy ? "Creating account…" : "Create account"}</button><div className="minimal-card-links"><span>Already have an account? <a href={pendingInvite() ? `/login#invite=${encodeURIComponent(pendingInvite())}` : "/login"}>Sign in</a></span></div></form></AuthPage>;
}

function VerifyEmail() {
  const flow = useMemo(readFlow, []); const token = useMemo(linkToken, []); const attempted = useRef(false);
  const [busy, setBusy] = useState(false); const [error, setError] = useState(""); const [status, setStatus] = useState(flow.email ? `We sent a verification message to ${flow.email}.` : "Enter the verification code from your email.");
  const verify = async (code?: string) => {
    setBusy(true); setError("");
    try {
      await bootstrapAuth(); await api("/auth/verify-email", { method: "POST", body: JSON.stringify(token ? { token } : { challenge_id: flow.challenge_id, email: flow.email, code }) });
      const next = await acceptPendingInvite(); go(next || "/account");
    } catch (caught) { setError(errorMessage(caught, "Verification failed")); } finally { setBusy(false); }
  };
  useEffect(() => { if (token && !attempted.current) { attempted.current = true; void verify(); } }, [token]);
  const submit = (event: FormEvent<HTMLFormElement>) => { event.preventDefault(); void verify(String(new FormData(event.currentTarget).get("code") || "")); };
  const resend = async () => {
    if (!flow.email) { setError("Return to sign up and enter your email again."); return; }
    setBusy(true); setError("");
    try { await bootstrapAuth(); const result = await api<{ challenge_id?: string; message?: string }>("/auth/resend-verification", { method: "POST", body: JSON.stringify({ email: flow.email }) }); writeFlow({ ...flow, challenge_id: result.challenge_id || flow.challenge_id }); setStatus(result.message || "A new verification email is on its way."); }
    catch (caught) { setError(errorMessage(caught, "Could not resend verification")); } finally { setBusy(false); }
  };
  return <AuthPage><form className="minimal-card" onSubmit={submit}><span className="minimal-kicker">VERIFY EMAIL</span><h1>Check your inbox</h1><p>{status}{pendingInvite() ? " After verification, we’ll place you directly into the invited workspace." : ""}</p>{!token && <label className="minimal-field"><span>Verification code</span><input name="code" inputMode="numeric" autoComplete="one-time-code" required /></label>}{error && <div className="minimal-error">{error}</div>}{!token && <button className="minimal-button minimal-button-primary minimal-full" disabled={busy}>{busy ? "Verifying…" : "Verify email"}</button>}<button className="minimal-button minimal-full" type="button" disabled={busy} onClick={() => void resend()}>Resend verification</button></form></AuthPage>;
}

function ForgotPassword() {
  const [busy, setBusy] = useState(false); const [error, setError] = useState(""); const [status, setStatus] = useState("");
  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault(); const email = String(new FormData(event.currentTarget).get("email") || ""); setBusy(true); setError("");
    try { await bootstrapAuth(); const result = await api<{ message: string }>("/auth/forgot-password", { method: "POST", body: JSON.stringify({ email }) }); writeFlow({ email }); setStatus(result.message); }
    catch (caught) { setError(errorMessage(caught, "Could not send reset instructions")); } finally { setBusy(false); }
  };
  return <AuthPage><form className="minimal-card" onSubmit={submit}><span className="minimal-kicker">PASSWORD RECOVERY</span><h1>Reset your password</h1><p>Enter your account email and we’ll send reset instructions.</p><label className="minimal-field"><span>Email</span><input name="email" type="email" autoComplete="email" required /></label>{status && <div className="minimal-success">{status}</div>}{error && <div className="minimal-error">{error}</div>}<button className="minimal-button minimal-button-primary minimal-full" disabled={busy}>{busy ? "Sending…" : "Send reset instructions"}</button><a href="/reset-password">I already have a reset code</a></form></AuthPage>;
}

function ResetPassword() {
  const flow = useMemo(readFlow, []); const token = useMemo(linkToken, []); const [busy, setBusy] = useState(false); const [error, setError] = useState("");
  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault(); const form = new FormData(event.currentTarget); if (form.get("password") !== form.get("confirm")) { setError("Passwords do not match"); return; }
    setBusy(true); setError("");
    try { await bootstrapAuth(); await api("/auth/reset-password", { method: "POST", body: JSON.stringify({ ...(token ? { token } : { email: form.get("email"), code: form.get("code") }), password: form.get("password") }) }); const next = await acceptPendingInvite(); go(next || "/account"); }
    catch (caught) { setError(errorMessage(caught, "Password reset failed")); } finally { setBusy(false); }
  };
  return <AuthPage><form className="minimal-card" onSubmit={submit}><span className="minimal-kicker">NEW PASSWORD</span><h1>Choose a new password</h1>{!token && <><label className="minimal-field"><span>Email</span><input name="email" type="email" defaultValue={flow.email || ""} required /></label><label className="minimal-field"><span>Reset code</span><input name="code" autoComplete="one-time-code" required /></label></>}<PasswordField name="password" label="New password" autoComplete="new-password" /><PasswordField name="confirm" label="Confirm new password" autoComplete="new-password" />{error && <div className="minimal-error">{error}</div>}<button className="minimal-button minimal-button-primary minimal-full" disabled={busy}>{busy ? "Resetting…" : "Reset password"}</button></form></AuthPage>;
}

function Account() {
  const [loading, setLoading] = useState(true); const [workspaces, setWorkspaces] = useState<Workspace[]>([]); const [error, setError] = useState("");
  useEffect(() => { api<Workspace[]>("/auth/workspaces").then(setWorkspaces).catch(() => go("/login")).finally(() => setLoading(false)); }, []);
  const logout = async () => { setError(""); try { await api("/auth/logout", { method: "POST" }); go("/"); } catch (caught) { setError(errorMessage(caught, "Could not sign out")); } };
  return <div className="minimal-page"><header className="minimal-header"><Brand /><button className="minimal-button" onClick={() => void logout()}>Sign out</button></header><main className="minimal-account-main"><section className="minimal-account-card"><span className="minimal-kicker">ACCOUNT</span><h1>{loading ? "Loading your account…" : "You’re signed in."}</h1><p>Your Operly account is active. Open a workspace to operate its business modules and data.</p>{!loading && <div className="minimal-account-meta"><span>Session active</span><span>{workspaces.length} workspace{workspaces.length === 1 ? "" : "s"} linked</span></div>}{error && <div className="minimal-error">{error}</div>}</section></main><footer className="minimal-footer"><span>© 2026 OPERLY</span><nav><a href="/privacy">Privacy</a><a href="/terms">Terms</a></nav></footer></div>;
}
function NotFound() { return <Page><main className="minimal-auth-main"><section className="minimal-card"><span className="minimal-kicker">404</span><h1>That page isn’t available.</h1><p>Return to Operly or open one of your workspaces.</p><a className="minimal-button minimal-button-primary" href="/">Return home</a></section></main></Page>; }

export function MinimalApp({ pathname }: { pathname: string }) {
  useEffect(() => { const label = pathname === "/" ? "OPERLY" : pathname.slice(1).replaceAll("-", " ") || "OPERLY"; document.title = `${label} · OPERLY`; }, [pathname]);
  if (pathname === "/") return <Landing />;
  if (pathname === "/join") return <JoinWorkspace />;
  if (pathname === "/login") return <Login />;
  if (pathname === "/signup") return <Signup />;
  if (pathname === "/verify-email") return <VerifyEmail />;
  if (pathname === "/forgot-password") return <ForgotPassword />;
  if (pathname === "/reset-password") return <ResetPassword />;
  if (pathname === "/account" || pathname === "/onboarding" || pathname === "/personal" || pathname === "/app" || pathname === "/channels" || pathname.startsWith("/channels/")) return <Account />;
  return <NotFound />;
}
