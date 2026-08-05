(function () {
  let conversationId = null;

  function escapeHtml(value = "") {
    return String(value).replace(/[&<>"']/g, (character) => ({
      "&": "&amp;",
      "<": "&lt;",
      ">": "&gt;",
      '"': "&quot;",
      "'": "&#39;"
    })[character]);
  }

  function formatDate(value) {
    return new Intl.DateTimeFormat(undefined, {
      month: "short",
      day: "numeric",
      hour: "numeric",
      minute: "2-digit"
    }).format(new Date(value));
  }

  function messageHtml(role, content) {
    return `<div class="ai-message ${role}">${escapeHtml(content)}</div>`;
  }

  async function loadConversations() {
    return api("/agent/conversations");
  }

  async function loadMessages(id) {
    return api(`/agent/conversations/${encodeURIComponent(id)}/messages`);
  }

  async function renderAssistant(openConversationId = null) {
    conversationId = openConversationId;
    const [conversations, applications] = await Promise.all([loadConversations(), api("/application-builder/applications")]);

    document.querySelector("#content").innerHTML = `
      <div class="page-head">
        <div><span class="kicker green">Shared business intelligence</span><h2>OPERLY AI</h2></div>
      </div>
      <div class="ai-layout">
        <aside class="ai-history">
          <div class="ai-history-head"><strong>Conversations</strong><button id="new-ai-chat" class="button secondary">New</button></div>
          <div class="ai-history-list">
            ${conversations.map((item) => `
              <button data-ai-conversation="${item.id}" class="${item.id === conversationId ? "active" : ""}">
                <strong>${escapeHtml(item.title)}</strong><span>${formatDate(item.updated_at)}</span>
              </button>
            `).join("") || `<div class="empty">No conversations yet.</div>`}
          </div>
        </aside>
        <section class="ai-chat">
          <div class="ai-chat-head"><h3>Business agent</h3><p>Same Ollama model and tools used by Discord.</p></div>
          <div id="ai-messages" class="ai-messages">
            <div class="ai-welcome">
              <span class="brand-mark">O</span>
              <h2>What should OPERLY do?</h2>
              <p>Ask questions or execute tenant-scoped business actions.</p>
              <div class="ai-suggestions">
                <button data-ai-suggestion="Give me a business summary">Business summary</button>
                <button data-ai-suggestion="List our open tasks">Open tasks</button>
                <button data-ai-suggestion="Show anything that needs attention">Needs attention</button>
              </div>
            </div>
          </div>
          <div>
            ${applications.length ? `<label class="ai-application-picker">Application<select id="ai-application">${applications.map((item) => `<option value="${escapeHtml(item.id)}">${escapeHtml(item.name)}</option>`).join("")}</select></label>` : ""}
            <form id="ai-form" class="ai-composer">
              <textarea id="ai-input" placeholder="Example: Add Ram as a contact and create a $500 lead." required></textarea>
              <button class="button primary">Send</button>
            </form>
            <div class="ai-security-note">Secrets stay server-side. Every tool call is tenant-bound, validated and audited.</div>
          </div>
        </section>
      </div>
    `;

    if (conversationId) {
      const messages = await loadMessages(conversationId);
      document.querySelector("#ai-messages").innerHTML = messages
        .map((item) => messageHtml(item.role, item.content))
        .join("");
      scrollMessages();
    }

    document.querySelector("#new-ai-chat").addEventListener("click", () => {
      renderAssistant(null);
    });

    document.querySelectorAll("[data-ai-conversation]").forEach((button) => {
      button.addEventListener("click", () => {
        renderAssistant(button.dataset.aiConversation);
      });
    });

    document.querySelectorAll("[data-ai-suggestion]").forEach((button) => {
      button.addEventListener("click", () => {
        document.querySelector("#ai-input").value = button.dataset.aiSuggestion;
        document.querySelector("#ai-input").focus();
      });
    });

    document.querySelector("#ai-form").addEventListener("submit", sendMessage);
  }

  async function sendMessage(event) {
    event.preventDefault();
    const input = document.querySelector("#ai-input");
    const text = input.value.trim();
    if (!text) return;

    const messages = document.querySelector("#ai-messages");
    const welcome = messages.querySelector(".ai-welcome");
    if (welcome) welcome.remove();

    messages.insertAdjacentHTML("beforeend", messageHtml("user", text));
    messages.insertAdjacentHTML(
      "beforeend",
      `<div id="ai-thinking" class="ai-message assistant">Working…</div>`
    );
    input.value = "";
    scrollMessages();

    try {
      const result = await api("/agent/chat", {
        method: "POST",
        body: JSON.stringify({
          message: text,
          conversation_id: conversationId,
          application_id: document.querySelector("#ai-application")?.value || null
        })
      });

      conversationId = result.conversation_id;
      document.querySelector("#ai-thinking").outerHTML =
        messageHtml("assistant", result.message);
      scrollMessages();
    } catch (error) {
      document.querySelector("#ai-thinking").outerHTML =
        messageHtml("assistant", `Request failed: ${error.message}`);
    }
  }

  function scrollMessages() {
    const messages = document.querySelector("#ai-messages");
    messages.scrollTop = messages.scrollHeight;
  }

  window.renderOperlyAssistant = renderAssistant;
})();
