/* Standalone mockups: sample data only. Never load app.js or manuscript APIs. */
(() => {
  "use strict";

  const $ = (id) => document.getElementById(id);
  const designs = ["studio", "editorial", "focus"];
  const surfaces = ["paper", "flat"];
  const state = {
    design: "studio", surface: "paper", language: "ko", scene: 4,
    section: "manuscript", codex: "characters", inspector: "check",
    inspectorOpen: true, navOpen: !matchMedia("(max-width: 700px)").matches,
    compare: false, font: "default", size: "17", leading: "1.95",
    timerRemaining: 25 * 60, timerRunning: false, timerEnd: 0,
    note: "", dictionary: {},
  };
  const dictionaries = new Map();
  const sceneCounts = [5412, 5108, 4870, 3284, 820];
  const icons = {
    paper: '<path d="M7 3h7l4 4v14H6V3z"/><path d="M14 3v5h4M9 12h6M9 16h6"/>',
    flat: '<rect x="3" y="5" width="18" height="14" rx="2"/><path d="M7 9h10M7 13h10"/>',
    grid: '<rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/><rect x="14" y="14" width="7" height="7" rx="1"/>',
    chevron: '<path d="m9 5 7 7-7 7"/>',
    down: '<path d="m6 9 6 6 6-6"/>',
    folder: '<path d="M3 7V5a2 2 0 0 1 2-2h5l2 3h7a2 2 0 0 1 2 2v11H3z"/>',
    book: '<path d="M12 5c-3-2-6-2-9-1v15c3-1 6-1 9 1 3-2 6-2 9-1V4c-3-1-6-1-9 1zm0 0v15"/>',
    people: '<circle cx="9" cy="7" r="3"/><path d="M3 21v-3a6 6 0 0 1 12 0v3M16 4a3 3 0 0 1 0 6M19 21v-3a5 5 0 0 0-3-4"/>',
    globe: '<circle cx="12" cy="12" r="9"/><ellipse cx="12" cy="12" rx="4" ry="9"/><path d="M3 12h18"/>',
    idea: '<path d="M9 18h6M10 21h4M8 14a6 6 0 1 1 8 0c-1 1-1 2-1 2H9s0-1-1-2z"/>',
    note: '<path d="M20 12v8H4V4h8M10 14l1-4 8-8 3 3-8 8z"/>',
    check: '<path d="m5 12 4 4L19 6"/>',
    focus: '<path d="M8 3H3v5M16 3h5v5M21 16v5h-5M8 21H3v-5"/>',
    panel: '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M15 4v16"/>',
    nav: '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M9 4v16"/>',
    close: '<path d="m6 6 12 12M18 6 6 18"/>',
    lock: '<rect x="5" y="10" width="14" height="11" rx="2"/><path d="M8 10V7a4 4 0 0 1 8 0v3"/>',
    play: '<path d="m8 4 12 8-12 8z"/>',
    pause: '<path d="M8 4v16M16 4v16"/>',
    quote: '<path d="M10 6H4v7h6V6zm0 7c0 4-2 5-5 6M21 6h-6v7h6V6zm0 7c0 4-2 5-5 6"/>',
    link: '<path d="m9 15 6-6M8 16l-1 1a4 4 0 0 1-6-6l4-4a4 4 0 0 1 6 0M16 8l1-1a4 4 0 0 1 6 6l-4 4a4 4 0 0 1-6 0" transform="translate(1 0) scale(.9)"/>',
  };
  const escape = (value) => String(value).replace(/[&<>"']/g, (char) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  })[char]);
  const t = (key, params = {}) => {
    let result = state.dictionary[`designV3.${key}`] ?? key;
    for (const [name, value] of Object.entries(params)) result = result.replaceAll(`{${name}}`, String(value));
    return result;
  };
  const label = (key, params) => escape(t(key, params));
  const icon = (name) => `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.45" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${icons[name] || icons.paper}</svg>`;
  const numeric = (number) => new Intl.NumberFormat(state.language).format(number);
  const iconButton = (name, key, action, pressed) => `<button type="button" class="icon-button" data-action="${action}" title="${label(key)}" aria-label="${label(key)}"${pressed === undefined ? "" : ` aria-pressed="${pressed}"`}>${icon(name)}</button>`;

  function translateShell() {
    document.documentElement.lang = state.language;
    document.title = t("title");
    $("language").value = state.language;
    document.querySelectorAll("[data-i18n]").forEach((element) => {
      element.textContent = state.dictionary[element.dataset.i18n] || element.dataset.i18n;
    });
    document.querySelectorAll("[data-i18n-aria-label]").forEach((element) => {
      element.setAttribute("aria-label", state.dictionary[element.dataset.i18nAriaLabel]);
    });
    document.querySelectorAll("[data-icon]").forEach((element) => { element.innerHTML = icon(element.dataset.icon); });
  }

  function renderBinder() {
    const scenes = sceneCounts.map((count, index) => {
      const number = index + 1;
      const status = number < 3 ? "complete" : number === 3 ? "revision" : "draft";
      return `<button type="button" class="scene-button" data-scene="${number}"${state.section === "manuscript" && state.scene === number ? ' aria-current="page"' : ""}>
        <span class="scene-number">${String(number).padStart(2, "0")}</span><span class="scene-title">${label(`scene${number}`)}</span>
        <span class="scene-meta"><i class="status-dot ${status}" aria-hidden="true"></i>${label(status)}<span>·</span>${label("chars", { n: numeric(count) })}</span></button>`;
    }).join("");
    const settings = [["people", "characters", "12"], ["globe", "world", "4"], ["link", "foreshadowing", "7"], ["idea", "ideas", "23"]]
      .map(([name, key, count]) => `<button type="button" class="small-link" data-codex="${key}">${icon(name)}<span>${label(key)}</span><span class="count">${count}</span></button>`).join("");
    return `<aside class="binder" aria-label="${label("binder")}">
      <div class="binder-tabs"><button type="button" data-section="manuscript" aria-pressed="${state.section === "manuscript"}">${label("manuscript")}</button><button type="button" data-section="codex" aria-pressed="${state.section === "codex"}">${label("codex")}</button></div>
      <div class="binder-scroll"><div class="project-caption">${label("novel")} / ${label("fantasy")}</div><h2 class="binder-title">${label("project")}</h2><p class="project-meta">${label("projectMeta")}</p>
        <div class="binder-section"><strong>${label(state.section === "codex" ? "codex" : "episodes")}</strong>${icon(state.section === "codex" ? "book" : "folder")}</div>
        ${state.section === "manuscript" ? `<div class="chapter-heading">${icon("down")}<span>${label("chapter")}</span></div><nav class="scene-list" aria-label="${label("episodes")}">${scenes}</nav><div class="binder-section"><strong>${label("quickReferences")}</strong></div>` : ""}
        <div class="setting-links">${settings}</div>
      </div>
      <div class="binder-footer"><div><span>${label("writingProgress")}</span><span>65%</span></div><div class="thin-progress" role="img" aria-label="${label("progressLabel")}"><span></span></div></div>
    </aside>`;
  }

  function renderManuscript() {
    if (state.section === "codex") {
      const key = state.codex;
      return `<article class="manuscript"><div class="manuscript-overline">${label("codex")}${icon("chevron")}${label(key)}</div><h1 class="manuscript-title">${label(key)}</h1><div class="manuscript-meta">${label("referenceNotice")}</div><div class="codex-list">${[1, 2, 3].map((n) => `<div class="codex-item"><strong>${label(`${key}${n}Title`)}</strong><p>${label(`${key}${n}Body`)}</p></div>`).join("")}</div></article>`;
    }
    const paragraphKeys = state.scene === 4 ? [1, 2, 3, 4, 5, 6, 7] : [1, 2, 3];
    const paragraphs = paragraphKeys.map((n) => `<p${(state.scene === 4 && [3, 4, 7].includes(n)) ? ' class="dialogue"' : ""}>${label(`excerpt${state.scene}_${n}`)}</p>`).join("");
    return `<article class="manuscript" aria-label="${label("sampleManuscript")}">
      <div class="manuscript-overline"><span>${label("chapter")}</span>${icon("chevron")}<span>${label("episodeNumber", { n: state.scene })}</span></div>
      <h1 class="manuscript-title">${label(`scene${state.scene}`)}</h1>
      <div class="manuscript-meta"><span class="draft-tag"><i class="status-dot ${state.scene < 3 ? "complete" : "draft"}" aria-hidden="true"></i>${label(state.scene < 3 ? "complete" : state.scene === 3 ? "revision" : "draft")}</span><span>${label("pov")}: ${label("characterName")}</span><span>·</span><span>${label("location")}</span></div>
      <div class="manuscript-copy" tabindex="0" aria-label="${label("sampleManuscript")}">${paragraphs}</div><div class="page-number">— 1 —</div>
    </article>`;
  }

  function renderEditor() {
    const title = state.section === "manuscript" ? t(`scene${state.scene}`) : t(state.codex);
    const defaultFont = state.design === "editorial" ? "serif" : "sans";
    const effectiveFont = state.font === "default" ? defaultFont : state.font;
    return `<section class="editor" aria-label="${label("writingArea")}">
      <div class="document-tabs"><div class="document-tab active">${icon(state.section === "manuscript" ? "paper" : "book")}<span>${escape(title)}</span></div>
        <button type="button" class="document-tab secondary" data-codex="characters">${icon("people")}<span>${label("characterName")} · ${label("characters")}</span></button>
        <div class="tab-trailing">${iconButton("nav", "toggleBinder", "nav", state.navOpen)}${iconButton("focus", "toggleFocus", "focus", !state.navOpen && !state.inspectorOpen)}</div>
      </div>
      <div class="writing-toolbar" role="group" aria-label="${label("typography")}">
        <select id="fontSelect" aria-label="${label("font")}"><option value="serif"${effectiveFont === "serif" ? " selected" : ""}>${label("serifFont")}</option><option value="sans"${effectiveFont === "sans" ? " selected" : ""}>Pretendard</option></select>
        <select id="sizeSelect" aria-label="${label("fontSize")}">${[15, 16, 17, 18].map((size) => `<option value="${size}"${state.size === String(size) ? " selected" : ""}>${size} px</option>`).join("")}</select>
        <select id="leadingSelect" aria-label="${label("lineSpacing")}">${["1.8", "1.95", "2.1"].map((leading) => `<option value="${leading}"${state.leading === leading ? " selected" : ""}>${label("leading", { n: leading })}</option>`).join("")}</select>
        <span class="toolbar-divider" aria-hidden="true"></span><div class="format-buttons" aria-hidden="true"><span><b>B</b></span><span><i>I</i></span><span><u>U</u></span></div>
        <div class="toolbar-right"><button type="button" class="text-button" data-action="note" title="${label("authorNote")}">${icon("note")}<span>${label("note")}</span></button><button type="button" class="text-button" data-action="inspector" title="${label("toggleHelper")}" aria-label="${label("toggleHelper")}" aria-pressed="${state.inspectorOpen}">${icon("panel")}<span>${label("helper")}</span></button></div>
      </div>
      <div class="desk">${renderManuscript()}</div>
      <div class="editor-footer"><span><strong>${numeric(sceneCounts[state.scene - 1])}</strong> ${label("characterUnit")}</span><span class="secondary-stat">${label("todayGoal")} <strong>5,000</strong></span><span class="right">${icon("lock")}${label("readOnly")}</span></div>
    </section>`;
  }

  function timeText() {
    return `${String(Math.floor(state.timerRemaining / 60)).padStart(2, "0")}:${String(state.timerRemaining % 60).padStart(2, "0")}`;
  }

  function renderInspectorBody() {
    if (state.inspector === "note") return `<label class="note-label" for="sampleNote">${label("authorNote")}</label><textarea class="sample-note" id="sampleNote" placeholder="${label("notePlaceholder")}">${escape(state.note)}</textarea><p class="note-hint">${label("noteHint")}</p>`;
    if (state.inspector === "record") return `<h3 class="section-title">${label("todayWriting")}</h3><div class="record-list"><div class="record-item"><span>${label("writtenCharacters")}</span><strong>${label("chars", { n: "1,904" })}</strong></div><div class="record-item"><span>${label("writingTime")}</span><strong>${label("timeSummary")}</strong></div><div class="record-item"><span>${label("focusSessions")}</span><strong>${label("sessions")}</strong></div></div><p class="note-hint">${label("sampleStats")}</p>`;
    return `<h3 class="section-title">${label("focusTimer")}<span>${label("timerDuration")}</span></h3>
      <div class="timer-row"><strong class="timer-value" data-timer-display>${timeText()}</strong><button type="button" class="timer-button" data-action="timer">${icon(state.timerRunning ? "pause" : "play")}<span data-timer-label>${label(state.timerRunning ? "pause" : "start")}</span></button></div>
      <div class="session-summary"><div><strong>1,904</strong><span>${label("writtenCharacters")}</span></div><div><strong>${label("shortTimeSummary")}</strong><span>${label("writingTime")}</span></div></div>
      <h3 class="section-title check-heading">${label("toryCheck")}<span>${label("suggestionCount")}</span></h3>
      <div class="check-item"><div class="check-label">${icon("quote")}${label("sentence")}</div><p><q>${label("quotedSentence")}</q><br>${label("sentenceSuggestion")}</p><button type="button" class="check-action" data-action="locate">${label("viewInManuscript")}</button></div>
      <div class="check-item"><div class="check-label">${icon("link")}${label("foreshadowing")}</div><p><q>${label("wetBook")}</q><br>${label("foreshadowingSuggestion")}</p><button type="button" class="check-action" data-codex="foreshadowing">${label("openCodex")}</button></div>
      <p class="quiet-message">${icon("check")}<span>${label("spellcheckClear")}</span></p>`;
  }

  function renderInspector() {
    return `<aside class="inspector" aria-label="${label("helper")}"><div class="inspector-header"><span class="app-symbol" aria-hidden="true">St.</span><strong>SuperTORY</strong>${iconButton("close", "closeHelper", "inspector")}</div>
      <div class="inspector-tabs">${["check", "note", "record"].map((key) => `<button type="button" data-inspector-tab="${key}" aria-pressed="${state.inspector === key}">${label(key)}</button>`).join("")}</div><div class="inspector-body">${renderInspectorBody()}</div><div class="inspector-bottom"><span>${label("gentleHelper")}</span>${icon("idea")}</div></aside>`;
  }

  function renderWorkspace() {
    $("workspace").innerHTML = `<header class="app-header"><button type="button" class="icon-button nav-toggle" data-action="nav" title="${label("toggleBinder")}" aria-label="${label("toggleBinder")}">${icon("nav")}</button><div class="app-brand"><span class="app-symbol" aria-hidden="true">St.</span><span>SuperTory</span></div><span class="header-divider"></span><div class="project-name">${label("project")}${icon("down")}</div><div class="header-right"><span class="local-label">${icon("lock")}${label("localWorkspace")}</span>${iconButton("focus", "toggleFocus", "focus", !state.navOpen && !state.inspectorOpen)}${iconButton("panel", "toggleHelper", "inspector", state.inspectorOpen)}</div></header>${renderBinder()}${renderEditor()}${renderInspector()}<div class="focus-dock"><strong>SuperTORY</strong><span class="dock-divider"></span><span data-timer-display>${timeText()}</span><span class="dock-divider"></span><button type="button" data-action="inspector" aria-expanded="${state.inspectorOpen}">${label("checkCount")}${icon("panel")}</button></div>`;
    applyState();
  }

  function applyState() {
    const root = $("workspace");
    Object.assign(root.dataset, { design: state.design, surface: state.surface, inspector: state.inspectorOpen ? "open" : "closed", nav: state.navOpen ? "open" : "closed" });
    root.style.setProperty("--editor-size", `${state.size}px`);
    root.style.setProperty("--editor-leading", state.leading);
    if (state.font === "default") root.style.removeProperty("--editor-font");
    else root.style.setProperty("--editor-font", state.font === "serif" ? "var(--serif)" : "var(--ui)");
    document.querySelectorAll(".design-switch [data-design]").forEach((button) => { button.setAttribute("aria-pressed", String(button.dataset.design === state.design)); });
    document.querySelectorAll(".surface-switch [data-surface]").forEach((button) => { button.setAttribute("aria-pressed", String(button.dataset.surface === state.surface)); });
    $("designName").textContent = `${String.fromCharCode(65 + designs.indexOf(state.design))} · ${t(state.design)}`;
    $("designDescription").textContent = t(`${state.design}Description`);
    $("compareToggle").setAttribute("aria-expanded", String(state.compare));
    root.hidden = state.compare;
    $("comparison").hidden = !state.compare;
    document.body.classList.toggle("comparing", state.compare);
    if (state.compare) resizeThumbnails();
    const url = new URL(location.href);
    url.hash = `${state.design}-${state.surface}${state.compare ? "-compare" : ""}`;
    if (state.language === "ko") url.searchParams.delete("lang");
    else url.searchParams.set("lang", state.language);
    history.replaceState(null, "", url);
  }

  function renderComparison() {
    $("comparison").innerHTML = `<div class="comparison-intro"><h1>${label("comparisonTitle")}</h1><p>${label("comparisonHint")}</p></div><div class="comparison-grid">${designs.map((design, index) => `<article class="concept-card"><div class="concept-card-head"><span class="design-letter">${String.fromCharCode(65 + index)}</span><h2>${label(design)}</h2></div><p>${label(`${design}Description`)}</p>${surfaces.map((surface) => `<button type="button" class="thumbnail-button" data-preview-design="${design}" data-preview-surface="${surface}" aria-label="${label("openPreview", { design: t(design), surface: t(surface) })}"><div class="thumbnail" aria-hidden="true"><iframe class="thumbnail-stage" src="main-design-v3.html?embed=1&amp;lang=${state.language}#${design}-${surface}" title="${label("openPreview", { design: t(design), surface: t(surface) })}" tabindex="-1" loading="lazy"></iframe></div><span class="thumbnail-label"><span>${label(surface)}</span><span>${icon("chevron")}</span></span></button>`).join("")}</article>`).join("")}</div>`;
    resizeThumbnails();
  }

  function resizeThumbnails() {
    $("comparison").querySelectorAll(".thumbnail-stage").forEach((stage) => {
      stage.style.transform = `scale(${stage.parentElement.clientWidth / 1200})`;
    });
  }

  async function setLanguage(language) {
    if (!dictionaries.has(language)) {
      const response = await fetch(new URL(`../locales/${language}.json`, location.href));
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      dictionaries.set(language, await response.json());
    }
    state.language = language;
    state.dictionary = dictionaries.get(language);
    translateShell();
    renderWorkspace();
    if (state.compare) renderComparison();
  }

  function setDesign(design) {
    if (!designs.includes(design)) return;
    state.design = design;
    state.font = "default";
    state.inspectorOpen = design !== "focus" && !matchMedia("(max-width: 960px)").matches;
    state.compare = false;
    renderWorkspace();
  }

  function toggleFocus() {
    const focused = !state.navOpen && !state.inspectorOpen;
    state.navOpen = focused;
    state.inspectorOpen = focused && state.design !== "focus" && !matchMedia("(max-width: 960px)").matches;
    renderWorkspace();
  }

  function toggleTimer() {
    state.timerRunning = !state.timerRunning;
    if (state.timerRunning) {
      if (state.timerRemaining === 0) state.timerRemaining = 25 * 60;
      state.timerEnd = Date.now() + state.timerRemaining * 1000;
    }
    renderWorkspace();
  }

  document.addEventListener("click", (event) => {
    const button = event.target.closest("button");
    if (!button || button.closest(".thumbnail-stage")) return;
    if (button.dataset.previewDesign) {
      state.surface = button.dataset.previewSurface;
      setDesign(button.dataset.previewDesign);
      return;
    }
    if (button.dataset.design) return setDesign(button.dataset.design);
    if (button.dataset.surface) { state.surface = button.dataset.surface; state.compare = false; return applyState(); }
    if (button.id === "compareToggle") {
      state.compare = !state.compare;
      if (state.compare) renderComparison();
      applyState();
      window.scrollTo(0, 0);
      return;
    }
    if (button.dataset.scene) {
      state.scene = Number(button.dataset.scene);
      state.section = "manuscript";
      if (matchMedia("(max-width: 700px)").matches) state.navOpen = false;
      return renderWorkspace();
    }
    if (button.dataset.section) { state.section = button.dataset.section; return renderWorkspace(); }
    if (button.dataset.codex) {
      state.section = "codex";
      state.codex = button.dataset.codex;
      if (matchMedia("(max-width: 700px)").matches) state.navOpen = false;
      return renderWorkspace();
    }
    if (button.dataset.inspectorTab) { state.inspector = button.dataset.inspectorTab; return renderWorkspace(); }
    if (button.dataset.action === "nav") { state.navOpen = !state.navOpen; return renderWorkspace(); }
    if (button.dataset.action === "inspector") { state.inspectorOpen = !state.inspectorOpen; return renderWorkspace(); }
    if (button.dataset.action === "focus") return toggleFocus();
    if (button.dataset.action === "timer") return toggleTimer();
    if (button.dataset.action === "note") { state.inspector = "note"; state.inspectorOpen = true; return renderWorkspace(); }
    if (button.dataset.action === "locate") {
      state.scene = 4;
      state.section = "manuscript";
      renderWorkspace();
      const paragraph = $("workspace").querySelector(".manuscript-copy p:nth-child(5)");
      paragraph.scrollIntoView({ block: "center", behavior: "instant" });
      paragraph.setAttribute("tabindex", "-1");
      paragraph.focus({ preventScroll: true });
    }
  });
  document.addEventListener("input", (event) => { if (event.target.id === "sampleNote") state.note = event.target.value; });
  document.addEventListener("change", (event) => {
    if (event.target.id === "language") {
      event.target.disabled = true;
      setLanguage(event.target.value).catch((error) => {
        console.error(error);
        $("language").value = state.language;
      }).finally(() => { $("language").disabled = false; });
    }
    if (event.target.id === "fontSelect") { state.font = event.target.value; applyState(); }
    if (event.target.id === "sizeSelect") { state.size = event.target.value; applyState(); }
    if (event.target.id === "leadingSelect") { state.leading = event.target.value; applyState(); }
  });
  document.addEventListener("keydown", (event) => {
    if (event.ctrlKey || event.metaKey || event.altKey || event.target.closest("input, textarea, select, button")) return;
    if (["1", "2", "3"].includes(event.key)) setDesign(designs[Number(event.key) - 1]);
    if (event.key.toLowerCase() === "p") { state.surface = "paper"; state.compare = false; applyState(); }
    if (event.key.toLowerCase() === "f") { state.surface = "flat"; state.compare = false; applyState(); }
    if (event.key === "Escape") {
      state.compare = false;
      if (state.design === "focus" || matchMedia("(max-width: 960px)").matches) state.inspectorOpen = false;
      if (matchMedia("(max-width: 700px)").matches) state.navOpen = false;
      renderWorkspace();
    }
  });
  const mobileQuery = matchMedia("(max-width: 700px)");
  const compactQuery = matchMedia("(max-width: 960px)");
  mobileQuery.addEventListener("change", (event) => { state.navOpen = !event.matches; renderWorkspace(); });
  compactQuery.addEventListener("change", (event) => { state.inspectorOpen = !event.matches && state.design !== "focus"; renderWorkspace(); });
  window.addEventListener("resize", resizeThumbnails);
  window.addEventListener("hashchange", () => {
    parseUrl();
    renderWorkspace();
    if (state.compare) renderComparison();
  });
  setInterval(() => {
    if (!state.timerRunning) return;
    state.timerRemaining = Math.max(0, Math.ceil((state.timerEnd - Date.now()) / 1000));
    document.querySelectorAll("[data-timer-display]").forEach((element) => { element.textContent = timeText(); });
    if (state.timerRemaining === 0) { state.timerRunning = false; renderWorkspace(); }
  }, 1000);

  function parseUrl() {
    const match = /^(studio|editorial|focus)-(paper|flat)(-compare)?$/.exec(location.hash.slice(1));
    if (match) { state.design = match[1]; state.surface = match[2]; state.compare = Boolean(match[3]); }
    state.inspectorOpen = state.design !== "focus" && !compactQuery.matches;
  }
  parseUrl();
  const urlParams = new URL(location.href).searchParams;
  if (urlParams.get("embed") === "1") document.body.classList.add("embedded");
  const requestedLanguage = urlParams.get("lang");
  setLanguage(["ko", "en", "es"].includes(requestedLanguage) ? requestedLanguage : "ko").then(() => {
    $("loading").hidden = true;
    $("preview").hidden = false;
    if (state.compare) resizeThumbnails();
  }).catch((error) => {
    console.error(error);
    $("loading").textContent = `SuperTory · ${error.message}`;
  });
})();
