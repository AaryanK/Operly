import { FormEvent, ReactNode, useEffect, useMemo, useRef, useState } from "react";

import { api, ApiError } from "../api";
import { OperlyMark } from "../ui/OperlyMark";

const AUTH_FLOW_KEY = "operly:minimal-auth-flow";

type AuthFlow = {
  email?: string;
  challenge_id?: string;
};

type Workspace = {
  id: string;
  name: string;
  role: string;
  current: boolean;
};

function readFlow(): AuthFlow {
  try {
    return JSON.parse(sessionStorage.getItem(AUTH_FLOW_KEY) || "{}");
  } catch {
    return {};
  }
}

function writeFlow(flow: AuthFlow) {
  sessionStorage.setItem(AUTH_FLOW_KEY, JSON.stringify(flow));
}

function go(path: string) {
  window.location.assign(path);
}

function linkToken() {
  return new URLSearchParams(window.location.hash.slice(1)).get("token") || "";
}

async function bootstrapAuth() {
  await api("/auth/bootstrap");
}

function errorMessage(error: unknown, fallback: string) {
  return error instanceof Error ? error.message : fallback;
}

function Brand() {
  return (
    <a className="minimal-brand" href="/">
      <OperlyMark className="minimal-brand-mark" />
      <span>OPERLY</span>
    </a>
  );
}

function Page({ children }: { children: ReactNode }) {
  return (
    <div className="minimal-page">
      <header className="minimal-header">
        <Brand />
        <nav>
          <a href="/login">Sign in</a>
          <a className="minimal-button minimal-button-primary" href="/signup">Create account</a>
        </nav>
      </header>
      {children}
      <footer className="minimal-footer">
        <span>© 2026 OPERLY</span>
        <nav><a href="/privacy">Privacy</a><a href="/terms">Terms</a></nav>
      </footer>
    </div>
  );
}

function AuthPage({ children }: { children: ReactNode }) {
  return (
    <div className="minimal-page minimal-auth-page">
      <header className="minimal-header"><Brand /><a href="/">Home</a></header>
      <main className="minimal-auth-main">{children}</main>
      <footer className="minimal-footer">
        <span>© 2026 OPERLY</span>
        <nav><a href="/privacy">Privacy</a><a href="/terms">Terms</a></nav>
      </footer>
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

function Landing() {
  const [signedIn, setSignedIn] = useState(false);
  useEffect(() => {
    api<Workspace[]>("/auth/workspaces").then(() => setSignedIn(true)).catch(() => setSignedIn(false));
  }, []);

  return (
    <Page>
      <main className="minimal-landing">
        <section className="minimal-hero">
          <div className="minimal-hero-mark"><OperlyMark /></div>
          <span className="minimal-kicker">OPERLY</span>
          <h1>A simpler Operly is live while we build what comes next.</h1>
          <p>Account access is available now. Sign in to your existing account or create a new one.</p>
          <div className="minimal-actions">
            {signedIn ? (
              <a className="minimal-button minimal-button-primary" href="/account">Open account</a>
            ) : (
              <>
                <a className="minimal-button minimal-button-primary" href="/signup">Create account</a>
                <a className="minimal-button" href="/login">Sign in</a>
              </>
            )}
          </div>
        </section>
        <section className="minimal-info-grid">
          <article><strong>Account access</strong><p>Sign in, sign up, verify your email, and recover your password.</p></article>
          <article><strong>Stable foundation</strong><p>This temporary surface is intentionally independent from the product runtime being rebuilt.</p></article>
          <article><strong>More soon</strong><p>Additional Operly features will return deliberately as the new foundation is ready.</p></article>
        </section>
      </main>
    </Page>
  );
}

function Login() {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    setBusy(true);
    setError("");
    try {
      await bootstrapAuth();
      await api("/auth/login", {
        method: "POST",
        body: JSON.stringify({ email: form.get("email"), password: form.get("password") }),
      });
      go("/account");
    } catch (caught) {
      if (caught instanceof ApiError && caught.code === "EMAIL_NOT_VERIFIED") {
        writeFlow({ email: String(form.get("email") || "") });
        go("/verify-email");
        return;
      }
      setError(errorMessage(caught, "Sign in failed"));
    } finally {
      setBusy(false);
    }
  };

  return (
    <AuthPage>
      <form className="minimal-card" onSubmit={submit}>
        <span className="minimal-kicker">SIGN IN</span>
        <h1>Welcome back</h1>
        <p>Sign in to your Operly account.</p>
        <label className="minimal-field"><span>Email</span><input name="email" type="email" autoComplete="username" required /></label>
        <PasswordField name="password" label="Password" autoComplete="current-password" />
        {error && <div className="minimal-error">{error}</div>}
        <button className="minimal-button minimal-button-primary minimal-full" disabled={busy}>{busy ? "Signing in…" : "Sign in"}</button>
        <div className="minimal-card-links"><a href="/forgot-password">Forgot password?</a><span>New here? <a href="/signup">Create an account</a></span></div>
      </form>
    </AuthPage>
  );
}

function Signup() {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    if (form.get("password") !== form.get("confirm")) {
      setError("Passwords do not match");
      return;
    }
    setBusy(true);
    setError("");
    try {
      await bootstrapAuth();
      const result = await api<{ email: string; challenge_id: string }>("/auth/signup", {
        method: "POST",
        body: JSON.stringify({
          display_name: form.get("name"),
          email: form.get("email"),
          password: form.get("password"),
        }),
      });
      writeFlow({ email: result.email, challenge_id: result.challenge_id });
      go("/verify-email");
    } catch (caught) {
      setError(errorMessage(caught, "Account creation failed"));
    } finally {
      setBusy(false);
    }
  };

  return (
    <AuthPage>
      <form className="minimal-card" onSubmit={submit}>
        <span className="minimal-kicker">CREATE ACCOUNT</span>
        <h1>Join Operly</h1>
        <p>Create your account now. Product features can be added back as they become ready.</p>
        <label className="minimal-field"><span>Name</span><input name="name" autoComplete="name" maxLength={200} required /></label>
        <label className="minimal-field"><span>Email</span><input name="email" type="email" autoComplete="email" required /></label>
        <PasswordField name="password" label="Password" autoComplete="new-password" />
        <PasswordField name="confirm" label="Confirm password" autoComplete="new-password" />
        {error && <div className="minimal-error">{error}</div>}
        <button className="minimal-button minimal-button-primary minimal-full" disabled={busy}>{busy ? "Creating account…" : "Create account"}</button>
        <div className="minimal-card-links"><span>Already have an account? <a href="/login">Sign in</a></span></div>
      </form>
    </AuthPage>
  );
}

