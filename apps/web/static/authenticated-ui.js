(() => {
  const $ = (selector, root = document) => root.querySelector(selector);
  const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];

  function pruneAssistantApplicationPicker(root = document) {
    const pickers = [];
    if (root instanceof Element && root.matches('.ai-application-picker')) pickers.push(root);
    if (root.querySelectorAll) pickers.push(...root.querySelectorAll('.ai-application-picker'));
    pickers.forEach(node => node.remove());
  }

  function prunePersonalScopePicker(root = document) {
    const personal = root instanceof Element && root.matches('#personal.personal-home')
      ? root
      : root.querySelector?.('#personal.personal-home') || $('#personal.personal-home');
    if (!personal) return;
    const compose = $('.personal-compose', personal);
    if (!compose) return;
    compose.querySelector('label[for="personal-workspace-select"]')?.remove();
    compose.querySelector('#personal-workspace-select')?.remove();
    compose.querySelector('.compose-context-note')?.remove();
  }

  function enhancePersonalMessages(root = document) {
    const renderer = window.operlyChatEnhancements?.renderMarkdown;
    if (typeof renderer !== 'function') return false;
    const messages = [];
    if (root instanceof Element && root.matches('.personal-message.assistant')) messages.push(root);
    if (root.querySelectorAll) messages.push(...root.querySelectorAll('.personal-message.assistant'));
    for (const message of messages) {
      if (message.dataset.personalMarkdownRendered === '1') continue;
      const body = $('.personal-message-body', message);
      const paragraph = body?.querySelector(':scope > p');
      if (!paragraph) continue;
      const block = document.createElement('div');
      block.className = 'personal-message-markdown ai-markdown';
      block.innerHTML = renderer(paragraph.textContent || '');
      paragraph.replaceWith(block);
      message.dataset.personalMarkdownRendered = '1';
    }
    return true;
  }

  function stabilizePersonalConversationLayout() {
    const personal = $('#personal.personal-home');
    if (!personal || personal.classList.contains('hidden')) return;
    const main = $('.personal-panel:not(.personal-side)', personal);
    const messages = $('#personal-messages', personal);
    const compose = $('.personal-compose', personal);
    if (!main || !messages || !compose) return;

    const phone = window.innerWidth <= 700;
    main.style.setProperty('display', 'grid', 'important');
    main.style.setProperty('grid-template-rows', 'auto minmax(0, 1fr) auto', 'important');
    main.style.setProperty('height', phone ? 'calc(100dvh - 64px)' : '100dvh', 'important');
    main.style.setProperty('min-height', '0', 'important');
    main.style.setProperty('overflow', 'hidden', 'important');

    messages.style.setProperty('min-height', '0', 'important');
    messages.style.setProperty('height', 'auto', 'important');
    messages.style.setProperty('overflow-y', 'auto', 'important');
    messages.style.setProperty('padding-bottom', phone ? '16px' : '24px', 'important');

    compose.style.setProperty('position', 'static', 'important');
    compose.style.setProperty('left', 'auto', 'important');
    compose.style.setProperty('right', 'auto', 'important');
    compose.style.setProperty('bottom', 'auto', 'important');
    compose.style.setProperty('width', '100%', 'important');
    compose.style.setProperty('border-top', '1px solid var(--ui-line)', 'important');
    compose.style.setProperty('background', '#fff', 'important');
    compose.style.setProperty('padding', phone ? '10px 10px 12px' : '12px max(28px,calc((100% - 920px)/2)) 18px', 'important');
  }

  function ensurePersonalMobileNavigation() {
    const personal = $('#personal.personal-home');
    if (!personal) return;
    const headerTitle = $('.personal-channel-title', personal);
    if (headerTitle && !$('.personal-mobile-nav-toggle', headerTitle)) {
      const button = document.createElement('button');
      button.type = 'button';
      button.className = 'personal-mobile-nav-toggle';
      button.setAttribute('aria-label', 'Open Personal navigation');
      button.setAttribute('aria-expanded', 'false');
      button.textContent = '☰';
      headerTitle.insertBefore(button, headerTitle.firstChild);
      button.addEventListener('click', () => setPersonalMobileNavigation(!personal.classList.contains('personal-mobile-nav-open')));
    }
    if (!$('.personal-mobile-backdrop', personal)) {
      const backdrop = document.createElement('button');
      backdrop.type = 'button';
      backdrop.className = 'personal-mobile-backdrop';
      backdrop.setAttribute('aria-label', 'Close Personal navigation');
      backdrop.addEventListener('click', () => setPersonalMobileNavigation(false));
      personal.appendChild(backdrop);
    }
    $$('.personal-side [data-conversation-id],.personal-side [data-account-tab],.personal-side [data-new-personal-chat]', personal).forEach(button => {
      if (button.dataset.mobileCloseBound === '1') return;
      button.dataset.mobileCloseBound = '1';
      button.addEventListener('click', () => setPersonalMobileNavigation(false));
    });
  }

  function setPersonalMobileNavigation(open) {
    const personal = $('#personal.personal-home');
    if (!personal) return;
    personal.classList.toggle('personal-mobile-nav-open', !!open);
    $('.personal-mobile-nav-toggle', personal)?.setAttribute('aria-expanded', String(!!open));
    document.body.classList.toggle('personal-mobile-nav-active', !!open);
  }

  function repairResponsiveState() {
    if (window.innerWidth > 1024) {
      const dashboard = $('#dashboard.workspace-shell-ready');
      dashboard?.classList.remove('operly-mobile-nav-open');
      $('#mobile-nav-toggle')?.setAttribute('aria-expanded', 'false');
    }
    if (window.innerWidth > 860) setPersonalMobileNavigation(false);
    stabilizePersonalConversationLayout();
  }

  function repair(root = document) {
    pruneAssistantApplicationPicker(root);
    prunePersonalScopePicker(root);
    enhancePersonalMessages(root);
    stabilizePersonalConversationLayout();
    ensurePersonalMobileNavigation();
  }

  let scheduled = false;
  const observer = new MutationObserver(mutations => {
    if (scheduled) return;
    scheduled = true;
    requestAnimationFrame(() => {
      scheduled = false;
      for (const mutation of mutations) {
        for (const node of mutation.addedNodes) {
          if (!(node instanceof Element)) continue;
          pruneAssistantApplicationPicker(node);
          enhancePersonalMessages(node);
        }
      }
      prunePersonalScopePicker();
      stabilizePersonalConversationLayout();
      ensurePersonalMobileNavigation();
    });
  });

  function start() {
    repair();
    observer.observe(document.documentElement, {childList: true, subtree: true});
    window.addEventListener('resize', repairResponsiveState, {passive: true});
    window.addEventListener('keydown', event => {
      if (event.key === 'Escape') setPersonalMobileNavigation(false);
    });
    let markdownAttempts = 0;
    const markdownTimer = window.setInterval(() => {
      markdownAttempts += 1;
      const ready = enhancePersonalMessages();
      if (ready || markdownAttempts >= 12) window.clearInterval(markdownTimer);
    }, 150);
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start, {once: true});
  else start();

  window.operlyAuthenticatedUI = {
    repair,
    closePersonalNavigation: () => setPersonalMobileNavigation(false),
  };
})();
