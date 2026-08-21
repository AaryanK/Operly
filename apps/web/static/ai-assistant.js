(function () {
  let conversationId = null;
  let selectedFiles = [];
  let speechRecognition = null;

  const $ = (selector, root = document) => root.querySelector(selector);
  const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];

  function escapeHtml(value = "") {
    return String(value ?? "").replace(/[&<>"']/g, character => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
    })[character]);
  }

  function asArray(value) { return Array.isArray(value) ? value : []; }
  function asObject(value) { return value && typeof value === "object" && !Array.isArray(value) ? value : {}; }
  function number(value) { const result = Number(value); return Number.isFinite(result) ? result : 0; }
  function formatDate(value) {
    if (!value) return "";
    try { return new Intl.DateTimeFormat(undefined, {month:"short", day:"numeric", hour:"numeric", minute:"2-digit"}).format(new Date(value)); }
    catch { return ""; }
  }
  function formatMoney(value, currency = "USD") {
    try { return new Intl.NumberFormat(undefined, {style:"currency", currency, maximumFractionDigits:0}).format(number(value)); }
    catch { return `${currency} ${number(value).toLocaleString()}`; }
  }
  function initials(value = "") {
    const words = String(value).trim().split(/\s+/).filter(Boolean);
    return (words.length > 1 ? words.slice(0,2).map(word => word[0]).join("") : (words[0] || "O").slice(0,2)).toUpperCase();
  }

  function messageHtml(role, content) {
    return `<div class="ai-message ${role}">${escapeHtml(content)}</div>`;
  }

  async function loadConversations() { return api("/agent/conversations"); }
  async function loadMessages(id) { return api(`/agent/conversations/${encodeURIComponent(id)}/messages`); }

  function renderSelectedFiles() {
    const target = $("#ai-attachments");
    if (!target) return;
    target.innerHTML = selectedFiles.map((file, index) => `
      <div class="ai-file-chip">
        <span class="ai-file-icon">${file.type?.startsWith("image/") ? "▧" : "＋"}</span>
        <span><strong>${escapeHtml(file.name)}</strong><small>${Math.max(1, Math.round(file.size / 1024))} KB</small></span>
        <button type="button" data-remove-file="${index}" aria-label="Remove ${escapeHtml(file.name)}">×</button>
      </div>`).join("");
    target.classList.toggle("hidden", !selectedFiles.length);
    $$('[data-remove-file]', target).forEach(button => button.addEventListener("click", () => {
      selectedFiles.splice(Number(button.dataset.removeFile), 1);
      renderSelectedFiles();
    }));
  }

  function addFiles(files) {
    for (const file of [...files]) {
      if (selectedFiles.length >= 10) break;
      if (file.size > 10 * 1024 * 1024) {
        alert(`${file.name} is larger than the 10 MB per-file limit.`);
        continue;
      }
      const duplicate = selectedFiles.some(item => item.name === file.name && item.size === file.size && item.lastModified === file.lastModified);
      if (!duplicate) selectedFiles.push(file);
    }
    renderSelectedFiles();
  }

  function setupSpeechInput() {
    const button = $("#ai-mic");
    const input = $("#ai-input");
    if (!button || !input) return;
    const Recognition = window.SpeechRecognition || window.webkitSpeechRecognition;
    if (!Recognition) {
      button.title = "Voice transcription is not available in this browser";
      button.addEventListener("click", () => alert("Voice transcription is not available in this browser yet."));
      return;
    }

    speechRecognition = new Recognition();
    speechRecognition.continuous = false;
    speechRecognition.interimResults = true;
    speechRecognition.lang = navigator.language || "en-US";
    let baseText = "";

    speechRecognition.onstart = () => {
      baseText = input.value.trim();
      button.classList.add("recording");
      button.setAttribute("aria-label", "Stop voice transcription");
      button.title = "Listening… tap to stop";
    };
    speechRecognition.onresult = event => {
      let finalText = "", interimText = "";
      for (let index = event.resultIndex; index < event.results.length; index += 1) {
        const transcript = event.results[index][0]?.transcript || "";
        if (event.results[index].isFinal) finalText += transcript;
        else interimText += transcript;
      }
      const spoken = `${finalText}${interimText}`.trim();
      input.value = [baseText, spoken].filter(Boolean).join(baseText ? " " : "");
      input.dispatchEvent(new Event("input"));
    };
    speechRecognition.onerror = event => {
      if (!["aborted", "no-speech"].includes(event.error)) alert(`Voice transcription failed: ${event.error}`);
    };
    speechRecognition.onend = () => {
      button.classList.remove("recording");
      button.setAttribute("aria-label", "Voice input");
      button.title = "Voice input";
    };
    button.addEventListener("click", () => button.classList.contains("recording") ? speechRecognition.stop() : speechRecognition.start());
  }

  function setupComposer() {
    const form = $("#ai-form"), input = $("#ai-input"), picker = $("#ai-file-input"), attach = $("#ai-attach");
    if (!form || !input || !picker || !attach) return;
    attach.addEventListener("click", () => picker.click());
    picker.addEventListener("change", () => { addFiles(picker.files || []); picker.value = ""; });
    input.addEventListener("keydown", event => {
      if (event.key === "Enter" && !event.shiftKey && !event.isComposing) { event.preventDefault(); form.requestSubmit(); }
    });
    input.addEventListener("input", () => {
      input.style.height = "auto";
      input.style.height = `${Math.min(input.scrollHeight, 180)}px`;
    });
    ["dragenter", "dragover"].forEach(name => form.addEventListener(name, event => { event.preventDefault(); form.classList.add("dragging"); }));
    ["dragleave", "drop"].forEach(name => form.addEventListener(name, event => { event.preventDefault(); form.classList.remove("dragging"); }));
    form.addEventListener("drop", event => addFiles(event.dataTransfer?.files || []));
    form.addEventListener("submit", sendMessage);
    setupSpeechInput();
  }

  function welcomeMarkup(snapshot, tasks, approvals, workspaceName) {
    const counts = asObject(snapshot.counts);
    const profile = asObject(snapshot.profile);
    const recentMessages = asArray(snapshot.recent_messages);
    const pending = approvals.filter(item => item.status === "pending").length || number(counts.pending_approvals);
    const openTasks = tasks.filter(item => item.status !== "completed").length || number(counts.open_tasks);
    const attention = number(counts.overdue_tasks) + number(counts.stale_leads) + number(counts.low_stock) + pending;
    const currency = profile.currency || "USD";
    const businessName = profile.trading_name || profile.legal_name || profile.display_name || workspaceName || "your workspace";

    return `
      <div class="ai-welcome ai-command-center">
        <div class="ai-welcome-heading">
          <img class="ai-orb" src="/static/operly-logo.png" alt="Operly logo">
          <div>
            <span class="ai-eyebrow">OPERLY · ${escapeHtml(businessName)}</span>
            <h2>What should we work on?</h2>
            <p>I can use the context and tools you’re authorized to access, then surface anything that still needs your approval.</p>
          </div>
        </div>

        <div class="ai-pulse-grid">
          <button data-ai-suggestion="What needs my attention right now?" class="ai-pulse-card ${attention ? "attention" : ""}"><span>Needs attention</span><strong>${attention}</strong><small>${attention ? "Review the exceptions" : "Workspace looks clear"}</small></button>
          <button data-ai-suggestion="Show me my open tasks and help me prioritize them" class="ai-pulse-card"><span>Open tasks</span><strong>${openTasks}</strong><small>${number(counts.overdue_tasks)} overdue</small></button>
          <button data-ai-suggestion="Summarize my current sales pipeline" class="ai-pulse-card"><span>Pipeline</span><strong>${escapeHtml(formatMoney(snapshot.pipeline_value, currency))}</strong><small>${number(counts.stale_leads)} stalled leads</small></button>
          <button data-ai-suggestion="Show me the actions waiting for my approval" class="ai-pulse-card"><span>Approvals</span><strong>${pending}</strong><small>${pending ? "Waiting for your decision" : "Nothing waiting"}</small></button>
        </div>

        <div class="ai-command-grid">
          <button data-ai-suggestion="Give me a concise business summary and tell me what changed recently"><span class="ai-command-icon">↗</span><span><strong>Business summary</strong><small>What changed and what matters</small></span></button>
          <button data-ai-suggestion="What needs my attention today? Rank it by urgency"><span class="ai-command-icon">!</span><span><strong>Prioritize my day</strong><small>Rank work by urgency</small></span></button>
          <button data-ai-suggestion="Search my connected Gmail for messages I should answer"><span class="ai-command-icon">✉</span><span><strong>Check Gmail</strong><small>Find messages worth answering</small></span></button>
          <button data-ai-suggestion="What does my calendar look like this week?"><span class="ai-command-icon">□</span><span><strong>Calendar</strong><small>See the week ahead</small></span></button>
          <button data-ai-suggestion="Review my leads and tell me who I should follow up with"><span class="ai-command-icon">◎</span><span><strong>Follow up leads</strong><small>Find the next sales action</small></span></button>
          <button data-ai-build><span class="ai-command-icon">＋</span><span><strong>Build a solution</strong><small>Website, workflow, app or tool</small></span></button>
        </div>

        ${recentMessages.length ? `<div class="ai-recent-context"><div class="ai-recent-head"><span>Recent workspace activity</span><small>${recentMessages.length} captured</small></div>${recentMessages.slice(-3).reverse().map(item => `<div class="ai-recent-row"><div class="ai-recent-avatar">${escapeHtml(initials(item.author || "?"))}</div><div><strong>${escapeHtml(item.author || "Unknown")}</strong><p>${escapeHtml(item.content || "")}</p></div></div>`).join("")}</div>` : ""}
      </div>`;
  }

  async function renderAssistant(openConversationId = null) {
    conversationId = openConversationId;
    selectedFiles = [];

    const [conversationsResult, applicationsResult, snapshotResult, tasksResult, approvalsResult] = await Promise.allSettled([
      loadConversations(), api("/application-builder/applications"), api("/operations/snapshot"), api("/tasks"), api("/approvals")
    ]);
    const conversations = conversationsResult.status === "fulfilled" ? asArray(conversationsResult.value) : [];
    const applications = applicationsResult.status === "fulfilled" ? asArray(applicationsResult.value) : [];
    const snapshot = snapshotResult.status === "fulfilled" ? asObject(snapshotResult.value) : {};
    const tasks = tasksResult.status === "fulfilled" ? asArray(tasksResult.value) : [];
    const approvals = approvalsResult.status === "fulfilled" ? asArray(approvalsResult.value) : [];
    const workspaceName = $("#operly-workspace-title")?.textContent || $("#workspace-name")?.textContent || "Workspace";
    const hasHistory = conversations.length > 0;

    const target = $("#content");
    if (!target) return;
    target.innerHTML = `
      <div class="ai-layout ${hasHistory ? "" : "ai-no-history"}">
        ${hasHistory ? `<aside class="ai-history">
          <div class="ai-history-head"><div><span class="ai-history-kicker">History</span><strong>Conversations</strong></div><button id="new-ai-chat" class="ai-new-chat">＋</button></div>
          <div class="ai-history-list">
            ${conversations.map(item => `<button data-ai-conversation="${escapeHtml(item.id)}" class="${item.id === conversationId ? "active" : ""}"><span class="ai-history-icon">✦</span><span><strong>${escapeHtml(item.title || "Conversation")}</strong><small>${formatDate(item.updated_at)}</small></span></button>`).join("")}
          </div>
        </aside>` : ""}
        <section class="ai-chat">
          <div class="ai-chat-head"><div><span class="ai-presence-dot"></span><div><h3>Operly</h3><small>Authorized workspace intelligence</small></div></div><div class="ai-chat-head-actions">${hasHistory ? `<button id="ai-new-inline" class="ai-head-button">New chat</button>` : ""}<span class="ai-context-pill">${escapeHtml(workspaceName)}</span></div></div>
          <div id="ai-messages" class="ai-messages">
            ${welcomeMarkup(snapshot, tasks, approvals, workspaceName)}
          </div>
          <div class="ai-composer-shell">
            ${applications.length ? `<label class="ai-application-picker"><span>Use with</span><select id="ai-application">${applications.map(item => `<option value="${escapeHtml(item.id)}">${escapeHtml(item.name)}</option>`).join("")}</select></label>` : ""}
            <div id="ai-attachments" class="ai-attachments hidden"></div>
            <form id="ai-form" class="ai-composer">
              <input id="ai-file-input" type="file" multiple hidden accept="image/*,.pdf,.txt,.md,.csv,.tsv,.json,.xml,.yaml,.yml,.docx,.pptx,.xlsx,.odt,.ods,.html,.log,.css,.sql,.py,.js,.ts,.tsx,.jsx,.java,.c,.cpp,.h,.go,.rs,.rb,.php,.sh">
              <div class="ai-composer-tools">
                <button type="button" id="ai-attach" class="ai-icon-button" aria-label="Attach files" title="Attach files">＋</button>
                <button type="button" id="ai-mic" class="ai-icon-button" aria-label="Voice input" title="Voice input">●</button>
              </div>
              <textarea id="ai-input" rows="1" placeholder="Ask Operly anything, or tell it what to do…" aria-label="Message Operly"></textarea>
              <button class="ai-send-button" aria-label="Send message" title="Send">↑</button>
            </form>
            <div class="ai-composer-hint"><span>Enter to send · Shift+Enter for a new line</span><span>Actions remain permission- and approval-gated.</span></div>
          </div>
        </section>
      </div>`;

    if (conversationId) {
      try {
        const messages = await loadMessages(conversationId);
        $("#ai-messages").innerHTML = asArray(messages).map(item => messageHtml(item.role, item.content)).join("") || welcomeMarkup(snapshot, tasks, approvals, workspaceName);
        scrollMessages();
      } catch (error) {
        $("#ai-messages").innerHTML = `<div class="ai-load-error"><strong>Could not load this conversation.</strong><span>${escapeHtml(error.message)}</span></div>`;
      }
    }

    $("#new-ai-chat")?.addEventListener("click", () => renderAssistant(null));
    $("#ai-new-inline")?.addEventListener("click", () => renderAssistant(null));
    $$('[data-ai-conversation]').forEach(button => button.addEventListener("click", () => renderAssistant(button.dataset.aiConversation)));
    $$('[data-ai-suggestion]').forEach(button => button.addEventListener("click", () => {
      const input = $("#ai-input"); input.value = button.dataset.aiSuggestion; input.focus(); input.dispatchEvent(new Event("input"));
    }));
    $$('[data-ai-build]').forEach(button => button.addEventListener("click", () => {
      if (window.operlyWorkspaceShell?.navigate) window.operlyWorkspaceShell.navigate("solutions");
      else if (typeof window.operlyOpenBuild === "function") window.operlyOpenBuild();
    }));
    setupComposer();
    $("#ai-input")?.focus();
  }

  async function multipartChat(text, files) {
    const form = new FormData();
    form.append("message", text);
    if (conversationId) form.append("conversation_id", conversationId);
    const applicationId = $("#ai-application")?.value;
    if (applicationId) form.append("application_id", applicationId);
    files.forEach(file => form.append("files", file, file.name));
    const headers = {};
    const csrf = csrfToken("/agent/chat-with-attachments");
    if (csrf) headers["X-CSRF-Token"] = csrf;
    const response = await fetch("/api/agent/chat-with-attachments", {method:"POST", body:form, headers, credentials:"same-origin"});
    let body = null;
    try { body = await response.json(); } catch {}
    if (!response.ok) {
      const detail = body?.detail;
      throw new Error(typeof detail === "string" ? detail : detail?.message || `Request failed (${response.status})`);
    }
    return body;
  }

  async function sendMessage(event) {
    event.preventDefault();
    const input = $("#ai-input");
    const text = input.value.trim();
    const files = [...selectedFiles];
    if (!text && !files.length) return;

    const messages = $("#ai-messages");
    messages.querySelector(".ai-welcome")?.remove();
    const display = text || "Uploaded attachment(s)";
    const names = files.length ? `\n${files.map(file => `📎 ${file.name}`).join("\n")}` : "";
    messages.insertAdjacentHTML("beforeend", messageHtml("user", display + names));
    messages.insertAdjacentHTML("beforeend", `<div id="ai-thinking" class="ai-message assistant ai-working"><span></span><span></span><span></span></div>`);
    input.value = ""; input.style.height = "auto"; input.disabled = true;
    const send = $(".ai-send-button"); if (send) send.disabled = true;
    selectedFiles = []; renderSelectedFiles(); scrollMessages();

    try {
      const result = files.length ? await multipartChat(text, files) : await api("/agent/chat", {
        method:"POST",
        body:JSON.stringify({message:text, conversation_id:conversationId, application_id:$("#ai-application")?.value || null})
      });
      conversationId = result.conversation_id;
      const thinking = $("#ai-thinking"); if (thinking) thinking.outerHTML = messageHtml("assistant", result.message || "Done.");
      scrollMessages();
    } catch (error) {
      const thinking = $("#ai-thinking");
      if (thinking) thinking.outerHTML = `<div class="ai-message assistant ai-error-message"><strong>Operly couldn’t complete that request.</strong>\n${escapeHtml(error.message)}</div>`;
    } finally {
      input.disabled = false; if (send) send.disabled = false; input.focus();
    }
  }

  function scrollMessages() {
    const messages = $("#ai-messages");
    if (messages) messages.scrollTop = messages.scrollHeight;
  }

  window.renderOperlyAssistant = renderAssistant;
})();
