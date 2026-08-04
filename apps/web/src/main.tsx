import React, { useEffect, useMemo, useState } from "react";
import ReactDOM from "react-dom/client";

import { api, clearToken, getToken, setToken } from "./api";
import { Icon } from "./icons";
import "./styles.css";

type Section =
  | "overview"
  | "inbox"
  | "tasks"
  | "memory"
  | "approvals"
  | "integrations"
  | "settings";

type Me = {
  user: { id: string; email: string; display_name: string };
  tenant: { id: string; name: string; timezone: string };
  role: string;
};

type Message = {
  id: string;
  channel_id: string;
  author_name: string;
  content: string;
  is_bot: boolean;
  created_at: string;
};

type Task = {
  id: string;
  title: string;
  status: string;
  due_at: string | null;
  created_at: string;
};

type Memory = {
  id: string;
  kind: string;
  content: string;
  created_at: string;
};

type Approval = {
  id: string;
  action: string;
  status: string;
  details: Record<string, unknown>;
  created_at: string;
};

type Integration = {
  provider: string;
  label: string;
  status: string;
  detail: string | null;
};

const navItems: Array<{ id: Section; label: string; icon: string }> = [
  { id: "overview", label: "Overview", icon: "overview" },
  { id: "inbox", label: "Inbox", icon: "inbox" },
  { id: "tasks", label: "Tasks", icon: "tasks" },
  { id: "memory", label: "Business brain", icon: "brain" },
  { id: "approvals", label: "Approvals", icon: "approvals" },
  { id: "integrations", label: "Integrations", icon: "integrations" },
  { id: "settings", label: "Settings", icon: "settings" },
];

function Brand({ dark = false }: { dark?: boolean }) {
  return (
    <div className={`brand ${dark ? "brand-dark" : ""}`}>
      <div className="brand-mark">
        <span />
      </div>
      <span className="brand-word">OPERLY</span>
    </div>
  );
}

function Landing({ onLogin }: { onLogin: () => void }) {
  return (
    <div className="landing">
      <header className="landing-header">
        <Brand dark />
        <nav>
          <a href="#product">Product</a>
          <a href="#how">How it works</a>
          <button className="btn btn-secondary" onClick={onLogin}>Sign in</button>
          <button className="btn btn-primary" onClick={onLogin}>Open OPERLY</button>
        </nav>
      </header>

      <main>
        <section className="hero">
          <div className="hero-orb orb-one" />
          <div className="hero-orb orb-two" />
          <div className="hero-copy">
            <div className="eyebrow"><Icon name="spark" size={16}/> Business, operating intelligently.</div>
            <h1>One AI layer for<br/><span>your entire business.</span></h1>
            <p>
              Connect every customer channel, give your team one shared business
              brain, and turn conversations into actions—all from one place.
            </p>
            <div className="hero-actions">
              <button className="btn btn-primary btn-large" onClick={onLogin}>
                Launch your workspace <Icon name="arrow" size={18}/>
              </button>
              <span className="microcopy">Discord live now · More channels next</span>
            </div>
          </div>

          <div className="hero-product">
            <div className="product-shell">
              <div className="mini-sidebar">
                <Brand />
                <div className="mini-nav active"/>
                <div className="mini-nav"/>
                <div className="mini-nav"/>
                <div className="mini-nav"/>
              </div>
              <div className="mini-main">
                <div className="mini-top">
                  <span>Good morning, Owner</span>
                  <div className="avatar">O</div>
                </div>
                <div className="mini-stats">
                  <div><b>128</b><span>Messages</span></div>
                  <div><b>12</b><span>Open tasks</span></div>
                  <div><b>4</b><span>Approvals</span></div>
                </div>
                <div className="mini-grid">
                  <div className="mini-card mini-chat">
                    <strong>Unified inbox</strong>
                    <p><i/> New travel inquiry from Discord</p>
                    <p><i/> Quotation requested</p>
                    <p><i/> OPERLY drafted a reply</p>
                  </div>
                  <div className="mini-card mini-brain">
                    <strong>Business brain</strong>
                    <div className="brain-visual"><span/><span/><span/><span/><span/></div>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </section>

        <section id="product" className="feature-strip">
          <article>
            <span>01</span>
            <h3>Know every conversation</h3>
            <p>One tenant-isolated history across your connected business channels.</p>
          </article>
          <article>
            <span>02</span>
            <h3>Turn language into action</h3>
            <p>Tasks, reminders, approvals and business workflows executed by tools.</p>
          </article>
          <article>
            <span>03</span>
            <h3>Keep the owner in control</h3>
            <p>Every consequential action is visible, auditable and reversible.</p>
          </article>
        </section>

        <section id="how" className="statement">
          <p>Not another chatbot.</p>
          <h2>The operating layer between your business, your team and your customers.</h2>
          <button className="btn btn-primary btn-large" onClick={onLogin}>Enter the dashboard</button>
        </section>
      </main>
    </div>
  );
}