function VerifyEmail() {
  const flow = useMemo(readFlow, []);
  const token = useMemo(linkToken, []);
  const attempted = useRef(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [status, setStatus] = useState(flow.email ? `We sent a verification message to ${flow.email}.` : "Enter the verification code from your email.");

  const verify = async (code?: string) => {
    setBusy(true);
    setError("");
    try {
      await bootstrapAuth();
      await api("/auth/verify-email", {
        method: "POST",
        body: JSON.stringify(token ? { token } : { challenge_id: flow.challenge_id, email: flow.email, code }),
      });
      go("/account");
    } catch (caught) {
      setError(errorMessage(caught, "Verification failed"));
    } finally {
      setBusy(false);
    }
  };

  useEffect(() => {
    if (token && !attempted.current) {
      attempted.current = true;
      verify();
    }
  }, [token]);

  const submit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    verify(String(new FormData(event.currentTarget).get("code") || ""));
  };

  const resend = async () => {
    if (!flow.email) {
      setError("Return to sign up and enter your email again.");
      return;
    }
    setBusy(true);
    setError("");
    try {
      await bootstrapAuth();
      const result = await api<{ challenge_id?: string; message?: string }>("/auth/resend-verification", {
        method: "POST",
        body: JSON.stringify({ email: flow.email }),
      });
      writeFlow({ ...flow, challenge_id: result.challenge_id || flow.challenge_id });
      setStatus(result.message || "A new verification email is on its way.");
    } catch (caught) {
      setError(errorMessage(caught, "Could not resend verification"));
    } finally {
      setBusy(false);
    }
  };

  return (
    <AuthPage>
      <form className="minimal-card" onSubmit={submit}>
        <span className="minimal-kicker">VERIFY EMAIL</span>
        <h1>Check your inbox</h1>
        <p>{status}</p>
        {!token && <label className="minimal-field"><span>Verification code</span><input name="code" inputMode="numeric" autoComplete="one-time-code" required /></label>}
        {error && <div className="minimal-error">{error}</div>}
        {!token && <button className="minimal-button minimal-button-primary minimal-full" disabled={busy}>{busy ? "Verifying…" : "Verify email"}</button>}
        <button className="minimal-button minimal-full" type="button" disabled={busy} onClick={resend}>Resend verification</button>
      </form>
    </AuthPage>
  );
}

function ForgotPassword() {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [status, setStatus] = useState("");

  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const email = String(new FormData(event.currentTarget).get("email") || "");
    setBusy(true);
    setError("");
    try {
      await bootstrapAuth();
      const result = await api<{ message: string }>("/auth/forgot-password", {
        method: "POST",
        body: JSON.stringify({ email }),
      });
      writeFlow({ email });
      setStatus(result.message);
    } catch (caught) {
      setError(errorMessage(caught, "Could not send reset instructions"));
    } finally {
      setBusy(false);
    }
  };

  return (
    <AuthPage>
      <form className="minimal-card" onSubmit={submit}>
        <span className="minimal-kicker">PASSWORD RECOVERY</span>
        <h1>Reset your password</h1>
        <p>Enter your account email and we’ll send reset instructions.</p>
        <label className="minimal-field"><span>Email</span><input name="email" type="email" autoComplete="email" required /></label>
        {status && <div className="minimal-success">{status}</div>}
        {error && <div className="minimal-error">{error}</div>}
        <button className="minimal-button minimal-button-primary minimal-full" disabled={busy}>{busy ? "Sending…" : "Send reset instructions"}</button>
        <a href="/reset-password">I already have a reset code</a>
      </form>
    </AuthPage>
  );
}

