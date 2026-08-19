(function () {
  let conversationId = null;
  let selectedFiles = [];
  let speechRecognition = null;

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

  function renderSelectedFiles() {
    const target = document.querySelector("#ai-attachments");
    if (!target) return;
    target.innerHTML = selectedFiles.map((file, index) => `
      <div class="ai-file-chip">
        <span class="ai-file-icon">${file.type?.startsWith("image/") ? "▧" : "＋"}</span>
        <span><strong>${escapeHtml(file.name)}</strong><small>${Math.max(1, Math.round(file.size / 1024))} KB</small></span>
        <button type="button" data-remove-file="${index}" aria-label="Remove ${escapeHtml(file.name)}">×</button>
      </div>`).join("");
    target.classList.toggle("hidden", !selectedFiles.length);
    target.querySelectorAll("[data-remove-file]").forEach(button => {
      button.addEventListener("click", () => {
        selectedFiles.splice(Number(button.dataset.removeFile), 1);
        renderSelectedFiles();
      });
    });
  }

  function addFiles(files) {
    const incoming = [...files];
    for (const file of incoming) {
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
    const button = document.querySelector("#ai-mic");
    const input = document.querySelector("#ai-input");
    if (!button || !input) return;
    const Recognition = window.SpeechRecognition || window.webkitSpeechRecognition;
    if (!Recognition) {
      button.title = "Voice transcription is not available in this browser";
      button.addEventListener("click", () => alert("Voice transcription is not available in this browser yet. You can still attach files or type your message."));
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
      let finalText = "";
      let interimText = "";
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
      if (event.error !== "aborted" && event.error !== "no-speech") {
        alert(`Voice transcription failed: ${event.error}`);
      }
    };
    speechRecognition.onend = () => {
      button.classList.remove("recording");
      button.setAttribute("aria-label", "Voice input");
      button.title = "Voice input";
    };
    button.addEventListener("click", () => {
      if (button.classList.contains("recording")) speechRecognition.stop();
      else speechRecognition.start();
    });
  }

  function setupComposer() {
    const form = document.querySelector("#ai-form");
    const input = document.querySelector("#ai-input");
    const picker = document.querySelector("#ai-file-input");
    const attach = document.querySelector("#ai-attach");
    if (!form || !input || !picker || !attach) return;

    attach.addEventListener("click", () => picker.click());
    picker.addEventListener("change", () => {
      addFiles(picker.files || []);
      picker.value = "";
    });
    input.addEventListener("keydown", event => {
      if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
        event.preventDefault();
        form.requestSubmit();
      }
    });
    input.addEventListener("input", () => {
      input.style.height = "auto";
      input.style.height = `${Math.min(input.scrollHeight, 180)}px`;
    });
    ["dragenter", "dragover"].forEach(name => form.addEventListener(name, event => {
      event.preventDefault();
      form.classList.add("dragging");
    }));
    ["dragleave", "drop"].forEach(name => form.addEventListener(name, event => {
      event.preventDefault();
      form.classList.remove("dragging");
    }));
    form.addEventListener("drop", event => addFiles(event.dataTransfer?.files || []));
    form.addEventListener("submit", sendMessage);
    setupSpeechInput();
  }

  async function renderAssistant(openConversationId = null) {
    conversationId = openConversationId;
    selectedFiles = [];
    const [conversations, applications] = await Promise.all([loadConversations(), api("/application-builder/applications")]);

    document.querySelector("#content").innerHTML = `
      <div class="page-head ai-page-head">
        <div><span class="kicker green">One Operly across every channel</span><h2>OPERLY AI</h2><p>Private human context + authorized workspace context + this conversation.</p></div>
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
          <div class="ai-chat-head"><div><span class="ai-presence-dot"></span><h3>Ask Operly</h3></div><p>Ask, upload, dictate, inspect, or take an approval-gated action.</p></div>
          <div id="ai-messages" class="ai-messages">
            <div class="ai-welcome">
              <span class="brand-mark">O</span>
              <h2>What should Operly do?</h2>
              <p>The same business agent can carry authorized context between Web and linked communication channels.</p>
              <div class="ai-suggestions">
                <button data-ai-suggestion="Give me a business summary">Business summary</button>
                <button data-ai-suggestion="What needs my attention today?">Needs attention</button>
                <button data-ai-suggestion="Search my connected Gmail for messages I should answer">Check Gmail</button>
                <button data-ai-suggestion="What does my calendar look like this week?">Calendar</button>
              </div>
            </div>
          </div>
          <div class="ai-composer-shell">
            ${applications.length ? `<label class="ai-application-picker">Application<select id="ai-application">${applications.map((item) => `<option value="${escapeHtml(item.id)}">${escapeHtml(item.name)}</option>`).join("")}</select></label>` : ""}
            <div id="ai-attachments" class="ai-attachments hidden"></div>
            <form id="ai-form" class="ai-composer">
              <input id="ai-file-input" type="file" multiple hidden accept="image/*,.pdf,.txt,.md,.csv,.tsv,.json,.xml,.yaml,.yml,.docx,.pptx,.xlsx,.odt,.ods,.html,.log,.css,.sql,.py,.js,.ts,.tsx,.jsx,.java,.c,.cpp,.h,.go,.rs,.rb,.php,.sh">
              <div class="ai-composer-tools">
                <button type="button" id="ai-attach" class="ai-icon-button" aria-label="Attach files" title="Attach files">＋</button>
                <button type="button" id="ai-mic" class="ai-icon-button" aria-label="Voice input" title="Voice input">●</button>
              </div>
              <textarea id="ai-input" rows="1" placeholder="Message Operly…" aria-label="Message Operly"></textarea>
              <button class="ai-send-button" aria-label="Send message" title="Send">↑</button>
            </form>
            <div class="ai-composer-hint"><span>Enter to send · Shift+Enter for a new line</span><span>Files are analyzed as untrusted data.</span></div>
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

    document.querySelector("#new-ai-chat").addEventListener("click", () => renderAssistant(null));
    document.querySelectorAll("[data-ai-conversation]").forEach((button) => {
      button.addEventListener("click", () => renderAssistant(button.dataset.aiConversation));
    });
    document.querySelectorAll("[data-ai-suggestion]").forEach((button) => {
      button.addEventListener("click", () => {
        document.querySelector("#ai-input").value = button.dataset.aiSuggestion;
        document.querySelector("#ai-input").focus();
      });
    });
    setupComposer();
    document.querySelector("#ai-input")?.focus();
  }

  async function multipartChat(text, files) {
    const form = new FormData();
    form.append("message", text);
    if (conversationId) form.append("conversation_id", conversationId);
    const applicationId = document.querySelector("#ai-application")?.value;
    if (applicationId) form.append("application_id", applicationId);
    files.forEach(file => form.append("files", file, file.name));
    const headers = {};
    const csrf = csrfToken("/agent/chat-with-attachments");
    if (csrf) headers["X-CSRF-Token"] = csrf;
    const response = await fetch("/api/agent/chat-with-attachments", {
      method: "POST",
      body: form,
      headers,
      credentials: "same-origin"
    });
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
    const input = document.querySelector("#ai-input");
    const text = input.value.trim();
    const files = [...selectedFiles];
    if (!text && !files.length) return;

    const messages = document.querySelector("#ai-messages");
    messages.querySelector(".ai-welcome")?.remove();
    const display = text || "Uploaded attachment(s)";
    const names = files.length ? `\n${files.map(file => `📎 ${file.name}`).join("\n")}` : "";
    messages.insertAdjacentHTML("beforeend", messageHtml("user", display + names));
    messages.insertAdjacentHTML(
      "beforeend",
      `<div id="ai-thinking" class="ai-message assistant ai-working"><span></span><span></span><span></span></div>`
    );
    input.value = "";
    input.style.height = "auto";
    selectedFiles = [];
    renderSelectedFiles();
    scrollMessages();

    try {
      const result = files.length ? await multipartChat(text, files) : await api("/agent/chat", {
        method: "POST",
        body: JSON.stringify({
          message: text,
          conversation_id: conversationId,
          application_id: document.querySelector("#ai-application")?.value || null
        })
      });

      conversationId = result.conversation_id;
      document.querySelector("#ai-thinking").outerHTML = messageHtml("assistant", result.message);
      scrollMessages();
    } catch (error) {
      const thinking = document.querySelector("#ai-thinking");
      if (thinking) thinking.outerHTML = messageHtml("assistant", `Request failed: ${error.message}`);
    }
  }

  function scrollMessages() {
    const messages = document.querySelector("#ai-messages");
    if (messages) messages.scrollTop = messages.scrollHeight;
  }

  window.renderOperlyAssistant = renderAssistant;
})();