function Login({ onSuccess, onBack }: { onSuccess: () => void; onBack: () => void }) {
  const [email, setEmail] = useState("admin@operly.local");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      const result = await api<{ token: string }>("/auth/login", {
        method: "POST",
        body: JSON.stringify({ email, password }),
      });
      setToken(result.token);
      onSuccess();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Login failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="auth-page">
      <button className="back-link" onClick={onBack}>← Back to home</button>
      <div className="auth-panel">
        <Brand dark />
        <div className="auth-copy">
          <span className="eyebrow">Owner access</span>
          <h1>Welcome to your<br/>business command center.</h1>
          <p>Messages, tasks, memory and approvals—one secure workspace.</p>
        </div>
        <div className="auth-quote">
          “OPERLY should increase the owner’s visibility, capability and control.”
        </div>
      </div>
      <form className="auth-form" onSubmit={submit}>
        <div>
          <span className="form-kicker">Sign in</span>
          <h2>Open your workspace</h2>
          <p>Use the admin credentials configured on your server.</p>
        </div>
        <label>
          Email
          <input value={email} onChange={(e) => setEmail(e.target.value)} type="email" required/>
        </label>
        <label>
          Password
          <input value={password} onChange={(e) => setPassword(e.target.value)} type="password" required/>
        </label>
        {error && <div className="error-box">{error}</div>}
        <button className="btn btn-primary btn-large full" disabled={busy}>
          {busy ? "Signing in…" : "Enter OPERLY"} <Icon name="arrow" size={18}/>
        </button>
      </form>
    </div>
  );
}

function Dashboard({ onLogout }: { onLogout: () => void }) {
  const [section, setSection] = useState<Section>("overview");
  const [me, setMe] = useState<Me | null>(null);
  const [mobileNav, setMobileNav] = useState(false);

  useEffect(() => {
    api<Me>("/me").then(setMe).catch(onLogout);
  }, [onLogout]);

  return (
    <div className="app-shell">
      <aside className={`sidebar ${mobileNav ? "mobile-open" : ""}`}>
        <div className="sidebar-head"><Brand/><button className="mobile-close" onClick={() => setMobileNav(false)}>×</button></div>
        <nav className="sidebar-nav">
          {navItems.map((item) => (
            <button
              key={item.id}
              className={section === item.id ? "active" : ""}
              onClick={() => { setSection(item.id); setMobileNav(false); }}
            >
              <Icon name={item.icon} size={19}/>
              <span>{item.label}</span>
              {item.id === "approvals" && <em>•</em>}
            </button>
          ))}
        </nav>
        <div className="sidebar-bottom">
          <div className="workspace-chip">
            <div className="workspace-avatar">{me?.tenant.name.slice(0, 1) || "O"}</div>
            <div>
              <strong>{me?.tenant.name || "Loading…"}</strong>
              <span>{me?.role || "owner"}</span>
            </div>
          </div>
          <button className="logout-button" onClick={onLogout}>
            <Icon name="logout" size={18}/> Sign out
          </button>
        </div>
      </aside>

      <main className="app-main">
        <header className="topbar">
          <button className="mobile-menu" onClick={() => setMobileNav(true)}>☰</button>
          <div>
            <span className="topbar-kicker">{me?.tenant.name}</span>
            <h1>{navItems.find((item) => item.id === section)?.label}</h1>
          </div>
          <div className="topbar-right">
            <span className="system-pill"><i/> System online</span>
            <div className="user-avatar">{me?.user.display_name.slice(0, 1) || "O"}</div>
          </div>
        </header>
        <div className="content">
          {section === "overview" && <Overview onNavigate={setSection}/>}
          {section === "inbox" && <Inbox/>}
          {section === "tasks" && <Tasks/>}
          {section === "memory" && <Memories/>}
          {section === "approvals" && <Approvals/>}
          {section === "integrations" && <Integrations/>}
          {section === "settings" && me && <Settings me={me} onUpdate={setMe}/>}
        </div>
      </main>
    </div>
  );
}

