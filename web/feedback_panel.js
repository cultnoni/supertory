/* 첨삭 피드백 패널(검토 모드·플로트 도크 공통). i18n 키를 쓰지 않고 한국어 문구를 직접 둔다.
 * 이후 하단 작업 바: 원고 열 높이를 줄이지 말고, 바가 보일 때만
 * 에디터 스크롤 영역 위에 겹친다(하단에서 12px). 스크롤 영역에만
 * padding-bottom(바 높이+여백)을 준다. styles.css 검토 모드 주석과 같음.
 */
(function (global) {
  const POLL_MS = 2000;
  const ORIGINAL_PREVIEW = 200;
  const DIFF_LIMIT = 3000;
  const DIFF_CONTEXT = 30;
  const LOCATE_DEBOUNCE_MS = 500;
  const UNDERLINE_KEY = "supertory.feedbackUnderline";
  const CHROME_KEY = "supertory.feedbackChrome";
  const AUTO_ADVANCE_KEY = "supertory.feedbackAutoAdvance";
  const LENS_KEY_PREFIX = "supertory.feedbackLens.";
  const REVIEW_PANEL_MIN = 420;
  const REVIEW_PANEL_MAX = 640;
  const REVIEW_PANEL_RATIO = 0.4;
  const MANUSCRIPT_MIN_PX = 360;
  const BINDER_RAIL_PX = 48;
  const HL_CARDS = "fb-cards";
  const HL_HIGH = "fb-high";
  const HL_MEDIUM = "fb-medium";
  const HL_LOW = "fb-low";
  const HL_ACTIVE = "fb-active";
  const HL_ACTIVE_NOTE = "fb-active-note";
  const HL_ACTIVE_MID = "fb-active-mid";
  const HL_DEL = "fb-del";
  const HL_ACTIVE_RANGE = "fb-active-range";
  const HL_ACTIVE_RANGE_MID = "fb-active-range-mid";
  const HL_HOVER = "fb-hover";
  const HL_APPLIED = "fb-applied";
  const MID_BAND_DENSITY = 0.55;
  const APPLIED_FLASH_MS = 800;
  const APPLY_UNDO_MAX = 5;
  const HISTORY_SYNC_MS = 300;
  const ACTION_BAR_GAP_PX = 12;
  const SORT_KEY_PREFIX = "supertory.feedbackSort.";
  const UL_LEVEL_KEY_PREFIX = "supertory.feedbackUnderlineLevel.";
  const DICT_HIDE_KEY_PREFIX = "supertory.feedbackHideDict.";
  const COLLECTED_KEY_PREFIX = "supertory.feedbackCollected.";
  const AI_RESULT_HISTORY_PREFIX = "supertory.aiResultHistory.";
  const TIER_RANK = { high: 3, medium: 2, low: 1, ref: 1 };
  const DEBUG_KEY = "supertory.fbDebug";
  const SCROLL_PAD_PX = 72;
  const SCROLL_FIT_RATIO = 0.7;
  const START_MARK_MS = 1200;
  const BLOCK_TAGS = {
    p: 1, div: 1, h1: 1, h2: 1, h3: 1, h4: 1, h5: 1, h6: 1, li: 1, blockquote: 1,
  };
  const OTHER_TAGS = { table: 1, img: 1, figure: 1 };
  const HEADING_TAGS = { h1: 1, h2: 1, h3: 1, h4: 1, h5: 1, h6: 1 };
  const VOID_TAGS = { br: 1, img: 1, hr: 1, meta: 1, input: 1, source: 1, col: 1, area: 1, wbr: 1 };
  const SKIP_TAGS = { script: 1, style: 1 };
  const DIVIDER_TEXTS = { "***": 1, "* * *": 1 };
  const STYLE_LABELS = {
    explain_less: "해설 줄이기",
    info_placement: "정보 배치",
    action_clarity: "액션 순서",
    redundancy: "반복",
    reader_question: "독자 의문",
    abstract_expression: "추상 표현",
    other: "기타",
  };
  const KIND_LABELS = {
    consistency: "설정·인물 일관성",
    structure: "구조",
  };
  const PRIORITY_LABELS = {
    high: "중요",
    medium: "보통",
    low: "낮음",
    ref: "참고",
  };
  const STATUS_LABELS = {
    open: "미결정",
    ignored: "무시됨",
    applied: "적용됨",
    applied_edited: "수정해서 적용됨",
    alternate: "대안",
    running: "실행 중",
    ok: "완료",
    partial: "일부 완료",
    failed: "실패",
  };
  const STAGE_LABELS = {
    queued: "문단 중복 검사",
    dup: "문단 중복 검사",
    consistency: "정합성 검사",
    consistency_p2: "정합성 검사",
    report: "리포트",
    cards: "카드 생성",
    priority: "마무리",
    done: "마무리",
    interrupted: "중단됨",
  };

  const stateBox = {
    mounted: false,
    bound: false,
    root: null,
    chromeMode: "",
    tab: "report",
    tabLocked: false,
    projectId: 0,
    sceneId: 0,
    sceneTitle: "",
    configured: false,
    serverFake: false,
    lens: "normal",
    defaultLens: "normal",
    runs: [],
    run: null,
    reveal: "high",
    sortMode: "priority",
    kindFilter: [],
    startPopOpen: false,
    filterPopOpen: false,
    displayPopOpen: false,
    pendingScrollGroup: "",
    pendingOpenRunId: 0,
    pollTimer: 0,
    reportOpen: true,
    collected: Object.create(null),
    collapsedCards: Object.create(null),
    expandedDiff: Object.create(null),
    expandedWarnings: Object.create(null),
    commentOpen: Object.create(null),
    commentsByCard: Object.create(null),
    commentDraft: Object.create(null),
    commentSending: Object.create(null),
    commentError: Object.create(null),
    commentLimit: Object.create(null),
    busy: false,
    locateTimer: 0,
    activeCardId: null,
    hoverCardId: null,
    showUnderline: true,
    underlineLevel: "high-medium",
    hideDict: true,
    scrollGen: 0,
    startMarkTimer: 0,
    keyBound: false,
    overlayBound: false,
    locateById: Object.create(null),
    mapping: [],
    ulHits: [],
    bracketCardId: null,
    autoAdvance: true,
    inlineEdit: false,
    applyBusy: false,
    applyMemory: [],
    preApplySavedKey: "",
    historySyncTimer: 0,
    pendingConfirm: false,
    applyToastKind: "",
    appliedFlashTimer: 0,
  };

  function esc(value) {
    return String(value == null ? "" : value)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function $(id) {
    return typeof document !== "undefined" ? document.getElementById(id) : null;
  }

  function panelEl() {
    if (stateBox.root) return stateBox.root;
    return $("feedbackPanel");
  }

  function mount(root) {
    if (root && root.nodeType === 1) {
      stateBox.root = root.id === "feedbackPanel"
        ? root
        : (root.querySelector && root.querySelector("#feedbackPanel")) || root;
    } else {
      stateBox.root = $("feedbackPanel") || null;
    }
    bindPanel();
    return stateBox.root;
  }

  function reviewPanelWidthPx(viewportWidth) {
    const vw = Number(viewportWidth);
    if (!Number.isFinite(vw) || vw <= 0) return REVIEW_PANEL_MIN;
    const raw = vw * REVIEW_PANEL_RATIO;
    return Math.round(Math.max(REVIEW_PANEL_MIN, Math.min(REVIEW_PANEL_MAX, raw)));
  }

  function shouldFallbackToFloat(viewportWidth, opts) {
    const options = opts || {};
    if (options.conflict) return true;
    const minRaw = Number(options.manuscriptMinWidth);
    const manuscriptMin = Number.isFinite(minRaw) && minRaw > 0 ? minRaw : MANUSCRIPT_MIN_PX;
    const railRaw = Number(options.binderRailWidth);
    const rail = Number.isFinite(railRaw) && railRaw >= 0 ? railRaw : BINDER_RAIL_PX;
    const vw = Number(viewportWidth);
    if (!Number.isFinite(vw) || vw <= 0) return true;
    return (vw - rail - reviewPanelWidthPx(vw)) < manuscriptMin;
  }

  function snapshotMainLayout(input) {
    const src = input || {};
    return {
      outlineWidth: Number(src.outlineWidth) || 300,
      binderCollapsed: Boolean(src.binderCollapsed),
      aiPanelWidth: Number(src.aiPanelWidth) || 300,
      aiCollapsed: Boolean(src.aiCollapsed),
    };
  }

  function planFeedbackChrome(snapshot, viewportWidth, opts) {
    const restore = snapshotMainLayout(snapshot);
    if (shouldFallbackToFloat(viewportWidth, opts)) {
      return { mode: "float", restore: restore };
    }
    return {
      mode: "review",
      restore: restore,
      applied: {
        binderCollapsed: true,
        aiCollapsed: false,
        aiPanelWidth: reviewPanelWidthPx(viewportWidth),
      },
    };
  }

  function restoredLayout(plan) {
    if (!plan || !plan.restore) return null;
    return snapshotMainLayout(plan.restore);
  }

  function readChromePref() {
    try {
      const value = localStorage.getItem(CHROME_KEY);
      if (value === "float" || value === "review") return value;
    } catch (_) { /* ignore */ }
    return "review";
  }

  function writeChromePref(mode) {
    if (mode !== "float" && mode !== "review") return;
    try { localStorage.setItem(CHROME_KEY, mode); } catch (_) { /* ignore */ }
  }

  function isChromeOpen() {
    const panel = panelEl();
    if (!panel || !panel.closest) return false;
    if (panel.closest(".idea-float")) return true;
    if (panel.closest("#feedbackReviewHost")) return true;
    return false;
  }

  function setChromeMode(mode) {
    stateBox.chromeMode = mode === "float" || mode === "review" ? mode : "";
    syncChromeButtons();
  }

  function syncChromeButtons() {
    const panel = panelEl();
    if (!panel) return;
    const switchBtn = panel.querySelector("[data-role='fb-switch-chrome']");
    if (switchBtn) {
      const toFloat = stateBox.chromeMode !== "float";
      const label = toFloat ? "작은 창으로 보기" : "넓은 패널로 보기";
      switchBtn.textContent = toFloat ? "⧉" : "▣";
      switchBtn.setAttribute("title", label);
      switchBtn.setAttribute("aria-label", label);
    }
    const closeBtn = panel.querySelector("[data-role='fb-close-chrome']");
    if (closeBtn) closeBtn.hidden = stateBox.chromeMode === "float";
    panel.setAttribute("data-chrome", stateBox.chromeMode || "");
  }

  function closePopovers() {
    stateBox.startPopOpen = false;
    stateBox.filterPopOpen = false;
    stateBox.displayPopOpen = false;
    syncPopovers();
  }

  function anyPopoverOpen() {
    return Boolean(stateBox.startPopOpen || stateBox.filterPopOpen || stateBox.displayPopOpen);
  }

  function syncPopovers() {
    const panel = panelEl();
    if (!panel) return;
    const startPop = panel.querySelector("[data-role='fb-start-pop']");
    const startBtn = panel.querySelector("[data-role='fb-start']");
    const filterPop = panel.querySelector("[data-role='fb-filter-pop']");
    const filterBtn = panel.querySelector("[data-role='fb-filter-menu']");
    const displayPop = panel.querySelector("[data-role='fb-display-pop']");
    const displayBtn = panel.querySelector("[data-role='fb-display-menu']");
    if (startPop) startPop.hidden = !stateBox.startPopOpen;
    if (startBtn) startBtn.setAttribute("aria-expanded", stateBox.startPopOpen ? "true" : "false");
    if (filterPop) filterPop.hidden = !stateBox.filterPopOpen;
    if (filterBtn) filterBtn.setAttribute("aria-expanded", stateBox.filterPopOpen ? "true" : "false");
    if (displayPop) displayPop.hidden = !stateBox.displayPopOpen;
    if (displayBtn) displayBtn.setAttribute("aria-expanded", stateBox.displayPopOpen ? "true" : "false");
  }

  function normalizeInvisible(text) {
    return String(text || "").replace(/\u00a0/g, " ").replace(/\u200b/g, "");
  }

  function unescapeHtml(text) {
    return String(text || "")
      .replace(/&nbsp;/gi, "\u00a0")
      .replace(/&amp;/g, "&")
      .replace(/&lt;/g, "<")
      .replace(/&gt;/g, ">")
      .replace(/&quot;/g, '"')
      .replace(/&#(\d+);/g, function (_, n) { return String.fromCharCode(Number(n)); })
      .replace(/&#x([0-9a-f]+);/gi, function (_, n) {
        return String.fromCharCode(parseInt(n, 16));
      });
  }

  function classNames(node) {
    const attrs = (node && node.attrs) || {};
    return String(attrs["class"] || attrs.className || "");
  }

  function isAuthorNote(node) {
    if (!node || typeof node !== "object") return false;
    if (node.$t) return false;
    const attrs = node.attrs || {};
    if (Object.prototype.hasOwnProperty.call(attrs, "data-author-note")) return true;
    return (classNames(node) + " ").indexOf("st-author-note ") >= 0;
  }

  function isAnno(child) {
    return Boolean(child && typeof child === "object" && child.$t === 1);
  }

  function wrapAnno(text, node, start) {
    return { $t: 1, text: String(text || ""), node: node || null, start: start || 0 };
  }

  function charsFromRaw(raw, node, start) {
    const original = String(raw || "");
    const un = unescapeHtml(original);
    const chars = [];
    if (node && un === original) {
      for (let i = 0; i < original.length; i += 1) {
        let ch = original.charAt(i);
        if (ch === "\u00a0") ch = " ";
        if (ch === "\u200b") continue;
        chars.push({ ch: ch, node: node, offset: (start || 0) + i });
      }
      return chars;
    }
    const norm = normalizeInvisible(un);
    for (let i = 0; i < norm.length; i += 1) {
      chars.push({ ch: norm.charAt(i), node: node || null, offset: (start || 0) + i });
    }
    return chars;
  }

  function collapseWsBeforeNl(chars) {
    const out = [];
    for (let i = 0; i < chars.length; i += 1) {
      const item = chars[i];
      if (item.ch === "\n") {
        while (out.length && (out[out.length - 1].ch === " " || out[out.length - 1].ch === "\t")) {
          out.pop();
        }
      }
      out.push(item);
    }
    return out;
  }

  function collapseSpaces(chars) {
    const out = [];
    let prevSpace = false;
    for (let i = 0; i < chars.length; i += 1) {
      const item = chars[i];
      if (item.ch === " " || item.ch === "\t") {
        if (prevSpace) continue;
        out.push({ ch: " ", node: item.node, offset: item.offset });
        prevSpace = true;
        continue;
      }
      prevSpace = false;
      out.push(item);
    }
    return out;
  }

  function stripChars(chars) {
    let a = 0;
    let b = chars.length;
    while (a < b && /[ \t\n\r]/.test(chars[a].ch)) a += 1;
    while (b > a && /[ \t\n\r]/.test(chars[b - 1].ch)) b -= 1;
    return chars.slice(a, b);
  }

  function charsToText(chars) {
    let text = "";
    for (let i = 0; i < chars.length; i += 1) text += chars[i].ch;
    return text;
  }

  function segmentsFromChars(chars) {
    const segs = [];
    for (let i = 0; i < (chars || []).length; i += 1) {
      const item = chars[i];
      if (!item || !item.node) continue;
      const last = segs[segs.length - 1];
      if (last && last.node === item.node && last.endOffset === item.offset) {
        last.endOffset = item.offset + 1;
      } else {
        segs.push({
          node: item.node,
          startOffset: item.offset,
          endOffset: item.offset + 1,
          textStart: i,
        });
      }
    }
    return segs;
  }

  function innerMapped(node) {
    const raw = [];
    const children = (node && node.children) || [];
    for (let i = 0; i < children.length; i += 1) {
      const child = children[i];
      if (typeof child === "string" || isAnno(child)) {
        const src = isAnno(child) ? child : wrapAnno(child, null, 0);
        const piece = charsFromRaw(src.text, src.node, src.start);
        for (let k = 0; k < piece.length; k += 1) raw.push(piece[k]);
        continue;
      }
      if (!child || typeof child !== "object") continue;
      if (isAuthorNote(child) || SKIP_TAGS[child.tag]) continue;
      if (child.tag === "br") {
        raw.push({ ch: "\n", node: null, offset: 0 });
        continue;
      }
      const nested = innerMapped(child);
      for (let k = 0; k < nested.length; k += 1) raw.push(nested[k]);
    }
    return stripChars(collapseSpaces(collapseWsBeforeNl(raw)));
  }

  function innerText(node) {
    return charsToText(innerMapped(node));
  }

  function splitCharsOnNewlines(chars) {
    const groups = [];
    let run = [];
    let sawNl = false;
    function flushRun() {
      if (run.length) groups.push(run);
      run = [];
    }
    for (let i = 0; i < (chars || []).length; i += 1) {
      const item = chars[i];
      if (item.ch === "\n") {
        if (!sawNl) flushRun();
        sawNl = true;
        continue;
      }
      sawNl = false;
      run.push(item);
    }
    flushRun();
    return groups;
  }

  function splitLeafMapped(node) {
    const buf = [];
    const chunks = [];
    function flush() {
      const chars = stripChars(collapseSpaces(buf.slice()));
      buf.length = 0;
      if (chars.length) chunks.push(chars);
    }
    function pushChars(chars) {
      const groups = splitCharsOnNewlines(chars);
      for (let i = 0; i < groups.length; i += 1) {
        if (i) flush();
        const group = groups[i];
        for (let k = 0; k < group.length; k += 1) buf.push(group[k]);
      }
    }
    function pushText(raw, srcNode, srcStart) {
      pushChars(charsFromRaw(raw, srcNode, srcStart));
    }
    const children = (node && node.children) || [];
    for (let i = 0; i < children.length; i += 1) {
      const child = children[i];
      if (child && typeof child === "object" && !isAnno(child) && child.tag === "br") {
        flush();
        continue;
      }
      if (typeof child === "string" || isAnno(child)) {
        const src = isAnno(child) ? child : wrapAnno(child, null, 0);
        pushText(src.text, src.node, src.start);
        continue;
      }
      if (child && typeof child === "object") {
        if (isAuthorNote(child) || SKIP_TAGS[child.tag]) continue;
        pushChars(innerMapped(child));
      }
    }
    flush();
    return chunks;
  }

  function splitLeaf(node) {
    return splitLeafMapped(node).map(charsToText);
  }

  function isStructural(child) {
    if (!child || typeof child !== "object" || isAnno(child)) return false;
    const tag = child.tag;
    if (BLOCK_TAGS[tag] || OTHER_TAGS[tag]) return true;
    return tag === "div" && classNames(child).indexOf("manuscript-scene-break") >= 0;
  }

  function hasNestedBlocks(node) {
    const children = (node && node.children) || [];
    for (let i = 0; i < children.length; i += 1) {
      if (isStructural(children[i])) return true;
    }
    return false;
  }

  function emitLeaf(node, acc, kind) {
    const chunks = splitLeafMapped(node);
    for (let i = 0; i < chunks.length; i += 1) {
      const text = charsToText(chunks[i]);
      let chunkKind = kind;
      if (DIVIDER_TEXTS[String(text).trim()]) chunkKind = "divider";
      acc.push({ kind: chunkKind, text: text, chars: chunks[i] });
    }
  }

  function walkMixed(node, acc) {
    const children = (node && node.children) || [];
    if (hasNestedBlocks(node)) {
      const buf = [];
      function flushInlines() {
        if (!buf.length) return;
        emitLeaf({ tag: "span", attrs: {}, children: buf.slice() }, acc, "text");
        buf.length = 0;
      }
      for (let i = 0; i < children.length; i += 1) {
        const child = children[i];
        if (child && typeof child === "object" && isAuthorNote(child)) continue;
        if (isStructural(child)) {
          flushInlines();
          walkBlocks(child, acc);
        } else {
          buf.push(child);
        }
      }
      flushInlines();
      return;
    }
    emitLeaf(node, acc, "text");
  }

  function walkBlocks(node, acc) {
    if (!node || typeof node !== "object" || isAnno(node)) return;
    if (isAuthorNote(node) || SKIP_TAGS[node.tag]) return;
    const tag = String(node.tag || "");
    if (tag === "img" || tag === "table" || tag === "figure") {
      let text = innerText(node) || String((node.attrs || {}).alt || "");
      if (tag === "img" && !innerText(node)) {
        text = String((node.attrs || {}).alt || "").trim() || "(이미지)";
      }
      if (!text) text = "(이미지)";
      acc.push({ kind: "other", text: text, chars: [] });
      return;
    }
    if (tag === "div" && classNames(node).indexOf("manuscript-scene-break") >= 0) {
      acc.push({ kind: "divider", text: innerText(node) || "***", chars: [] });
      return;
    }
    if (BLOCK_TAGS[tag]) {
      if (hasNestedBlocks(node)) {
        walkMixed(node, acc);
        return;
      }
      emitLeaf(node, acc, HEADING_TAGS[tag] ? "other" : "text");
      return;
    }
    walkMixed(node, acc);
  }

  function plainChunks(text) {
    const source = normalizeInvisible(String(text || "")).replace(/\r\n/g, "\n").replace(/\r/g, "\n");
    if (!source.trim()) return [];
    const raw = /\n\s*\n/.test(source) ? source.split(/\n\s*\n+/) : source.split("\n");
    const out = [];
    for (let i = 0; i < raw.length; i += 1) {
      const body = raw[i].trim();
      if (!body) continue;
      let kind = "text";
      if (DIVIDER_TEXTS[body]) kind = "divider";
      else if (body.charAt(0) === "#") kind = "other";
      out.push([kind, body]);
    }
    return out;
  }

  function paragraphsFromTree(root) {
    const acc = [];
    walkBlocks(root, acc);
    if (!acc.length) {
      const extra = plainChunks(innerText(root));
      for (let i = 0; i < extra.length; i += 1) {
        acc.push({ kind: extra[i][0], text: extra[i][1], chars: [] });
      }
    }
    const out = [];
    for (let i = 0; i < acc.length; i += 1) {
      const item = acc[i];
      let kind = item.kind;
      const text = item.text;
      if (kind === "text" && DIVIDER_TEXTS[String(text).trim()]) kind = "divider";
      out.push({
        i: i + 1,
        text: text,
        type: kind,
        segments: segmentsFromChars(item.chars || []),
        chars: item.chars || [],
      });
    }
    return out;
  }

  function parseAttrs(raw) {
    const attrs = {};
    const src = String(raw || "");
    const re = /([^\s=]+)(?:\s*=\s*(?:"([^"]*)"|'([^']*)'|(\S+)))?/g;
    let match;
    while ((match = re.exec(src))) {
      attrs[match[1].toLowerCase()] = match[2] != null ? match[2] : (match[3] != null ? match[3] : (match[4] || ""));
    }
    return attrs;
  }

  function parseHtmlToTree(html) {
    const root = { tag: "root", attrs: {}, children: [] };
    const stack = [root];
    const src = String(html || "");
    let i = 0;
    while (i < src.length) {
      if (src[i] !== "<") {
        const next = src.indexOf("<", i);
        const chunk = next < 0 ? src.slice(i) : src.slice(i, next);
        if (chunk) stack[stack.length - 1].children.push(chunk);
        i = next < 0 ? src.length : next;
        continue;
      }
      if (src.slice(i, i + 4) === "<!--") {
        const end = src.indexOf("-->", i + 4);
        i = end < 0 ? src.length : end + 3;
        continue;
      }
      const close = src.indexOf(">", i + 1);
      if (close < 0) break;
      const inner = src.slice(i + 1, close);
      i = close + 1;
      if (!inner) continue;
      if (inner[0] === "/") {
        const tag = inner.slice(1).trim().toLowerCase();
        for (let s = stack.length - 1; s > 0; s -= 1) {
          if (stack[s].tag === tag) {
            stack.length = s;
            break;
          }
        }
        continue;
      }
      const selfClosing = inner.charAt(inner.length - 1) === "/";
      const body = selfClosing ? inner.slice(0, -1) : inner;
      const sp = body.search(/\s/);
      const tag = (sp < 0 ? body : body.slice(0, sp)).toLowerCase();
      if (SKIP_TAGS[tag]) {
        if (!VOID_TAGS[tag] && !selfClosing) {
          const closer = src.toLowerCase().indexOf("</" + tag, i);
          i = closer < 0 ? src.length : src.indexOf(">", closer) + 1;
        }
        continue;
      }
      const attrs = parseAttrs(sp < 0 ? "" : body.slice(sp));
      const node = { tag: tag, attrs: attrs, children: [] };
      stack[stack.length - 1].children.push(node);
      if (!VOID_TAGS[tag] && !selfClosing) stack.push(node);
    }
    return root;
  }

  function elementToNode(el) {
    if (!el) return null;
    if (el.nodeType === 3) {
      const value = el.nodeValue || "";
      if (!value) return null;
      return wrapAnno(value, el, 0);
    }
    if (el.nodeType !== 1) return null;
    const tag = String(el.tagName || "").toLowerCase();
    if (SKIP_TAGS[tag]) return null;
    const attrs = {};
    const list = el.attributes || [];
    for (let i = 0; i < list.length; i += 1) {
      attrs[String(list[i].name || "").toLowerCase()] = list[i].value || "";
    }
    const node = { tag: tag, attrs: attrs, children: [] };
    if (isAuthorNote(node)) return null;
    const kids = el.childNodes || [];
    for (let i = 0; i < kids.length; i += 1) {
      const child = elementToNode(kids[i]);
      if (child == null || child === "") continue;
      node.children.push(child);
    }
    return node;
  }

  function treeToDomLike(node) {
    if (typeof node === "string" || isAnno(node)) {
      const text = isAnno(node) ? node.text : node;
      return { nodeType: 3, nodeValue: text, childNodes: [] };
    }
    if (!node || typeof node !== "object") return null;
    const attrs = node.attrs || {};
    const names = Object.keys(attrs);
    const attrList = names.map(function (name) {
      return { name: name, value: String(attrs[name] == null ? "" : attrs[name]) };
    });
    const children = [];
    const srcKids = node.children || [];
    for (let i = 0; i < srcKids.length; i += 1) {
      const child = treeToDomLike(srcKids[i]);
      if (child) children.push(child);
    }
    return {
      nodeType: 1,
      tagName: String(node.tag || "div").toUpperCase(),
      attributes: attrList,
      childNodes: children,
      getAttribute: function (name) {
        const key = String(name || "").toLowerCase();
        for (let i = 0; i < attrList.length; i += 1) {
          if (String(attrList[i].name).toLowerCase() === key) return attrList[i].value;
        }
        return null;
      },
    };
  }

  function htmlToDomLike(html) {
    return treeToDomLike(parseHtmlToTree(html));
  }

  function mapEditorParagraphs(rootElement) {
    if (!rootElement) return [];
    const root = { tag: "root", attrs: {}, children: [] };
    const kids = rootElement.childNodes || [];
    for (let i = 0; i < kids.length; i += 1) {
      const child = elementToNode(kids[i]);
      if (child == null || child === "") continue;
      root.children.push(child);
    }
    return paragraphsFromTree(root);
  }

  function paragraphsFromEditor(rootElement) {
    return mapEditorParagraphs(rootElement).map(function (row) {
      return { i: row.i, text: row.text, type: row.type };
    });
  }

  function paragraphsFromHtml(html) {
    if (typeof document !== "undefined" && document.createElement) {
      const wrap = document.createElement("div");
      wrap.innerHTML = String(html || "");
      return paragraphsFromEditor(wrap);
    }
    return paragraphsFromTree(parseHtmlToTree(html)).map(function (row) {
      return { i: row.i, text: row.text, type: row.type };
    });
  }

  function snapshotEditorMarkup(rootElement) {
    if (!rootElement) return "";
    if (typeof rootElement.innerHTML === "string") return rootElement.innerHTML;
    const kids = rootElement.childNodes || [];
    let out = "";
    for (let i = 0; i < kids.length; i += 1) {
      const child = kids[i];
      if (!child) continue;
      if (child.nodeType === 3) out += child.nodeValue || "";
      else if (child.nodeType === 1) {
        const tag = String(child.tagName || "div").toLowerCase();
        out += "<" + tag + ">" + snapshotEditorMarkup(child) + "</" + tag + ">";
      }
    }
    return out;
  }

  function foldSearchText(text) {
    return foldAlign(text).folded;
  }

  function foldAlign(text) {
    const src = normalizeInvisible(String(text || ""));
    let folded = "";
    const foldToOrigStart = [];
    const foldToOrigEnd = [];
    let i = 0;
    function emit(ch, origStart, origEnd) {
      folded += ch;
      foldToOrigStart.push(origStart);
      foldToOrigEnd.push(origEnd);
    }
    while (i < src.length) {
      const ch = src[i];
      if (ch === "“" || ch === "”") {
        emit('"', i, i + 1);
        i += 1;
        continue;
      }
      if (ch === "‘" || ch === "’") {
        emit("'", i, i + 1);
        i += 1;
        continue;
      }
      if (ch === "…") {
        let j = i;
        while (src[j] === "…") j += 1;
        emit("…", i, j);
        i = j;
        continue;
      }
      if (ch === "." && src[i + 1] === "." && src[i + 2] === ".") {
        emit("…", i, i + 3);
        i += 3;
        continue;
      }
      if ((ch === " " || ch === "\t") && (src[i + 1] === " " || src[i + 1] === "\t")) {
        let j = i;
        while (src[j] === " " || src[j] === "\t") j += 1;
        emit(" ", i, j);
        i = j;
        continue;
      }
      emit(ch, i, i + 1);
      i += 1;
    }
    return {
      folded: folded,
      foldToOrigStart: foldToOrigStart,
      foldToOrigEnd: foldToOrigEnd,
    };
  }

  function normalizeNeedle(text) {
    return foldSearchText(text).trim();
  }

  function textParas(paras) {
    return (paras || []).filter(function (p) {
      return String(p && p.type) !== "divider";
    });
  }

  function paintTextParas(paras) {
    return (paras || []).filter(function (p) {
      return String(p && p.type) !== "divider" && String((p && p.text) || "").trim() !== "";
    });
  }

  function joinParaRange(mapping, startN, endN) {
    const list = Array.isArray(mapping) ? mapping : [];
    const lo = Math.min(startN, endN);
    const hi = Math.max(startN, endN);
    const paras = [];
    for (let i = 0; i < list.length; i += 1) {
      const n = Number(list[i].i);
      if (n >= lo && n <= hi) paras.push(list[i]);
    }
    return paras;
  }

  function joinedText(paras) {
    return paras.map(function (p) { return p.text; }).join("\n");
  }

  function joinedParaLayout(paras) {
    const rows = [];
    let cursor = 0;
    const list = Array.isArray(paras) ? paras : [];
    for (let p = 0; p < list.length; p += 1) {
      const text = String(list[p].text || "");
      const textStart = cursor;
      const textEnd = cursor + text.length;
      const hasSep = p < list.length - 1;
      rows.push({
        para: list[p],
        i: Number(list[p].i) || (p + 1),
        text: text,
        textStart: textStart,
        textEnd: textEnd,
        sepStart: textEnd,
        sepEnd: hasSep ? textEnd + 1 : textEnd,
      });
      cursor = hasSep ? textEnd + 1 : textEnd;
    }
    return { length: cursor, rows: rows };
  }

  function joinedFullText(mapping) {
    const layout = joinedParaLayout(mapping);
    let out = "";
    const rows = layout.rows || [];
    for (let i = 0; i < rows.length; i += 1) {
      out += rows[i].text;
      if (i < rows.length - 1) out += "\n";
    }
    return out;
  }

  function joinedTextAt(mapping, start, length) {
    const full = joinedFullText(mapping);
    const a = Math.max(0, Number(start) || 0);
    const n = Number(length);
    if (!(n >= 0)) return "";
    return full.slice(a, a + n);
  }

  function sliceJoinedSpanToParas(layout, start, end) {
    const slices = [];
    const a = Number(start);
    const b = Number(end);
    if (!(b > a) || !layout || !layout.rows) return slices;
    for (let i = 0; i < layout.rows.length; i += 1) {
      const row = layout.rows[i];
      const lo = Math.max(a, row.textStart);
      const hi = Math.min(b, row.textEnd);
      if (hi > lo) {
        slices.push({
          i: row.i,
          para: row.para,
          localStart: lo - row.textStart,
          localEnd: hi - row.textStart,
        });
      }
    }
    return slices;
  }

  function mapDelSpansToParaSlices(original, suggestion, paras) {
    const rows = Array.isArray(paras) ? paras : [];
    const layout = joinedParaLayout(rows);
    const mapped = mapDelSpansToSource(original, suggestion, joinedText(rows));
    if (mapped.skipped) return { skipped: true, slices: [], newlineOnly: false };
    const slices = [];
    let hitSep = false;
    let hitText = false;
    for (let i = 0; i < mapped.spans.length; i += 1) {
      const span = mapped.spans[i];
      const parts = sliceJoinedSpanToParas(layout, span.start, span.end);
      for (let p = 0; p < parts.length; p += 1) slices.push(parts[p]);
      for (let r = 0; r < layout.rows.length; r += 1) {
        const row = layout.rows[r];
        if (row.sepEnd > row.sepStart && span.start < row.sepEnd && span.end > row.sepStart) hitSep = true;
        if (span.start < row.textEnd && span.end > row.textStart) hitText = true;
      }
    }
    return {
      skipped: false,
      slices: slices,
      newlineOnly: Boolean(hitSep && !hitText && !slices.length),
    };
  }

  function paraGlobalSpan(mapping, paraI) {
    const list = Array.isArray(mapping) ? mapping : [];
    const start = joinOffsetOfPara(list, paraI);
    let text = "";
    for (let i = 0; i < list.length; i += 1) {
      if (Number(list[i].i) === Number(paraI)) {
        text = String(list[i].text || "");
        break;
      }
    }
    return { start: start, end: start + text.length };
  }

  function paraBandPlan(startPara, endPara, mapping) {
    const lo = Math.min(Number(startPara) || 0, Number(endPara) || 0);
    const hi = Math.max(Number(startPara) || 0, Number(endPara) || 0);
    const span = lo && hi ? (hi - lo + 1) : 0;
    const paras = paintTextParas(joinParaRange(mapping, lo, hi));
    const ids = paras.map(function (p) { return Number(p.i); }).filter(Boolean);
    if (span <= 5) return { strong: ids.slice(), mid: [], span: span };
    if (!ids.length) return { strong: [], mid: [], span: span };
    return {
      strong: [ids[0], ids[ids.length - 1]],
      mid: ids.slice(1, -1),
      span: span,
    };
  }

  function paraBandDensities(startPara, endPara, mapping) {
    const band = paraBandPlan(startPara, endPara, mapping);
    const lo = Math.min(Number(startPara) || 0, Number(endPara) || 0);
    const hi = Math.max(Number(startPara) || 0, Number(endPara) || 0);
    const rows = [];
    const paras = joinParaRange(mapping, lo, hi);
    for (let i = 0; i < paras.length; i += 1) {
      const para = paras[i];
      const n = Number(para.i);
      const isText = String(para.type) !== "divider" && String(para.text || "").trim() !== "";
      let bandName = "";
      if (isText) {
        if (band.strong.indexOf(n) >= 0) bandName = "strong";
        else if (band.mid.indexOf(n) >= 0) bandName = "mid";
      }
      rows.push({
        i: n,
        type: para.type || "text",
        text: isText,
        band: bandName,
        density: bandName === "strong" ? 1 : bandName === "mid" ? MID_BAND_DENSITY : 0,
      });
    }
    return rows;
  }

  function underlineParaList(startPara, endPara, mapping) {
    return paraBandPlan(startPara, endPara, mapping).strong.slice();
  }

  function underlineJoinSpans(found, mapping) {
    const start = Number(found && found.startPara) || 0;
    const end = Number(found && found.endPara) || start;
    const ids = underlineParaList(start, end, mapping);
    const spans = [];
    for (let i = 0; i < ids.length; i += 1) {
      const g = paraGlobalSpan(mapping, ids[i]);
      if (g.end > g.start) spans.push({ start: g.start, end: g.end, para: ids[i] });
    }
    return spans;
  }

  function isSettledStatus(status) {
    const s = String(status || "open");
    return s === "applied" || s === "applied_edited" || s === "ignored";
  }

  function isAppliedStatus(status) {
    const s = String(status || "");
    return s === "applied" || s === "applied_edited";
  }

  function locateTextForCard(card) {
    if (isAppliedStatus(card && card.status)) {
      const edited = card.final_text != null && String(card.final_text).trim() !== ""
        ? String(card.final_text)
        : String(card.suggestion || "");
      if (edited.trim()) return edited;
    }
    return String((card && card.original_text) || "");
  }

  function isOpenStatus(status) {
    const s = String(status == null ? "open" : status);
    return s === "open" || s === "" || s === "running" || s === "alternate";
  }

  function normalizeApplyText(text) {
    return normalizeInvisible(String(text || "")).replace(/\r\n/g, "\n").replace(/\r/g, "\n");
  }

  function suggestionLines(text) {
    return String(text == null ? "" : text).replace(/\r\n/g, "\n").replace(/\r/g, "\n").split("\n");
  }

  function slicesFromFound(found, mapping) {
    const list = Array.isArray(mapping) ? mapping : [];
    if (found && Number(found.joinEnd) > Number(found.joinStart)) {
      return sliceJoinedSpanToParas(joinedParaLayout(list), Number(found.joinStart), Number(found.joinEnd));
    }
    const start = Number(found && found.startPara) || 0;
    const end = Number(found && found.endPara) || start;
    const paras = joinParaRange(list, start, end);
    const out = [];
    for (let i = 0; i < paras.length; i += 1) {
      const text = String(paras[i].text || "");
      if (!text) continue;
      out.push({ i: paras[i].i, para: paras[i], localStart: 0, localEnd: text.length });
    }
    return out;
  }

  function liveTextFromSlices(slices) {
    return (slices || []).map(function (s) {
      return String(s.para && s.para.text || "").slice(s.localStart, s.localEnd);
    }).join("\n");
  }

  function planCardApply(card, mapping, found, editedText) {
    if (!found || !found.ok) {
      return {
        ok: false,
        reason: "locate",
        message: "위치를 찾을 수 없어요",
        copyOnly: true,
        steps: [],
      };
    }
    const slices = slicesFromFound(found, mapping);
    if (!slices.length) {
      return {
        ok: false,
        reason: "locate",
        message: "위치를 찾을 수 없어요",
        copyOnly: true,
        steps: [],
      };
    }
    const live = liveTextFromSlices(slices);
    const orig = String((card && card.original_text) || "");
    if (normalizeApplyText(live) !== normalizeApplyText(orig)) {
      return {
        ok: false,
        reason: "mismatch",
        message: "원고가 바뀌어서 적용할 수 없어요. 수정안을 복사해서 직접 고쳐 주세요",
        copyOnly: true,
        live: live,
        original: orig,
        steps: [],
      };
    }
    const nextText = editedText != null ? String(editedText) : String((card && card.suggestion) || "");
    const lines = suggestionLines(nextText);
    const n = slices.length;
    const m = lines.length;
    const mode = m === n ? "each" : "all";
    const steps = [];
    if (mode === "each") {
      for (let i = 0; i < n; i += 1) {
        const slice = slices[i];
        const line = lines[i];
        const cur = String(slice.para.text || "").slice(slice.localStart, slice.localEnd);
        if (normalizeApplyText(cur) === normalizeApplyText(line)) {
          steps.push({ kind: "skip", slice: slice, text: line, para: slice.i });
        } else if (line === "") {
          steps.push({ kind: "delete", slice: slice, text: "", para: slice.i });
        } else {
          steps.push({ kind: "replace", slice: slice, text: line, para: slice.i });
        }
      }
    } else {
      steps.push({
        kind: nextText === "" ? "delete-all" : "replace-all",
        slices: slices,
        text: nextText,
        html: suggestionLines(nextText).map(function (line) { return esc(line); }).join("<br>"),
      });
    }
    return {
      ok: true,
      reason: "",
      mode: mode,
      needsConfirm: mode === "all",
      slices: slices,
      steps: steps,
      expectedText: nextText,
      originalText: orig,
      startPara: Number(found.startPara) || Number(card && card.start_para) || 0,
      endPara: Number(found.endPara) || Number(card && card.end_para) || 0,
      joinStart: found.joinStart,
      joinEnd: found.joinEnd,
      live: live,
    };
  }

  function openWalkIds(cards, reveal, sortMode, kindKey) {
    const list = Array.isArray(cards) ? cards : [];
    const ids = visibleWalkOrder(list, reveal, sortMode, kindKey);
    return ids.filter(function (id) {
      for (let i = 0; i < list.length; i += 1) {
        if (String(list[i].id) === String(id)) return isOpenStatus(list[i].status);
      }
      return false;
    });
  }

  function openWalkState(ids, activeId) {
    const list = Array.isArray(ids) ? ids : [];
    const total = list.length;
    let index = 0;
    for (let i = 0; i < list.length; i += 1) {
      if (String(list[i]) === String(activeId)) {
        index = i + 1;
        break;
      }
    }
    const pos = index ? index - 1 : -1;
    return {
      ids: list,
      total: total,
      remaining: total,
      index: index,
      prevId: pos > 0 ? list[pos - 1] : null,
      nextId: pos >= 0 && pos < total - 1 ? list[pos + 1] : null,
      label: index ? (index + " / " + total) : ("– / " + total),
    };
  }

  function historySyncDecision(memory, liveText, inputType) {
    if (!memory) return { action: "none" };
    const snap = liveText && typeof liveText === "object"
      ? liveText
      : { expected: liveText, original: liveText };
    const expectedLive = normalizeApplyText(snap.expected);
    const originalLive = normalizeApplyText(snap.original);
    const expected = normalizeApplyText(memory.expectedText);
    const original = normalizeApplyText(memory.originalText);
    const hasExpected = expectedLive === expected;
    const hasOriginal = originalLive === original;
    if (inputType === "historyUndo" && !hasExpected && hasOriginal) {
      return { action: "reopen", toast: "되돌려서 카드를 미결정으로 바꿨어요" };
    }
    if (inputType === "historyRedo" && hasExpected && isOpenStatus(memory.status)) {
      return { action: "reapply", status: memory.appliedStatus || "applied" };
    }
    return { action: "none", hasExpected: hasExpected, hasOriginal: hasOriginal };
  }

  function inlineBoxPlacement(input) {
    const view = input && input.view ? input.view : {};
    const line = input && input.lastLine ? input.lastLine : {};
    const scrollTop = Number(view.scrollTop) || 0;
    const viewH = Math.max(0, Number(view.viewHeight) || 0);
    const barH = input && input.barVisible ? (Number(input.barHeight) || 44) + ACTION_BAR_GAP_PX : 0;
    const gap = input && input.gap != null ? Number(input.gap) : 6;
    const desired = Math.max(48, Number(input && input.desiredHeight) || 120);
    const maxH = Math.max(48, viewH * 0.4);
    const boxH = Math.min(desired, maxH);
    const viewBottom = scrollTop + viewH - barH;
    const below = Number(line.bottom) + gap;
    const lastTop = Number(line.top);
    const lastBottom = Number(line.bottom);
    const visible = lastBottom >= scrollTop && lastTop < scrollTop + viewH;
    const spaceBelow = viewBottom - below;
    const left = Number(line.left) || 0;
    const width = Math.max(120, Number(line.width) || Number(view.viewWidth) || 240);
    if (visible && spaceBelow >= Math.min(boxH, 72)) {
      return { top: below, left: left, width: width, maxHeight: Math.min(boxH, Math.max(72, spaceBelow)), mode: "below" };
    }
    return {
      top: Math.max(scrollTop + 8, viewBottom - boxH),
      left: left,
      width: width,
      maxHeight: boxH,
      mode: "bar-top",
    };
  }

  function actionBarPlacement(input) {
    const scrollTop = Number(input && input.scrollTop) || 0;
    const viewH = Math.max(0, Number(input && input.viewHeight) || 0);
    const viewW = Math.max(0, Number(input && input.viewWidth) || 0);
    const barH = Math.max(36, Number(input && input.barHeight) || 44);
    const barW = Math.min(Math.max(280, Number(input && input.barWidth) || 420), Math.max(200, viewW - 16));
    return {
      top: scrollTop + viewH - ACTION_BAR_GAP_PX - barH,
      left: Math.max(8, (viewW - barW) / 2),
      width: barW,
      height: barH,
      pad: barH + ACTION_BAR_GAP_PX,
    };
  }

  function addedWordHtml(original, suggestion) {
    const segs = diffSegments(original, suggestion) || [{ type: "same", text: suggestion }];
    return segs.filter(function (seg) { return seg.type !== "del"; }).map(function (seg) {
      const text = esc(seg.text || "");
      if (seg.type === "add") return "<span class=\"fb-add-word\">" + text + "</span>";
      return text;
    }).join("");
  }

  function firstTwoSentences(text) {
    const src = String(text || "").trim();
    if (!src) return "";
    const parts = src.replace(/([.!?。…])\s+/g, "$1\n").split("\n").filter(Boolean);
    return parts.slice(0, 2).join(" ");
  }

  function findAll(hay, needle) {
    const hits = [];
    if (!needle) return hits;
    let from = 0;
    while (from <= hay.length) {
      const at = hay.indexOf(needle, from);
      if (at < 0) break;
      hits.push({ start: at, end: at + needle.length });
      from = at + Math.max(1, needle.length);
    }
    return hits;
  }

  function findAllFolded(hay, needleRaw) {
    const aligned = foldAlign(hay);
    const needle = foldSearchText(needleRaw).trim();
    if (!needle) return [];
    return findAll(aligned.folded, needle).map(function (hit) {
      const origStart = aligned.foldToOrigStart[hit.start];
      const origEnd = aligned.foldToOrigEnd[Math.max(hit.start, hit.end - 1)];
      return {
        start: origStart == null ? hit.start : origStart,
        end: origEnd == null ? hit.end : origEnd,
      };
    });
  }

  function hitsInParas(paras, original, needle) {
    const hay = joinedText(paras);
    const raw = String(original || "").trim();
    let hits = raw ? findAll(hay, raw) : [];
    if (hits.length !== 1) {
      const folded = needle ? findAllFolded(hay, needle) : [];
      if (folded.length) hits = folded;
    }
    return hits;
  }

  function indexToPoint(paras, index) {
    let cursor = 0;
    for (let p = 0; p < paras.length; p += 1) {
      const para = paras[p];
      const text = String(para.text || "");
      const next = cursor + text.length;
      if (index < next) {
        const local = Math.max(0, Math.min(text.length, index - cursor));
        const chars = para.chars || [];
        if (local >= chars.length) {
          const last = chars[chars.length - 1];
          if (!last) return null;
          return { node: last.node, offset: last.offset + 1, para: para };
        }
        const ch = chars[local];
        if (!ch || !ch.node) {
          for (let k = local; k < chars.length; k += 1) {
            if (chars[k] && chars[k].node) return { node: chars[k].node, offset: chars[k].offset, para: para };
          }
          for (let k = local; k >= 0; k -= 1) {
            if (chars[k] && chars[k].node) return { node: chars[k].node, offset: chars[k].offset + 1, para: para };
          }
          return null;
        }
        return { node: ch.node, offset: ch.offset, para: para };
      }
      if (index === next) {
        const chars = para.chars || [];
        const last = chars[chars.length - 1];
        if (last && last.node) return { node: last.node, offset: last.offset + 1, para: para, virtual: true };
        if (p === paras.length - 1) {
          if (!last) return null;
          return { node: last.node, offset: last.offset + 1, para: para, virtual: true };
        }
      }
      cursor = next + 1;
    }
    const lastPara = paras[paras.length - 1];
    const lastChars = (lastPara && lastPara.chars) || [];
    const last = lastChars[lastChars.length - 1];
    if (!last) return null;
    return { node: last.node, offset: last.offset + 1, para: lastPara };
  }

  function rangeFromHit(paras, start, end) {
    if (!(Number(end) > Number(start))) return null;
    const a = indexToPoint(paras, start);
    const lastIdx = Math.max(Number(start), Number(end) - 1);
    const b = indexToPoint(paras, lastIdx);
    if (!a || !b || !a.node || !b.node) return null;
    const endOffset = b.virtual ? b.offset : b.offset + 1;
    if (typeof document !== "undefined" && document.createRange) {
      try {
        const range = document.createRange();
        range.setStart(a.node, a.offset);
        range.setEnd(b.node, endOffset);
        return range;
      } catch (_) { /* fall through */ }
    }
    return {
      startContainer: a.node,
      startOffset: a.offset,
      endContainer: b.node,
      endOffset: endOffset,
      collapsed: false,
      getBoundingClientRect: function () {
        return { top: 0, bottom: 0, left: 0, right: 0, width: 0, height: 0 };
      },
    };
  }

  function paraWholeRange(mapping, number) {
    const span = joinParaRange(mapping, number, number);
    if (!span.length) return null;
    const joined = joinedText(span);
    if (!joined.length) {
      const para = span[0];
      const chars = (para && para.chars) || [];
      const first = chars[0];
      if (first && first.node && typeof document !== "undefined" && document.createRange) {
        try {
          const range = document.createRange();
          range.setStart(first.node, first.offset);
          range.setEnd(first.node, first.offset);
          return range;
        } catch (_) { /* fall through */ }
      }
      return null;
    }
    return rangeFromHit(span, 0, joined.length);
  }

  function joinOffsetOfPara(mapping, paraI) {
    const list = Array.isArray(mapping) ? mapping : [];
    let cursor = 0;
    const want = Number(paraI);
    for (let i = 0; i < list.length; i += 1) {
      if (Number(list[i].i) === want) return cursor;
      cursor += String(list[i].text || "").length + 1;
    }
    return cursor;
  }

  function subsetHitToGlobal(mapping, subset, localStart, localEnd) {
    const list = Array.isArray(mapping) ? mapping : [];
    const rows = Array.isArray(subset) ? subset : [];
    const localToGlobal = [];
    for (let p = 0; p < rows.length; p += 1) {
      const para = rows[p];
      const base = joinOffsetOfPara(list, para.i);
      const text = String(para.text || "");
      for (let c = 0; c < text.length; c += 1) localToGlobal.push(base + c);
      if (p < rows.length - 1) localToGlobal.push(base + text.length);
    }
    const startIdx = Math.max(0, Number(localStart) || 0);
    const endIdx = Math.max(startIdx, Number(localEnd) || 0);
    if (!localToGlobal.length) return { start: 0, end: 0 };
    const start = startIdx >= localToGlobal.length
      ? localToGlobal[localToGlobal.length - 1] + 1
      : localToGlobal[startIdx];
    let end;
    if (endIdx <= startIdx) end = start;
    else if (endIdx - 1 >= localToGlobal.length) {
      const last = rows[rows.length - 1];
      end = joinOffsetOfPara(list, last.i) + String(last.text || "").length;
    } else {
      end = localToGlobal[endIdx - 1] + 1;
    }
    return { start: start, end: end };
  }

  function rangesFromParaSlices(slices) {
    const ranges = [];
    const list = Array.isArray(slices) ? slices : [];
    for (let i = 0; i < list.length; i += 1) {
      const slice = list[i];
      if (!slice || !slice.para) continue;
      const range = rangeFromHit([slice.para], slice.localStart, slice.localEnd);
      if (range) ranges.push(range);
    }
    return ranges;
  }

  function rangeFromGlobalSpan(mapping, start, end) {
    const ranges = rangesFromGlobalSpan(mapping, start, end);
    return ranges.length ? ranges[0] : null;
  }

  function rangesFromGlobalSpan(mapping, start, end) {
    const list = Array.isArray(mapping) ? mapping : [];
    if (!list.length || !(Number(end) > Number(start))) return [];
    const slices = sliceJoinedSpanToParas(joinedParaLayout(list), Number(start), Number(end));
    return rangesFromParaSlices(slices);
  }

  function findParaWithQuote(list, quote, preferN) {
    const needle = normalizeNeedle(quote);
    if (!needle) return null;
    const hits = [];
    for (let i = 0; i < list.length; i += 1) {
      const hay = foldSearchText(list[i].text || "");
      if (hay.indexOf(needle) >= 0) hits.push(list[i]);
    }
    if (!hits.length) return null;
    const prefer = Number(preferN) || 0;
    if (prefer) {
      for (let i = 0; i < hits.length; i += 1) {
        if (Number(hits[i].i) === prefer) return hits[i];
      }
    }
    if (hits.length === 1) return hits[0];
    hits.sort(function (a, b) {
      return Math.abs(Number(a.i) - prefer) - Math.abs(Number(b.i) - prefer);
    });
    return hits[0];
  }

  function locateResult(range, startPara, endPara, method, extra) {
    extra = extra || {};
    const lo = Number(startPara) || 0;
    const hi = Number(endPara) || lo;
    const spanCount = extra.span != null ? extra.span : (lo && hi ? Math.abs(hi - lo) + 1 : 0);
    return {
      ok: Boolean(range),
      range: range,
      startPara: startPara,
      endPara: endPara,
      method: method,
      reason: extra.reason || "",
      span: spanCount,
      edgeRanges: extra.edgeRanges || null,
      midRanges: extra.midRanges || null,
      allRanges: extra.allRanges || null,
      joinStart: extra.joinStart,
      joinEnd: extra.joinEnd,
    };
  }

  function longRangeEmphasis(startPara, endPara) {
    const lo = Number(startPara) || 0;
    const hi = Number(endPara) || lo;
    if (!lo) return { strong: [], mid: [] };
    const span = Math.abs(hi - lo) + 1;
    if (span <= 5) {
      const all = [];
      const a = Math.min(lo, hi);
      const b = Math.max(lo, hi);
      for (let n = a; n <= b; n += 1) all.push(n);
      return { strong: all, mid: [] };
    }
    const a = Math.min(lo, hi);
    const b = Math.max(lo, hi);
    const mid = [];
    for (let n = a + 1; n < b; n += 1) mid.push(n);
    return { strong: [a, b], mid: mid };
  }

  function rangeBand(list, startPara, endPara) {
    const plan = paraBandPlan(startPara, endPara, list);
    const edges = [];
    for (let i = 0; i < plan.strong.length; i += 1) {
      const range = paraWholeRange(list, plan.strong[i]);
      if (range) edges.push(range);
    }
    const mid = [];
    for (let i = 0; i < plan.mid.length; i += 1) {
      const range = paraWholeRange(list, plan.mid[i]);
      if (range) mid.push(range);
    }
    const all = edges.concat(mid);
    return {
      span: plan.span,
      edgeRanges: edges,
      midRanges: mid,
      allRanges: all,
    };
  }

  function locateFail(reason) {
    return {
      ok: false,
      range: null,
      method: "fail",
      reason: reason || "인용문을 찾지 못했어요",
    };
  }

  function locateByQuotes(card, list) {
    const startN = Number(card.start_para) || 0;
    const endN = Number(card.end_para) || startN;
    const startQ = String(card.start_quote || "").trim();
    const endQ = String(card.end_quote || "").trim();
    if (!startQ && !endQ) return null;
    const startP = startQ
      ? findParaWithQuote(list, startQ, startN)
      : (startN ? joinParaRange(list, startN, startN)[0] : null);
    const endP = endQ
      ? findParaWithQuote(list, endQ, endN)
      : (endN ? joinParaRange(list, endN, endN)[0] : null);
    if (!startP || !endP) return null;
    const lo = Math.min(Number(startP.i), Number(endP.i));
    const hi = Math.max(Number(startP.i), Number(endP.i));
    const spanParas = joinParaRange(list, lo, hi);
    if (!spanParas.length) return null;
    const joined = joinedText(spanParas);
    const range = rangeFromHit(spanParas, 0, joined.length);
    if (!range) return null;
    const band = rangeBand(list, lo, hi);
    const g = subsetHitToGlobal(list, spanParas, 0, joined.length);
    return locateResult(range, lo, hi, "quotes", {
      span: hi - lo + 1,
      edgeRanges: band.edgeRanges,
      midRanges: band.midRanges,
      allRanges: band.allRanges,
      joinStart: g.start,
      joinEnd: g.end,
    });
  }

  function locateCard(card, mapping) {
    const list = Array.isArray(mapping) ? mapping : [];
    if (!card || !list.length) return locateFail("인용문을 찾지 못했어요");
    const startN = Number(card.start_para) || 0;
    const endN = Number(card.end_para) || startN;
    const span = startN && endN ? Math.abs(endN - startN) + 1 : 0;
    const applied = isAppliedStatus(card.status);
    if (span > 5 && !applied) {
      const quoted = locateByQuotes(card, list);
      if (quoted && quoted.ok) return quoted;
    }
    const sourceText = locateTextForCard(card);
    const needle = normalizeNeedle(sourceText);
    if (!needle) {
      const spanParas = joinParaRange(list, startN, endN);
      if (!spanParas.length) {
        const quoted = locateByQuotes(card, list);
        return quoted && quoted.ok ? quoted : locateFail(span > 5 ? "범위가 너무 넓어요" : "인용문을 찾지 못했어요");
      }
      const joined = joinedText(spanParas);
      const range = rangeFromHit(spanParas, 0, joined.length);
      if (!range) return locateFail("인용문을 찾지 못했어요");
      const band = rangeBand(list, spanParas[0].i, spanParas[spanParas.length - 1].i);
      const g = subsetHitToGlobal(list, spanParas, 0, joined.length);
      band.joinStart = g.start;
      band.joinEnd = g.end;
      return locateResult(range, spanParas[0].i, spanParas[spanParas.length - 1].i, "para", band);
    }
    const preferred = textParas(joinParaRange(list, startN, endN));
    if (preferred.length) {
      const hits = hitsInParas(preferred, sourceText, needle);
      if (hits.length === 1) {
        const range = rangeFromHit(preferred, hits[0].start, hits[0].end);
        if (range) {
          const band = rangeBand(list, preferred[0].i, preferred[preferred.length - 1].i);
          const g = subsetHitToGlobal(list, preferred, hits[0].start, hits[0].end);
          band.joinStart = g.start;
          band.joinEnd = g.end;
          return locateResult(range, preferred[0].i, preferred[preferred.length - 1].i, "para", band);
        }
      }
    }
    if (!applied) {
      const quoted = locateByQuotes(card, list);
      if (quoted && quoted.ok) return quoted;
    }
    const pool = textParas(list);
    const hits = hitsInParas(pool, sourceText, needle);
    if (!hits.length) {
      return locateFail(span > 5 ? "범위가 너무 넓어요" : "인용문을 찾지 못했어요");
    }
    const mappedHits = [];
    for (let i = 0; i < hits.length; i += 1) {
      const startPt = indexToPoint(pool, hits[i].start);
      const endPt = indexToPoint(pool, Math.max(hits[i].start, hits[i].end - 1));
      if (!startPt || !endPt) continue;
      mappedHits.push({
        hit: hits[i],
        startPara: startPt.para && startPt.para.i,
        endPara: endPt.para && endPt.para.i,
        dist: Math.abs(Number(startPt.para && startPt.para.i) - (startN || Number(startPt.para && startPt.para.i))),
      });
    }
    if (!mappedHits.length) return locateFail("인용문을 찾지 못했어요");
    mappedHits.sort(function (a, b) {
      if (a.dist !== b.dist) return a.dist - b.dist;
      return Number(a.startPara) - Number(b.startPara);
    });
    const chosen = mappedHits[0];
    const range = rangeFromHit(pool, chosen.hit.start, chosen.hit.end);
    if (!range) return locateFail("인용문을 찾지 못했어요");
    const band = rangeBand(list, chosen.startPara, chosen.endPara);
    const g = subsetHitToGlobal(list, pool, chosen.hit.start, chosen.hit.end);
    band.joinStart = g.start;
    band.joinEnd = g.end;
    return locateResult(range, chosen.startPara, chosen.endPara, "window", band);
  }

  function highlightSupportsCss() {
    return typeof CSS !== "undefined"
      && CSS.highlights
      && typeof Highlight === "function";
  }

  function highlightNames() {
    return [
      HL_CARDS, HL_HIGH, HL_MEDIUM, HL_LOW,
      HL_ACTIVE, HL_ACTIVE_NOTE, HL_ACTIVE_MID,
      HL_DEL, HL_ACTIVE_RANGE, HL_ACTIVE_RANGE_MID, HL_HOVER, HL_APPLIED,
    ];
  }

  function clearCardHighlights() {
    if (!highlightSupportsCss()) return;
    const names = highlightNames();
    for (let i = 0; i < names.length; i += 1) {
      try { CSS.highlights.delete(names[i]); } catch (_) { /* ignore */ }
    }
  }

  function setHighlight(name, ranges, priority) {
    const HighlightCtor = Highlight;
    const all = (ranges || []).filter(Boolean);
    try {
      if (!all.length) {
        CSS.highlights.delete(name);
        return;
      }
      const hl = new HighlightCtor(...all);
      if (priority != null && typeof hl === "object") hl.priority = priority;
      CSS.highlights.set(name, hl);
    } catch (_) {
      try { CSS.highlights.delete(name); } catch (__) { /* ignore */ }
    }
  }

  function paintCardHighlights(byTier, active) {
    if (!highlightSupportsCss()) return false;
    let groups = byTier || {};
    if (Array.isArray(byTier)) groups = { high: byTier, medium: [], low: [] };
    let del = [];
    let rangeBg = [];
    let rangeMid = [];
    let note = [];
    let mid = [];
    let hover = [];
    let applied = [];
    if (active && typeof active === "object" && !active.startContainer && !Array.isArray(active)
        && (active.del || active.range || active.rangeMid || active.note || active.mid || active.replace || active.hover || active.applied)) {
      del = active.del || active.replace || [];
      rangeBg = active.range || [];
      rangeMid = active.rangeMid || [];
      note = active.note || [];
      mid = active.mid || [];
      hover = active.hover || [];
      applied = active.applied || [];
    } else if (Array.isArray(active)) {
      del = active;
    } else if (active) {
      del = [active];
    }
    setHighlight(HL_LOW, groups.low || [], 1);
    setHighlight(HL_MEDIUM, groups.medium || [], 2);
    setHighlight(HL_HIGH, groups.high || [], 3);
    setHighlight(HL_HOVER, hover, 3);
    setHighlight(HL_APPLIED, applied, 7);
    setHighlight(HL_ACTIVE_MID, mid, 4);
    setHighlight(HL_ACTIVE_RANGE_MID, rangeMid, 5);
    setHighlight(HL_ACTIVE_RANGE, rangeBg, 6);
    setHighlight(HL_ACTIVE_NOTE, note, 7);
    setHighlight(HL_DEL, del, 8);
    try { CSS.highlights.delete(HL_ACTIVE); } catch (_) { /* ignore */ }
    try { CSS.highlights.delete(HL_CARDS); } catch (_) { /* ignore */ }
    return true;
  }

  function tokenizeDiff(text) {
    const src = String(text || "");
    return src.match(/\s+|[^\s]+/g) || [];
  }

  function diffSegments(original, suggestion) {
    const orig = original == null ? "" : String(original);
    const sug = suggestion == null ? "" : String(suggestion);
    if (orig.length + sug.length > DIFF_LIMIT) return null;
    const a = tokenizeDiff(orig);
    const b = tokenizeDiff(sug);
    const n = a.length;
    const m = b.length;
    const dp = [];
    for (let i = 0; i <= n; i += 1) {
      dp[i] = new Array(m + 1);
      dp[i][0] = 0;
    }
    for (let j = 0; j <= m; j += 1) dp[0][j] = 0;
    for (let i = 1; i <= n; i += 1) {
      for (let j = 1; j <= m; j += 1) {
        dp[i][j] = a[i - 1] === b[j - 1]
          ? dp[i - 1][j - 1] + 1
          : Math.max(dp[i - 1][j], dp[i][j - 1]);
      }
    }
    const rev = [];
    let i = n;
    let j = m;
    while (i > 0 && j > 0) {
      if (a[i - 1] === b[j - 1]) {
        rev.push({ type: "same", text: a[i - 1] });
        i -= 1;
        j -= 1;
      } else if (dp[i - 1][j] >= dp[i][j - 1]) {
        rev.push({ type: "del", text: a[--i] });
      } else {
        rev.push({ type: "add", text: b[--j] });
      }
    }
    while (i > 0) rev.push({ type: "del", text: a[--i] });
    while (j > 0) rev.push({ type: "add", text: b[--j] });
    rev.reverse();
    const segs = [];
    for (let k = 0; k < rev.length; k += 1) {
      const item = rev[k];
      const last = segs[segs.length - 1];
      if (last && last.type === item.type) last.text += item.text;
      else segs.push({ type: item.type, text: item.text });
    }
    return segs;
  }

  function changeSummary(original, suggestion, segments) {
    let del = 0;
    let add = 0;
    if (Array.isArray(segments)) {
      for (let i = 0; i < segments.length; i += 1) {
        if (segments[i].type === "del") del += String(segments[i].text || "").length;
        if (segments[i].type === "add") add += String(segments[i].text || "").length;
      }
    } else {
      del = String(original || "").length;
      add = String(suggestion || "").length;
    }
    return "− " + del + "자 / + " + add + "자";
  }

  function compactSide(segments, side, radius) {
    const keep = side === "orig"
      ? { same: "same", del: "del" }
      : { same: "same", add: "add" };
    const tokens = [];
    const list = Array.isArray(segments) ? segments : [];
    for (let i = 0; i < list.length; i += 1) {
      const type = keep[list[i].type];
      if (!type) continue;
      tokens.push({ type: type, text: String(list[i].text || "") });
    }
    const full = tokens.map(function (t) { return t.text; }).join("");
    let first = -1;
    let last = -1;
    let pos = 0;
    for (let i = 0; i < tokens.length; i += 1) {
      if (tokens[i].type !== "same") {
        if (first < 0) first = pos;
        last = pos + tokens[i].text.length;
      }
      pos += tokens[i].text.length;
    }
    if (first < 0) {
      const shown = full.length > radius * 2 ? full.slice(0, radius * 2) + "…" : full;
      return [{ type: "same", text: shown }];
    }
    const from = Math.max(0, first - radius);
    const to = Math.min(full.length, last + radius);
    const out = [];
    if (from > 0) out.push({ type: "same", text: "…" });
    pos = 0;
    for (let i = 0; i < tokens.length; i += 1) {
      const end = pos + tokens[i].text.length;
      if (end > from && pos < to) {
        const a = Math.max(0, from - pos);
        const b = Math.min(tokens[i].text.length, to - pos);
        const slice = tokens[i].text.slice(a, b);
        if (slice) {
          const prev = out[out.length - 1];
          if (prev && prev.type === tokens[i].type && prev.text !== "…") prev.text += slice;
          else out.push({ type: tokens[i].type, text: slice });
        }
      }
      pos = end;
    }
    if (to < full.length) out.push({ type: "same", text: "…" });
    return out;
  }

  function renderSegHtml(segs) {
    return (segs || []).map(function (seg) {
      if (seg.type === "del") return "<del class=\"fb-del\">" + renderDelInner(seg.text) + "</del>";
      if (seg.type === "add") return "<ins class=\"fb-add\">" + esc(seg.text || "") + "</ins>";
      return esc(seg.text || "");
    }).join("");
  }

  function renderDelInner(text) {
    const src = String(text || "");
    if (!src) return "";
    const parts = src.split("\n");
    const out = [];
    for (let i = 0; i < parts.length; i += 1) {
      if (parts[i]) out.push(esc(parts[i]));
      if (i < parts.length - 1) out.push("<span class=\"fb-nl-del\">줄바꿈 삭제</span>");
    }
    return out.join("");
  }

  function firstSentence(text) {
    const src = String(text || "").trim();
    const match = src.match(/^[\s\S]+?[.!?。…](?:\s|$)/);
    return match ? match[0].trim() : src;
  }

  function reasonHtml(reason, full) {
    const src = String(reason || "").trim();
    if (!full) return esc(firstSentence(src));
    const parts = src.replace(/([.!?。…])\s+/g, "$1\n").split("\n").filter(Boolean);
    if (!parts.length) return esc(src);
    return parts.map(function (part) {
      return "<span class=\"fb-reason-sent\">" + esc(part) + "</span>";
    }).join("");
  }

  function sortCards(cards) {
    const list = Array.isArray(cards) ? cards.slice() : [];
    list.sort(function (a, b) {
      const sa = Number(a && a.start_para);
      const sb = Number(b && b.start_para);
      const na = Number.isFinite(sa) ? sa : 1e9;
      const nb = Number.isFinite(sb) ? sb : 1e9;
      if (na !== nb) return na - nb;
      return (Number(a && a.ord) || 0) - (Number(b && b.ord) || 0);
    });
    return list;
  }

  function cardPriorityGroup(card) {
    const pri = card && card.priority;
    if (pri == null || pri === "" || pri === "high" || pri === "ref") return "high";
    if (pri === "medium") return "medium";
    return "low";
  }

  function underlineTier(card) {
    const pri = card && card.priority;
    if (pri === "high") return "high";
    if (pri === "medium") return "medium";
    return "low";
  }

  function normalizeUnderlineLevel(level) {
    if (level === "high" || level === "all") return level;
    return "high-medium";
  }

  function shouldPaintUnderline(card, level, extra) {
    extra = extra || {};
    const id = card && card.id;
    if (id != null && (String(id) === String(extra.hoverId) || String(id) === String(extra.activeId))) {
      return true;
    }
    const lv = normalizeUnderlineLevel(level);
    const tier = underlineTier(card);
    if (lv === "all") return true;
    if (tier === "high") return true;
    if (tier === "medium") return lv === "high-medium";
    return false;
  }

  function cardHasSuggestion(card) {
    if (!card) return false;
    if (isNoteOnlyCard(card)) return false;
    const warnings = warningList((card.warnings || card.warnings_json) || []);
    for (let i = 0; i < warnings.length; i += 1) {
      const code = warningCode(warnings[i]);
      if (code === "suggestion_removed" || code === "generation_failed") return false;
    }
    const sug = card.suggestion;
    return !(sug == null || String(sug).trim() === "");
  }

  function mergeUnderlineSpans(spans) {
    const list = (spans || []).filter(function (row) {
      return row && Number(row.end) > Number(row.start);
    }).map(function (row, i) {
      const tier = row.tier === "high" ? "high" : row.tier === "medium" ? "medium" : "low";
      return {
        start: Number(row.start),
        end: Number(row.end),
        tier: tier,
        cardId: row.cardId,
        index: row.index != null ? Number(row.index) : i + 1,
        order: row.order != null ? Number(row.order) : i,
      };
    });
    if (!list.length) return [];
    const points = [];
    for (let i = 0; i < list.length; i += 1) {
      points.push(list[i].start, list[i].end);
    }
    points.sort(function (a, b) { return a - b; });
    const uniq = [];
    for (let i = 0; i < points.length; i += 1) {
      if (!i || points[i] !== points[i - 1]) uniq.push(points[i]);
    }
    const atoms = [];
    for (let i = 0; i < uniq.length - 1; i += 1) {
      const a = uniq[i];
      const b = uniq[i + 1];
      if (b <= a) continue;
      const cover = [];
      for (let j = 0; j < list.length; j += 1) {
        if (list[j].start <= a && list[j].end >= b) cover.push(list[j]);
      }
      if (!cover.length) continue;
      cover.sort(function (x, y) {
        const rank = (TIER_RANK[y.tier] || 0) - (TIER_RANK[x.tier] || 0);
        if (rank) return rank;
        if (x.order !== y.order) return x.order - y.order;
        return x.index - y.index;
      });
      atoms.push({
        start: a,
        end: b,
        tier: cover[0].tier,
        cardId: cover[0].cardId,
        cardIds: cover.map(function (row) { return row.cardId; }),
        indexes: cover.map(function (row) { return row.index; }),
      });
    }
    const out = [];
    for (let i = 0; i < atoms.length; i += 1) {
      const cur = atoms[i];
      const prev = out[out.length - 1];
      const sameIds = prev && prev.cardIds.join("\0") === cur.cardIds.join("\0");
      if (prev && prev.end === cur.start && prev.tier === cur.tier && sameIds) {
        prev.end = cur.end;
      } else {
        out.push({
          start: cur.start,
          end: cur.end,
          tier: cur.tier,
          cardId: cur.cardId,
          cardIds: cur.cardIds.slice(),
          indexes: cur.indexes.slice(),
        });
      }
    }
    return out;
  }

  function overlapTooltipLabel(indexes) {
    const nums = (indexes || []).map(function (n) { return padCardIndex(n); });
    if (!nums.length) return "";
    return "카드 " + nums.join(", ");
  }

  function delSpansFromDiff(original, suggestion) {
    const segs = diffSegments(original, suggestion);
    if (segs == null) return { skipped: true, spans: [] };
    let pos = 0;
    const spans = [];
    for (let i = 0; i < segs.length; i += 1) {
      const text = String(segs[i].text || "");
      if (segs[i].type === "del") {
        if (text.length) spans.push({ start: pos, end: pos + text.length });
        pos += text.length;
      } else if (segs[i].type === "same") {
        pos += text.length;
      }
    }
    return { skipped: false, spans: spans };
  }

  function origIndexToFoldIndex(align, origIndex) {
    const n = (align && align.folded) ? align.folded.length : 0;
    if (!n) return 0;
    if (origIndex >= (align.foldToOrigEnd[n - 1] || 0)) return n;
    for (let f = 0; f < n; f += 1) {
      const lo = align.foldToOrigStart[f];
      const hi = align.foldToOrigEnd[f];
      if (lo <= origIndex && origIndex < hi) return f;
      if (lo >= origIndex) return f;
    }
    return n;
  }

  function buildOrigToSourceIndex(orig, source) {
    const src = String(orig || "");
    const hay = String(source || "");
    const map = new Array(src.length + 1);
    function fill(shift) {
      for (let i = 0; i <= src.length; i += 1) map[i] = shift + i;
      return map;
    }
    if (src === hay) return fill(0);
    const exact = hay.indexOf(src);
    if (exact >= 0) return fill(exact);
    const oA = foldAlign(src);
    const sA = foldAlign(hay);
    const fat = oA.folded ? sA.folded.indexOf(oA.folded) : -1;
    if (oA.folded && fat >= 0) {
      for (let i = 0; i <= src.length; i += 1) {
        const fo = origIndexToFoldIndex(oA, i);
        if (fo >= oA.folded.length) {
          const last = fat + oA.folded.length - 1;
          map[i] = last >= 0 && sA.foldToOrigEnd[last] != null ? sA.foldToOrigEnd[last] : hay.length;
        } else {
          const idx = sA.foldToOrigStart[fat + fo];
          map[i] = idx == null ? hay.length : idx;
        }
      }
      return map;
    }
    return fill(0);
  }

  function mapDelSpansToSource(original, suggestion, sourceText) {
    const diff = delSpansFromDiff(original, suggestion);
    if (diff.skipped) return { skipped: true, spans: [] };
    if (!diff.spans.length) return { skipped: false, spans: [] };
    const origToSrc = buildOrigToSourceIndex(original, sourceText);
    const out = [];
    for (let i = 0; i < diff.spans.length; i += 1) {
      const a = origToSrc[diff.spans[i].start];
      const b = origToSrc[diff.spans[i].end];
      if (a == null || b == null || b <= a) continue;
      const last = out[out.length - 1];
      if (last && last.end === a) last.end = b;
      else out.push({ start: a, end: b });
    }
    return { skipped: false, spans: out };
  }

  function groupCardsByPriority(cards) {
    const sorted = sortCards(cards);
    const groups = { high: [], medium: [], low: [] };
    for (let i = 0; i < sorted.length; i += 1) {
      groups[cardPriorityGroup(sorted[i])].push(sorted[i]);
    }
    return groups;
  }

  function visibleCardLayout(cards, reveal, sortMode, kindKey) {
    const filtered = applyKindFilter(cards, kindKey);
    const level = reveal === "low" ? 2 : reveal === "medium" ? 1 : 0;
    const groups = groupCardsByPriority(filtered);
    const shown = {
      high: groups.high,
      medium: level >= 1 ? groups.medium : [],
      low: level >= 2 ? groups.low : [],
    };
    const items = shown.high.concat(shown.medium, shown.low);
    if (sortMode === "manuscript") {
      return { mode: "manuscript", items: sortCards(items), groups: shown };
    }
    return { mode: "priority", items: items, groups: shown };
  }

  function visibleCards(cards, reveal, sortMode, kindKey) {
    return visibleCardLayout(cards, reveal, sortMode, kindKey).items;
  }

  function numberCards(items) {
    const list = Array.isArray(items) ? items : [];
    return list.map(function (card, i) {
      return { card: card, index: i + 1, id: card && card.id };
    });
  }

  function visibleWalkOrder(cards, reveal, sortMode, kindKey) {
    return numberCards(visibleCards(cards, reveal, sortMode, kindKey)).map(function (row) {
      return row.id;
    });
  }

  function padCardIndex(index) {
    const n = Number(index) || 0;
    return n < 10 ? "0" + n : String(n);
  }

  function rangesOverlap(a, b) {
    const a0 = Number(a && a.start_para) || 0;
    const a1 = Number((a && a.end_para) || a0);
    const b0 = Number(b && b.start_para) || 0;
    const b1 = Number((b && b.end_para) || b0);
    if (!a0 || !b0) return { ratio: 0, samePara: false };
    const lo = Math.max(a0, b0);
    const hi = Math.min(a1, b1);
    if (hi < lo) return { ratio: 0, samePara: false };
    const inter = hi - lo + 1;
    const minLen = Math.min(a1 - a0 + 1, b1 - b0 + 1);
    return {
      ratio: minLen ? inter / minLen : 0,
      samePara: a0 === b0 && a1 === b1,
    };
  }

  function shouldLinkOverlap(a, b) {
    if (!a || !b || a === b) return false;
    const hit = rangesOverlap(a, b);
    const struct = { structure: 1, consistency: 1 };
    if (struct[a.kind] && struct[b.kind] && hit.ratio >= 0.5) return true;
    if (hit.samePara) return true;
    const a0 = Number(a.start_para) || 0;
    const b0 = Number(b.start_para) || 0;
    if (a0 && a0 === b0 && (Number(a.end_para) || a0) === a0 && (Number(b.end_para) || b0) === b0) {
      return true;
    }
    return false;
  }

  function overlapPeerMap(numbered) {
    const list = Array.isArray(numbered) ? numbered : [];
    const map = Object.create(null);
    for (let i = 0; i < list.length; i += 1) {
      const a = list[i];
      if (!a || !a.card) continue;
      for (let j = 0; j < list.length; j += 1) {
        if (i === j) continue;
        const b = list[j];
        if (!b || !b.card) continue;
        if (shouldLinkOverlap(a.card, b.card)) {
          if (!map[a.id]) map[a.id] = [];
          map[a.id].push({ id: b.id, index: b.index });
        }
      }
    }
    return map;
  }

  function shouldIgnoreCardClick(eventLike) {
    const event = eventLike || {};
    const target = event.target;
    if (target && typeof target.closest === "function") {
      if (target.closest("a, button, input, textarea, [data-fb-noclick]")) return true;
    }
    let sel = event.selectionText;
    if (sel == null && typeof window !== "undefined" && window.getSelection) {
      try { sel = window.getSelection().toString(); } catch (_) { sel = ""; }
    }
    return Boolean(String(sel || "").trim());
  }

  const COMMENT_TURN_LIMIT = 20;

  function commentCountOf(card) {
    if (!card || card.id == null) return 0;
    const loaded = stateBox.commentsByCard[String(card.id)];
    if (Array.isArray(loaded)) return loaded.length;
    return Math.max(0, Number(card.comment_count) || 0);
  }

  function commentButtonLabel(count) {
    const n = Math.max(0, Number(count) || 0);
    return n ? (i18nText("index.질문", "질문") + " " + n) : i18nText("index.질문하기", "질문하기");
  }

  function commentChipLabel(count) {
    const n = Math.max(0, Number(count) || 0);
    return n ? String(n) : "";
  }

  function commentLimitReached(count) {
    return Math.max(0, Number(count) || 0) >= COMMENT_TURN_LIMIT;
  }

  function renderTalkThread(comments) {
    return (Array.isArray(comments) ? comments : []).map(function (row) {
      const mine = String((row && row.role) || "") === "user";
      return "<div class=\"fb-talk-msg " + (mine ? "is-user" : "is-ai") + "\">"
        + "<span class=\"fb-talk-who\">" + (mine ? "나" : "토리") + "</span>"
        + "<p>" + esc((row && row.body) || "") + "</p></div>";
    }).join("");
  }

  function renderCommentThread(card, options) {
    const id = String((card && card.id) || "");
    const count = commentCountOf(card);
    const open = Boolean(options && options.open);
    const sending = Boolean(options && options.sending);
    const error = Boolean(options && options.error);
    const draft = String((options && options.draft) || "");
    const comments = (options && options.comments) || [];
    const limited = Boolean(options && options.limited) || commentLimitReached(
      Array.isArray(comments) && comments.length ? comments.length : count
    );
    let html = "<button type=\"button\" class=\"secondary compact-btn\" data-role=\"fb-comment-toggle\">"
      + esc(commentButtonLabel(count)) + "</button>";
    if (!open) return html;
    html += "<div class=\"fb-card-talk\" data-fb-noclick>";
    if (Array.isArray(comments) && comments.length) {
      html += "<div class=\"fb-talk-log\">" + renderTalkThread(comments) + "</div>";
    }
    if (sending) html += "<p class=\"hint fb-talk-loading\">답을 기다리는 중…</p>";
    if (error) {
      html += "<p class=\"fb-talk-error\">답을 받지 못했어요 "
        + "<button type=\"button\" class=\"fb-text-btn\" data-role=\"fb-comment-retry\">다시 시도</button></p>";
    }
    if (limited) {
      html += "<p class=\"fb-talk-limit\">이 카드는 대화를 너무 많이 나눴어요. 새로 분석해서 다시 확인해 보세요</p>";
    } else {
      html += "<div class=\"fb-talk-compose\">"
        + "<textarea data-role=\"fb-comment-input\" rows=\"3\" placeholder=\""
        + esc(i18nText("index.이_지적에_대해_궁금한_점이나_다른_의견을_물", "이 지적에 대해 궁금한 점이나 다른 의견을 물어보세요"))
        + "\""
        + (sending ? " disabled" : "") + ">" + esc(draft) + "</textarea>"
        + "<button type=\"button\" class=\"fb-primary compact-btn\" data-role=\"fb-comment-send\""
        + (sending ? " disabled" : "") + ">"
        + (sending ? "보내는 중…" : "보내기") + "</button></div>";
    }
    html += "</div>";
    return html;
  }

  function commentUiState(card) {
    const id = String((card && card.id) || "");
    const comments = stateBox.commentsByCard[id];
    const count = commentCountOf(card);
    return {
      open: Boolean(stateBox.commentOpen[id]),
      sending: Boolean(stateBox.commentSending[id]),
      error: Boolean(stateBox.commentError[id]),
      draft: stateBox.commentDraft[id] || "",
      comments: Array.isArray(comments) ? comments : [],
      limited: Boolean(stateBox.commentLimit[id]) || commentLimitReached(
        Array.isArray(comments) ? comments.length : count
      ),
    };
  }

  function countCards(cards) {
    const counts = { total: 0, high: 0, medium: 0, low: 0, ref: 0, analyzing: 0 };
    const list = Array.isArray(cards) ? cards : [];
    counts.total = list.length;
    for (let i = 0; i < list.length; i += 1) {
      const pri = list[i] && list[i].priority;
      if (pri == null || pri === "") counts.analyzing += 1;
      else if (counts[pri] != null) counts[pri] += 1;
    }
    return counts;
  }

  function formatCountLine(counts, planned, running) {
    const n = Math.max(0, Number(counts && counts.total) || 0);
    const a = Math.max(0, Number(counts && counts.high) || 0);
    const b = Math.max(0, Number(counts && counts.medium) || 0);
    const c = Math.max(0, Number(counts && counts.low) || 0);
    const ref = Math.max(0, Number(counts && counts.ref) || 0);
    const plan = Math.max(0, Number(planned) || 0);
    let line = "첨삭 제안 총 " + n + "개 · 중요 " + a + " · 보통 " + b + " · 낮음 " + c;
    if (ref) line += " · 참고 " + ref;
    if (running && plan > 0) line = "총 " + plan + "개 중 " + n + "개 생성됨";
    return line;
  }

  function stageLabel(progress, cardCount, planned) {
    const stage = String((progress && progress.stage) || "");
    if (stage === "cards") {
      const done = cardCount != null ? cardCount : Number(progress.done) || 0;
      const total = planned || Number(progress.total) || 0;
      return "카드 생성 " + done + "/" + (total || "m");
    }
    return STAGE_LABELS[stage] || "문단 중복 검사";
  }

  function styleTypeLabel(card) {
    const kind = String((card && card.kind) || "");
    if (KIND_LABELS[kind]) return KIND_LABELS[kind];
    const style = String((card && card.style_type) || "other");
    return STYLE_LABELS[style] || STYLE_LABELS.other;
  }

  function lookupReportTitle(report, ref) {
    const id = String(ref || "");
    if (!id || !report) return "";
    const groups = [report.weaknesses, report.consistency];
    for (let g = 0; g < groups.length; g += 1) {
      const list = Array.isArray(groups[g]) ? groups[g] : [];
      for (let i = 0; i < list.length; i += 1) {
        if (String((list[i] && list[i].id) || "") === id) {
          return String((list[i] && list[i].title) || "").trim();
        }
      }
    }
    return "";
  }

  function cardTitle(card, report) {
    const fromReport = lookupReportTitle(report, card && card.report_ref);
    if (fromReport) return fromReport;
    const stored = String((card && card.title) || "").trim();
    if (stored) return stored;
    return styleTypeLabel(card);
  }

  function warningList(warnings) {
    return Array.isArray(warnings) ? warnings : [];
  }

  function warningCode(item) {
    return typeof item === "string" ? "" : String((item && item.code) || "");
  }

  function isInfoWarning(item) {
    if (warningCode(item) === "note_only") return true;
    if (warningCode(item) === "names_removed_by_deletion") return true;
    const sev = typeof item === "string" ? "" : String((item && item.severity) || "");
    return sev === "info";
  }

  function actionableWarnings(warnings) {
    return warningList(warnings).filter(function (item) {
      return !isInfoWarning(item);
    });
  }

  function infoWarnings(warnings) {
    return warningList(warnings).filter(function (item) {
      return warningCode(item) === "names_removed_by_deletion" || (
        typeof item !== "string" && String((item && item.severity) || "") === "info"
          && warningCode(item) !== "note_only"
      );
    });
  }

  function isNoteOnlyCard(card) {
    const warnings = (card && (card.warnings || card.warnings_json)) || [];
    return warningList(warnings).some(function (item) {
      return warningCode(item) === "note_only";
    });
  }

  function noSuggestionHint(card) {
    const kind = String((card && card.kind) || "");
    const style = String((card && card.style_type) || "");
    const warnings = (card && (card.warnings || card.warnings_json)) || [];
    const codes = {};
    for (let i = 0; i < warnings.length; i += 1) {
      const item = warnings[i];
      const code = typeof item === "string" ? "" : String((item && item.code) || "");
      if (code) codes[code] = true;
    }
    if (codes.note_only && !codes.suggestion_removed && !codes.generation_failed) {
      return "수정안 없이 위치와 이유만 보여드리는 참고 지적이에요";
    }
    if (codes.suggestion_removed || codes.generation_failed) {
      return "수정안을 안전하게 만들지 못했어요. 이유를 참고해서 직접 고쳐 주세요.";
    }
    if (kind === "structure") {
      return "구조를 바꾸는 제안이라 문장 수정안은 만들지 않았어요. 위치를 확인해서 직접 고쳐 주세요.";
    }
    if (kind === "consistency") {
      return "작가님이 직접 확인해야 하는 항목이에요. 원고나 설정을 확인해 주세요.";
    }
    if (kind === "style" && (style === "info_placement" || style === "other")) {
      return "이 유형은 수정안 없이 이유만 보여드려요.";
    }
    return "이 항목은 수정안 없이 이유만 제공됩니다";
  }

  function formatKindCounts(cards) {
    const list = Array.isArray(cards) ? cards : [];
    const order = [];
    const map = Object.create(null);
    for (let i = 0; i < list.length; i += 1) {
      const card = list[i];
      if (!card || String(card.status || "") === "ignored") continue;
      const label = styleTypeLabel(card);
      if (!map[label]) {
        map[label] = 0;
        order.push(label);
      }
      map[label] += 1;
    }
    return order.map(function (label) { return label + " " + map[label]; }).join(" · ");
  }

  function normalizeKindFilter(kindKey) {
    if (Array.isArray(kindKey)) {
      return kindKey.map(function (key) { return String(key || ""); }).filter(Boolean);
    }
    const one = String(kindKey || "");
    return one ? [one] : [];
  }

  function applyKindFilter(cards, kindKey) {
    const list = Array.isArray(cards) ? cards : [];
    const keys = normalizeKindFilter(kindKey);
    if (!keys.length) return list.slice();
    const want = Object.create(null);
    for (let i = 0; i < keys.length; i += 1) want[keys[i]] = true;
    return list.filter(function (card) { return want[styleTypeLabel(card)]; });
  }

  function toggleKindFilter(kindKey, key, on) {
    const cur = normalizeKindFilter(kindKey);
    const has = cur.indexOf(key) >= 0;
    if (on && !has) return cur.concat([key]);
    if (!on && has) return cur.filter(function (item) { return item !== key; });
    return cur.slice();
  }

  function kindChipList(cards) {
    const list = Array.isArray(cards) ? cards : [];
    const order = [];
    const map = Object.create(null);
    let total = 0;
    for (let i = 0; i < list.length; i += 1) {
      const card = list[i];
      if (!card) continue;
      total += 1;
      const key = styleTypeLabel(card);
      if (!map[key]) {
        map[key] = 0;
        order.push(key);
      }
      map[key] += 1;
    }
    return {
      total: total,
      items: order.map(function (key) {
        return { key: key, label: key, count: map[key] };
      }),
    };
  }

  function moreSeeCounts(cards, reveal, kindKey) {
    const filtered = applyKindFilter(cards, kindKey);
    const counts = countCards(filtered);
    const level = reveal === "low" ? "low" : reveal === "medium" ? "medium" : "high";
    return {
      moreMedium: level === "high" && counts.medium > 0,
      moreLow: level !== "low" && counts.low > 0 && (level === "medium" || counts.medium === 0),
      medium: counts.medium,
      low: counts.low,
    };
  }

  function formatListSummary(counts, planned, running) {
    const n = Math.max(0, Number(counts && counts.total) || 0);
    const plan = Math.max(0, Number(planned) || 0);
    return {
      title: running && plan > 0 ? (plan + "개 중 " + n + "개 생성됨") : (n + "개"),
      high: Math.max(0, Number(counts && counts.high) || 0),
      medium: Math.max(0, Number(counts && counts.medium) || 0),
      low: Math.max(0, Number(counts && counts.low) || 0) + Math.max(0, Number(counts && counts.ref) || 0),
    };
  }

  function formatCardsSummary(counts, planned, running, kindFilter) {
    const sum = formatListSummary(counts, planned, running);
    const keys = normalizeKindFilter(kindFilter);
    return {
      title: sum.title,
      filterNote: keys.length ? ("(필터: " + keys.join(", ") + ")") : "",
      high: sum.high,
      medium: sum.medium,
      low: Math.max(0, Number(counts && counts.low) || 0),
      ref: Math.max(0, Number(counts && counts.ref) || 0),
    };
  }

  function underlinePaintTargets(level) {
    const lv = normalizeUnderlineLevel(level);
    if (lv === "high") return ["high"];
    if (lv === "all") return ["high", "medium", "low", "ref"];
    return ["high", "medium"];
  }

  function viewOptionsState(opts) {
    const o = opts || {};
    const sortMode = o.sortMode === "manuscript" ? "manuscript" : "priority";
    const kinds = normalizeKindFilter(o.kindFilter);
    const level = normalizeUnderlineLevel(o.underlineLevel);
    const underlineOn = o.showUnderline !== false;
    const hideDict = o.hideDict !== false;
    const displayChanged = !underlineOn || level !== "high-medium" || !hideDict;
    return {
      sortMode: sortMode,
      sortLabel: sortMode === "manuscript" ? "원고순" : "중요도순",
      filterCount: kinds.length,
      filterOn: kinds.length > 0,
      filterLabel: kinds.length ? ("필터 " + kinds.length) : "필터",
      underlineOn: underlineOn,
      underlineLevel: level,
      hideDict: hideDict,
      displayChanged: displayChanged,
      underlineTargets: underlinePaintTargets(level),
    };
  }

  function viewButtonState(opts) {
    const s = viewOptionsState(opts);
    return {
      label: s.sortLabel,
      badge: (s.filterOn ? 1 : 0) + (s.displayChanged ? 1 : 0),
    };
  }

  function segmentPaintState(values, selected) {
    const sel = String(selected == null ? "" : selected);
    return (values || []).map(function (value) {
      const on = String(value) === sel;
      return { value: value, selected: on, ariaChecked: on ? "true" : "false" };
    });
  }

  function filterResetState(kindFilter) {
    const n = normalizeKindFilter(kindFilter).length;
    return {
      enabled: n > 0,
      label: n > 0 ? "초기화 ✕" : "초기화",
      hint: n > 0 ? (n + "개 유형 선택됨") : "필터 없음",
    };
  }

  function formatFilterBanner(kindFilter, shownCount) {
    const keys = normalizeKindFilter(kindFilter);
    if (!keys.length) return "";
    const who = keys.length === 1
      ? (keys[0] + "만 보는 중")
      : (keys.join(", ") + "만 보는 중");
    return who + " · " + Math.max(0, Number(shownCount) || 0) + "개 표시";
  }

  function formatFilterSummaryNote(kindFilter, shownCount) {
    if (!normalizeKindFilter(kindFilter).length) return "";
    return "(필터로 " + Math.max(0, Number(shownCount) || 0) + "개 표시 중)";
  }

  function controlDisplayState(opts) {
    const o = opts || {};
    const view = viewOptionsState(o);
    const kinds = normalizeKindFilter(o.kindFilter);
    const chipsSrc = Array.isArray(o.kindChips) ? o.kindChips : [];
    const shown = Math.max(0, Number(o.shownCount) || 0);
    const lens = o.lens === "strong" ? "strong" : "normal";
    const tab = o.tab === "cards" ? "cards" : "report";
    return {
      sortMode: view.sortMode,
      sortLabel: view.sortLabel,
      sort: segmentPaintState(["priority", "manuscript"], view.sortMode),
      filterOn: view.filterOn,
      filterCount: view.filterCount,
      filterLabel: view.filterLabel,
      filterReset: filterResetState(kinds),
      chips: chipsSrc.map(function (item) {
        const selected = kinds.indexOf(item.key) >= 0;
        return {
          key: item.key,
          label: item.label,
          count: item.count,
          selected: selected,
          ariaPressed: selected ? "true" : "false",
        };
      }),
      underlineOn: view.underlineOn,
      underlineLevel: view.underlineLevel,
      underline: segmentPaintState(["high", "high-medium", "all"], view.underlineLevel),
      hideDict: view.hideDict,
      displayChanged: view.displayChanged,
      lens: lens,
      lensCards: segmentPaintState(["normal", "strong"], lens),
      tab: tab,
      banner: formatFilterBanner(kinds, shown),
      summaryNote: formatFilterSummaryNote(kinds, shown),
      emptyFilter: view.filterOn && shown < 1 && !o.hasMore,
    };
  }

  function underlineTargetCards(cards, opts) {
    const o = opts || {};
    const list = visibleCards(cards, o.reveal, o.sortMode, o.kindFilter);
    return list.filter(function (card) {
      return shouldPaintUnderline(card, o.underlineLevel, o.extra || {});
    });
  }

  function warningPreview(warnings) {
    const list = Array.isArray(warnings) ? warnings : [];
    if (!list.length) return "";
    const first = list[0];
    const msg = typeof first === "string" ? first : String((first && first.message) || "");
    const extra = list.length > 1 ? " 외 " + (list.length - 1) + "건" : "";
    return msg + extra;
  }

  function locationLabel(card, found) {
    const start = Number((found && found.startPara) || (card && card.start_para)) || 0;
    const end = Number((found && found.endPara) || (card && card.end_para)) || start;
    if (!start) return "";
    if (!end || end === start) return "문단 " + start;
    const span = Math.abs(end - start) + 1;
    return "문단 " + start + "~" + end + " · " + span + "문단";
  }

  function cardHasWarning(card) {
    const warnings = (card && (card.warnings || card.warnings_json)) || [];
    return actionableWarnings(warnings).length > 0;
  }

  function dropReasonMessage(reason) {
    const code = String(reason || "");
    if (code === "quote_invalid" || code === "range_invalid" || code === "no_original_range") {
      return "위치를 찾지 못했어요";
    }
    if (code === "cap_reached") return "카드 개수 한도에 걸려 빠졌어요";
    if (code === "generation_failed") return "카드를 만들지 못했어요";
    if (code === "p2_filtered") return "문제가 아니라 제외했어요";
    if (code === "not_cardable") return "카드로 만들지 못했어요";
    return "카드로 만들지 못했어요";
  }

  function defaultExplanationLens(main, sub, detail) {
    const blob = [main, sub, detail].filter(Boolean).join(" ");
    const strong = ["에세이", "순문학", "서정", "일반문학", "문학"];
    const genreNormal = ["웹소설", "로맨스", "판타지", "무협", "장르문학"];
    const extraStrong = ["에세이", "순문학", "서정", "일반문학"];
    if (strong.some(function (marker) { return blob.indexOf(marker) >= 0; })) {
      if (genreNormal.some(function (marker) { return blob.indexOf(marker) >= 0; })) {
        if (extraStrong.some(function (marker) { return blob.indexOf(marker) >= 0; })) {
          return "strong";
        }
        return "normal";
      }
      return "strong";
    }
    return "normal";
  }

  function resolveExplanationLens(stored, defaultLens) {
    if (stored === "strong" || stored === "normal") return stored;
    return defaultLens === "strong" ? "strong" : "normal";
  }

  function lensLabel(lens) {
    if (lens === "strong") return "자세히";
    if (lens === "normal") return "보통";
    if (lens === "off") return "끔";
    return "";
  }

  function lensStorageKey(projectId) {
    return LENS_KEY_PREFIX + String(projectId || 0);
  }

  function readStoredLens(projectId) {
    try {
      return localStorage.getItem(lensStorageKey(projectId));
    } catch (_) {
      return null;
    }
  }

  function writeStoredLens(projectId, lens) {
    if (!projectId) return;
    try {
      localStorage.setItem(lensStorageKey(projectId), lens);
    } catch (_) { /* ignore */ }
  }

  function analysisRequestBody(sceneId, lens) {
    return { scene_id: sceneId, explanation_lens: lens };
  }

  function referenceNotes(report, cards, dropped) {
    const list = [];
    const refs = Object.create(null);
    const titleRefs = Object.create(null);
    const cardList = Array.isArray(cards) ? cards : [];
    for (let i = 0; i < cardList.length; i += 1) {
      const ref = String((cardList[i] && cardList[i].report_ref) || "");
      const title = String((cardList[i] && cardList[i].title) || "");
      if (ref) refs[ref] = true;
      if (title) titleRefs[title] = true;
    }
    const dropById = Object.create(null);
    const dropByTitle = Object.create(null);
    const dropList = Array.isArray(dropped) ? dropped : [];
    for (let i = 0; i < dropList.length; i += 1) {
      const row = dropList[i] || {};
      const id = String(row.id || "");
      const title = String(row.title || "");
      if (id) dropById[id] = row;
      if (title) dropByTitle[title] = row;
    }
    function reasonFor(item) {
      const id = String((item && item.id) || "");
      const title = String((item && item.title) || "");
      const row = (id && dropById[id]) || (title && dropByTitle[title]) || null;
      return dropReasonMessage(row && row.reason);
    }
    function becameCard(item) {
      const id = String((item && item.id) || "");
      const title = String((item && item.title) || "");
      if (id && refs[id]) return true;
      if (title && titleRefs[title]) return true;
      return false;
    }
    const weaknesses = (report && report.weaknesses) || [];
    for (let i = 0; i < weaknesses.length; i += 1) {
      const item = weaknesses[i];
      if (!item || typeof item !== "object") continue;
      if (becameCard(item)) continue;
      list.push({
        id: String(item.id || ""),
        title: item.title || "",
        body: item.body || "",
        group: "weakness",
        reason: reasonFor(item),
      });
    }
    const cons = (report && report.consistency) || [];
    for (let i = 0; i < cons.length; i += 1) {
      const item = cons[i];
      if (!item || typeof item !== "object") continue;
      if (becameCard(item)) continue;
      list.push({
        id: String(item.id || ""),
        title: item.title || "",
        body: item.body || "",
        group: "consistency",
        reason: reasonFor(item),
      });
    }
    return list;
  }

  function paragraphsDiffer(saved, live) {
    const a = Array.isArray(saved) ? saved : [];
    const b = Array.isArray(live) ? live : [];
    if (!a.length) return false;
    if (a.length !== b.length) return true;
    for (let i = 0; i < a.length; i += 1) {
      if (String(a[i].text || "") !== String(b[i].text || "")) return true;
      if (String(a[i].type || "") !== String(b[i].type || "")) return true;
    }
    return false;
  }

  function progressPercent(progress, cardCount, planned) {
    const stage = String((progress && progress.stage) || "");
    if (stage === "cards" && planned) {
      return Math.max(0, Math.min(100, Math.round((Number(cardCount) || 0) / planned * 100)));
    }
    const total = Number(progress && progress.total) || 5;
    const done = Number(progress && progress.done) || 0;
    if (!total) return 0;
    return Math.max(0, Math.min(100, Math.round(done / total * 100)));
  }

  function formatWhen(iso) {
    if (!iso) return "";
    const date = new Date(iso);
    if (Number.isNaN(date.getTime())) return String(iso);
    const pad = function (n) { return n < 10 ? "0" + n : String(n); };
    return date.getFullYear() + "-" + pad(date.getMonth() + 1) + "-" + pad(date.getDate())
      + " " + pad(date.getHours()) + ":" + pad(date.getMinutes());
  }

  function formatRunWhenShort(iso) {
    if (!iso) return "";
    const date = iso instanceof Date ? iso : new Date(iso);
    if (Number.isNaN(date.getTime())) return String(iso);
    const pad = function (n) { return n < 10 ? "0" + n : String(n); };
    return (date.getMonth() + 1) + "/" + date.getDate() + " " + pad(date.getHours()) + ":" + pad(date.getMinutes());
  }

  function formatRunOption(row) {
    const total = runCardTotal(row) || Number(row && row.planned_cards) || 0;
    const status = String((row && row.status) || "");
    let extra = "";
    if (status === "running") extra = " · 분석 중";
    else if (status === "failed") extra = " · 실패";
    else if (status === "partial" || status === "interrupted") extra = " · 일부 실패";
    const primary = Number(row && row.is_primary) === 1 ? " · 기준" : "";
    return formatRunWhenShort(row && row.created_at)
      + primary
      + " · 카드 " + total
      + extra;
  }

  function sortHistoryRuns(rows) {
    return (Array.isArray(rows) ? rows.slice() : []).sort(function (a, b) {
      const ta = String((a && a.created_at) || "");
      const tb = String((b && b.created_at) || "");
      if (ta !== tb) return ta < tb ? 1 : -1;
      return (Number(b && b.id) || 0) - (Number(a && a.id) || 0);
    });
  }

  function historyRunStatusLabel(status) {
    const s = String(status || "");
    if (s === "ok") return "완료";
    if (s === "running") return "진행 중";
    if (s === "failed") return "실패";
    if (s === "partial" || s === "interrupted") return "일부 실패";
    return STATUS_LABELS[s] || s;
  }

  function formatCardCountSummary(counts) {
    const c = counts || {};
    const open = Number(c.open) || 0;
    const applied = (Number(c.applied) || 0) + (Number(c.applied_edited) || 0);
    const ignored = Number(c.ignored) || 0;
    return "미결정 " + open + " · 적용 " + applied + " · 무시 " + ignored;
  }

  function historyKindTags(row) {
    const tags = [];
    if (String(row && row.run_kind) === "legacy") tags.push({ key: "legacy", label: "예전 기록" });
    const fake = runFakeFlag(row);
    if (fake === true) tags.push({ key: "fake", label: "시험" });
    else if (fake === false) tags.push({ key: "real", label: "실제 분석" });
    return tags;
  }

  function formatHistoryRow(row) {
    const running = String(row && row.status) === "running";
    const primary = Number(row && row.is_primary) === 1;
    return {
      id: row && row.id,
      when: formatWhen(row && row.created_at),
      lens: runLensLabel(row) || "",
      status: String((row && row.status) || ""),
      statusLabel: historyRunStatusLabel(row && row.status),
      countsLabel: formatCardCountSummary((row && row.card_counts) || {}),
      cardTotal: runCardTotal(row) || Number(row && row.planned_cards) || 0,
      tags: historyKindTags(row),
      primary: primary,
      canDelete: !running,
      deleteTitle: running ? "실행 중인 첨삭은 삭제할 수 없습니다." : "",
      showPrimaryBtn: !primary,
    };
  }

  function pickDefaultRun(rows) {
    const list = sortHistoryRuns(rows);
    const primary = list.find(function (row) { return Number(row && row.is_primary) === 1; });
    return primary || list[0] || null;
  }

  function nextRunAfterDelete(rows, deletedId, currentId) {
    const rest = sortHistoryRuns(rows).filter(function (row) {
      return String(row && row.id) !== String(deletedId);
    });
    if (currentId != null && String(currentId) !== String(deletedId)) {
      const keep = rest.find(function (row) { return String(row && row.id) === String(currentId); });
      if (keep) return keep;
    }
    return pickDefaultRun(rest);
  }

  function deleteRunConfirmMessage(cardCount) {
    const n = Math.max(0, Number(cardCount) || 0);
    return "이 분석 기록을 삭제할까요? 카드 " + n + "개가 함께 지워지고 되돌릴 수 없어요";
  }

  function isLegacyHistoryMode(mode) {
    return mode === "analyze" || mode === "analyze_multi";
  }

  function legacyHistoryEntries(items) {
    return (Array.isArray(items) ? items : []).filter(function (item) {
      return item && item.id && item.text && isLegacyHistoryMode(item.mode);
    });
  }

  function formatLegacyImportMessage(imported, skipped) {
    const got = Number(imported) || 0;
    const skip = Number(skipped) || 0;
    if (got && skip) return got + "개 가져왔어요(" + skip + "개는 이미 있어서 건너뜀)";
    if (got) return got + "개 가져왔어요";
    if (skip) return "가져올 새 기록이 없어요(" + skip + "개는 이미 있어서 건너뜀)";
    return "가져올 예전 기록이 없어요";
  }

  function i18nText(key, fallback) {
    if (typeof i18n !== "undefined" && i18n && typeof i18n.t === "function") {
      const text = i18n.t(key);
      if (text && text !== key) return text;
    }
    return fallback;
  }

  function legacyImportButtonState(entries) {
    const has = legacyHistoryEntries(entries).length > 0;
    const key = has ? "index.예전_피드백_기록_가져오기" : "index.가져올_예전_기록이_없어요";
    const label = has ? "예전 피드백 기록 가져오기" : "가져올 예전 기록이 없어요";
    return {
      disabled: !has,
      i18nKey: key,
      label: label,
      title: label,
    };
  }

  function paintLegacyImportButton(btn, entries) {
    const st = legacyImportButtonState(entries);
    if (!btn) return st;
    const label = i18nText(st.i18nKey, st.label);
    btn.disabled = st.disabled;
    btn.setAttribute("aria-disabled", st.disabled ? "true" : "false");
    btn.title = label;
    btn.setAttribute("data-i18n", st.i18nKey);
    btn.setAttribute("data-i18n-title", st.i18nKey);
    btn.textContent = label;
    return st;
  }

  function formatReportCollectText(run) {
    if (!run) return "";
    const report = run.report && typeof run.report === "object" ? run.report : null;
    const parts = [];
    if (report && report.summary) parts.push(String(report.summary));
    const scores = report && Array.isArray(report.scores) ? report.scores : [];
    if (scores.length) {
      parts.push(scores.map(function (row) {
        const item = String((row && row.item) || "");
        const score = row && row.score != null ? String(row.score) : "";
        const comment = row && row.comment ? String(row.comment) : "";
        return (item + " " + score).trim() + (comment ? " — " + comment : "");
      }).join("\n"));
    }
    if (!parts.length && run.report_md) parts.push(String(run.report_md));
    return parts.join("\n\n").trim();
  }

  function formatCardCollectText(card, report) {
    if (!card) return "";
    const parts = [];
    const title = cardTitle(card, report);
    if (title) parts.push(String(title));
    if (card.reason) parts.push(String(card.reason));
    if (cardHasSuggestion(card)) parts.push("수정안\n" + String(card.suggestion || ""));
    return parts.join("\n\n").trim();
  }

  function runSelectTags(row) {
    const tags = [];
    if (runFakeFlag(row) === true) tags.push({ key: "fake", label: "시험" });
    const lens = runLensLabel(row);
    if (lens) tags.push({ key: "lens", label: lens });
    return tags;
  }

  function formatRunStatusLine(row) {
    if (!row) return "";
    const parts = [];
    const fake = runFakeLabel(row);
    if (fake) parts.push(fake);
    const lens = runLensLabel(row);
    if (lens) parts.push(lens);
    const status = String(row.status || "");
    let st = STATUS_LABELS[status] || status;
    if (status === "ok") st = "완료";
    else if (status === "running") st = "진행 중";
    else if (status === "failed") st = "실패";
    else if (status === "partial" || status === "interrupted") st = "일부 실패";
    if (st) parts.push(st);
    const total = runCardTotal(row) || Number(row.planned_cards) || 0;
    parts.push("카드 " + total + "개");
    return parts.join(" · ");
  }

  function runCardTotal(row) {
    const counts = (row && row.card_counts) || {};
    return ["open", "applied", "applied_edited", "ignored", "alternate"]
      .reduce(function (sum, key) { return sum + (Number(counts[key]) || 0); }, 0);
  }

  function toastMsg(message, durationMs, options) {
    if (typeof toast === "function") toast(message, durationMs, options);
  }

  function currentScene() {
    const st = typeof state !== "undefined" ? state : null;
    return {
      projectId: Number(st && st.projectId) || 0,
      sceneId: Number(st && st.sceneId) || 0,
      title: (st && st.scene && st.scene.title)
        || ($("sceneTitle") && $("sceneTitle").value)
        || "",
    };
  }

  function editorRoot() {
    return $("sceneContent");
  }

  function readUnderlinePref() {
    try {
      return localStorage.getItem(UNDERLINE_KEY) !== "0";
    } catch (_) {
      return true;
    }
  }

  function writeUnderlinePref(on) {
    try { localStorage.setItem(UNDERLINE_KEY, on ? "1" : "0"); } catch (_) { /* ignore */ }
  }

  function sortStorageKey(projectId) {
    return SORT_KEY_PREFIX + String(projectId || 0);
  }

  function readSortPref(projectId) {
    try {
      const value = localStorage.getItem(sortStorageKey(projectId));
      if (value === "manuscript" || value === "priority") return value;
    } catch (_) { /* ignore */ }
    return "priority";
  }

  function writeSortPref(projectId, mode) {
    if (!projectId) return;
    try { localStorage.setItem(sortStorageKey(projectId), mode); } catch (_) { /* ignore */ }
  }

  function ulLevelStorageKey(projectId) {
    return UL_LEVEL_KEY_PREFIX + String(projectId || 0);
  }

  function readUnderlineLevel(projectId) {
    try {
      return normalizeUnderlineLevel(localStorage.getItem(ulLevelStorageKey(projectId)));
    } catch (_) { /* ignore */ }
    return "high-medium";
  }

  function writeUnderlineLevel(projectId, level) {
    if (!projectId) return;
    try { localStorage.setItem(ulLevelStorageKey(projectId), normalizeUnderlineLevel(level)); } catch (_) { /* ignore */ }
  }

  function dictHideStorageKey(projectId) {
    return DICT_HIDE_KEY_PREFIX + String(projectId || 0);
  }

  function readHideDictPref(projectId) {
    try {
      const value = localStorage.getItem(dictHideStorageKey(projectId));
      if (value === "0") return false;
    } catch (_) { /* ignore */ }
    return true;
  }

  function writeHideDictPref(projectId, on) {
    if (!projectId) return;
    try { localStorage.setItem(dictHideStorageKey(projectId), on ? "1" : "0"); } catch (_) { /* ignore */ }
  }

  function readAutoAdvancePref() {
    try {
      const value = localStorage.getItem(AUTO_ADVANCE_KEY);
      if (value === "0") return false;
    } catch (_) { /* ignore */ }
    return true;
  }

  function writeAutoAdvancePref(on) {
    try { localStorage.setItem(AUTO_ADVANCE_KEY, on ? "1" : "0"); } catch (_) { /* ignore */ }
  }

  function collectedStorageKey(projectId) {
    return COLLECTED_KEY_PREFIX + String(projectId || 0);
  }

  function loadCollectedMarks(projectId) {
    try {
      const raw = localStorage.getItem(collectedStorageKey(projectId));
      const obj = raw ? JSON.parse(raw) : {};
      return obj && typeof obj === "object" && !Array.isArray(obj) ? obj : {};
    } catch (_) {
      return {};
    }
  }

  function saveCollectedMarks(projectId, marks) {
    try {
      localStorage.setItem(collectedStorageKey(projectId), JSON.stringify(marks || {}));
    } catch (_) { /* ignore */ }
  }

  function collectMarkKey(kind, id) {
    return String(kind || "") + ":" + String(id || "");
  }

  function hasCollectedMark(kind, id) {
    return Boolean(stateBox.collected && stateBox.collected[collectMarkKey(kind, id)]);
  }

  function rememberCollectedMark(kind, id) {
    if (!stateBox.collected) stateBox.collected = Object.create(null);
    stateBox.collected[collectMarkKey(kind, id)] = 1;
    saveCollectedMarks(stateBox.projectId, stateBox.collected);
  }

  function readLegacyHistoryEntries(projectId) {
    try {
      const raw = localStorage.getItem(AI_RESULT_HISTORY_PREFIX + String(projectId || "0"));
      const arr = raw ? JSON.parse(raw) : [];
      return legacyHistoryEntries(arr);
    } catch (_) {
      return [];
    }
  }

  function applyDictReviewMute() {
    const hide = Boolean(isChromeOpen() && stateBox.hideDict);
    if (typeof setFeedbackDictHighlightMute === "function") {
      setFeedbackDictHighlightMute(hide);
    }
  }

  function isFbDebug() {
    try { return localStorage.getItem(DEBUG_KEY) === "1"; } catch (_) { return false; }
  }

  function formatLocateDiag(cards, locateById, mapping) {
    const numbered = numberCards(cards || []);
    const lines = ["순번\t제목\t범위\t성공\t방식\t이유\t원문30\t비슷한30"];
    for (let i = 0; i < numbered.length; i += 1) {
      const card = numbered[i].card;
      const found = locateById && locateById[String(card.id)];
      const title = String(card.title || "").replace(/\s+/g, " ").slice(0, 20);
      const orig = String(card.original_text || "").replace(/\s+/g, " ").slice(0, 30);
      let similar = "";
      const needle = String(card.original_text || "").replace(/\s+/g, " ").slice(0, 12);
      const map = Array.isArray(mapping) ? mapping : [];
      for (let p = 0; p < map.length; p += 1) {
        if (needle && String(map[p].text || "").indexOf(needle) >= 0) {
          similar = String(map[p].text || "").replace(/\s+/g, " ").slice(0, 30);
          break;
        }
      }
      lines.push([
        padCardIndex(numbered[i].index),
        title,
        (card.start_para || "") + "~" + (card.end_para || ""),
        found && found.ok ? "성공" : "실패",
        found && found.ok ? (found.method === "quotes" ? "인용" : found.method === "window" ? "창 탐색" : "문단번호") : "실패",
        found && found.ok ? "" : locateFailMessage(card, found),
        orig,
        similar,
      ].join("\t"));
    }
    return lines.join("\n");
  }

  function copyLocateDiag() {
    const run = stateBox.run;
    const mapping = mapEditorParagraphs(editorRoot());
    const layout = visibleCardLayout(
      (run && run.cards) || [],
      run && run.status === "running" ? "low" : stateBox.reveal,
      stateBox.sortMode
    );
    copyText(formatLocateDiag(layout.items, stateBox.locateById, mapping), "위치 진단을 복사했어요.");
  }

  function otherEditorActive() {
    if (typeof isFocusWriteOpen === "function" && isFocusWriteOpen()) return true;
    const st = typeof state !== "undefined" ? state : null;
    if (st && st.splitEnabled) return true;
    return false;
  }

  function editorScrollParent() {
    const editor = editorRoot();
    if (!editor) return null;
    const page = editor.closest(".manuscript-page");
    if (page) return page;
    return editor.parentElement;
  }

  function cardsListScroller() {
    const panel = panelEl();
    if (!panel) return null;
    return panel.querySelector(".fb-tab-panels") || panel.querySelector("[data-role='fb-cards']");
  }

  function firstLineRect(range) {
    if (!range) return null;
    if (typeof range.getClientRects === "function") {
      try {
        const rects = range.getClientRects();
        if (rects && rects.length) return rects[0];
      } catch (_) { /* ignore */ }
    }
    if (typeof range.getBoundingClientRect === "function") {
      try { return range.getBoundingClientRect(); } catch (_) { /* ignore */ }
    }
    return null;
  }

  function targetScrollTop(input) {
    const scrollTop = Number(input && input.scrollTop) || 0;
    const viewH = Math.max(0, Number(input && input.viewHeight) || 0);
    const contentH = Math.max(0, Number(input && input.contentHeight) || 0);
    const firstTop = Number(input && input.firstLineOffset) || 0;
    const rangeH = Math.max(0, Number(input && input.rangeHeight) || 0);
    let pad = input && input.pad != null ? Number(input.pad) : SCROLL_PAD_PX;
    if (!Number.isFinite(pad)) pad = SCROLL_PAD_PX;
    if (viewH && rangeH > 0 && rangeH <= viewH * SCROLL_FIT_RATIO && pad + rangeH > viewH) {
      pad = Math.max(0, viewH - rangeH);
    }
    const maxScroll = Math.max(0, contentH - viewH);
    let next = scrollTop + firstTop - pad;
    if (next < 0) next = 0;
    if (next > maxScroll) next = maxScroll;
    return next;
  }

  function ensureFlashEl() {
    const page = $("manuscriptPage");
    if (!page || typeof document === "undefined") return null;
    let el = $("fbLocateFlash");
    if (el) return el;
    el = document.createElement("div");
    el.id = "fbLocateFlash";
    el.className = "fb-locate-flash";
    el.setAttribute("aria-hidden", "true");
    page.appendChild(el);
    return el;
  }

  function ensureStartMarkEl() {
    const page = $("manuscriptPage");
    if (!page || typeof document === "undefined") return null;
    let el = $("fbLocateStart");
    if (el) return el;
    el = document.createElement("div");
    el.id = "fbLocateStart";
    el.className = "fb-locate-start";
    el.setAttribute("aria-hidden", "true");
    page.appendChild(el);
    return el;
  }

  function ensureAppliedFlashEl() {
    const page = $("manuscriptPage");
    if (!page || typeof document === "undefined") return null;
    let el = $("fbAppliedFlash");
    if (el) return el;
    el = document.createElement("div");
    el.id = "fbAppliedFlash";
    el.className = "fb-applied-flash";
    el.setAttribute("aria-hidden", "true");
    page.appendChild(el);
    return el;
  }

  function flashAppliedRange(found) {
    const flash = ensureAppliedFlashEl();
    const scroller = editorScrollParent();
    const range = found && found.range;
    if (!flash || !scroller || !range) return;
    const first = firstLineRect(range);
    const last = lastLineRect(range) || first;
    if (!first || !last) return;
    const host = scroller.getBoundingClientRect();
    const editor = editorRoot();
    const editorBox = editor && editor.getBoundingClientRect ? editor.getBoundingClientRect() : host;
    const top = first.top - host.top + (scroller.scrollTop || 0);
    const bottom = last.bottom - host.top + (scroller.scrollTop || 0);
    flash.style.top = top + "px";
    flash.style.left = (editorBox.left - host.left + (scroller.scrollLeft || 0)) + "px";
    flash.style.width = Math.max(24, editorBox.width || first.width) + "px";
    flash.style.height = Math.max(16, bottom - top) + "px";
    flash.classList.remove("is-on");
    void flash.offsetWidth;
    flash.classList.add("is-on");
    if (stateBox.appliedFlashTimer) clearTimeout(stateBox.appliedFlashTimer);
    stateBox.appliedFlashTimer = setTimeout(function () {
      stateBox.appliedFlashTimer = 0;
      flash.classList.remove("is-on");
    }, APPLIED_FLASH_MS);
  }

  function snapshotScroller() {
    const s = editorScrollParent();
    if (!s) return null;
    return { top: s.scrollTop || 0, left: s.scrollLeft || 0 };
  }

  function restoreScroller(snap) {
    const s = editorScrollParent();
    if (!s || !snap) return;
    s.scrollTop = snap.top;
    s.scrollLeft = snap.left;
  }

  function flashRange(range) {
    const flash = ensureFlashEl();
    const scroller = editorScrollParent();
    if (!flash || !range || typeof range.getBoundingClientRect !== "function") return;
    const rect = range.getBoundingClientRect();
    const host = scroller ? scroller.getBoundingClientRect() : { top: 0, left: 0 };
    flash.style.top = (rect.top - host.top + (scroller ? scroller.scrollTop : 0)) + "px";
    flash.style.left = (rect.left - host.left + (scroller ? scroller.scrollLeft : 0)) + "px";
    flash.style.width = Math.max(8, rect.width) + "px";
    flash.style.height = Math.max(8, rect.height) + "px";
    flash.classList.remove("is-on");
    void flash.offsetWidth;
    flash.classList.add("is-on");
    setTimeout(function () { flash.classList.remove("is-on"); }, 450);
  }

  function ensureBracketEl() {
    const page = $("manuscriptPage");
    if (!page || typeof document === "undefined") return null;
    let el = $("fbRangeBracket");
    if (el) return el;
    el = document.createElement("div");
    el.id = "fbRangeBracket";
    el.className = "fb-range-bracket";
    el.setAttribute("aria-hidden", "true");
    page.appendChild(el);
    return el;
  }

  function hideBracket() {
    const el = $("fbRangeBracket");
    if (el) el.classList.remove("is-on");
    stateBox.bracketCardId = null;
  }

  function placeOverlayBox(el, top, left, height, width) {
    if (!el) return;
    el.style.top = top + "px";
    el.style.left = left + "px";
    if (height != null) el.style.height = height + "px";
    if (width != null) el.style.width = width + "px";
  }

  function showBracket(range, tier) {
    const el = ensureBracketEl();
    const scroller = editorScrollParent();
    if (!el || !scroller || !range || typeof range.getBoundingClientRect !== "function") {
      hideBracket();
      return;
    }
    const box = range.getBoundingClientRect();
    const host = scroller.getBoundingClientRect();
    const top = box.top - host.top + (scroller.scrollTop || 0);
    const height = Math.max(12, box.height);
    const editor = editorRoot();
    const editorBox = editor && editor.getBoundingClientRect ? editor.getBoundingClientRect() : host;
    const left = Math.max(2, editorBox.left - host.left + (scroller.scrollLeft || 0) - 6);
    el.className = "fb-range-bracket is-on tier-" + (tier || "medium");
    placeOverlayBox(el, top, left, height, 3);
  }

  function ensureUlTipEl() {
    const page = $("manuscriptPage");
    if (!page || typeof document === "undefined") return null;
    let el = $("fbUlTip");
    if (el) return el;
    el = document.createElement("button");
    el.id = "fbUlTip";
    el.type = "button";
    el.className = "fb-ul-tip";
    el.hidden = true;
    el.addEventListener("click", function (event) {
      event.preventDefault();
      event.stopPropagation();
      const id = el.getAttribute("data-card-id");
      if (id) selectCard(id);
    });
    page.appendChild(el);
    return el;
  }

  function hideUlTip() {
    const el = $("fbUlTip");
    if (!el) return;
    el.hidden = true;
    el.textContent = "";
    el.removeAttribute("data-card-id");
  }

  function showUlTip(hit, clientX, clientY) {
    const el = ensureUlTipEl();
    const scroller = editorScrollParent();
    if (!el || !scroller || !hit) return;
    const host = scroller.getBoundingClientRect();
    el.textContent = overlapTooltipLabel(hit.indexes);
    el.setAttribute("data-card-id", String((hit.cardIds && hit.cardIds[0]) || ""));
    el.hidden = false;
    const x = clientX - host.left + (scroller.scrollLeft || 0);
    const y = clientY - host.top + (scroller.scrollTop || 0) + 16;
    placeOverlayBox(el, y, x, null, null);
  }

  function hitTestUl(clientX, clientY) {
    const hits = stateBox.ulHits || [];
    for (let i = 0; i < hits.length; i += 1) {
      const range = hits[i].range;
      if (!range || typeof range.getClientRects !== "function") continue;
      let rects;
      try { rects = range.getClientRects(); } catch (_) { continue; }
      if (!rects) continue;
      for (let r = 0; r < rects.length; r += 1) {
        const b = rects[r];
        if (clientX >= b.left && clientX <= b.right && clientY >= b.top && clientY <= b.bottom) {
          return hits[i];
        }
      }
    }
    return null;
  }

  function onEditorPointerMove(event) {
    if (!isChromeOpen() || !stateBox.showUnderline) {
      hideUlTip();
      return;
    }
    const tip = $("fbUlTip");
    if (tip && !tip.hidden && tip.contains(event.target)) return;
    const hit = hitTestUl(event.clientX, event.clientY);
    if (hit) showUlTip(hit, event.clientX, event.clientY);
    else hideUlTip();
  }

  function lastLineRect(range) {
    if (!range) return null;
    if (typeof range.getClientRects === "function") {
      try {
        const rects = range.getClientRects();
        if (rects && rects.length) return rects[rects.length - 1];
      } catch (_) { /* ignore */ }
    }
    return firstLineRect(range);
  }

  function ensureInlineBox() {
    const page = $("manuscriptPage");
    if (!page || typeof document === "undefined") return null;
    let el = $("fbInlineBox");
    if (el) return el;
    el = document.createElement("div");
    el.id = "fbInlineBox";
    el.className = "fb-inline-box";
    el.hidden = true;
    el.addEventListener("mousedown", function (event) {
      if (event.target && event.target.closest && event.target.closest("button, textarea")) {
        event.preventDefault();
      }
    });
    el.addEventListener("click", onInlineBoxClick);
    page.appendChild(el);
    return el;
  }

  function ensureActionBar() {
    const page = $("manuscriptPage");
    if (!page || typeof document === "undefined") return null;
    let el = $("fbActionBar");
    if (el) return el;
    el = document.createElement("div");
    el.id = "fbActionBar";
    el.className = "fb-action-bar";
    el.hidden = true;
    el.addEventListener("mousedown", function (event) {
      if (event.target && event.target.closest && event.target.closest("button")) event.preventDefault();
    });
    el.addEventListener("click", onActionBarClick);
    page.appendChild(el);
    return el;
  }

  function setScrollerApplyPad(on, pad) {
    const scroller = editorScrollParent();
    if (!scroller) return;
    if (on) {
      scroller.classList.add("fb-has-action-bar");
      scroller.style.paddingBottom = Math.max(48, Number(pad) || 64) + "px";
    } else {
      scroller.classList.remove("fb-has-action-bar");
      scroller.style.paddingBottom = "";
    }
  }

  function hideReviewChrome() {
    const box = $("fbInlineBox");
    const bar = $("fbActionBar");
    if (box) {
      box.hidden = true;
      box.removeAttribute("data-paint");
    }
    if (bar) {
      bar.hidden = true;
      bar.removeAttribute("data-paint");
    }
    setScrollerApplyPad(false);
    stateBox.inlineEdit = false;
    stateBox.pendingConfirm = false;
  }

  function activeCardRecord() {
    const id = stateBox.activeCardId;
    if (id == null) return null;
    const cards = (stateBox.run && stateBox.run.cards) || [];
    for (let i = 0; i < cards.length; i += 1) {
      if (String(cards[i].id) === String(id)) return cards[i];
    }
    return null;
  }

  function copyEditorType(el) {
    const editor = editorRoot();
    if (!el || !editor || typeof window === "undefined" || !window.getComputedStyle) return;
    const cs = window.getComputedStyle(editor);
    el.style.fontFamily = cs.fontFamily;
    el.style.fontSize = cs.fontSize;
    el.style.lineHeight = cs.lineHeight;
    el.style.color = cs.color;
  }

  function renderInlineBoxHtml(card, found) {
    const hasSug = cardHasSuggestion(card);
    const missing = !found || !found.ok;
    const other = otherEditorActive();
    const warn = cardHasWarning(card);
    let html = "<button type=\"button\" class=\"fb-inline-close\" data-fb-act=\"close\" aria-label=\"닫기\">×</button>";
    if (warn) html += "<p class=\"fb-inline-warn\">AI 검사, 확인 필요</p>";
    if (stateBox.inlineEdit) {
      const seed = hasSug ? String(card.suggestion || "") : String(card.original_text || "");
      html += "<p class=\"fb-inline-label\">수정해서 적용</p>";
      html += "<textarea data-role=\"fb-inline-edit\">" + esc(seed) + "</textarea>";
      html += "<div class=\"fb-inline-edit-actions\">";
      html += "<button type=\"button\" class=\"fb-primary\" data-fb-act=\"apply-edited\"" + (missing || other ? " disabled" : "") + ">적용</button>";
      html += "<button type=\"button\" class=\"secondary compact-btn\" data-fb-act=\"edit-cancel\">취소</button>";
      html += "</div>";
      if (other) html += "<p class=\"fb-inline-hint\">이 화면에서는 적용할 수 없어요. 수정안 복사를 이용해 주세요</p>";
      return html;
    }
    if (hasSug) {
      html += "<p class=\"fb-inline-label\">수정안(초안)</p>";
      html += "<div class=\"fb-inline-body\">" + addedWordHtml(card.original_text, card.suggestion) + "</div>";
      html += "<div class=\"fb-inline-actions\">";
      html += "<button type=\"button\" class=\"fb-primary\" data-fb-act=\"apply\"" + (missing || other ? " disabled" : "") + ">고치기</button>";
      html += "<button type=\"button\" class=\"secondary compact-btn\" data-fb-act=\"edit\">수정해서 적용</button>";
      html += "<button type=\"button\" class=\"secondary compact-btn\" data-fb-act=\"ignore\">무시하기</button>";
      html += "<button type=\"button\" class=\"secondary compact-btn\" data-fb-act=\"copy\">복사</button>";
      html += "</div>";
      if (missing) html += "<p class=\"fb-inline-hint\">위치를 찾을 수 없어요</p>";
      if (other) html += "<p class=\"fb-inline-hint\">이 화면에서는 적용할 수 없어요. 수정안 복사를 이용해 주세요</p>";
      if (stateBox.pendingConfirm) html += "<p class=\"fb-inline-warn\">줄 구조가 바뀔 수 있어요. 적용할까요? <button type=\"button\" class=\"fb-primary\" data-fb-act=\"apply-confirm\">적용</button></p>";
      return html;
    }
    html += "<p class=\"fb-inline-label\">" + esc(cardTitle(card, stateBox.run && stateBox.run.report)) + "</p>";
    html += "<p class=\"fb-inline-note\">" + esc(firstTwoSentences(card.reason || "")) + "</p>";
    html += "<p class=\"fb-inline-hint\">원고에서 직접 고쳐 주세요</p>";
    html += "<div class=\"fb-inline-actions\">";
    html += "<button type=\"button\" class=\"fb-primary\" data-fb-act=\"mark-applied\">해결함으로 표시</button>";
    html += "<button type=\"button\" class=\"secondary compact-btn\" data-fb-act=\"ignore\">무시하기</button>";
    html += "<button type=\"button\" class=\"secondary compact-btn\" data-fb-act=\"close\">닫기</button>";
    html += "</div>";
    return html;
  }

  function layoutInlineBox(card, found) {
    const el = ensureInlineBox();
    const scroller = editorScrollParent();
    const missing = !found || !found.ok;
    if (!el || !scroller || !card || isSettledStatus(card.status) || missing) {
      if (el) {
        el.hidden = true;
        el.removeAttribute("data-paint");
      }
      return;
    }
    const paintKey = [
      card.id,
      card.status || "open",
      stateBox.inlineEdit ? "e" : "v",
      stateBox.pendingConfirm ? "c" : "",
      String((card.suggestion || "").length),
      otherEditorActive() ? "o" : "m",
    ].join(":");
    if (el.getAttribute("data-paint") !== paintKey) {
      el.innerHTML = renderInlineBoxHtml(card, found);
      el.setAttribute("data-paint", paintKey);
    }
    el.hidden = false;
    copyEditorType(el.querySelector(".fb-inline-body") || el.querySelector("textarea") || el);
    const host = scroller.getBoundingClientRect();
    const range = found && found.ok ? found.range : null;
    const last = lastLineRect(range) || firstLineRect(range);
    const bar = $("fbActionBar");
    const barH = bar && !bar.hidden ? bar.offsetHeight : 0;
    const editor = editorRoot();
    const editorBox = editor && editor.getBoundingClientRect ? editor.getBoundingClientRect() : host;
    const line = last ? {
      top: last.top - host.top + (scroller.scrollTop || 0),
      bottom: last.bottom - host.top + (scroller.scrollTop || 0),
      left: editorBox.left - host.left + (scroller.scrollLeft || 0),
      width: editorBox.width,
    } : {
      top: scroller.scrollTop,
      bottom: scroller.scrollTop + 24,
      left: 16,
      width: editorBox.width || host.width,
    };
    const place = inlineBoxPlacement({
      view: { scrollTop: scroller.scrollTop || 0, viewHeight: scroller.clientHeight || host.height, viewWidth: scroller.clientWidth || host.width },
      lastLine: line,
      barVisible: Boolean(bar && !bar.hidden),
      barHeight: barH,
      desiredHeight: Math.min(240, el.scrollHeight || 160),
    });
    el.style.left = place.left + "px";
    el.style.top = place.top + "px";
    el.style.width = place.width + "px";
    el.style.maxHeight = place.maxHeight + "px";
  }

  function layoutActionBar(card) {
    const el = ensureActionBar();
    const scroller = editorScrollParent();
    if (!el || !scroller || !card || String(card.status) === "ignored") {
      if (el) {
        el.hidden = true;
        el.removeAttribute("data-paint");
      }
      setScrollerApplyPad(false);
      return;
    }
    const cards = (stateBox.run && stateBox.run.cards) || [];
    const walk = openWalkState(openWalkIds(cards, stateBox.reveal, stateBox.sortMode, stateBox.kindFilter), card.id);
    const hasSug = cardHasSuggestion(card);
    const found = stateBox.locateById[String(card.id)];
    const missing = !found || !found.ok;
    const other = otherEditorActive();
    const paintKey = [card.id, walk.label, walk.remaining, hasSug ? "s" : "n", missing ? "x" : "ok", other ? "o" : "m", isAppliedStatus(card.status) ? "a" : "o"].join(":");
    el.hidden = false;
    if (el.getAttribute("data-paint") !== paintKey) {
      el.innerHTML = "<div class=\"fb-action-nav\">"
        + "<button type=\"button\" class=\"secondary compact-btn\" data-fb-act=\"prev\" aria-label=\"이전\"" + (walk.prevId ? "" : " disabled") + ">◀</button>"
        + "<span class=\"fb-action-count\">" + esc(walk.label) + "</span>"
        + "<button type=\"button\" class=\"secondary compact-btn\" data-fb-act=\"next\" aria-label=\"다음\"" + (walk.nextId ? "" : " disabled") + ">▶</button>"
        + "</div>"
        + (hasSug && !isAppliedStatus(card.status) ? "<button type=\"button\" class=\"fb-primary\" data-fb-act=\"apply\"" + (missing || other ? " disabled" : "") + ">고치기</button>" : "")
        + "<button type=\"button\" class=\"secondary compact-btn\" data-fb-act=\"ignore\">무시하기</button>"
        + "<span class=\"fb-action-remain\">검토하지 않은 항목 " + walk.remaining + "개</span>"
        + "<button type=\"button\" class=\"secondary compact-btn\" data-fb-act=\"finish\">검토 끝내기</button>";
      el.setAttribute("data-paint", paintKey);
    }
    const host = scroller.getBoundingClientRect();
    const place = actionBarPlacement({
      scrollTop: scroller.scrollTop || 0,
      viewHeight: scroller.clientHeight || host.height,
      viewWidth: scroller.clientWidth || host.width,
      barHeight: el.offsetHeight || 44,
      barWidth: Math.min(520, (scroller.clientWidth || host.width) - 24),
    });
    el.style.top = place.top + "px";
    el.style.left = place.left + "px";
    el.style.width = place.width + "px";
    setScrollerApplyPad(true, place.pad);
  }

  function layoutReviewChrome() {
    if (!isChromeOpen() || !stateBox.activeCardId) {
      hideReviewChrome();
      return;
    }
    const card = activeCardRecord();
    const found = stateBox.locateById[String(stateBox.activeCardId)];
    layoutActionBar(card);
    layoutInlineBox(card, found);
  }

  function finishReview() {
    stateBox.activeCardId = null;
    hideReviewChrome();
    applyHighlights();
    renderCards();
  }

  function moveOpenCard(dir) {
    const cards = (stateBox.run && stateBox.run.cards) || [];
    const walk = openWalkState(openWalkIds(cards, stateBox.reveal, stateBox.sortMode, stateBox.kindFilter), stateBox.activeCardId);
    const next = dir < 0 ? walk.prevId : walk.nextId;
    if (next) selectCard(next, { force: true });
  }

  function peekNextOpen(cardId) {
    const cards = (stateBox.run && stateBox.run.cards) || [];
    return openWalkState(
      openWalkIds(cards, stateBox.reveal, stateBox.sortMode, stateBox.kindFilter),
      cardId
    ).nextId;
  }

  function afterResolveAdvance(nextId) {
    if (stateBox.autoAdvance && nextId) {
      selectCard(nextId, { force: true });
      return;
    }
    if (stateBox.autoAdvance && !nextId) {
      toastMsg("검토를 마쳤어요");
      finishReview();
      return;
    }
    applyHighlights();
    renderCards();
  }

  function onInlineBoxClick(event) {
    const btn = event.target && event.target.closest && event.target.closest("[data-fb-act]");
    if (!btn) return;
    event.preventDefault();
    handleReviewAct(btn.getAttribute("data-fb-act"));
  }

  function onActionBarClick(event) {
    const btn = event.target && event.target.closest && event.target.closest("[data-fb-act]");
    if (!btn) return;
    event.preventDefault();
    handleReviewAct(btn.getAttribute("data-fb-act"));
  }

  function handleReviewAct(act) {
    const card = activeCardRecord();
    if (act === "close") {
      finishReview();
      return;
    }
    if (act === "finish") {
      finishReview();
      return;
    }
    if (act === "prev") { moveOpenCard(-1); return; }
    if (act === "next") { moveOpenCard(1); return; }
    if (!card) return;
    if (act === "copy") { copyText(card.suggestion); return; }
    if (act === "ignore") { ignoreCardAndAdvance(card.id); return; }
    if (act === "edit") { stateBox.inlineEdit = true; stateBox.pendingConfirm = false; layoutReviewChrome(); return; }
    if (act === "edit-cancel") { stateBox.inlineEdit = false; layoutReviewChrome(); return; }
    if (act === "apply") { applyCardFromUi(card.id, {}); return; }
    if (act === "apply-confirm") { applyCardFromUi(card.id, { confirm: true }); return; }
    if (act === "apply-edited") {
      const area = $("fbInlineBox") && $("fbInlineBox").querySelector("[data-role='fb-inline-edit']");
      applyCardFromUi(card.id, { editedText: area ? area.value : card.suggestion, edited: true, confirm: true });
      return;
    }
    if (act === "mark-applied") { markCardAppliedWithoutEdit(card.id); return; }
  }

  function relayoutOverlays() {
    if (!isChromeOpen()) return;
    const id = stateBox.activeCardId;
    const found = id != null ? stateBox.locateById[String(id)] : null;
    if (found && found.ok && Number(found.span) >= 2 && found.range) {
      const card = ((stateBox.run && stateBox.run.cards) || []).find(function (item) {
        return String(item.id) === String(id);
      });
      showBracket(found.range, underlineTier(card));
    } else {
      hideBracket();
    }
    layoutReviewChrome();
  }

  function bindOverlayRelayout() {
    if (stateBox.overlayBound || typeof document === "undefined") return;
    const scroller = editorScrollParent();
    if (scroller) scroller.addEventListener("scroll", relayoutOverlays, { passive: true });
    window.addEventListener("resize", relayoutOverlays);
    const editor = editorRoot();
    if (editor) {
      editor.addEventListener("mousemove", onEditorPointerMove);
      editor.addEventListener("mouseleave", function (event) {
        const tip = $("fbUlTip");
        if (tip && event.relatedTarget && tip.contains(event.relatedTarget)) return;
        hideUlTip();
      });
    }
    if (scroller && scroller !== editor) {
      scroller.addEventListener("mousemove", onEditorPointerMove);
    }
    stateBox.overlayBound = true;
  }

  function flashStartMark(range, gen) {
    const mark = ensureStartMarkEl();
    const scroller = editorScrollParent();
    const line = firstLineRect(range);
    if (!mark || !scroller || !line) return;
    const host = scroller.getBoundingClientRect();
    mark.style.top = (line.top - host.top + scroller.scrollTop) + "px";
    mark.style.left = Math.max(0, line.left - host.left + (scroller.scrollLeft || 0) - 6) + "px";
    mark.style.height = Math.max(16, line.height) + "px";
    mark.classList.remove("is-on");
    void mark.offsetWidth;
    mark.classList.add("is-on");
    if (stateBox.startMarkTimer) clearTimeout(stateBox.startMarkTimer);
    stateBox.startMarkTimer = setTimeout(function () {
      stateBox.startMarkTimer = 0;
      if (gen != null && gen !== stateBox.scrollGen) return;
      mark.classList.remove("is-on");
    }, START_MARK_MS);
  }

  function scrollRangeIntoView(range, opts) {
    const scroller = editorScrollParent();
    if (!scroller || !range) return;
    const line = firstLineRect(range);
    const box = typeof range.getBoundingClientRect === "function" ? range.getBoundingClientRect() : line;
    if (!line || !box) return;
    const host = scroller.getBoundingClientRect();
    const gen = (stateBox.scrollGen || 0) + 1;
    stateBox.scrollGen = gen;
    const next = targetScrollTop({
      scrollTop: scroller.scrollTop || 0,
      viewHeight: scroller.clientHeight || host.height || 0,
      contentHeight: scroller.scrollHeight || 0,
      firstLineOffset: line.top - host.top,
      rangeHeight: box.height,
      pad: SCROLL_PAD_PX,
    });
    if (typeof scroller.scrollTo === "function") {
      scroller.scrollTo({ top: next, behavior: "smooth" });
    } else {
      scroller.scrollTop = next;
    }
    if (!(opts && opts.mark === false)) flashStartMark(range, gen);
  }

  function visibleCardList() {
    const run = stateBox.run;
    const cards = (run && run.cards) || [];
    const running = Boolean(run && run.status === "running");
    return visibleCards(cards, running ? "low" : stateBox.reveal, stateBox.sortMode, stateBox.kindFilter);
  }

  function locateFailMessage(card, found) {
    if (found && found.ok) return "";
    if (isAppliedStatus(card && card.status)) {
      return "적용된 위치를 찾을 수 없어요. 원고가 그 뒤에 바뀐 것 같아요";
    }
    if (found && found.reason) return found.reason;
    const start = Number(card && card.start_para) || 0;
    const end = Number(card && card.end_para) || start;
    if (start && end - start + 1 > 5) return "범위가 너무 넓어요";
    const saved = stateBox.run && (stateBox.run.paragraphs || []);
    const live = paragraphsFromEditor(editorRoot());
    if (paragraphsDiffer(saved, live)) return "분석 때와 원고가 달라요";
    return "인용문을 찾지 못했어요";
  }

  function underlineRangesFor(card, found, isActive, mapping) {
    if (!found || !found.ok) return [];
    const map = Array.isArray(mapping) ? mapping : [];
    if (map.length) return underlineJoinSpans(found, map);
    const start = Number(found.startPara) || Number(card && card.start_para) || 0;
    const end = Number(found.endPara) || Number(card && card.end_para) || start;
    return longRangeEmphasis(start, end).strong.map(function (n) {
      return { para: n };
    });
  }

  function activeCoveragePlan(found, mapping) {
    const start = Number(found && found.startPara) || 0;
    const end = Number(found && found.endPara) || start;
    const band = paraBandPlan(start, end, mapping);
    const strong = [];
    const mid = [];
    for (let i = 0; i < band.strong.length; i += 1) {
      const range = paraWholeRange(mapping, band.strong[i]);
      if (range) strong.push(range);
    }
    for (let i = 0; i < band.mid.length; i += 1) {
      const range = paraWholeRange(mapping, band.mid[i]);
      if (range) mid.push(range);
    }
    return {
      strong: strong,
      mid: mid,
      strongParas: band.strong.slice(),
      midParas: band.mid.slice(),
      span: band.span,
    };
  }

  function activeBgPlan(found, mapping) {
    const map = Array.isArray(mapping) ? mapping : [];
    if (found && found.ok && map.length && (Number(found.startPara) || Number(found.endPara))) {
      return activeCoveragePlan(found, map);
    }
    const span = Number(found && found.span) || (Number(found && found.endPara) - Number(found && found.startPara) + 1);
    if (span > 5) {
      return {
        strong: (found.edgeRanges && found.edgeRanges.length)
          ? found.edgeRanges
          : (found.range ? [found.range] : []),
        mid: found.midRanges || [],
      };
    }
    if (found.allRanges && found.allRanges.length) return { strong: found.allRanges, mid: [] };
    if (found.edgeRanges && found.edgeRanges.length) return { strong: found.edgeRanges, mid: [] };
    return { strong: found.range ? [found.range] : [], mid: [] };
  }

  function activePaintPlan(card, found, mapping) {
    const del = [];
    const range = [];
    const rangeMid = [];
    const note = [];
    const mid = [];
    if (!found || !found.ok) {
      return { del: del, range: range, rangeMid: rangeMid, note: note, mid: mid, replace: del };
    }
    const hasSug = cardHasSuggestion(card);
    const bg = activeBgPlan(found, mapping);
    const dels = found.delRanges || [];
    if (hasSug) {
      for (let i = 0; i < dels.length; i += 1) del.push(dels[i]);
      for (let i = 0; i < bg.strong.length; i += 1) range.push(bg.strong[i]);
      for (let i = 0; i < bg.mid.length; i += 1) rangeMid.push(bg.mid[i]);
    } else {
      for (let i = 0; i < bg.strong.length; i += 1) note.push(bg.strong[i]);
      for (let i = 0; i < bg.mid.length; i += 1) mid.push(bg.mid[i]);
    }
    return { del: del, range: range, rangeMid: rangeMid, note: note, mid: mid, replace: del };
  }

  function delRangesForCard(card, found, mapping) {
    if (!cardHasSuggestion(card) || !found || !found.ok) {
      return { skipped: false, ranges: [], newlineOnly: false };
    }
    const list = Array.isArray(mapping) ? mapping : [];
    const startP = Number(found.startPara) || Number(card.start_para) || 0;
    const endP = Number(found.endPara) || Number(card.end_para) || startP;
    const subset = joinParaRange(list, startP, endP);
    const mapped = mapDelSpansToParaSlices(card.original_text, card.suggestion, subset);
    if (mapped.skipped) return { skipped: true, ranges: [], newlineOnly: false };
    return {
      skipped: false,
      ranges: rangesFromParaSlices(mapped.slices),
      newlineOnly: Boolean(mapped.newlineOnly),
    };
  }

  function applyHighlights() {
    if (!isChromeOpen()) {
      clearCardHighlights();
      hideUlTip();
      hideBracket();
      hideReviewChrome();
      return;
    }
    if (otherEditorActive()) {
      clearCardHighlights();
      hideUlTip();
      hideBracket();
      layoutReviewChrome();
      return;
    }
    const mapping = stateBox.mapping || [];
    const visible = visibleCardList();
    const numbered = numberCards(visible);
    const extra = { hoverId: stateBox.hoverCardId, activeId: stateBox.activeCardId };
    const ulSpans = [];
    let active = { del: [], range: [], rangeMid: [], note: [], mid: [], hover: [], applied: [] };
    let bracketFound = null;
    let bracketTier = "medium";
    for (let i = 0; i < numbered.length; i += 1) {
      const card = numbered[i].card;
      const isActive = String(card.id) === String(stateBox.activeCardId);
      const isHover = String(card.id) === String(stateBox.hoverCardId) && !isActive;
      if (isSettledStatus(card && card.status)) {
        if (isActive && isAppliedStatus(card.status)) {
          const found = stateBox.locateById[String(card.id)];
          if (found && found.ok) {
            const cov = activeCoveragePlan(found, mapping);
            const appliedRanges = (cov.strong || []).concat(cov.mid || []);
            for (let a = 0; a < appliedRanges.length; a += 1) active.applied.push(appliedRanges[a]);
          }
        }
        continue;
      }
      const found = stateBox.locateById[String(card.id)];
      if (isActive && found && found.ok) {
        const dels = delRangesForCard(card, found, mapping);
        found.delRanges = dels.skipped ? [] : dels.ranges;
        found.diffSkipped = dels.skipped;
        active = activePaintPlan(card, found, mapping);
        active.hover = [];
        bracketFound = found;
        bracketTier = underlineTier(card);
      } else if (isHover && found && found.ok) {
        const cov = activeCoveragePlan(found, mapping);
        const hoverRanges = (cov.strong || []).concat(cov.mid || []);
        for (let h = 0; h < hoverRanges.length; h += 1) active.hover.push(hoverRanges[h]);
      }
      if (stateBox.showUnderline && shouldPaintUnderline(card, stateBox.underlineLevel, extra)) {
        if (found && found.ok) {
          const spans = underlineJoinSpans(found, mapping);
          for (let s = 0; s < spans.length; s += 1) {
            ulSpans.push({
              start: spans[s].start,
              end: spans[s].end,
              tier: underlineTier(card),
              cardId: card.id,
              index: numbered[i].index,
              order: spans[s].start,
            });
          }
        }
      }
    }
    const merged = mergeUnderlineSpans(ulSpans);
    const byTier = { high: [], medium: [], low: [] };
    const hits = [];
    for (let i = 0; i < merged.length; i += 1) {
      const seg = merged[i];
      const ranges = rangesFromGlobalSpan(mapping, seg.start, seg.end);
      if (!ranges.length) continue;
      for (let r = 0; r < ranges.length; r += 1) byTier[seg.tier].push(ranges[r]);
      if (seg.cardIds && seg.cardIds.length > 1) {
        hits.push({
          range: ranges[0],
          cardIds: seg.cardIds,
          indexes: seg.indexes,
          tier: seg.tier,
        });
      }
    }
    stateBox.ulHits = hits;
    paintCardHighlights(byTier, active);
    if (bracketFound && Number(bracketFound.span) >= 2 && bracketFound.range) {
      showBracket(bracketFound.range, bracketTier);
    } else {
      hideBracket();
    }
    layoutReviewChrome();
  }

  function refreshLocations() {
    const editor = editorRoot();
    const run = stateBox.run;
    stateBox.locateById = Object.create(null);
    if (!editor || !run || otherEditorActive()) {
      stateBox.mapping = [];
      clearCardHighlights();
      hideUlTip();
      hideBracket();
      hideReviewChrome();
      return;
    }
    const mapping = mapEditorParagraphs(editor);
    stateBox.mapping = mapping;
    const cards = Array.isArray(run.cards) ? run.cards : [];
    for (let i = 0; i < cards.length; i += 1) {
      const card = cards[i];
      stateBox.locateById[String(card.id)] = locateCard(card, mapping);
    }
    applyHighlights();
  }

  function scheduleLocateRefresh() {
    if (stateBox.locateTimer) clearTimeout(stateBox.locateTimer);
    stateBox.locateTimer = setTimeout(function () {
      stateBox.locateTimer = 0;
      refreshLocations();
      renderCards();
    }, LOCATE_DEBOUNCE_MS);
  }

  function selectCard(cardId, opts) {
    const silent = opts && opts.silent;
    const force = opts && opts.force;
    if (!force && !silent && cardId && String(stateBox.activeCardId) === String(cardId)) {
      finishReview();
      return;
    }
    stateBox.inlineEdit = false;
    stateBox.pendingConfirm = false;
    stateBox.activeCardId = cardId;
    const card = ((stateBox.run && stateBox.run.cards) || []).find(function (item) {
      return String(item.id) === String(cardId);
    });
    const found = stateBox.locateById[String(cardId)];
    applyHighlights();
    renderCards();
    if (silent) return;
    if (otherEditorActive()) {
      toastMsg("이 화면에서는 위치 표시를 지원하지 않아요");
      layoutReviewChrome();
      return;
    }
    if (!found || !found.ok || !found.range) {
      toastMsg(locateFailMessage(card, found));
      layoutReviewChrome();
      return;
    }
    if (isAppliedStatus(card && card.status)) {
      scrollRangeIntoView(found.range, { mark: false });
      flashAppliedRange(found);
      layoutReviewChrome();
      return;
    }
    scrollRangeIntoView(found.range);
    if (!highlightSupportsCss()) flashRange(found.range);
    layoutReviewChrome();
  }

  async function persistThenContinue() {
    if (typeof sceneDirty !== "undefined" && sceneDirty) {
      if (typeof persistScene !== "function") {
        toastMsg("저장할 수 없어 분석을 시작하지 않았어요.");
        return false;
      }
      try {
        const saved = await persistScene({ quiet: false, saveNote: "첨삭 전 저장" });
        if (saved && saved.localOnly) {
          toastMsg("저장에 실패해서 분석을 시작하지 않았어요.");
          return false;
        }
      } catch (error) {
        toastMsg((error && error.message) || "저장에 실패해서 분석을 시작하지 않았어요.");
        return false;
      }
    }
    if (typeof waitForSceneSaveIdle === "function") await waitForSceneSaveIdle();
    return true;
  }

  function stopPolling() {
    if (stateBox.pollTimer) {
      clearInterval(stateBox.pollTimer);
      stateBox.pollTimer = 0;
    }
  }

  function startPolling() {
    stopPolling();
    if (!isChromeOpen()) return;
    const run = stateBox.run;
    if (!run || run.status !== "running") return;
    stateBox.pollTimer = setInterval(function () {
      loadRun(run.id, { quiet: true }).catch(function (error) {
        if (typeof handleError === "function") handleError(error);
      });
    }, POLL_MS);
  }

  async function apiCall(path, options) {
    if (typeof api !== "function") throw new Error("서버에 연결할 수 없습니다.");
    return api(path, options || {});
  }

  async function loadStatus() {
    const ctx = currentScene();
    const qs = ctx.projectId ? ("?project_id=" + ctx.projectId) : "";
    const body = await apiCall("/api/feedback/status" + qs);
    stateBox.configured = Boolean(body && body.configured);
    stateBox.serverFake = Boolean(body && body.fake);
    const serverDefault = (body && body.default_explanation_lens) || "normal";
    stateBox.defaultLens = serverDefault === "strong" ? "strong" : "normal";
    stateBox.lens = resolveExplanationLens(
      readStoredLens(ctx.projectId),
      stateBox.defaultLens
    );
    return body;
  }

  async function loadRuns() {
    const ctx = currentScene();
    if (!ctx.projectId || !ctx.sceneId) {
      stateBox.runs = [];
      return [];
    }
    const rows = await apiCall(
      "/api/projects/" + ctx.projectId + "/feedback/runs?scene_id=" + ctx.sceneId
    );
    stateBox.runs = Array.isArray(rows) ? rows : [];
    return stateBox.runs;
  }

  async function loadRun(runId, options) {
    const quiet = options && options.quiet;
    const body = await apiCall("/api/feedback/runs/" + runId);
    const sameQuiet = quiet && Number(stateBox.run && stateBox.run.id) === Number(runId);
    stateBox.run = body;
    if (!sameQuiet) {
      stateBox.kindFilter = [];
      stateBox.commentsByCard = Object.create(null);
      stateBox.commentOpen = Object.create(null);
      stateBox.commentSending = Object.create(null);
      stateBox.commentError = Object.create(null);
      stateBox.commentLimit = Object.create(null);
    }
    closePopovers();
    if (!quiet) stateBox.reveal = "high";
    if (body && body.status !== "running") {
      try { await loadRuns(); } catch (_) { /* keep current run */ }
    }
    renderAll();
    if (body && body.status === "running") startPolling();
    else stopPolling();
    return body;
  }

  async function refreshForScene() {
    const ctx = currentScene();
    stateBox.projectId = ctx.projectId;
    stateBox.sceneId = ctx.sceneId;
    stateBox.sceneTitle = ctx.title;
    stateBox.sortMode = readSortPref(ctx.projectId);
    stateBox.underlineLevel = readUnderlineLevel(ctx.projectId);
    stateBox.hideDict = readHideDictPref(ctx.projectId);
    stateBox.collected = loadCollectedMarks(ctx.projectId);
    applyDictReviewMute();
    if (!isChromeOpen()) {
      stopPolling();
      return;
    }
    try {
      await loadStatus();
      const rows = await loadRuns();
      const pendingId = Number(stateBox.pendingOpenRunId) || 0;
      stateBox.pendingOpenRunId = 0;
      if (pendingId) {
        const pending = rows.find(function (row) {
          return Number(row && row.id) === pendingId;
        });
        if (pending) {
          stateBox.tab = historyTabForRun(pending);
          stateBox.tabLocked = true;
          await loadRun(pending.id, { quiet: true });
          return;
        }
      }
      const chosen = pickDefaultRun(rows);
      if (chosen) await loadRun(chosen.id, { quiet: true });
      else {
        stateBox.run = null;
        renderAll();
      }
    } catch (error) {
      if (typeof handleError === "function") handleError(error);
      else toastMsg((error && error.message) || "기록을 불러오지 못했어요.");
    }
  }

  function startBtnDisabled() {
    return !stateBox.configured || stateBox.busy
      || Boolean(stateBox.run && stateBox.run.status === "running");
  }

  async function startAnalysis() {
    const ctx = currentScene();
    if (!ctx.projectId || !ctx.sceneId) {
      toastMsg("먼저 목차에서 회차 하나를 열어 주세요.");
      return;
    }
    if (!stateBox.configured) {
      toastMsg("ANTHROPIC_API_KEY가 설정되지 않았습니다. 설정 방법은 .env에 키를 넣는 것입니다.");
      return;
    }
    if (stateBox.busy) return;
    stateBox.busy = true;
    stateBox.tabLocked = false;
    stateBox.tab = "report";
    renderAll();
    try {
      const saved = await persistThenContinue();
      if (!saved) return;
      const created = await apiCall("/api/projects/" + ctx.projectId + "/feedback/runs", {
        method: "POST",
        body: JSON.stringify(analysisRequestBody(ctx.sceneId, stateBox.lens)),
      });
      stateBox.reveal = "high";
      await loadRuns();
      await loadRun(created.run_id, { quiet: true });
    } catch (error) {
      toastMsg((error && error.message) || "분석을 시작하지 못했어요.");
    } finally {
      stateBox.busy = false;
      renderAll();
    }
  }

  async function cancelRun() {
    const run = stateBox.run;
    if (!run || !run.id) return;
    try {
      await apiCall("/api/feedback/runs/" + run.id + "/cancel", {
        method: "POST",
        body: JSON.stringify({}),
      });
      await loadRun(run.id, { quiet: true });
    } catch (error) {
      toastMsg((error && error.message) || "취소하지 못했어요.");
    }
  }

  async function putCardStatus(cardId, status, extra) {
    const body = { status: status };
    if (extra && extra.final_text != null) body.final_text = extra.final_text;
    await apiCall("/api/feedback/cards/" + cardId, {
      method: "PUT",
      body: JSON.stringify(body),
    });
    if (stateBox.run && stateBox.run.id) await loadRun(stateBox.run.id, { quiet: true });
  }

  async function setCardStatus(cardId, status) {
    try {
      await putCardStatus(cardId, status);
    } catch (error) {
      toastMsg((error && error.message) || "카드 상태를 바꾸지 못했어요.");
    }
  }

  function liveRangeFromSlice(slice) {
    if (!slice || !slice.para) return null;
    return rangeFromHit([slice.para], slice.localStart, slice.localEnd);
  }

  function liveRangeFromSlices(slices) {
    if (!slices || !slices.length) return null;
    const first = liveRangeFromSlice(slices[0]);
    const last = liveRangeFromSlice(slices[slices.length - 1]);
    if (!first || !last) return first;
    if (typeof document !== "undefined" && document.createRange) {
      try {
        const range = document.createRange();
        range.setStart(first.startContainer, first.startOffset);
        range.setEnd(last.endContainer, last.endOffset);
        return range;
      } catch (_) { return first; }
    }
    return first;
  }

  function focusEditorForApply() {
    const editor = editorRoot();
    if (!editor) return null;
    try {
      if (editor.focus) editor.focus({ preventScroll: true });
    } catch (_) {
      try { editor.focus(); } catch (__) { /* ignore */ }
    }
    return editor;
  }

  function execSelectRange(range) {
    if (!range || typeof window === "undefined" || !window.getSelection) return false;
    const sel = window.getSelection();
    sel.removeAllRanges();
    if (range.cloneRange) {
      sel.addRange(range.cloneRange());
      return true;
    }
    if (typeof document !== "undefined" && document.createRange && range.startContainer) {
      const next = document.createRange();
      next.setStart(range.startContainer, range.startOffset);
      next.setEnd(range.endContainer, range.endOffset);
      sel.addRange(next);
      return true;
    }
    return false;
  }

  function execCommandNamed(name, value) {
    if (typeof document === "undefined" || typeof document.execCommand !== "function") return false;
    try {
      if (value == null) return document.execCommand(name);
      return document.execCommand(name, false, value);
    } catch (_) {
      return false;
    }
  }

  function runApplyPlan(plan, opts) {
    opts = opts || {};
    const editor = opts.editor || focusEditorForApply();
    if (!plan || !plan.ok || !editor) return { ok: false, reason: "editor", undoCount: 0 };
    if (plan.needsConfirm && !opts.confirm) return { ok: false, reason: "confirm", needsConfirm: true, undoCount: 0 };
    const steps = (plan.steps || []).slice();
    steps.sort(function (a, b) {
      const pa = Number((a.slice && a.slice.i) || (a.para) || 0);
      const pb = Number((b.slice && b.slice.i) || (b.para) || 0);
      if (pb !== pa) return pb - pa;
      const la = a.slice ? Number(a.slice.localStart) : 0;
      const lb = b.slice ? Number(b.slice.localStart) : 0;
      return lb - la;
    });
    let undoCount = 0;
    for (let i = 0; i < steps.length; i += 1) {
      const step = steps[i];
      if (step.kind === "skip") continue;
      if (step.kind === "replace-all" || step.kind === "delete-all") {
        const range = liveRangeFromSlices(step.slices || plan.slices);
        if (!range || !execSelectRange(range)) return { ok: false, reason: "range", undoCount: undoCount };
        const ok = step.kind === "delete-all"
          ? execCommandNamed("delete")
          : execCommandNamed("insertHTML", step.html);
        if (!ok) return { ok: false, reason: "exec", undoCount: undoCount };
        undoCount += 1;
        continue;
      }
      const range = liveRangeFromSlice(step.slice);
      if (!range || !execSelectRange(range)) return { ok: false, reason: "range", undoCount: undoCount };
      const ok = step.kind === "delete"
        ? execCommandNamed("delete")
        : execCommandNamed("insertText", step.text);
      if (!ok) return { ok: false, reason: "exec", undoCount: undoCount };
      undoCount += 1;
    }
    return { ok: true, undoCount: undoCount };
  }

  function expectedLiveAfterPlan(plan) {
    if (!plan) return "";
    if (plan.mode === "all") return String(plan.expectedText || "");
    const parts = [];
    const steps = plan.steps || [];
    for (let i = 0; i < steps.length; i += 1) {
      if (steps[i].kind === "delete" || steps[i].kind === "delete-all") continue;
      parts.push(String(steps[i].text || ""));
    }
    return parts.join("\n");
  }

  function readPlanLiveText(mapping, plan) {
    if (!plan) return "";
    const expected = String(plan.expectedText || "");
    if (plan.joinStart != null) {
      return joinedTextAt(mapping, plan.joinStart, expected.length);
    }
    if (plan.mode === "all") {
      return joinedText(joinParaRange(mapping, plan.startPara, plan.endPara));
    }
    const slices = slicesFromFound({
      ok: true,
      startPara: plan.startPara,
      endPara: plan.endPara,
      joinStart: plan.joinStart,
      joinEnd: plan.joinEnd,
    }, mapping);
    return liveTextFromSlices(slices);
  }

  function applyVerifyOk(mapping, plan) {
    if (!plan) return false;
    const expected = expectedLiveAfterPlan(plan);
    if (expected === "") {
      const live = joinedTextAt(mapping, plan.joinStart, String(plan.originalText || "").length);
      return normalizeApplyText(live) !== normalizeApplyText(plan.originalText || "");
    }
    return normalizeApplyText(joinedTextAt(mapping, plan.joinStart, expected.length)) === normalizeApplyText(expected);
  }

  function undoApplyEdits(maxTimes, checkFn) {
    let n = 0;
    const limit = Math.max(1, Number(maxTimes) || APPLY_UNDO_MAX);
    while (n < limit) {
      if (typeof checkFn === "function" && checkFn()) return n;
      if (!execCommandNamed("undo")) break;
      n += 1;
      if (typeof checkFn === "function" && checkFn()) return n;
    }
    return n;
  }

  function rememberApply(card, plan, status) {
    stateBox.applyMemory = (stateBox.applyMemory || []).filter(function (row) {
      return String(row.cardId) !== String(card.id);
    });
    stateBox.applyMemory.push({
      cardId: card.id,
      status: status,
      appliedStatus: status,
      expectedText: plan.expectedText,
      originalText: plan.originalText,
      startPara: plan.startPara,
      endPara: plan.endPara,
      joinStart: plan.joinStart,
      joinEnd: plan.joinEnd,
      sceneId: currentScene().sceneId,
    });
  }

  function dispatchEditorInput() {
    const editor = editorRoot();
    if (!editor || typeof document === "undefined") return;
    try {
      editor.dispatchEvent(new Event("input", { bubbles: true }));
    } catch (_) { /* ignore */ }
    if (typeof markSceneDirty === "function") markSceneDirty();
  }

  async function persistBeforeFirstApply() {
    const ctx = currentScene();
    const key = String(ctx.sceneId || "") + ":" + String((stateBox.run && stateBox.run.id) || "");
    if (stateBox.preApplySavedKey === key) return true;
    if (typeof persistScene !== "function") {
      toastMsg("저장할 수 없어 적용하지 않았어요.");
      return false;
    }
    try {
      const saved = await persistScene({ quiet: false, saveNote: "첨삭 적용 전 저장" });
      if (saved && saved.localOnly) {
        toastMsg("저장에 실패해서 적용하지 않았어요.");
        return false;
      }
      stateBox.preApplySavedKey = key;
      return true;
    } catch (error) {
      toastMsg((error && error.message) || "저장에 실패해서 적용하지 않았어요.");
      return false;
    }
  }

  function verifyAppliedText(plan) {
    return applyVerifyOk(mapEditorParagraphs(editorRoot()), plan);
  }

  async function applyCardFromUi(cardId, opts) {
    opts = opts || {};
    if (stateBox.applyBusy) return;
    const cards = (stateBox.run && stateBox.run.cards) || [];
    let card = null;
    for (let i = 0; i < cards.length; i += 1) {
      if (String(cards[i].id) === String(cardId)) card = cards[i];
    }
    if (!card) return;
    if (String(stateBox.activeCardId) !== String(cardId)) selectCard(cardId, { force: true });
    if (otherEditorActive()) {
      toastMsg("이 화면에서는 적용할 수 없어요. 수정안 복사를 이용해 주세요");
      layoutReviewChrome();
      return;
    }
    refreshLocations();
    const found = stateBox.locateById[String(cardId)];
    const plan = planCardApply(card, stateBox.mapping, found, opts.editedText);
    if (!plan.ok) {
      toastMsg(plan.message);
      layoutReviewChrome();
      return;
    }
    if (plan.needsConfirm && !opts.confirm) {
      stateBox.pendingConfirm = true;
      layoutReviewChrome();
      return;
    }
    stateBox.pendingConfirm = false;
    const nextId = peekNextOpen(cardId);
    const scrollerSnap = snapshotScroller();
    if (!(await persistBeforeFirstApply())) {
      restoreScroller(scrollerSnap);
      return;
    }
    restoreScroller(scrollerSnap);
    stateBox.applyBusy = true;
    const ran = runApplyPlan(plan, { confirm: true });
    if (!ran.ok) {
      stateBox.applyBusy = false;
      toastMsg("적용하지 못했어요. 수정안을 복사해 주세요");
      restoreScroller(scrollerSnap);
      return;
    }
    if (!verifyAppliedText(plan)) {
      undoApplyEdits(APPLY_UNDO_MAX, function () {
        const mapping = mapEditorParagraphs(editorRoot());
        return normalizeApplyText(readPlanLiveText(mapping, plan)) === normalizeApplyText(plan.originalText);
      });
      stateBox.applyBusy = false;
      toastMsg("적용에 실패해서 되돌렸어요. 수정안을 복사해 주세요");
      restoreScroller(scrollerSnap);
      return;
    }
    dispatchEditorInput();
    const status = opts.edited ? "applied_edited" : "applied";
    card.status = status;
    if (opts.edited) card.final_text = plan.expectedText;
    rememberApply(card, plan, status);
    try {
      await putCardStatus(cardId, status, opts.edited ? { final_text: plan.expectedText } : null);
    } catch (error) {
      toastMsg("기록에 저장하지 못했어요", 8000, {
        action: {
          label: "다시 시도",
          onClick: function () {
            putCardStatus(cardId, status, opts.edited ? { final_text: plan.expectedText } : null).catch(function (err) {
              toastMsg((err && err.message) || "기록에 저장하지 못했어요");
            });
          },
        },
      });
    }
    refreshLocations();
    stateBox.applyBusy = false;
    stateBox.inlineEdit = false;
    const shouldAdvance = Boolean(stateBox.autoAdvance && nextId);
    toastMsg("적용했어요", 10000, {
      action: {
        label: "되돌리기",
        onClick: function () { undoLastApply(cardId); },
      },
    });
    if (shouldAdvance) {
      afterResolveAdvance(nextId);
      return;
    }
    restoreScroller(scrollerSnap);
    afterResolveAdvance(nextId);
    restoreScroller(scrollerSnap);
    layoutReviewChrome();
    flashAppliedRange(stateBox.locateById[String(cardId)]);
  }

  function undoLastApply(cardId) {
    const mem = (stateBox.applyMemory || []).filter(function (row) {
      return String(row.cardId) === String(cardId);
    }).pop();
    if (!mem) return;
    focusEditorForApply();
    undoApplyEdits(APPLY_UNDO_MAX, function () {
      const mapping = mapEditorParagraphs(editorRoot());
      const live = joinedTextAt(mapping, mem.joinStart, String(mem.originalText || "").length);
      return normalizeApplyText(live) === normalizeApplyText(mem.originalText);
    });
    dispatchEditorInput();
    putCardStatus(cardId, "open").catch(function (error) {
      toastMsg((error && error.message) || "카드 상태를 바꾸지 못했어요.");
    });
    refreshLocations();
  }

  async function ignoreCardAndAdvance(cardId) {
    const nextId = peekNextOpen(cardId);
    await setCardStatus(cardId, "ignored");
    toastMsg("무시했어요", 5000, {
      action: {
        label: "되돌리기",
        onClick: function () {
          setCardStatus(cardId, "open");
        },
      },
    });
    afterResolveAdvance(nextId);
  }

  async function markCardAppliedWithoutEdit(cardId) {
    const nextId = peekNextOpen(cardId);
    await setCardStatus(cardId, "applied");
    toastMsg("해결함으로 표시했어요", 5000, {
      action: {
        label: "되돌리기",
        onClick: function () { setCardStatus(cardId, "open"); },
      },
    });
    afterResolveAdvance(nextId);
  }

  function scheduleHistorySync(inputType) {
    if (stateBox.historySyncTimer) clearTimeout(stateBox.historySyncTimer);
    stateBox.historySyncTimer = setTimeout(function () {
      stateBox.historySyncTimer = 0;
      syncApplyMemory(inputType);
    }, HISTORY_SYNC_MS);
  }

  function syncApplyMemory(inputType) {
    const list = stateBox.applyMemory || [];
    if (!list.length) return;
    const mapping = mapEditorParagraphs(editorRoot());
    const cards = (stateBox.run && stateBox.run.cards) || [];
    list.forEach(function (mem) {
      const live = {
        expected: joinedTextAt(mapping, mem.joinStart, String(mem.expectedText || "").length),
        original: joinedTextAt(mapping, mem.joinStart, String(mem.originalText || "").length),
      };
      const decision = historySyncDecision(mem, live, inputType);
      if (decision.action === "reopen") {
        mem.status = "open";
        putCardStatus(mem.cardId, "open").then(function () {
          toastMsg(decision.toast);
        }).catch(function () { /* ignore */ });
      } else if (decision.action === "reapply") {
        let cardStatus = "open";
        for (let i = 0; i < cards.length; i += 1) {
          if (String(cards[i].id) === String(mem.cardId)) cardStatus = cards[i].status;
        }
        if (isOpenStatus(cardStatus)) {
          mem.status = decision.status;
          putCardStatus(mem.cardId, decision.status).catch(function () { /* ignore */ });
        }
      }
    });
  }

  function copyText(text, doneMsg) {
    const value = String(text || "");
    if (!value) return;
    const okMsg = doneMsg || "수정안을 복사했어요.";
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(value).then(function () {
        toastMsg(okMsg);
      }).catch(function () {
        fallbackCopy(value, okMsg);
      });
      return;
    }
    fallbackCopy(value, okMsg);
  }

  function fallbackCopy(value, doneMsg) {
    if (typeof document === "undefined") return;
    const area = document.createElement("textarea");
    area.value = value;
    document.body.appendChild(area);
    area.select();
    try { document.execCommand("copy"); toastMsg(doneMsg || "수정안을 복사했어요."); }
    catch (_) { toastMsg("복사하지 못했어요."); }
    area.remove();
  }

  function runCardLength() {
    const cards = (stateBox.run && stateBox.run.cards) || [];
    return cards.length;
  }

  function autoSelectTab() {
    if (stateBox.tabLocked) return;
    stateBox.tab = runCardLength() > 0 ? "cards" : "report";
  }

  function applyTab() {
    const panel = panelEl();
    if (!panel) return;
    const tab = stateBox.tab === "cards" ? "cards" : (stateBox.tab === "history" ? "history" : "report");
    const buttons = panel.querySelectorAll("[data-role='fb-tab']");
    for (let i = 0; i < buttons.length; i += 1) {
      const on = buttons[i].getAttribute("data-tab") === tab;
      buttons[i].classList.toggle("is-active", on);
      buttons[i].setAttribute("aria-selected", on ? "true" : "false");
    }
    const report = panel.querySelector("[data-role='fb-tab-report']");
    const cards = panel.querySelector("[data-role='fb-tab-cards']");
    const history = panel.querySelector("[data-role='fb-tab-history']");
    if (report) report.hidden = tab !== "report";
    if (cards) cards.hidden = tab !== "cards";
    if (history) history.hidden = tab !== "history";
    const countEl = panel.querySelector("[data-role='fb-card-count']");
    if (countEl) countEl.textContent = String(runCardLength());
  }

  function currentControlDisplay() {
    const run = stateBox.run;
    const cards = (run && run.cards) || [];
    const running = Boolean(run && run.status === "running");
    const reveal = running ? "low" : stateBox.reveal;
    const layout = visibleCardLayout(cards, reveal, stateBox.sortMode, stateBox.kindFilter);
    const more = moreSeeCounts(cards, reveal, stateBox.kindFilter);
    return controlDisplayState({
      sortMode: stateBox.sortMode,
      kindFilter: stateBox.kindFilter,
      showUnderline: stateBox.showUnderline,
      underlineLevel: stateBox.underlineLevel,
      hideDict: stateBox.hideDict,
      lens: stateBox.lens,
      tab: stateBox.tab,
      kindChips: kindChipList(cards).items,
      shownCount: layout.items.length,
      hasMore: more.moreMedium || more.moreLow,
    });
  }

  function paintSegmentButtons(nodes, attr, paints) {
    const byVal = Object.create(null);
    for (let i = 0; i < (paints || []).length; i += 1) byVal[String(paints[i].value)] = paints[i];
    for (let i = 0; i < nodes.length; i += 1) {
      const val = nodes[i].getAttribute(attr);
      const paint = byVal[String(val)] || { selected: false, ariaChecked: "false" };
      nodes[i].classList.toggle("is-on", Boolean(paint.selected));
      nodes[i].setAttribute("aria-checked", paint.ariaChecked);
    }
  }

  function paintControls() {
    const panel = panelEl();
    if (!panel) return currentControlDisplay();
    const display = currentControlDisplay();
    paintSegmentButtons(panel.querySelectorAll("[data-role='fb-sort']"), "data-sort", display.sort);
    paintSegmentButtons(panel.querySelectorAll("[data-role='fb-ul-level']"), "data-level", display.underline);
    paintSegmentButtons(panel.querySelectorAll("[data-role='fb-lens']"), "data-lens", display.lensCards);
    const underline = panel.querySelector("[data-role='fb-underline']");
    if (underline) underline.setAttribute("aria-checked", display.underlineOn ? "true" : "false");
    const hideDict = panel.querySelector("[data-role='fb-hide-dict']");
    if (hideDict) hideDict.setAttribute("aria-checked", display.hideDict ? "true" : "false");
    const autoAdv = panel.querySelector("[data-role='fb-auto-advance']");
    if (autoAdv) autoAdv.setAttribute("aria-checked", stateBox.autoAdvance ? "true" : "false");
    const filterLabel = panel.querySelector("[data-role='fb-filter-label']");
    if (filterLabel) filterLabel.textContent = display.filterLabel;
    const filterBtn = panel.querySelector("[data-role='fb-filter-menu']");
    if (filterBtn) {
      filterBtn.classList.toggle("is-on", display.filterOn);
      filterBtn.setAttribute("aria-label", display.filterLabel);
    }
    const filterDot = panel.querySelector("[data-role='fb-filter-dot']");
    if (filterDot) filterDot.hidden = !display.filterOn;
    const displayDot = panel.querySelector("[data-role='fb-display-dot']");
    if (displayDot) displayDot.hidden = !display.displayChanged;
    const displayBtn = panel.querySelector("[data-role='fb-display-menu']");
    if (displayBtn) displayBtn.classList.toggle("is-on", display.displayChanged);
    const resetBtn = panel.querySelector("[data-role='fb-filter-pop'] [data-role='fb-kind-reset']");
    if (resetBtn) {
      resetBtn.disabled = !display.filterReset.enabled;
      resetBtn.textContent = display.filterReset.label;
      resetBtn.setAttribute("aria-label", display.filterReset.label);
    }
    const hint = panel.querySelector("[data-role='fb-filter-hint']");
    if (hint) hint.textContent = display.filterReset.hint;
    const list = panel.querySelector("[data-role='fb-kind-list']");
    if (list) {
      list.innerHTML = display.chips.map(function (item) {
        return "<button type=\"button\" class=\"fb-chip" + (item.selected ? " is-on" : "")
          + "\" data-role=\"fb-kind-chip\" data-kind=\"" + esc(item.key)
          + "\" aria-pressed=\"" + item.ariaPressed
          + "\" aria-label=\"" + esc(item.label) + " " + item.count + "\">"
          + esc(item.label) + " " + item.count + "</button>";
      }).join("") || "<p class=\"hint\">아직 카드가 없어요</p>";
    }
    const tabCards = panel.querySelector("[data-role='fb-tab-cards']");
    if (tabCards) tabCards.classList.toggle("has-kind-filter", display.filterOn);
    const banner = panel.querySelector("[data-role='fb-filter-banner']");
    if (banner) {
      banner.hidden = !display.filterOn;
      banner.innerHTML = display.filterOn
        ? ("<span>" + esc(display.banner) + "</span>"
          + "<button type=\"button\" class=\"fb-text-btn fb-banner-clear\" data-role=\"fb-kind-clear\" aria-label=\"필터 해제\">해제</button>")
        : "";
    }
    return display;
  }

  function refreshListAndChrome() {
    renderToolbar();
    renderCards();
    applyHighlights();
  }

  function runFakeFlag(row) {
    const params = row && row.params;
    if (!params || typeof params !== "object") return null;
    if (!Object.prototype.hasOwnProperty.call(params, "fake")) return null;
    return Boolean(params.fake);
  }

  function runFakeLabel(row) {
    const flag = runFakeFlag(row);
    if (flag === true) return "시험 기록";
    if (flag === false) return "실제 분석";
    return "";
  }

  function runLensLabel(row) {
    const params = row && row.params;
    if (!params || typeof params !== "object") return "";
    return lensLabel(params.explanation_lens);
  }

  function setLens(lens) {
    const next = resolveExplanationLens(lens, stateBox.defaultLens);
    stateBox.lens = next;
    writeStoredLens(stateBox.projectId || currentScene().projectId, next);
    renderToolbar();
  }

  function renderToolbar() {
    const panel = panelEl();
    if (!panel) return;
    const titleEl = panel.querySelector("[data-role='fb-scene']");
    if (titleEl) {
      titleEl.textContent = stateBox.sceneTitle || "회차를 열어 주세요";
      titleEl.setAttribute("title", stateBox.sceneTitle || "회차를 열어 주세요");
    }
    const nextFake = panel.querySelector("[data-role='fb-next-fake']");
    if (nextFake) {
      nextFake.hidden = !stateBox.serverFake;
    }
    const debugBtn = panel.querySelector("[data-role='fb-debug-locate']");
    if (debugBtn) debugBtn.hidden = !isFbDebug();
    const keyBanner = panel.querySelector("[data-role='fb-key']");
    if (keyBanner) {
      keyBanner.hidden = stateBox.configured;
      keyBanner.textContent = "ANTHROPIC_API_KEY가 설정되지 않았습니다. 설정 방법은 .env에 키를 넣는 것입니다.";
    }
    const running = Boolean(stateBox.run && stateBox.run.status === "running");
    const startBtn = panel.querySelector("[data-role='fb-start']");
    if (startBtn) {
      const blocked = !stateBox.configured || stateBox.busy || running;
      startBtn.disabled = blocked;
      startBtn.textContent = running || stateBox.busy ? "분석 중" : "새로 분석";
    }
    const lensBtns = panel.querySelectorAll("[data-role='fb-lens']");
    for (let i = 0; i < lensBtns.length; i += 1) {
      lensBtns[i].disabled = !stateBox.projectId;
    }
    const recs = panel.querySelectorAll("[data-rec]");
    for (let i = 0; i < recs.length; i += 1) {
      recs[i].hidden = recs[i].getAttribute("data-rec") !== stateBox.defaultLens;
    }
    const underline = panel.querySelector("[data-role='fb-underline']");
    if (underline) underline.disabled = otherEditorActive();
    const levelOff = otherEditorActive() || !stateBox.showUnderline;
    const levelBtns = panel.querySelectorAll("[data-role='fb-ul-level']");
    for (let i = 0; i < levelBtns.length; i += 1) levelBtns[i].disabled = levelOff;
    const levelGroup = panel.querySelector("[data-role='fb-display-pop'] .fb-seg");
    if (levelGroup) {
      levelGroup.classList.toggle("is-off", levelOff);
      levelGroup.setAttribute("aria-disabled", levelOff ? "true" : "false");
    }
    const locateMsg = panel.querySelector("[data-role='fb-locate-msg']");
    if (locateMsg) {
      locateMsg.hidden = !otherEditorActive();
      locateMsg.textContent = otherEditorActive()
        ? "이 화면에서는 위치 표시를 지원하지 않아요"
        : "";
    }
    const select = panel.querySelector("[data-role='fb-runs']");
    if (select) {
      const currentId = stateBox.run && stateBox.run.id;
      select.innerHTML = stateBox.runs.map(function (row) {
        return "<option value=\"" + esc(row.id) + "\""
          + (Number(row.id) === Number(currentId) ? " selected" : "")
          + ">" + esc(formatRunOption(row) || "실행 기록") + "</option>";
      }).join("") || "<option value=\"\">실행 기록 없음</option>";
    }
    const tagsEl = panel.querySelector("[data-role='fb-run-tags']");
    if (tagsEl) {
      const tags = runSelectTags(stateBox.run);
      tagsEl.innerHTML = tags.map(function (tag) {
        return "<span class=\"fb-run-tag\">" + esc(tag.label) + "</span>";
      }).join("");
      const selectEl = panel.querySelector("[data-role='fb-runs']");
      if (selectEl) {
        selectEl.style.paddingRight = tags.length ? (22 + tags.length * 36) + "px" : "22px";
      }
    }
    syncChromeButtons();
    syncPopovers();
    renderCardsHead();
    applyTab();
  }

  function renderParaInfo() {
    const panel = panelEl();
    if (!panel) return;
    const info = panel.querySelector("[data-role='fb-para-info']");
    const warn = panel.querySelector("[data-role='fb-para-warn']");
    const run = stateBox.run;
    if (!info || !warn) return;
    if (!run) {
      info.hidden = true;
      warn.hidden = true;
      info.textContent = "";
      warn.textContent = "";
      return;
    }
    const paras = Array.isArray(run.paragraphs) ? run.paragraphs : [];
    const n = Number(run.paragraph_count) || paras.length;
    info.hidden = !n;
    info.textContent = n ? ("서버가 원고를 문단 " + n + "개로 나눴어요") : "";
    const warnings = Array.isArray(run.warnings) ? run.warnings : [];
    let single = warnings.find(function (item) {
      return item && item.code === "single_paragraph";
    });
    const textLen = paras.reduce(function (sum, row) {
      return sum + String((row && row.text) || "").length;
    }, 0);
    if (!single && n === 1 && textLen > 800) {
      single = {
        message: "이 회차가 한 문단으로 인식됐어요. 위치 표시가 정확하지 않을 수 있어요",
      };
    }
    warn.hidden = !single;
    warn.textContent = single ? String(single.message || "") : "";
  }

  function renderMismatch() {
    renderParaInfo();
    const banner = panelEl() && panelEl().querySelector("[data-role='fb-mismatch']");
    if (!banner) return;
    const saved = stateBox.run && (stateBox.run.paragraphs || []);
    const live = paragraphsFromEditor(editorRoot());
    const mismatch = paragraphsDiffer(saved, live);
    banner.hidden = !mismatch;
    banner.textContent = mismatch
      ? "분석한 뒤 원고가 바뀌었어요. 일부 위치가 어긋날 수 있어요"
      : "";
  }

  function renderProgress() {
    const wrap = panelEl() && panelEl().querySelector("[data-role='fb-progress']");
    if (!wrap) return;
    const run = stateBox.run;
    const running = Boolean(run && run.status === "running");
    wrap.hidden = !running;
    if (!running) {
      wrap.innerHTML = "";
      return;
    }
    const progress = run.progress || (run.params && run.params.progress) || {};
    const cards = Array.isArray(run.cards) ? run.cards : [];
    const planned = Number(run.planned_cards) || 0;
    const pct = progressPercent(progress, cards.length, planned);
    const label = stageLabel(progress, cards.length, planned);
    wrap.innerHTML =
      "<div class=\"fb-progress-head\">"
      + "<span>" + esc(label) + "</span>"
      + "<button type=\"button\" class=\"secondary compact-btn\" data-role=\"fb-cancel\">취소</button>"
      + "</div>"
      + "<div class=\"fb-progress-bar\" aria-hidden=\"true\"><span style=\"width:" + pct + "%\"></span></div>";
  }

  function renderRunStatus() {
    const el = panelEl() && panelEl().querySelector("[data-role='fb-result-status']");
    if (!el) return;
    const run = stateBox.run;
    if (!run) {
      el.hidden = false;
      el.innerHTML = "<p class=\"hint\">이 회차의 첨삭 기록이 없어요. [새로 분석]을 눌러 시작하세요.</p>";
      return;
    }
    if (run.status === "running") {
      el.hidden = true;
      el.innerHTML = "";
      return;
    }
    el.hidden = false;
    if (run.status === "partial") {
      el.innerHTML = "<p class=\"fb-status-msg\">일부 단계가 실패했어요</p>";
    } else if (run.status === "failed") {
      el.innerHTML = "<p class=\"fb-status-msg\">분석에 실패했어요</p>"
        + "<button type=\"button\" class=\"primary compact-btn\" data-role=\"fb-retry\">다시 시도</button>";
    } else {
      el.innerHTML = "<p class=\"hint\">분석이 끝났어요.</p>";
    }
  }

  function renderReport() {
    const panel = panelEl();
    const el = panel && panel.querySelector("[data-role='fb-report']");
    const collectBtn = panel && panel.querySelector("[data-role='fb-collect-report']");
    const run = stateBox.run;
    const report = run && (run.report || null);
    const md = run ? String(run.report_md || "") : "";
    const hasStructured = Boolean(report && (report.summary || (Array.isArray(report.scores) && report.scores.length)));
    const collectText = formatReportCollectText(run);
    if (collectBtn) {
      collectBtn.hidden = !collectText;
      collectBtn.classList.toggle("is-on", Boolean(run && hasCollectedMark("run", run.id)));
      collectBtn.setAttribute("aria-pressed", run && hasCollectedMark("run", run.id) ? "true" : "false");
    }
    if (!el) return;
    if (!hasStructured && !md) {
      el.hidden = true;
      el.innerHTML = "";
      return;
    }
    el.hidden = false;
    if (!hasStructured && md) {
      el.innerHTML =
        "<button type=\"button\" class=\"fb-fold\" data-role=\"fb-report-toggle\" aria-expanded=\""
        + (stateBox.reportOpen ? "true" : "false") + "\">리포트</button>"
        + "<div class=\"fb-report-body fb-report-md\"" + (stateBox.reportOpen ? "" : " hidden") + ">"
        + esc(md).replace(/\n/g, "<br>")
        + "</div>";
      return;
    }
    const scores = Array.isArray(report.scores) ? report.scores : [];
    const strengths = Array.isArray(report.strengths) ? report.strengths : [];
    const notes = referenceNotes(report, run.cards, (run.params && run.params.dropped) || []);
    const scoreRows = scores.map(function (row) {
      return "<tr><th>" + esc(row.item || "") + "</th><td>" + esc(row.score) + "</td><td>"
        + esc(row.comment || "") + "</td></tr>";
    }).join("");
    const strengthHtml = strengths.map(function (item) {
      return "<li><strong>" + esc(item.title || "") + "</strong><p>" + esc(item.body || "") + "</p></li>";
    }).join("");
    const notesHtml = notes.map(function (item) {
      const reason = item.reason
        ? "<p class=\"hint\">" + esc(item.reason) + "</p>"
        : "";
      const body = item.body ? "<p>" + esc(item.body) + "</p>" : "";
      return "<li><strong>" + esc(item.title || "") + "</strong>" + reason + body + "</li>";
    }).join("");
    el.innerHTML =
      "<button type=\"button\" class=\"fb-fold\" data-role=\"fb-report-toggle\" aria-expanded=\""
      + (stateBox.reportOpen ? "true" : "false") + "\">리포트</button>"
      + "<div class=\"fb-report-body\"" + (stateBox.reportOpen ? "" : " hidden") + ">"
      + "<p class=\"fb-summary\">" + esc(report.summary || "") + "</p>"
      + (scoreRows
        ? "<table class=\"fb-scores\"><thead><tr><th>항목</th><th>점수</th><th>코멘트</th></tr></thead><tbody>"
          + scoreRows + "</tbody></table>"
        : "")
      + (strengthHtml ? "<h4>강점</h4><ul class=\"fb-list\">" + strengthHtml + "</ul>" : "")
      + (notesHtml ? "<h4>참고 지적</h4><ul class=\"fb-list\">" + notesHtml + "</ul>" : "")
      + "</div>";
  }

  function renderCard(card, opts) {
    const id = card && card.id;
    const ignored = String(card.status || "") === "ignored";
    const pri = card.priority;
    const analyzing = pri == null || pri === "";
    const located = stateBox.locateById[String(id)];
    const missing = Boolean(id) && located && !located.ok;
    const selected = String(id) === String(stateBox.activeCardId);
    const index = opts && opts.index;
    const peers = (opts && opts.peers) || [];
    const tier = underlineTier(card);
    const numChip = index
      ? "<span class=\"fb-card-num\">" + esc(padCardIndex(index)) + "</span>"
      : "";
    const applied = card.status === "applied" || card.status === "applied_edited";
    if (applied) {
      const title = cardTitle(card, stateBox.run && stateBox.run.report);
      const talk = renderCommentThread(card, commentUiState(card));
      return "<article class=\"fb-card is-settled tier-" + esc(tier) + "\" data-card-id=\"" + esc(id) + "\">"
        + "<div class=\"fb-card-settled-row\">"
        + numChip
        + "<span>" + esc(title) + "</span>"
        + "<span class=\"fb-badge\">" + esc(STATUS_LABELS[card.status] || "적용됨") + "</span>"
        + "<button type=\"button\" class=\"secondary compact-btn\" data-role=\"fb-restore\">상태만 되돌리기</button>"
        + "</div>"
        + "<p class=\"fb-card-settled-hint\">원고 텍스트는 그대로예요. 원고에서 되돌리려면 Ctrl+Z를 쓰세요</p>"
        + "<div class=\"fb-card-actions fb-card-talk-actions\">" + talk + "</div>"
        + "</article>";
    }
    if (ignored) {
      const talk = renderCommentThread(card, commentUiState(card));
      return "<article class=\"fb-card is-ignored tier-" + esc(tier) + "\" data-card-id=\"" + esc(id) + "\">"
        + "<div class=\"fb-card-ignored-row\">"
        + numChip
        + "<span>" + esc(styleTypeLabel(card)) + " · " + esc(locationLabel(card, located)) + "</span>"
        + "<span class=\"fb-badge\">무시됨</span>"
        + "<button type=\"button\" class=\"secondary compact-btn\" data-role=\"fb-restore\">되돌리기</button>"
        + "</div>"
        + "<div class=\"fb-card-actions fb-card-talk-actions\">" + talk + "</div>"
        + "</article>";
    }
    const original = String(card.original_text || "");
    const hasSuggestion = cardHasSuggestion(card);
    const suggestion = hasSuggestion ? String(card.suggestion) : "";
    const collapsed = Boolean(stateBox.collapsedCards[id]);
    const fullDiff = Boolean(stateBox.expandedDiff[id]);
    const warnings = card.warnings || card.warnings_json || [];
    const warnOpen = Boolean(stateBox.expandedWarnings[id]);
    const maybe = String(card.kind || "") === "consistency" && String(card.certainty || "") === "maybe";
    const segs = hasSuggestion ? diffSegments(original, suggestion) : null;
    const summary = hasSuggestion ? changeSummary(original, suggestion, segs) : "";
    const title = cardTitle(card, stateBox.run && stateBox.run.report);
    let html = "<article class=\"fb-card" + (collapsed ? " is-collapsed" : "")
      + (selected ? " is-active" : "")
      + " tier-" + esc(tier)
      + "\" data-card-id=\"" + esc(id) + "\">";
    html += "<div class=\"fb-card-head\">";
    html += numChip;
    html += "<button type=\"button\" class=\"fb-fold-card\" data-role=\"fb-card-toggle\" aria-expanded=\""
      + (collapsed ? "false" : "true") + "\">"
      + (collapsed ? "펼치기" : "접기") + "</button>";
    html += "<span class=\"fb-kind\">" + esc(styleTypeLabel(card)) + "</span>";
    html += "<span class=\"fb-badge pri-" + esc(analyzing ? "analyzing" : pri) + "\">"
      + esc(analyzing ? "분석 중" : (PRIORITY_LABELS[pri] || pri || "")) + "</span>";
    html += "<button type=\"button\" class=\"fb-loc\" data-role=\"fb-goto\">"
      + esc(locationLabel(card, located)) + "</button>";
    html += "<span class=\"fb-badge\">" + esc(STATUS_LABELS[card.status] || "미결정") + "</span>";
    const talkCount = commentCountOf(card);
    if (collapsed && talkCount) {
      html += "<button type=\"button\" class=\"fb-talk-chip\" data-role=\"fb-comment-toggle\" title=\""
        + esc(commentButtonLabel(talkCount)) + "\" aria-label=\""
        + esc(commentButtonLabel(talkCount)) + "\">💬 " + esc(commentChipLabel(talkCount)) + "</button>";
    }
    if (missing) {
      html += "<span class=\"fb-badge fb-missing\">위치를 찾을 수 없어요</span>";
      html += "<span class=\"fb-missing-why\">" + esc(locateFailMessage(card, located)) + "</span>";
    }
    if (peers.length) {
      html += "<span class=\"fb-peer\">같은 장면의 다른 지적: "
        + peers.map(function (peer) {
          return "<button type=\"button\" class=\"fb-peer-link\" data-role=\"fb-peer\" data-peer-id=\""
            + esc(peer.id) + "\">" + esc(padCardIndex(peer.index)) + "</button>";
        }).join(", ")
        + "</span>";
    }
    html += "</div>";
    html += "<p class=\"fb-card-title\">" + esc(title) + "</p>";
    html += "<p class=\"fb-reason\">" + reasonHtml(card.reason || "", !collapsed) + "</p>";
    if (summary) html += "<p class=\"fb-diff-summary\">" + esc(summary) + "</p>";
    if (!collapsed) {
      if (hasSuggestion) {
        const origSegs = segs ? (fullDiff ? segs.filter(function (s) { return s.type !== "add"; }) : compactSide(segs, "orig", DIFF_CONTEXT)) : [{ type: "same", text: original }];
        const sugSegs = segs ? (fullDiff ? segs.filter(function (s) { return s.type !== "del"; }) : compactSide(segs, "sug", DIFF_CONTEXT)) : [{ type: "same", text: suggestion }];
        html += "<p class=\"fb-draft-label\">수정안 있음(초안)</p>";
        html += "<div class=\"fb-compare" + (fullDiff ? " is-full" : "") + "\">";
        html += "<div class=\"fb-compare-row\"><span class=\"fb-compare-label\">원문</span>"
          + "<div class=\"fb-compare-text\">" + renderSegHtml(origSegs) + "</div></div>";
        html += "<div class=\"fb-compare-row\"><span class=\"fb-compare-label\">수정안</span>"
          + "<div class=\"fb-compare-text\">" + renderSegHtml(sugSegs) + "</div></div>";
        html += "</div>";
        const long = original.length + suggestion.length > DIFF_CONTEXT * 4 || (segs && original.length > DIFF_CONTEXT * 2);
        if (long || fullDiff) {
          html += "<button type=\"button\" class=\"fb-diff-toggle\" data-role=\"fb-diff-toggle\">"
            + (fullDiff ? "줄여 보기" : "전체 보기") + "</button>";
        }
      } else if (original) {
        const shown = !fullDiff && original.length > ORIGINAL_PREVIEW
          ? original.slice(0, ORIGINAL_PREVIEW) + "…"
          : original;
        html += "<div class=\"fb-compare\"><div class=\"fb-compare-row\">"
          + "<span class=\"fb-compare-label\">원문</span>"
          + "<div class=\"fb-compare-text\">" + esc(shown) + "</div></div></div>";
        if (original.length > ORIGINAL_PREVIEW) {
          html += "<button type=\"button\" class=\"fb-diff-toggle\" data-role=\"fb-diff-toggle\">"
            + (fullDiff ? "줄여 보기" : "전체 보기") + "</button>";
        }
        html += "<p class=\"hint\">" + esc(noSuggestionHint(card)) + "</p>";
      } else {
        html += "<p class=\"hint\">" + esc(noSuggestionHint(card)) + "</p>";
      }
      const actionWarn = actionableWarnings(warnings);
      const infoWarn = infoWarnings(warnings);
      if (isNoteOnlyCard(card)) {
        html += "<span class=\"fb-note-label\">참고 지적</span>";
      }
      if (infoWarn.length) {
        html += infoWarn.map(function (item) {
          const msg = typeof item === "string" ? item : (item && item.message) || "";
          return "<p class=\"fb-info-hint\">" + esc(msg) + "</p>";
        }).join("");
      }
      if (actionWarn.length) {
        html += "<button type=\"button\" class=\"fb-warn-toggle\" data-role=\"fb-warn\">AI 검사, 확인 필요"
          + (warnOpen ? "" : " · " + esc(warningPreview(actionWarn))) + "</button>";
        if (warnOpen) {
          html += "<ul class=\"fb-warn-list\">" + actionWarn.map(function (item) {
            const msg = typeof item === "string" ? item : (item && item.message) || "";
            return "<li>" + esc(msg) + "</li>";
          }).join("") + "</ul>";
        }
      }
      if (maybe) html += "<p class=\"fb-maybe\">확인이 필요한 항목</p>";
      html += "<div class=\"fb-card-actions\">";
      if (hasSuggestion) {
        html += "<button type=\"button\" class=\"fb-primary compact-btn\" data-role=\"fb-apply\"" + (missing || otherEditorActive() ? " disabled" : "") + ">고치기</button>";
        html += "<button type=\"button\" class=\"secondary compact-btn\" data-role=\"fb-copy\">수정안 복사</button>";
      }
      html += "<button type=\"button\" class=\"secondary compact-btn\" data-role=\"fb-ignore\">무시하기</button>";
      html += renderCommentThread(card, commentUiState(card));
      html += "<button type=\"button\" class=\"fb-collect-btn" + (hasCollectedMark("card", id) ? " is-on" : "")
        + "\" data-role=\"fb-collect-card\" title=\"수집에 저장\" aria-label=\"수집\" aria-pressed=\""
        + (hasCollectedMark("card", id) ? "true" : "false") + "\">"
        + (hasCollectedMark("card", id) ? "★" : "☆") + "</button>";
      html += "</div>";
    }
    html += "</article>";
    return html;
  }

  function renderCardsHead() {
    const panel = panelEl();
    if (!panel) return;
    const run = stateBox.run;
    const cards = (run && run.cards) || [];
    const running = Boolean(run && run.status === "running");
    const counts = countCards(cards);
    const planned = Number(run && run.planned_cards) || 0;
    const sum = formatListSummary(counts, planned, running);
    const display = paintControls();
    const summary = panel.querySelector("[data-role='fb-cards-summary']");
    if (summary) {
      summary.innerHTML = "<strong>" + esc(sum.title) + "</strong>"
        + (display.summaryNote ? ("<span class=\"fb-filter-shown\">" + esc(display.summaryNote) + "</span>") : "")
        + "<span class=\"fb-dot is-high\">중요 " + sum.high + "</span>"
        + "<span class=\"fb-dot is-medium\">보통 " + sum.medium + "</span>"
        + "<span class=\"fb-dot is-low\">낮음 " + sum.low + "</span>";
    }
  }

  function renderCards() {
    renderCardsHead();
    const el = panelEl() && panelEl().querySelector("[data-role='fb-cards']");
    if (!el) return;
    const listScroller = cardsListScroller();
    const savedListScroll = listScroller ? listScroller.scrollTop : 0;
    const run = stateBox.run;
    const cards = (run && run.cards) || [];
    const running = Boolean(run && run.status === "running");
    const reveal = running ? "low" : stateBox.reveal;
    const layout = visibleCardLayout(cards, reveal, stateBox.sortMode, stateBox.kindFilter);
    const allVisible = visibleCards(cards, reveal, stateBox.sortMode);
    const numberedAll = numberCards(allVisible);
    const byId = Object.create(null);
    for (let i = 0; i < numberedAll.length; i += 1) byId[numberedAll[i].id] = numberedAll[i];
    const numbered = layout.items.map(function (card) {
      return byId[card && card.id] || { card: card, index: 0, id: card && card.id };
    });
    const peers = overlapPeerMap(numberedAll);
    const more = moreSeeCounts(cards, reveal, stateBox.kindFilter);
    const moreMedium = !running && more.moreMedium;
    const moreLow = !running && more.moreLow;
    const groupLabels = { high: "중요", medium: "보통", low: "낮음" };
    const display = controlDisplayState({
      sortMode: stateBox.sortMode,
      kindFilter: stateBox.kindFilter,
      shownCount: layout.items.length,
      hasMore: moreMedium || moreLow,
    });
    let body = "";
    if (display.emptyFilter) {
      body = "<p class=\"fb-filter-empty\">선택한 유형의 제안이 없어요 "
        + "<button type=\"button\" class=\"fb-text-btn\" data-role=\"fb-kind-clear\" aria-label=\"필터 해제\">해제</button></p>";
    } else if (layout.mode === "manuscript") {
      body = numbered.map(function (row) {
        return renderCard(row.card, { index: row.index, peers: peers[row.id] || [] });
      }).join("");
    } else {
      const order = ["high", "medium", "low"];
      for (let g = 0; g < order.length; g += 1) {
        const key = order[g];
        const groupCards = layout.groups[key] || [];
        if (!groupCards.length) continue;
        body += "<h4 class=\"fb-group-h\" data-fb-group=\"" + key + "\">"
          + esc(groupLabels[key] + " " + groupCards.length) + "</h4>";
        body += groupCards.map(function (card) {
          const row = byId[card.id];
          return renderCard(card, { index: row && row.index, peers: peers[card.id] || [] });
        }).join("");
      }
    }
    el.innerHTML = body
      + (moreMedium
        ? "<button type=\"button\" class=\"fb-ctrl fb-more\" data-role=\"fb-more-medium\">보통 "
          + more.medium + "개 더 보기</button>"
        : "")
      + (moreLow
        ? "<button type=\"button\" class=\"fb-ctrl fb-more\" data-role=\"fb-more-low\">낮음 "
          + more.low + "개 더 보기</button>"
        : "");
    if (stateBox.pendingScrollGroup) {
      const header = el.querySelector('[data-fb-group="' + stateBox.pendingScrollGroup + '"]');
      stateBox.pendingScrollGroup = "";
      if (header && header.scrollIntoView) {
        try { header.scrollIntoView({ block: "nearest" }); } catch (_) { /* ignore */ }
      }
    } else if (listScroller) {
      listScroller.scrollTop = savedListScroll;
    }
  }

  function renderHistory() {
    const panel = panelEl();
    if (!panel) return;
    const listEl = panel.querySelector("[data-role='fb-history-list']");
    const emptyEl = panel.querySelector("[data-role='fb-history-empty']");
    const importBtn = panel.querySelector("[data-role='fb-import-legacy']");
    const rows = sortHistoryRuns(stateBox.runs);
    if (listEl) {
      if (!rows.length) {
        listEl.innerHTML = "";
      } else {
        listEl.innerHTML = rows.map(function (row) {
          const view = formatHistoryRow(row);
          const tags = (view.tags || []).map(function (tag) {
            return "<span class=\"fb-run-tag\">" + esc(tag.label) + "</span>";
          }).join("");
          return "<article class=\"fb-hist-row\" data-run-id=\"" + esc(view.id) + "\">"
            + "<div class=\"fb-hist-meta\">"
            + "<span class=\"fb-hist-when\">" + esc(view.when) + "</span>"
            + (view.lens ? "<span class=\"fb-hist-lens\">" + esc(view.lens) + "</span>" : "")
            + "<span class=\"fb-hist-status\">" + esc(view.statusLabel) + "</span>"
            + "<span class=\"fb-hist-counts\">" + esc(view.countsLabel) + "</span>"
            + tags
            + (view.primary ? "<span class=\"fb-badge fb-hist-primary\">기준</span>" : "")
            + "</div>"
            + "<div class=\"fb-hist-actions\">"
            + "<button type=\"button\" class=\"secondary compact-btn\" data-role=\"fb-hist-view\">보기</button>"
            + (view.showPrimaryBtn
              ? "<button type=\"button\" class=\"secondary compact-btn\" data-role=\"fb-hist-primary\">기준으로 설정</button>"
              : "")
            + "<button type=\"button\" class=\"secondary compact-btn\" data-role=\"fb-hist-delete\""
            + (view.canDelete ? "" : " disabled")
            + (view.deleteTitle ? " title=\"" + esc(view.deleteTitle) + "\"" : "")
            + ">삭제</button>"
            + "</div></article>";
        }).join("");
      }
    }
    if (emptyEl) emptyEl.hidden = rows.length > 0;
    paintLegacyImportButton(importBtn, readLegacyHistoryEntries(stateBox.projectId));
  }

  function findRunCard(cardId) {
    return ((stateBox.run && stateBox.run.cards) || []).find(function (item) {
      return String(item.id) === String(cardId);
    });
  }

  async function loadCardComments(cardId, force) {
    const id = String(cardId || "");
    if (!id) return [];
    if (!force && Array.isArray(stateBox.commentsByCard[id])) return stateBox.commentsByCard[id];
    const body = await apiCall("/api/feedback/cards/" + id + "/comments");
    const rows = (body && Array.isArray(body.comments)) ? body.comments : [];
    stateBox.commentsByCard[id] = rows;
    const card = findRunCard(id);
    if (card) card.comment_count = rows.length;
    if (commentLimitReached(rows.length)) stateBox.commentLimit[id] = true;
    return rows;
  }

  function mergeCardComments(cardId, extra) {
    const id = String(cardId || "");
    const current = Array.isArray(stateBox.commentsByCard[id]) ? stateBox.commentsByCard[id] : [];
    const byId = Object.create(null);
    current.concat(Array.isArray(extra) ? extra : []).forEach(function (row) {
      if (row && row.id != null) byId[String(row.id)] = row;
    });
    const merged = Object.keys(byId).map(function (key) { return byId[key]; });
    merged.sort(function (a, b) { return Number(a.id) - Number(b.id); });
    stateBox.commentsByCard[id] = merged;
    const card = findRunCard(id);
    if (card) card.comment_count = merged.length;
    if (commentLimitReached(merged.length)) stateBox.commentLimit[id] = true;
    return merged;
  }

  async function toggleCardComments(cardId) {
    const id = String(cardId || "");
    if (!id) return;
    const next = !stateBox.commentOpen[id];
    stateBox.commentOpen[id] = next;
    if (next) {
      stateBox.collapsedCards[id] = false;
      renderCards();
      try {
        await loadCardComments(id, false);
      } catch (error) {
        stateBox.commentError[id] = true;
      }
    }
    renderCards();
  }

  async function sendCardComment(cardId) {
    const id = String(cardId || "");
    const message = String(stateBox.commentDraft[id] || "").trim();
    if (!id || !message || stateBox.commentSending[id]) return;
    const existing = Array.isArray(stateBox.commentsByCard[id]) ? stateBox.commentsByCard[id] : [];
    if (stateBox.commentLimit[id] || commentLimitReached(existing.length)) {
      stateBox.commentLimit[id] = true;
      renderCards();
      return;
    }
    stateBox.commentSending[id] = true;
    stateBox.commentError[id] = false;
    renderCards();
    try {
      const body = await apiCall("/api/feedback/cards/" + id + "/comments", {
        method: "POST",
        body: JSON.stringify({ message: message }),
      });
      mergeCardComments(id, (body && body.comments) || []);
      stateBox.commentDraft[id] = "";
      stateBox.commentError[id] = false;
    } catch (error) {
      if (error && Number(error.status) === 409) {
        stateBox.commentLimit[id] = true;
        try { await loadCardComments(id, true); } catch (_) { /* keep draft */ }
      } else {
        stateBox.commentError[id] = true;
        if (error && error.status >= 500) {
          try { await loadCardComments(id, true); } catch (_) { /* keep draft */ }
        }
      }
    }
    stateBox.commentSending[id] = false;
    renderCards();
  }

  function renderAll() {
    const panel = panelEl();
    if (!panel || !isChromeOpen()) return;
    autoSelectTab();
    renderToolbar();
    renderMismatch();
    renderProgress();
    renderRunStatus();
    renderReport();
    renderHistory();
    refreshLocations();
    renderCards();
  }

  function onClick(event) {
    const btn = event.target.closest("button[data-role]");
    const role = btn && btn.getAttribute("data-role");
    const card = event.target.closest("[data-card-id]");
    const cardId = card && card.getAttribute("data-card-id");
    if (role === "fb-goto" && cardId) {
      selectCard(cardId);
      return;
    }
    if (role === "fb-peer") {
      const peerId = btn.getAttribute("data-peer-id");
      if (peerId) selectCard(peerId);
      return;
    }
    if (role === "fb-ul-level") {
      if (btn.disabled) return;
      stateBox.underlineLevel = normalizeUnderlineLevel(btn.getAttribute("data-level"));
      writeUnderlineLevel(stateBox.projectId, stateBox.underlineLevel);
      refreshListAndChrome();
      return;
    }
    if (role === "fb-underline") {
      if (btn.disabled) return;
      stateBox.showUnderline = !stateBox.showUnderline;
      writeUnderlinePref(stateBox.showUnderline);
      refreshListAndChrome();
      return;
    }
    if (role === "fb-hide-dict") {
      stateBox.hideDict = !stateBox.hideDict;
      writeHideDictPref(stateBox.projectId, stateBox.hideDict);
      applyDictReviewMute();
      renderToolbar();
      return;
    }
    if (role === "fb-filter-menu") {
      stateBox.filterPopOpen = !stateBox.filterPopOpen;
      stateBox.displayPopOpen = false;
      stateBox.startPopOpen = false;
      syncPopovers();
      return;
    }
    if (role === "fb-display-menu") {
      stateBox.displayPopOpen = !stateBox.displayPopOpen;
      stateBox.filterPopOpen = false;
      stateBox.startPopOpen = false;
      syncPopovers();
      return;
    }
    if (role === "fb-kind-reset" || role === "fb-kind-clear") {
      if (!normalizeKindFilter(stateBox.kindFilter).length) return;
      stateBox.kindFilter = [];
      refreshListAndChrome();
      return;
    }
    if (role === "fb-kind-chip") {
      const key = btn.getAttribute("data-kind") || "";
      const selected = normalizeKindFilter(stateBox.kindFilter);
      stateBox.kindFilter = toggleKindFilter(stateBox.kindFilter, key, selected.indexOf(key) < 0);
      refreshListAndChrome();
      return;
    }
    if (role === "fb-sort") {
      stateBox.sortMode = btn.getAttribute("data-sort") === "manuscript" ? "manuscript" : "priority";
      writeSortPref(stateBox.projectId, stateBox.sortMode);
      refreshListAndChrome();
      return;
    }
    if (role === "fb-debug-locate") {
      copyLocateDiag();
      return;
    }
    if (role === "fb-start") {
      if (startBtnDisabled()) return;
      stateBox.startPopOpen = !stateBox.startPopOpen;
      stateBox.filterPopOpen = false;
      stateBox.displayPopOpen = false;
      syncPopovers();
      return;
    }
    if (role === "fb-start-go") {
      closePopovers();
      startAnalysis();
      return;
    }
    if (role === "fb-start-cancel") {
      closePopovers();
      return;
    }
    if (role === "fb-retry") {
      closePopovers();
      startAnalysis();
      return;
    }
    if (role === "fb-lens") {
      setLens(btn.getAttribute("data-lens"));
      return;
    }
    if (role === "fb-switch-chrome") {
      if (typeof switchFeedbackChrome === "function") switchFeedbackChrome();
      return;
    }
    if (role === "fb-close-chrome") {
      if (typeof closeFeedbackChrome === "function") closeFeedbackChrome();
      return;
    }
    if (role === "fb-tab") {
      const next = btn.getAttribute("data-tab");
      if (next === "report" || next === "cards" || next === "history") {
        stateBox.tab = next;
        stateBox.tabLocked = true;
        applyTab();
        if (next === "history") renderHistory();
      }
      return;
    }
    if (role === "fb-hist-view") {
      const runId = btn.closest("[data-run-id]") && btn.closest("[data-run-id]").getAttribute("data-run-id");
      if (runId) viewHistoryRun(runId);
      return;
    }
    if (role === "fb-hist-primary") {
      const runId = btn.closest("[data-run-id]") && btn.closest("[data-run-id]").getAttribute("data-run-id");
      if (runId) setRunPrimary(runId);
      return;
    }
    if (role === "fb-hist-delete") {
      const runId = btn.closest("[data-run-id]") && btn.closest("[data-run-id]").getAttribute("data-run-id");
      if (runId) deleteHistoryRun(runId);
      return;
    }
    if (role === "fb-import-legacy") {
      if (btn.disabled) return;
      importLegacyHistory();
      return;
    }
    if (role === "fb-collect-report") {
      collectReportToVault();
      return;
    }
    if (role === "fb-collect-card" && cardId) {
      collectCardToVault(cardId);
      return;
    }
    if (role === "fb-cancel") {
      cancelRun();
      return;
    }
    if (role === "fb-report-toggle") {
      stateBox.reportOpen = !stateBox.reportOpen;
      renderReport();
      return;
    }
    if (role === "fb-more-medium") {
      stateBox.reveal = "medium";
      stateBox.pendingScrollGroup = "medium";
      refreshListAndChrome();
      return;
    }
    if (role === "fb-more-low") {
      stateBox.reveal = "low";
      stateBox.pendingScrollGroup = "low";
      refreshListAndChrome();
      return;
    }
    if (role === "fb-card-toggle" && cardId) {
      stateBox.collapsedCards[cardId] = !stateBox.collapsedCards[cardId];
      renderCards();
      return;
    }
    if (role === "fb-diff-toggle" && cardId) {
      stateBox.expandedDiff[cardId] = !stateBox.expandedDiff[cardId];
      renderCards();
      return;
    }
    if (role === "fb-warn" && cardId) {
      stateBox.expandedWarnings[cardId] = !stateBox.expandedWarnings[cardId];
      renderCards();
      return;
    }
    if (role === "fb-copy" && cardId) {
      const found = ((stateBox.run && stateBox.run.cards) || []).find(function (item) {
        return String(item.id) === String(cardId);
      });
      copyText(found && found.suggestion);
      return;
    }
    if (role === "fb-apply" && cardId) {
      applyCardFromUi(cardId, {});
      return;
    }
    if (role === "fb-ignore" && cardId) {
      ignoreCardAndAdvance(cardId);
      return;
    }
    if (role === "fb-comment-toggle" && cardId) {
      toggleCardComments(cardId).catch(function (error) {
        if (typeof handleError === "function") handleError(error);
      });
      return;
    }
    if (role === "fb-comment-send" && cardId) {
      const area = card && card.querySelector("[data-role='fb-comment-input']");
      if (area) stateBox.commentDraft[cardId] = area.value;
      sendCardComment(cardId).catch(function (error) {
        if (typeof handleError === "function") handleError(error);
      });
      return;
    }
    if (role === "fb-comment-retry" && cardId) {
      sendCardComment(cardId).catch(function (error) {
        if (typeof handleError === "function") handleError(error);
      });
      return;
    }
    if (role === "fb-restore" && cardId) {
      setCardStatus(cardId, "open");
      return;
    }
    if (role === "fb-auto-advance") {
      stateBox.autoAdvance = !stateBox.autoAdvance;
      writeAutoAdvancePref(stateBox.autoAdvance);
      paintControls();
      return;
    }
    if (cardId && !shouldIgnoreCardClick(event)) {
      selectCard(cardId);
    }
  }

  function onChange(event) {
    const select = event.target.closest("[data-role='fb-runs']");
    if (!select) return;
    const id = Number(select.value);
    if (!id) return;
    loadRun(id, { quiet: true }).catch(function (error) {
      toastMsg((error && error.message) || "실행을 불러오지 못했어요.");
    });
  }

  function historyTabForRun(row) {
    const total = runCardTotal(row) || Number(row && row.planned_cards) || 0;
    if (String(row && row.run_kind) === "legacy" || !total) return "report";
    return "cards";
  }

  function viewHistoryRun(runId) {
    const row = (stateBox.runs || []).find(function (item) {
      return String(item.id) === String(runId);
    });
    stateBox.tab = historyTabForRun(row);
    stateBox.tabLocked = true;
    loadRun(runId, { quiet: true }).catch(function (error) {
      toastMsg((error && error.message) || "실행을 불러오지 못했어요.");
    });
  }

  function openHistoryRun(runId) {
    const id = Number(runId) || 0;
    if (!id) return false;
    stateBox.pendingOpenRunId = id;
    if (isChromeOpen()) viewHistoryRun(id);
    return true;
  }

  function isMarkupHistoryItem(item) {
    return Boolean(item && (item.kind === "markupfeedback" || item.mode === "markupfeedback"));
  }

  function toryHistoryItemFromRun(run) {
    if (!run || run.id == null) return null;
    const counts = (run.card_counts && typeof run.card_counts === "object") ? run.card_counts : {};
    const cardTotal = ["open", "applied", "applied_edited", "ignored", "alternate"]
      .reduce(function (sum, key) { return sum + (Number(counts[key]) || 0); }, 0)
      || Number(run.planned_cards) || 0;
    const fake = runFakeFlag(run) === true;
    const status = String(run.status || "");
    return {
      id: "fb-run-" + String(run.id),
      kind: "markupfeedback",
      mode: "markupfeedback",
      modeLabel: fake ? "첨삭 피드백 · 시험 기록" : "첨삭 피드백",
      runId: Number(run.id),
      sceneId: run.scene_id != null ? Number(run.scene_id) : null,
      sceneTitle: String(run.scene_title || ""),
      createdAt: String(run.created_at || ""),
      status: status,
      cardTotal: cardTotal,
      fake: fake,
      text: "",
    };
  }

  function markupHistoryStatusLabel(item) {
    const status = String((item && item.status) || "");
    if (status === "running") return "분석 중";
    if (status === "failed") return "실패";
    const n = Math.max(0, Number(item && item.cardTotal) || 0);
    return "카드 " + n + "개";
  }

  function formatMarkupHistoryPreview(item) {
    const scene = String((item && item.sceneTitle) || "").trim() || "회차";
    return scene + " · " + markupHistoryStatusLabel(item);
  }

  function formatMarkupHistoryLine(item, whenText) {
    const parts = ["첨삭 피드백"];
    if (item && item.fake) parts.push("시험 기록");
    const scene = String((item && item.sceneTitle) || "").trim();
    if (scene) parts.push(scene);
    if (whenText) parts.push(whenText);
    parts.push(markupHistoryStatusLabel(item));
    return parts.join(" · ");
  }

  function historyTimeValue(iso) {
    const t = Date.parse(String(iso || ""));
    return Number.isFinite(t) ? t : 0;
  }

  function mergeToryHistoryItems(localItems, runs, max) {
    const cap = Math.max(1, Number(max) || 40);
    const local = (Array.isArray(localItems) ? localItems : []).filter(function (item) {
      return item && item.id && !isMarkupHistoryItem(item);
    });
    const markup = (Array.isArray(runs) ? runs : []).map(toryHistoryItemFromRun).filter(Boolean);
    const merged = local.concat(markup);
    merged.sort(function (a, b) {
      const tb = historyTimeValue(b && b.createdAt) - historyTimeValue(a && a.createdAt);
      if (tb) return tb;
      const ida = String((a && a.id) || "");
      const idb = String((b && b.id) || "");
      if (idb < ida) return -1;
      if (idb > ida) return 1;
      return 0;
    });
    return merged.slice(0, cap);
  }

  async function setRunPrimary(runId) {
    const ctx = currentScene();
    if (!ctx.sceneId) return;
    try {
      await apiCall("/api/feedback/runs/" + runId + "/primary", {
        method: "POST",
        body: JSON.stringify({ scene_id: ctx.sceneId }),
      });
      await loadRuns();
      renderHistory();
      renderToolbar();
    } catch (error) {
      toastMsg((error && error.message) || "기준으로 바꾸지 못했어요.");
    }
  }

  async function deleteHistoryRun(runId) {
    const row = (stateBox.runs || []).find(function (item) {
      return String(item.id) === String(runId);
    });
    if (!row || String(row.status) === "running") return;
    if (!window.confirm(deleteRunConfirmMessage(runCardTotal(row) || Number(row.planned_cards) || 0))) return;
    const currentId = stateBox.run && stateBox.run.id;
    try {
      await apiCall("/api/feedback/runs/" + runId, { method: "DELETE" });
      const remaining = await loadRuns();
      const next = nextRunAfterDelete(remaining, runId, currentId);
      if (next) await loadRun(next.id, { quiet: true });
      else {
        stateBox.run = null;
        renderAll();
      }
    } catch (error) {
      toastMsg((error && error.message) || "삭제하지 못했어요.");
    }
  }

  async function importLegacyHistory() {
    const ctx = currentScene();
    const entries = readLegacyHistoryEntries(ctx.projectId);
    if (!entries.length) {
      toastMsg("가져올 예전 기록이 없어요");
      return;
    }
    try {
      const body = await apiCall("/api/projects/" + ctx.projectId + "/feedback/import-legacy", {
        method: "POST",
        body: JSON.stringify({ entries: entries }),
      });
      toastMsg(formatLegacyImportMessage(body && body.imported, body && body.skipped));
      const remaining = await loadRuns();
      if (!stateBox.run && remaining && remaining.length) {
        const chosen = pickDefaultRun(remaining);
        if (chosen) await loadRun(chosen.id, { quiet: true });
        else renderAll();
      } else {
        renderHistory();
        renderToolbar();
      }
    } catch (error) {
      toastMsg((error && error.message) || "가져오지 못했어요.");
    }
  }

  function saveToToryVault(payload) {
    if (typeof collectToToryVault !== "function") {
      toastMsg("수집창고를 쓸 수 없어요");
      return null;
    }
    const item = collectToToryVault({
      title: payload.title,
      text: payload.text,
      prompt: "",
      mode: payload.mode || "analyze",
      quiet: true,
    });
    return item;
  }

  function collectReportToVault() {
    const run = stateBox.run;
    const text = formatReportCollectText(run);
    if (!text) {
      toastMsg("수집할 리포트가 없어요");
      return;
    }
    const already = hasCollectedMark("run", run.id);
    const item = saveToToryVault({
      title: "첨삭 리포트" + (stateBox.sceneTitle ? " · " + stateBox.sceneTitle : ""),
      text: text,
      mode: "analyze",
    });
    if (!item) return;
    rememberCollectedMark("run", run.id);
    toastMsg(already ? "이미 수집함에 있어요. 하나 더 저장했어요" : "수집함에 저장했어요");
    renderReport();
  }

  function collectCardToVault(cardId) {
    const card = ((stateBox.run && stateBox.run.cards) || []).find(function (item) {
      return String(item.id) === String(cardId);
    });
    const text = formatCardCollectText(card, stateBox.run && stateBox.run.report);
    if (!text) {
      toastMsg("수집할 카드 내용이 없어요");
      return;
    }
    const already = hasCollectedMark("card", cardId);
    const item = saveToToryVault({
      title: cardTitle(card, stateBox.run && stateBox.run.report) || "첨삭 카드",
      text: text,
      mode: "analyze",
    });
    if (!item) return;
    rememberCollectedMark("card", cardId);
    toastMsg(already ? "이미 수집함에 있어요. 하나 더 저장했어요" : "수집함에 저장했어요");
    renderCards();
  }

  function moveSegment(event) {
    const t = event.target;
    if (!t || typeof t.closest !== "function") return false;
    const btn = t.closest(".fb-seg-btn");
    const group = t.closest(".fb-seg");
    if (!btn || !group) return false;
    const key = event.key;
    if (key !== "ArrowLeft" && key !== "ArrowRight" && key !== "Home" && key !== "End") return false;
    const btns = Array.prototype.slice.call(group.querySelectorAll(".fb-seg-btn:not([disabled])"));
    const i = btns.indexOf(btn);
    if (i < 0 || !btns.length) return false;
    let next = i;
    if (key === "ArrowLeft") next = Math.max(0, i - 1);
    if (key === "ArrowRight") next = Math.min(btns.length - 1, i + 1);
    if (key === "Home") next = 0;
    if (key === "End") next = btns.length - 1;
    if (!btns[next] || btns[next] === btn) return true;
    event.preventDefault();
    btns[next].focus();
    btns[next].click();
    return true;
  }

  function onDocKey(event) {
    if (!event) return;
    if (moveSegment(event)) return;
    if (event.key !== "Escape") return;
    if (!isChromeOpen()) return;
    if (anyPopoverOpen()) {
      closePopovers();
      event.preventDefault();
      return;
    }
    if (stateBox.activeCardId) {
      event.preventDefault();
      finishReview();
    }
  }

  function onDocPointerDown(event) {
    if (!anyPopoverOpen()) return;
    const t = event.target;
    if (t && typeof t.closest === "function") {
      if (t.closest("[data-role='fb-start-pop'], [data-role='fb-start'], [data-role='fb-filter-pop'], [data-role='fb-filter-menu'], [data-role='fb-display-pop'], [data-role='fb-display-menu']")) {
        return;
      }
    }
    closePopovers();
  }

  function onPanelHover(event) {
    const card = event.target && event.target.closest && event.target.closest("[data-card-id]");
    const next = card ? card.getAttribute("data-card-id") : null;
    if (String(stateBox.hoverCardId || "") === String(next || "")) return;
    stateBox.hoverCardId = next;
    applyHighlights();
  }

  function bindPanel() {
    const panel = panelEl();
    if (!panel || stateBox.bound) return;
    panel.addEventListener("click", onClick);
    panel.addEventListener("change", onChange);
    panel.addEventListener("input", function (event) {
      const area = event.target && event.target.closest && event.target.closest("[data-role='fb-comment-input']");
      if (!area) return;
      const wrap = area.closest("[data-card-id]");
      const id = wrap && wrap.getAttribute("data-card-id");
      if (id) stateBox.commentDraft[id] = area.value;
    });
    panel.addEventListener("mouseover", onPanelHover);
    panel.addEventListener("mouseout", function (event) {
      if (!panel.contains(event.relatedTarget)) {
        if (stateBox.hoverCardId) {
          stateBox.hoverCardId = null;
          applyHighlights();
        }
      }
    });
    stateBox.bound = true;
    if (typeof document !== "undefined" && !stateBox.keyBound) {
      document.addEventListener("keydown", onDocKey);
      document.addEventListener("mousedown", onDocPointerDown);
      stateBox.keyBound = true;
    }
  }

  function onEditorInput(event) {
    const type = event && event.inputType;
    if (type === "historyUndo" || type === "historyRedo") {
      scheduleHistorySync(type);
    } else if (type && String(type).indexOf("history") !== 0) {
      if ((stateBox.applyMemory || []).length) {
        if (typeof hideToast === "function") hideToast();
      }
    }
    if (!isChromeOpen()) return;
    renderMismatch();
    scheduleLocateRefresh();
    relayoutOverlays();
  }

  function init() {
    bindPanel();
    bindOverlayRelayout();
    stateBox.showUnderline = readUnderlinePref();
    stateBox.autoAdvance = readAutoAdvancePref();
    if (typeof document !== "undefined" && !stateBox.mounted) {
      const editor = editorRoot();
      if (editor) editor.addEventListener("input", onEditorInput);
      stateBox.mounted = true;
    }
  }

  function onOpen() {
    init();
    if (typeof rememberDictHighlightForReview === "function") rememberDictHighlightForReview();
    stateBox.hideDict = readHideDictPref(currentScene().projectId);
    applyDictReviewMute();
    refreshForScene();
  }

  function onClose() {
    stopPolling();
    if (stateBox.locateTimer) {
      clearTimeout(stateBox.locateTimer);
      stateBox.locateTimer = 0;
    }
    stateBox.activeCardId = null;
    stateBox.ulHits = [];
    closePopovers();
    hideUlTip();
    hideBracket();
    hideReviewChrome();
    clearCardHighlights();
    if (typeof restoreDictHighlightAfterReview === "function") restoreDictHighlightAfterReview();
  }

  function onSceneChange() {
    stateBox.activeCardId = null;
    stateBox.tabLocked = false;
    stateBox.preApplySavedKey = "";
    hideReviewChrome();
    clearCardHighlights();
    if (!isChromeOpen()) return;
    refreshForScene();
  }

  global.FeedbackPanel = {
    paragraphsFromEditor: paragraphsFromEditor,
    paragraphsFromHtml: paragraphsFromHtml,
    mapEditorParagraphs: mapEditorParagraphs,
    locateCard: locateCard,
    htmlToDomLike: htmlToDomLike,
    snapshotEditorMarkup: snapshotEditorMarkup,
    paintCardHighlights: paintCardHighlights,
    clearCardHighlights: clearCardHighlights,
    cardTitle: cardTitle,
    noSuggestionHint: noSuggestionHint,
    formatKindCounts: formatKindCounts,
    sortCards: sortCards,
    countCards: countCards,
    visibleCards: visibleCards,
    formatCountLine: formatCountLine,
    applyKindFilter: applyKindFilter,
    kindChipList: kindChipList,
    moreSeeCounts: moreSeeCounts,
    formatCardsSummary: formatCardsSummary,
    formatListSummary: formatListSummary,
    viewButtonState: viewButtonState,
    viewOptionsState: viewOptionsState,
    controlDisplayState: controlDisplayState,
    segmentPaintState: segmentPaintState,
    filterResetState: filterResetState,
    formatFilterBanner: formatFilterBanner,
    formatFilterSummaryNote: formatFilterSummaryNote,
    underlineTargetCards: underlineTargetCards,
    underlinePaintTargets: underlinePaintTargets,
    normalizeKindFilter: normalizeKindFilter,
    toggleKindFilter: toggleKindFilter,
    runSelectTags: runSelectTags,
    formatRunWhenShort: formatRunWhenShort,
    formatRunOption: formatRunOption,
    formatRunStatusLine: formatRunStatusLine,
    pickDefaultRun: pickDefaultRun,
    sortHistoryRuns: sortHistoryRuns,
    formatHistoryRow: formatHistoryRow,
    openHistoryRun: openHistoryRun,
    toryHistoryItemFromRun: toryHistoryItemFromRun,
    mergeToryHistoryItems: mergeToryHistoryItems,
    formatMarkupHistoryPreview: formatMarkupHistoryPreview,
    formatMarkupHistoryLine: formatMarkupHistoryLine,
    isMarkupHistoryItem: isMarkupHistoryItem,
    commentButtonLabel: commentButtonLabel,
    commentChipLabel: commentChipLabel,
    commentLimitReached: commentLimitReached,
    renderTalkThread: renderTalkThread,
    renderCommentThread: renderCommentThread,
    formatCardCountSummary: formatCardCountSummary,
    historyRunStatusLabel: historyRunStatusLabel,
    historyKindTags: historyKindTags,
    nextRunAfterDelete: nextRunAfterDelete,
    deleteRunConfirmMessage: deleteRunConfirmMessage,
    legacyHistoryEntries: legacyHistoryEntries,
    legacyImportButtonState: legacyImportButtonState,
    formatLegacyImportMessage: formatLegacyImportMessage,
    formatReportCollectText: formatReportCollectText,
    formatCardCollectText: formatCardCollectText,
    stageLabel: stageLabel,
    styleTypeLabel: styleTypeLabel,
    locationLabel: locationLabel,
    referenceNotes: referenceNotes,
    paragraphsDiffer: paragraphsDiffer,
    cardHasWarning: cardHasWarning,
    isNoteOnlyCard: isNoteOnlyCard,
    dropReasonMessage: dropReasonMessage,
    defaultExplanationLens: defaultExplanationLens,
    resolveExplanationLens: resolveExplanationLens,
    lensLabel: lensLabel,
    analysisRequestBody: analysisRequestBody,
    shouldIgnoreCardClick: shouldIgnoreCardClick,
    visibleCardLayout: visibleCardLayout,
    numberCards: numberCards,
    visibleWalkOrder: visibleWalkOrder,
    padCardIndex: padCardIndex,
    rangesOverlap: rangesOverlap,
    shouldLinkOverlap: shouldLinkOverlap,
    overlapPeerMap: overlapPeerMap,
    cardPriorityGroup: cardPriorityGroup,
    underlineTier: underlineTier,
    locateFailMessage: locateFailMessage,
    formatLocateDiag: formatLocateDiag,
    foldSearchText: foldSearchText,
    isInfoWarning: isInfoWarning,
    infoWarnings: infoWarnings,
    underlineRangesFor: underlineRangesFor,
    cardHasSuggestion: cardHasSuggestion,
    shouldPaintUnderline: shouldPaintUnderline,
    normalizeUnderlineLevel: normalizeUnderlineLevel,
    longRangeEmphasis: longRangeEmphasis,
    paraBandPlan: paraBandPlan,
    paraBandDensities: paraBandDensities,
    underlineParaList: underlineParaList,
    underlineJoinSpans: underlineJoinSpans,
    joinedParaLayout: joinedParaLayout,
    sliceJoinedSpanToParas: sliceJoinedSpanToParas,
    mapDelSpansToParaSlices: mapDelSpansToParaSlices,
    delRangesForCard: delRangesForCard,
    activeCoveragePlan: activeCoveragePlan,
    rangesFromGlobalSpan: rangesFromGlobalSpan,
    renderSegHtml: renderSegHtml,
    targetScrollTop: targetScrollTop,
    activePaintPlan: activePaintPlan,
    activeBgPlan: activeBgPlan,
    mergeUnderlineSpans: mergeUnderlineSpans,
    overlapTooltipLabel: overlapTooltipLabel,
    delSpansFromDiff: delSpansFromDiff,
    mapDelSpansToSource: mapDelSpansToSource,
    diffSegments: diffSegments,
    changeSummary: changeSummary,
    reviewPanelWidthPx: reviewPanelWidthPx,
    shouldFallbackToFloat: shouldFallbackToFloat,
    snapshotMainLayout: snapshotMainLayout,
    planFeedbackChrome: planFeedbackChrome,
    restoredLayout: restoredLayout,
    readChromePref: readChromePref,
    writeChromePref: writeChromePref,
    mount: mount,
    isOpen: isChromeOpen,
    setChromeMode: setChromeMode,
    runFakeFlag: runFakeFlag,
    runFakeLabel: runFakeLabel,
    init: init,
    onOpen: onOpen,
    onClose: onClose,
    onSceneChange: onSceneChange,
    startAnalysis: startAnalysis,
    planCardApply: planCardApply,
    openWalkIds: openWalkIds,
    openWalkState: openWalkState,
    historySyncDecision: historySyncDecision,
    inlineBoxPlacement: inlineBoxPlacement,
    actionBarPlacement: actionBarPlacement,
    normalizeApplyText: normalizeApplyText,
    isSettledStatus: isSettledStatus,
    isAppliedStatus: isAppliedStatus,
    locateTextForCard: locateTextForCard,
    isOpenStatus: isOpenStatus,
    runApplyPlan: runApplyPlan,
    undoApplyEdits: undoApplyEdits,
    addedWordHtml: addedWordHtml,
    firstTwoSentences: firstTwoSentences,
    liveTextFromSlices: liveTextFromSlices,
    slicesFromFound: slicesFromFound,
    suggestionLines: suggestionLines,
    joinedTextAt: joinedTextAt,
    joinedFullText: joinedFullText,
    readPlanLiveText: readPlanLiveText,
    applyVerifyOk: applyVerifyOk,
    expectedLiveAfterPlan: expectedLiveAfterPlan,
  };
})(typeof window !== "undefined" ? window : globalThis);
