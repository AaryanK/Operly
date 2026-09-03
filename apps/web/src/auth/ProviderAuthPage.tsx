import { FormEvent, useEffect, useRef, useState } from "react";

import { api, ApiError } from "../api";
import { OperlyMark } from "../ui/OperlyMark";

const AUTH_FLOW_KEY = "operly:minimal-auth-flow";
const INVITE_FLOW_KEY = "operly:workspace-invite";

type AuthBootstrap = { google_client_id?: string | null; google_nonce?: string | null };
type InviteAccept = { ok: boolean; workspace_id: string; workspace_name: string; role: string; next: string };

declare global {
  interface Window {
    google?: any;
  }
}

function go(path: string) { window.location.assign(path); }
function hashParam(name: string) { return new URLSearchParams(window.location.hash.slice(1)).get(name) || ""; }
function captureInvite() {
  const token = hashParam("invite");
  if (token) sessionStorage.setItem(INVITE_FLOW_KEY, token);
  return token || sessionStorage.getItem(INVITE_FLOW_KEY) || "";
}
function pendingInvite() { return sessionStorage.getItem(INVITE_FLOW_KEY) || ""; }
function writeAuthFlow(flow: { email?: string; challenge_id?: string }) {
  sessionStorage.setItem(AUTH_FLOW_KEY, JSON.stringify(flow));
}
function errorMessage(error: unknown, fallback: string) {
  return error instanceof Error ? error.message : fallback;
}
async function acceptPendingInvite() {
  const token = pendingInvite();
  if (!token) return null;
  const result = await api<InviteAccept>("/workspace-os/invitation/accept", {
    method: "POST",
    body: JSON.stringify({ token }),
  });
  sessionStorage.removeItem(INVITE_FLOW_KEY);
  return result.next;
}
async function finishAuthentication() {
  const next = await acceptPendingInvite();
  go(next || "/channels/@me");
}

function discordError() {
  return new URLSearchParams(window.location.search).get("discord_error") || "";
}

function Brand() {
  return <a className="minimal-brand" href="/"><OperlyMark className="minimal-brand-mark" /><span>OPERLY</span></a>;
}

function AuthShell({ children }: { children: React.ReactNode }) {
  return (
    <div className="minimal-page minimal-auth-page">
      <header className="minimal-header"><Brand /><a href="/">Home</a></header>
      <main className="minimal-auth-main">{children}</main>
      <footer className="minimal-footer"><span>© 2026 OPERLY</span><nav><a href="/privacy">Privacy</a><a href="/terms">Terms</a></nav></footer>
    </div>
  );
}

function PasswordField({ name, label, autoComplete }: { name: string; label: string; autoComplete: string }) {
  const [visible, setVisible] = useState(false);
  return (
    <label className="minimal-field">
      <span>{label}</span>
      <div className="minimal-password-wrap">
        <input name={name} type={visible ? "text" : "password"} autoComplete={autoComplete} minLength={12} required />
        <button type="button" onClick={() => setVisible((value) => !value)}>{visible ? "Hide" : "Show"}</button>
      </div>
    </label>
  );
}

function GoogleButton({ id, onCredential }: { id: string; onCredential: (credential: string) => Promise<void> }) {
  const [bootstrap, setBootstrap] = useState<AuthBootstrap | null>(null);
  const handler = useRef(onCredential);
  handler.current = onCredential;

  useEffect(() => {
    api<AuthBootstrap>("/auth/bootstrap").then(setBootstrap).catch(() => setBootstrap({}));
  }, []);

  useEffect(() => {
    if (!bootstrap?.google_client_id) return;
    let cancelled = false;

    const render = () => {
      if (cancelled || !window.google?.accounts?.id) return;
      const target = document.getElementById(id);
      if (!target) return;
      const width = Math.max(220, Math.min(360, target.clientWidth || 360));
      window.google.accounts.id.initialize({
        client_id: bootstrap.google_client_id,
        nonce: bootstrap.google_nonce,
        callback: (result: any) => void handler.current(String(result?.credential || "")),
        auto_select: false,
        cancel_on_tap_outside: true,
      });
      target.replaceChildren();
      window.google.accounts.id.renderButton(target, {
        theme: "outline",
        size: "large",
        text: "continue_with",
        shape: "rectangular",
        width,
      });
    };

    if (window.google?.accounts?.id) render();
    else {
      const source = "https://accounts.google.com/gsi/client";
      const existing = document.querySelector<HTMLScriptElement>(`script[src="${source}"]`);
      if (existing) existing.addEventListener("load", render, { once: true });
      else {
        const script = document.createElement("script");
        script.src = source;
        script.async = true;
        script.defer = true;
        script.addEventListener("load", render, { once: true });
        document.head.appendChild(script);
      }
    }
    return () => { cancelled = true; };
  }, [bootstrap?.google_client_id, bootstrap?.google_nonce, id]);

  if (!bootstrap?.google_client_id) return null;
  return <div id={id} style={{ width: "100%", display: "flex", justifyContent: "center", marginBottom: 12 }} />;
}

function DiscordButton() {
  if (pendingInvite()) return null;
  return (
    <a
      className="minimal-button minimal-full"
      href="/api/identities/discord/sign-in"
      style={{ display: "flex", alignItems: "center", justifyContent: "center", marginBottom: 12, textDecoration: "none" }}
    >
      Continue with Discord
    </a>
  );
}