function Overview({ onNavigate }: { onNavigate: (section: Section) => void }) {
  const [data, setData] = useState<{
    stats: Record<string, number>;
    recent_messages: Message[];
  } | null>(null);

  useEffect(() => {
    api<typeof data>("/dashboard").then(setData);
  }, []);

  const stats = [
    { key: "messages", label: "Messages captured", icon: "inbox", note: "Tenant-scoped" },
    { key: "open_tasks", label: "Open tasks", icon: "tasks", note: "Needs attention" },
    { key: "memories", label: "Business facts", icon: "brain", note: "Shared context" },
    { key: "pending_approvals", label: "Pending approvals", icon: "approvals", note: "Owner control" },
  ];

  return (
    <div className="page-stack">
      <section className="welcome-card">
        <div>
          <span className="eyebrow">Today in your business</span>
          <h2>Everything that needs your attention, in one place.</h2>
          <p>OPERLY is collecting your server context and turning it into usable business operations.</p>
        </div>
        <button className="btn btn-light" onClick={() => onNavigate("inbox")}>
          Open inbox <Icon name="arrow" size={17}/>
        </button>
      </section>

      <section className="stats-grid">
        {stats.map((stat) => (
          <article className="stat-card" key={stat.key}>
            <div className="stat-icon"><Icon name={stat.icon}/></div>
            <strong>{data?.stats[stat.key] ?? "—"}</strong>
            <span>{stat.label}</span>
            <small>{stat.note}</small>
          </article>
        ))}
      </section>

      <section className="dashboard-grid">
        <div className="panel">
          <div className="panel-head">
            <div><span className="section-kicker">Live activity</span><h3>Recent conversations</h3></div>
            <button className="text-button" onClick={() => onNavigate("inbox")}>View all</button>
          </div>
          <div className="activity-list">
            {(data?.recent_messages || []).length === 0 && <EmptyState text="Messages from Discord will appear here."/>}
            {(data?.recent_messages || []).map((message) => (
              <div className="activity-row" key={message.id}>
                <div className={`message-avatar ${message.is_bot ? "bot" : ""}`}>
                  {message.is_bot ? "O" : message.author_name.slice(0, 1)}
                </div>
                <div>
                  <strong>{message.author_name}</strong>
                  <p>{message.content}</p>
                </div>
                <time>{formatTime(message.created_at)}</time>
              </div>
            ))}
          </div>
        </div>

        <div className="panel insight-panel">
          <div className="panel-head">
            <div><span className="section-kicker">OPERLY insight</span><h3>Your control loop</h3></div>
            <div className="ai-dot"><Icon name="spark" size={18}/></div>
          </div>
          <div className="flow-list">
            <div><span>1</span><p><b>Listen</b>Capture business conversations</p></div>
            <div><span>2</span><p><b>Understand</b>Use tenant-isolated context</p></div>
            <div><span>3</span><p><b>Act</b>Run tools with permissions</p></div>
            <div><span>4</span><p><b>Report</b>Surface what needs attention</p></div>
          </div>
          <button className="btn btn-dark full" onClick={() => onNavigate("integrations")}>
            Manage integrations <Icon name="arrow" size={17}/>
          </button>
        </div>
      </section>
    </div>
  );
}

function Inbox() {
  const [messages, setMessages] = useState<Message[]>([]);
  const [search, setSearch] = useState("");

  async function load(value = "") {
    const query = value ? `?search=${encodeURIComponent(value)}` : "";
    setMessages(await api<Message[]>(`/messages${query}`));
  }

  useEffect(() => { load(); }, []);

  return (
    <div className="page-stack">
      <div className="page-title-row">
        <div><span className="section-kicker">Unified communication</span><h2>Every business conversation</h2></div>
        <div className="search-box">
          <Icon name="search" size={18}/>
          <input
            placeholder="Search messages"
            value={search}
            onChange={(e) => { setSearch(e.target.value); load(e.target.value); }}
          />
        </div>
      </div>
      <div className="panel inbox-panel">
        <div className="inbox-toolbar">
          <span>Discord</span><small>{messages.length} messages</small>
        </div>
        <div className="message-list">
          {messages.length === 0 && <EmptyState text="No matching conversations yet."/>}
          {messages.map((message) => (
            <article className="message-item" key={message.id}>
              <div className={`message-avatar ${message.is_bot ? "bot" : ""}`}>
                {message.is_bot ? "O" : message.author_name.slice(0, 1)}
              </div>
              <div className="message-body">
                <div><strong>{message.author_name}</strong><time>{formatDate(message.created_at)}</time></div>
                <p>{message.content}</p>
                <small>Channel {message.channel_id}</small>
              </div>
            </article>
          ))}
        </div>
      </div>
    </div>
  );
}

