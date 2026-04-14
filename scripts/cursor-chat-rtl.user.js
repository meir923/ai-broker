// ==UserScript==
// @name         Cursor / VS Code Web — RTL עברית בצ'אט (גיבוי)
// @namespace    local-aibroker
// @version      1.0
// @description  מנסה להחיל כיוון RTL על אלמנטי צ'אט אם Cursor נפתח בדפדפן (נדיר). ב-Cursor שולחני עדיף cursor-chat-rtl.css + Custom CSS Loader.
// @match        *://*/*
// @grant        none
// @run-at       document-idle
// ==/UserScript==

(function () {
  "use strict";

  const STYLE_ID = "aibroker-cursor-chat-rtl";
  const CSS = `
    [class*="interactive-session"] pre,
    [class*="interactive-session"] code,
    .monaco-editor { direction: ltr !important; text-align: left !important; unicode-bidi: isolate !important; }

    [class*="interactive-session"],
    [class*="interactive-item-container"],
    [class*="chat-editor-container"] {
      direction: rtl !important;
      text-align: right !important;
      unicode-bidi: plaintext !important;
    }
  `;

  function inject() {
    if (document.getElementById(STYLE_ID)) return;
    const el = document.createElement("style");
    el.id = STYLE_ID;
    el.textContent = CSS;
    document.documentElement.appendChild(el);
  }

  inject();
  const obs = new MutationObserver(() => inject());
  obs.observe(document.documentElement, { childList: true, subtree: true });
})();