function Divider() {
  return <div style={{ display: "flex", alignItems: "center", gap: 12, margin: "4px 0 20px", color: "#8f879d", fontSize: 12 }}><span style={{ height: 1, background: "rgba(255,255,255,.09)", flex: 1 }} /><span>or continue with email</span><span style={{ height: 1, background: "rgba(255,255,255,.09)", flex: 1 }} /></div>;
}

function Login() {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(() => discordError());
  useEffect(() => { captureInvite(); }, []);

  const google = async (credential: string) => {
    if (!credential || busy) return;
    setBusy(true); setError("");
    try {
      await api("/auth/google", { method: "POST", body: JSON.stringify({ credential }) });
      await finishAuthentication();
    } catch (caught) {
      setError(errorMessage(caught, "Google sign-in failed"));
      setBusy(false);
    }
  };

  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    setBusy(true); setError("");
    try {
      await api("/auth/bootstrap");
      await api("/auth/login", { method: "POST", body: JSON.stringify({ email: form.get("email"), password: form.get("password") }) });
      await finishAuthentication();
    } catch (caught) {
      if (caught instanceof ApiError && caught.code === "EMAIL_NOT_VERIFIED") {
        writeAuthFlow({ email: String(form.get("email") || "") });
        go("/verify-email");
        return;
      }
      setError(errorMessage(caught, "Sign in failed"));
      setBusy(false);
    }
  };

  return (
    <AuthShell>
      <form className="minimal-card" onSubmit={submit}>
        <span className="minimal-kicker">SIGN IN</span>
        <h1>Welcome back</h1>
        <p>{pendingInvite() ? "Sign in to accept your workspace invitation." : "Sign in to your Personal Operly or any workspace you belong to."}</p>
        <GoogleButton id="google-login-current" onCredential={google} />
        <DiscordButton />
        <Divider />
        <label className="minimal-field"><span>Email</span><input name="email" type="email" autoComplete="username" required /></label>
        <PasswordField name="password" label="Password" autoComplete="current-password" />
        {error && <div className="minimal-error">{error}</div>}
        <button className="minimal-button minimal-button-primary minimal-full" disabled={busy}>{busy ? "Signing in…" : "Sign in"}</button>
        <div className="minimal-card-links"><a href="/forgot-password">Forgot password?</a><span>New here? <a href={pendingInvite() ? `/signup#invite=${encodeURIComponent(pendingInvite())}` : "/signup"}>Create an account</a></span></div>
      </form>
    </AuthShell>
  );
}

function Signup() {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(() => discordError());
  useEffect(() => { captureInvite(); }, []);

  const google = async (credential: string) => {
    if (!credential || busy) return;
    setBusy(true); setError("");
    try {
      await api("/auth/google", { method: "POST", body: JSON.stringify({ credential }) });
      await finishAuthentication();
    } catch (caught) {
      setError(errorMessage(caught, "Google account creation failed"));
      setBusy(false);
    }
  };

  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    if (form.get("password") !== form.get("confirm")) { setError("Passwords do not match"); return; }
    setBusy(true); setError("");
    try {
      await api("/auth/bootstrap");
      const result = await api<{ email: string; challenge_id: string }>("/auth/signup", {
        method: "POST",
        body: JSON.stringify({ display_name: form.get("name"), email: form.get("email"), password: form.get("password") }),
      });
      writeAuthFlow({ email: result.email, challenge_id: result.challenge_id });
      go("/verify-email");
    } catch (caught) {
      setError(errorMessage(caught, "Account creation failed"));
      setBusy(false);
    }
  };

  return (
    <AuthShell>
      <form className="minimal-card" onSubmit={submit}>
        <span className="minimal-kicker">CREATE ACCOUNT</span>
        <h1>{pendingInvite() ? "Create your account & join" : "Create your Personal Operly"}</h1>
        <p>{pendingInvite() ? "Your workspace invitation will be accepted after authentication." : "Start with a private Personal Operly, then create or join workspaces whenever you need them."}</p>
        <GoogleButton id="google-signup-current" onCredential={google} />
        <DiscordButton />
        <Divider />
        <label className="minimal-field"><span>Name</span><input name="name" autoComplete="name" maxLength={200} required /></label>
        <label className="minimal-field"><span>Email</span><input name="email" type="email" autoComplete="email" required /></label>
        <PasswordField name="password" label="Password" autoComplete="new-password" />
        <PasswordField name="confirm" label="Confirm password" autoComplete="new-password" />
        {error && <div className="minimal-error">{error}</div>}
        <button className="minimal-button minimal-button-primary minimal-full" disabled={busy}>{busy ? "Creating account…" : "Create account"}</button>
        <div className="minimal-card-links"><span>Already have an account? <a href={pendingInvite() ? `/login#invite=${encodeURIComponent(pendingInvite())}` : "/login"}>Sign in</a></span></div>
      </form>
    </AuthShell>
  );
}

export function ProviderAuthPage({ pathname }: { pathname: string }) {
  useEffect(() => { document.title = `${pathname === "/signup" ? "create account" : "sign in"} · OPERLY`; }, [pathname]);
  return pathname === "/signup" ? <Signup /> : <Login />;
}