function Tasks() {
  const [tasks, setTasks] = useState<Task[]>([]);
  const [title, setTitle] = useState("");

  async function load() {
    setTasks(await api<Task[]>("/tasks"));
  }

  useEffect(() => { load(); }, []);

  async function create(event: React.FormEvent) {
    event.preventDefault();
    if (!title.trim()) return;
    await api<Task>("/tasks", { method: "POST", body: JSON.stringify({ title }) });
    setTitle("");
    await load();
  }

  async function complete(id: string) {
    await api(`/tasks/${id}/complete`, { method: "PATCH" });
    await load();
  }

  return (
    <div className="page-stack">
      <div className="page-title-row">
        <div><span className="section-kicker">Execution</span><h2>Tasks that move the business</h2></div>
      </div>
      <form className="create-bar" onSubmit={create}>
        <div className="create-icon"><Icon name="plus"/></div>
        <input value={title} onChange={(e) => setTitle(e.target.value)} placeholder="Create a task…"/>
        <button className="btn btn-primary">Add task</button>
      </form>
      <div className="panel">
        <div className="task-list">
          {tasks.length === 0 && <EmptyState text="No tasks yet. Create one above or ask OPERLY in Discord."/>}
          {tasks.map((task) => (
            <article className={`task-row ${task.status}`} key={task.id}>
              <button className="task-check" onClick={() => complete(task.id)} disabled={task.status === "completed"}>
                {task.status === "completed" && <Icon name="check" size={15}/>}
              </button>
              <div>
                <strong>{task.title}</strong>
                <span>{task.due_at ? `Due ${formatDate(task.due_at)}` : "No deadline"}</span>
              </div>
              <em>{task.status}</em>
            </article>
          ))}
        </div>
      </div>
    </div>
  );
}

function Memories() {
  const [memories, setMemories] = useState<Memory[]>([]);
  const [content, setContent] = useState("");

  async function load() {
    setMemories(await api<Memory[]>("/memories"));
  }

  useEffect(() => { load(); }, []);

  async function create(event: React.FormEvent) {
    event.preventDefault();
    if (!content.trim()) return;
    await api("/memories", { method: "POST", body: JSON.stringify({ content, kind: "fact" }) });
    setContent("");
    await load();
  }

  return (
    <div className="page-stack">
      <div className="page-title-row">
        <div><span className="section-kicker">Persistent context</span><h2>Your business brain</h2></div>
      </div>
      <form className="memory-composer" onSubmit={create}>
        <div className="brain-badge"><Icon name="brain" size={24}/></div>
        <div>
          <h3>Teach OPERLY something important</h3>
          <p>Facts are stored only inside this business workspace.</p>
          <textarea value={content} onChange={(e) => setContent(e.target.value)} placeholder="Example: Refunds over $100 require manager approval."/>
          <button className="btn btn-primary">Store memory</button>
        </div>
      </form>
      <div className="memory-grid">
        {memories.length === 0 && <div className="panel"><EmptyState text="Important facts remembered in Discord will appear here."/></div>}
        {memories.map((memory) => (
          <article className="memory-card" key={memory.id}>
            <span>{memory.kind}</span>
            <p>{memory.content}</p>
            <time>{formatDate(memory.created_at)}</time>
          </article>
        ))}
      </div>
    </div>
  );
}

function Approvals() {
  const [approvals, setApprovals] = useState<Approval[]>([]);

  async function load() {
    setApprovals(await api<Approval[]>("/approvals"));
  }

  useEffect(() => { load(); }, []);

  async function decide(id: string, status: "approved" | "rejected") {
    await api(`/approvals/${id}`, { method: "PATCH", body: JSON.stringify({ status }) });
    await load();
  }

  return (
    <div className="page-stack">
      <div className="page-title-row">
        <div><span className="section-kicker">Human-controlled autonomy</span><h2>Review consequential actions</h2></div>
      </div>
      <div className="approval-list">
        {approvals.length === 0 && <div className="panel"><EmptyState text="No approval requests are waiting."/></div>}
        {approvals.map((approval) => (
          <article className="approval-card" key={approval.id}>
            <div className="approval-icon"><Icon name="approvals"/></div>
            <div>
              <span className={`status ${approval.status}`}>{approval.status}</span>
              <h3>{approval.action}</h3>
              <p>{Object.values(approval.details).join(" · ") || "No additional details"}</p>
              <time>{formatDate(approval.created_at)}</time>
            </div>
            {approval.status === "pending" && (
              <div className="approval-actions">
                <button className="btn btn-secondary" onClick={() => decide(approval.id, "rejected")}>Reject</button>
                <button className="btn btn-primary" onClick={() => decide(approval.id, "approved")}>Approve</button>
              </div>
            )}
          </article>
        ))}
      </div>
    </div>
  );
}

