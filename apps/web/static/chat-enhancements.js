(function () {
  const RENDERED_ATTR = "data-operly-markdown-rendered";

  function escapeHtml(value = "") {
    return String(value ?? "").replace(/[&<>"']/g, character => ({
      "&": "&amp;",
      "<": "&lt;",
      ">": "&gt;",
      '"': "&quot;",
      "'": "&#39;",
    })[character]);
  }

  function safeHref(value = "") {
    const href = String(value).trim();
    if (!href) return null;
    if (/^(https?:|mailto:)/i.test(href) || href.startsWith("/") || href.startsWith("#")) return href;
    return null;
  }

  function renderInlineMarkdown(value = "") {
    const codeSpans = [];
    let source = String(value).replace(/`([^`\n]+)`/g, (_match, code) => {
      const index = codeSpans.push(code) - 1;
      return `\uE000${index}\uE001`;
    });

    let html = escapeHtml(source);
    html = html.replace(/\[([^\]]+)\]\(([^)\s]+)(?:\s+&quot;[^&]*&quot;)?\)/g, (_match, label, href) => {
      const safe = safeHref(href);
      if (!safe) return `${label} (${href})`;
      return `<a href="${safe}" target="_blank" rel="noopener noreferrer">${label}</a>`;
    });
    html = html.replace(/\*\*([^*\n]+)\*\*/g, "<strong>$1</strong>");
    html = html.replace(/__([^_\n]+)__/g, "<strong>$1</strong>");
    html = html.replace(/~~([^~\n]+)~~/g, "<del>$1</del>");
    html = html.replace(/(^|[^*])\*([^*\n]+)\*/g, "$1<em>$2</em>");
    html = html.replace(/(^|[^_])_([^_\n]+)_/g, "$1<em>$2</em>");
    html = html.replace(/\uE000(\d+)\uE001/g, (_match, index) => `<code>${escapeHtml(codeSpans[Number(index)] || "")}</code>`);
    return html;
  }

  function tableCells(line) {
    const trimmed = String(line).trim().replace(/^\|/, "").replace(/\|$/, "");
    return trimmed.split("|").map(cell => cell.trim());
  }

  function isTableDivider(line) {
    const cells = tableCells(line);
    return cells.length > 0 && cells.every(cell => /^:?-{3,}:?$/.test(cell));
  }

  function renderMarkdown(value = "") {
    const lines = String(value ?? "").replace(/\r\n?/g, "\n").split("\n");
    const output = [];
    let paragraph = [];
    let listType = null;

    function flushParagraph() {
      if (!paragraph.length) return;
      output.push(`<p>${paragraph.map(renderInlineMarkdown).join("<br>")}</p>`);
      paragraph = [];
    }

    function closeList() {
      if (!listType) return;
      output.push(`</${listType}>`);
      listType = null;
    }

    function openList(type) {
      flushParagraph();
      if (listType === type) return;
      closeList();
      listType = type;
      output.push(`<${type}>`);
    }

    for (let index = 0; index < lines.length; index += 1) {
      const line = lines[index];
      const trimmed = line.trim();

      const fence = trimmed.match(/^```([a-z0-9_+.-]*)\s*$/i);
      if (fence) {
        flushParagraph();
        closeList();
        const code = [];
        index += 1;
        while (index < lines.length && !/^```\s*$/.test(lines[index].trim())) {
          code.push(lines[index]);
          index += 1;
        }
        const language = fence[1] ? ` class="language-${escapeHtml(fence[1])}"` : "";
        output.push(`<pre><code${language}>${escapeHtml(code.join("\n"))}</code></pre>`);
        continue;
      }

      if (!trimmed) {
        flushParagraph();
        closeList();
        continue;
      }

      if (line.includes("|") && index + 1 < lines.length && isTableDivider(lines[index + 1])) {
        flushParagraph();
        closeList();
        const headers = tableCells(line);
        const rows = [];
        index += 2;
        while (index < lines.length && lines[index].trim() && lines[index].includes("|")) {
          rows.push(tableCells(lines[index]));
          index += 1;
        }
        index -= 1;
        output.push(`<div class="ai-markdown-table-wrap"><table><thead><tr>${headers.map(cell => `<th>${renderInlineMarkdown(cell)}</th>`).join("")}</tr></thead><tbody>${rows.map(row => `<tr>${headers.map((_header, cellIndex) => `<td>${renderInlineMarkdown(row[cellIndex] || "")}</td>`).join("")}</tr>`).join("")}</tbody></table></div>`);
        continue;
      }

      const heading = line.match(/^\s*(#{1,6})\s+(.+)$/);
      if (heading) {
        flushParagraph();
        closeList();
        const level = heading[1].length;
        output.push(`<h${level}>${renderInlineMarkdown(heading[2])}</h${level}>`);
        continue;
      }

      if (/^\s*(?:-{3,}|\*{3,}|_{3,})\s*$/.test(line)) {
        flushParagraph();
        closeList();
        output.push("<hr>");
        continue;
      }

      const unordered = line.match(/^\s*[-+*]\s+(.+)$/);
      if (unordered) {
        openList("ul");
        output.push(`<li>${renderInlineMarkdown(unordered[1])}</li>`);
        continue;
      }

      const ordered = line.match(/^\s*\d+[.)]\s+(.+)$/);
      if (ordered) {
        openList("ol");
        output.push(`<li>${renderInlineMarkdown(ordered[1])}</li>`);
        continue;
      }

      const quote = line.match(/^\s*>\s?(.*)$/);
      if (quote) {
        flushParagraph();
        closeList();
        output.push(`<blockquote>${renderInlineMarkdown(quote[1])}</blockquote>`);
        continue;
      }

      closeList();
      paragraph.push(line);
    }

    flushParagraph();
    closeList();
    return output.join("");
  }

  function enhanceMessages(root = document) {
    const elements = [];
    if (root instanceof Element && root.matches(".ai-message")) elements.push(root);
    if (root.querySelectorAll) elements.push(...root.querySelectorAll(".ai-message"));

    for (const element of elements) {
      if (element.hasAttribute(RENDERED_ATTR) || element.classList.contains("ai-working") || element.classList.contains("ai-error-message")) continue;
      const source = element.textContent || "";
      element.innerHTML = renderMarkdown(source);
      element.classList.add("ai-markdown");
      element.setAttribute(RENDERED_ATTR, "1");
    }
  }

  function setComposerStatus(message = "", tone = "") {
    const shell = document.querySelector(".ai-composer-shell");
    const form = document.querySelector("#ai-form");
    if (!shell || !form) return;
    let status = shell.querySelector("#ai-composer-status");
    if (!status) {
      status = document.createElement("div");
      status.id = "ai-composer-status";
      status.className = "ai-composer-status";
      status.setAttribute("role", "status");
      status.setAttribute("aria-live", "polite");
      form.insertAdjacentElement("afterend", status);
    }
    status.className = `ai-composer-status${tone ? ` ${tone}` : ""}`;
    status.textContent = message;
  }

  function voiceErrorMessage(error) {
    const code = typeof error === "string" ? error : (error?.error || error?.name || "unknown");
    if (["not-allowed", "NotAllowedError", "SecurityError"].includes(code)) {
      return "Microphone access is blocked for Operly. Allow microphone access in your browser's site permissions, then try again.";
    }
    if (code === "service-not-allowed") return "This browser is blocking its speech-recognition service. Try another supported browser or use text input.";
    if (["audio-capture", "NotFoundError", "DevicesNotFoundError"].includes(code)) return "No usable microphone was found. Check your microphone connection and browser input settings.";
    if (["network", "NetworkError"].includes(code)) return "Speech recognition could not reach its transcription service. Check your connection and try again.";
    if (code === "no-speech") return "I didn't hear any speech. Tap the microphone and try again.";
    return `Voice transcription failed (${code}). Please try again or use text input.`;
  }

  let activeRecognition = null;
  let activeButton = null;
  let voiceStarting = false;
  let microphoneReady = false;
  let statusTimer = null;

  function clearStatusSoon() {
    clearTimeout(statusTimer);
    statusTimer = setTimeout(() => setComposerStatus(""), 1800);
  }

  async function ensureMicrophonePermission() {
    if (!window.isSecureContext) {
      const error = new Error("Microphone input requires HTTPS.");
      error.name = "SecurityError";
      throw error;
    }
    if (microphoneReady) return;

    if (navigator.permissions?.query) {
      try {
        const permission = await navigator.permissions.query({ name: "microphone" });
        if (permission.state === "denied") {
          const error = new Error("Microphone permission is blocked.");
          error.name = "NotAllowedError";
          throw error;
        }
      } catch (error) {
        if (error?.name === "NotAllowedError") throw error;
      }
    }

    if (navigator.mediaDevices?.getUserMedia) {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      stream.getTracks().forEach(track => track.stop());
    }
    microphoneReady = true;
  }

  function resetVoiceButton() {
    if (activeButton) {
      activeButton.classList.remove("recording", "starting");
      activeButton.setAttribute("aria-label", "Voice input");
      activeButton.title = "Voice input";
    }
  }

  document.addEventListener("click", async event => {
    const button = event.target.closest?.("#ai-mic");
    if (!button) return;

    // Capture the click before ai-assistant.js's legacy listener so permission
    // failures can be handled in the composer instead of a browser alert.
    event.preventDefault();
    event.stopImmediatePropagation();

    if (activeRecognition && button.classList.contains("recording")) {
      activeRecognition.stop();
      return;
    }
    if (voiceStarting) return;

    const input = document.querySelector("#ai-input");
    const Recognition = window.SpeechRecognition || window.webkitSpeechRecognition;
    if (!input || !Recognition) {
      setComposerStatus("Voice transcription isn't available in this browser. You can keep using text input.", "error");
      return;
    }

    voiceStarting = true;
    button.classList.add("starting");
    setComposerStatus("Requesting microphone access…");

    try {
      await ensureMicrophonePermission();
      const recognition = new Recognition();
      recognition.continuous = false;
      recognition.interimResults = true;
      recognition.lang = navigator.language || "en-US";
      const baseText = input.value.trim();
      let failed = false;

      activeRecognition = recognition;
      activeButton = button;

      recognition.onstart = () => {
        voiceStarting = false;
        button.classList.remove("starting");
        button.classList.add("recording");
        button.setAttribute("aria-label", "Stop voice transcription");
        button.title = "Listening… tap to stop";
        setComposerStatus("Listening… speak naturally.", "listening");
      };

      recognition.onresult = resultEvent => {
        let spoken = "";
        for (let index = 0; index < resultEvent.results.length; index += 1) {
          spoken += resultEvent.results[index][0]?.transcript || "";
        }
        input.value = [baseText, spoken.trim()].filter(Boolean).join(baseText ? " " : "");
        input.dispatchEvent(new Event("input", { bubbles: true }));
      };

      recognition.onerror = errorEvent => {
        failed = true;
        setComposerStatus(voiceErrorMessage(errorEvent), "error");
      };

      recognition.onend = () => {
        resetVoiceButton();
        activeRecognition = null;
        activeButton = null;
        voiceStarting = false;
        if (!failed) {
          setComposerStatus("Voice input added to your message.", "success");
          clearStatusSoon();
        }
      };

      recognition.start();
    } catch (error) {
      voiceStarting = false;
      button.classList.remove("starting", "recording");
      setComposerStatus(voiceErrorMessage(error), "error");
    }
  }, true);

  const observer = new MutationObserver(mutations => {
    for (const mutation of mutations) {
      for (const node of mutation.addedNodes) {
        if (node instanceof Element) enhanceMessages(node);
      }
    }
  });

  function start() {
    enhanceMessages(document);
    observer.observe(document.body, { childList: true, subtree: true });
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start, { once: true });
  else start();

  window.operlyChatEnhancements = { renderMarkdown, renderInlineMarkdown };
})();