function ResetPassword() {
  const flow = useMemo(readFlow, []);
  const token = useMemo(linkToken, []);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    if (form.get("password") !== form.get("confirm")) {
      setError("Passwords do not match");
      return;
    }
    setBusy(true);
    setError("");
    try {
      await bootstrapAuth();
      await api("/auth/reset-password", {
        method: "POST",
        body: JSON.stringify({
          ...(token ? { token } : { email: form.get("email"), code: form.get("code") }),
          password: form.get("password"),
        }),
      });
      go("/account");
    } catch (caught) {
      setError(errorMessage(caught, "Password reset failed"));
    } finally {
      setBusy(false);
    }
  };

  return (
    <AuthPage>
      <form className="minimal-card" onSubmit={submit}>
        <span className="minimal-kicker">NEW PASSWORD</span>
        <h1>Choose a new password</h1>
        {!token && <><label className="minimal-field"><span>Email</span><input name="email" type="email" defaultValue={flow.email || ""} required /></label><label className="minimal-field"><span>Reset code</span><input name="code" autoComplete="one-time-code" required /></label></>}
        <PasswordField name="password" label="New password" autoComplete="new-password" />
        <PasswordField name="confirm" label="Confirm new password" autoComplete="new-password" />
        {error && <div className="minimal-error">{error}</div>}
        <button className="minimal-button minimal-button-primary minimal-full" disabled={busy}>{busy ? "Resetting…" : "Reset password"}</button>
      </form>
    </AuthPage>
  );
}

function Account() {
  const [loading, setLoading] = useState(true);
  const [workspaces, setWorkspaces] = useState<Workspace[]>([]);
  const [error, setError] = useState("");

  useEffect(() => {
    api<Workspace[]>("/auth/workspaces")
      .then((result) => setWorkspaces(result))
      .catch(() => go("/login"))
      .finally(() => setLoading(false));
  }, []);

  const logout = async () => {
    setError("");
    try {
      await api("/auth/logout", { method: "POST" });
      go("/");
    } catch (caught) {
      setError(errorMessage(caught, "Could not sign out"));
    }
  };

  return (
    <div className="minimal-page">
      <header className="minimal-header"><Brand /><button className="minimal-button" onClick={logout}>Sign out</button></header>
      <main className="minimal-account-main">
        <section className="minimal-account-card">
          <span className="minimal-kicker">ACCOUNT</span>
          <h1>{loading ? "Loading your account…" : "You’re signed in."}</h1>
          <p>Your Operly account is active. Product features are intentionally unavailable from this temporary shell while the next Operly foundation is prepared.</p>
          {!loading && <div className="minimal-account-meta"><span>Session active</span><span>{workspaces.length} workspace{workspaces.length === 1 ? "" : "s"} linked</span></div>}
          {error && <div className="minimal-error">{error}</div>}
        </section>
      </main>
      <footer className="minimal-footer"><span>© 2026 OPERLY</span><nav><a href="/privacy">Privacy</a><a href="/terms">Terms</a></nav></footer>
    </div>
  );
}

function NotFound() {
  return (
    <Page>
      <main className="minimal-auth-main">
        <section className="minimal-card">
          <span className="minimal-kicker">404</span>
          <h1>That page isn’t available.</h1>
          <p>This temporary Operly surface currently exposes account access only.</p>
          <a className="minimal-button minimal-button-primary" href="/">Return home</a>
        </section>
      </main>
    </Page>
  );
}

export function MinimalApp({ pathname }: { pathname: string }) {
  useEffect(() => {
    const label = pathname === "/" ? "OPERLY" : pathname.slice(1).replaceAll("-", " ") || "OPERLY";
    document.title = `${label} · OPERLY`;
  }, [pathname]);

  if (pathname === "/") return <Landing />;
  if (pathname === "/login" || pathname === "/join") return <Login />;
  if (pathname === "/signup") return <Signup />;
  if (pathname === "/verify-email") return <VerifyEmail />;
  if (pathname === "/forgot-password") return <ForgotPassword />;
  if (pathname === "/reset-password") return <ResetPassword />;
  if (pathname === "/account" || pathname === "/onboarding" || pathname === "/personal" || pathname === "/app" || pathname === "/channels" || pathname.startsWith("/channels/")) return <Account />;
  return <NotFound />;
}