function Integrations() {
  const [integrations, setIntegrations] = useState<Integration[]>([]);

  useEffect(() => {
    api<Integration[]>("/integrations").then(setIntegrations);
  }, []);

  const initials: Record<string, string> = {
    discord: "D",
    whatsapp: "W",
    instagram: "I",
    facebook: "f",
    x: "X",
  };

  return (
    <div className="page-stack">
      <div className="page-title-row">
        <div><span className="section-kicker">One communication layer</span><h2>Connect where customers find you</h2></div>
      </div>
      <div className="integration-grid">
        {integrations.map((item) => (
          <article className="integration-card" key={item.provider}>
            <div className={`integration-logo ${item.provider}`}>{initials[item.provider]}</div>
            <div>
              <h3>{item.label}</h3>
              <p>{item.detail || (item.status === "coming_soon" ? "Connector planned after MVP" : "Not connected")}</p>
            </div>
            <span className={`status ${item.status}`}>
              {item.status.replace("_", " ")}
            </span>
          </article>
        ))}
      </div>
      <div className="integration-note">
        <Icon name="spark"/>
        <div><strong>Discord is the first adapter—not the product.</strong><p>The same tenant, memory, inbox and tool harness will power every future channel.</p></div>
      </div>
    </div>
  );
}

function Settings({ me, onUpdate }: { me: Me; onUpdate: (me: Me) => void }) {
  const [name, setName] = useState(me.tenant.name);
  const [timezone, setTimezone] = useState(me.tenant.timezone);
  const [saved, setSaved] = useState(false);

  async function save(event: React.FormEvent) {
    event.preventDefault();
    const tenant = await api<Me["tenant"]>("/settings/tenant", {
      method: "PATCH",
      body: JSON.stringify({ name, timezone }),
    });
    onUpdate({ ...me, tenant });
    setSaved(true);
    setTimeout(() => setSaved(false), 1800);
  }

  return (
    <div className="page-stack">
      <div className="page-title-row">
        <div><span className="section-kicker">Workspace configuration</span><h2>Business settings</h2></div>
      </div>
      <form className="settings-panel panel" onSubmit={save}>
        <div className="settings-section">
          <h3>Business identity</h3>
          <p>This identity scopes every message, memory, task and action.</p>
          <label>Business name<input value={name} onChange={(e) => setName(e.target.value)}/></label>
          <label>Timezone<input value={timezone} onChange={(e) => setTimezone(e.target.value)} placeholder="Asia/Kathmandu"/></label>
        </div>
        <div className="settings-section">
          <h3>Security boundary</h3>
          <div className="security-box">
            <Icon name="approvals"/>
            <div><strong>Tenant isolation active</strong><p>All API reads and writes require this workspace’s tenant ID.</p></div>
          </div>
        </div>
        <button className="btn btn-primary">{saved ? "Saved" : "Save settings"}</button>
      </form>
    </div>
  );
}

function EmptyState({ text }: { text: string }) {
  return <div className="empty-state"><div className="empty-mark">O</div><p>{text}</p></div>;
}

function formatDate(value: string) {
  return new Intl.DateTimeFormat(undefined, {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  }).format(new Date(value));
}

function formatTime(value: string) {
  return new Intl.DateTimeFormat(undefined, {
    hour: "numeric",
    minute: "2-digit",
  }).format(new Date(value));
}

function App() {
  const [mode, setMode] = useState<"landing" | "login" | "dashboard">(
    getToken() ? "dashboard" : "landing",
  );

  useEffect(() => {
    const logout = () => setMode("login");
    window.addEventListener("operly:logout", logout);
    return () => window.removeEventListener("operly:logout", logout);
  }, []);

  function logout() {
    clearToken();
    setMode("landing");
  }

  if (mode === "landing") return <Landing onLogin={() => setMode("login")}/>;
  if (mode === "login") return <Login onSuccess={() => setMode("dashboard")} onBack={() => setMode("landing")}/>;
  return <Dashboard onLogout={logout}/>;
}

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode><App/></React.StrictMode>,
);
