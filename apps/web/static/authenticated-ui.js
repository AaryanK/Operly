(() => {
  const $ = (selector, root = document) => root.querySelector(selector);
  const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];

  function pruneAssistantApplicationPicker(root = document) {
    const pickers = [];
    if (root instanceof Element && root.matches('.ai-application-picker')) pickers.push(root);
    if (root.querySelectorAll) pickers.push(...root.querySelectorAll('.ai-application-picker'));
    pickers.forEach(node => node.remove());
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
  }

  function repair(root = document) {
    pruneAssistantApplicationPicker(root);
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
          if (node instanceof Element) pruneAssistantApplicationPicker(node);
        }
      }
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
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start, {once: true});
  else start();

  window.operlyAuthenticatedUI = {
    repair,
    closePersonalNavigation: () => setPersonalMobileNavigation(false),
  };
})();
