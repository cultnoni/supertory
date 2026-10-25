/* 조판(쪽쓰기) 서식 보존 모델.
 * 본문 HTML <-> "서식 런(run) 목록" 변환. 조판은 글자 격자로 쪽을 나누므로 본문을
 * 평문 + 런(서식 정보)으로 들고 있다가, 되쓸 때 같은 서식으로 HTML을 다시 만든다.
 *
 * 런: { t: 글자들, ch: 인라인 서식 껍데기[], i: 블록 인스턴스 번호, b: 블록 껍데기|null|"O", e?: 삽입물 번호 }
 *  - 줄바꿈 "\n" 은 한 블록(문단) 끝마다 하나씩 붙는 끝맺음 + 블록 안의 <br> 이다.
 *  - 이미지·각주 표시·목록·표·작가의 말처럼 글로 고칠 수 없는 것은 U+FFFC 한 글자짜리 "삽입물"로 두고
 *    원래 HTML을 그대로 들고 있다가 되쓴다.
 *  - 열기 전에 roundTripOk()로 "변환했다 되돌린 결과가 원본과 같은지" 확인한다. 다르면 열지 않는다(원고 보호).
 */
(function (global) {
  "use strict";

  const OPAQUE = "O";
  const EMBED_CHAR = "￼";
  const WS_RE = /[ \t\r\n\f]+/g;
  const WS_ONLY_RE = /^[ \t\r\n\f]*$/;
  const BLOCK_TAGS = new Set(["DIV", "P", "H1", "H2", "H3", "H4", "H5", "H6", "BLOCKQUOTE"]);
  const NOTE_SELECTOR = "[data-author-note],.st-author-note";
  const BLOCKISH_SELECTOR = "div,p,h1,h2,h3,h4,h5,h6,blockquote,ul,ol,li,table,thead,tbody,tfoot,tr,td,th,"
    + "pre,hr,figure,figcaption,section,article,aside,header,footer,nav,dl,dt,dd,details,summary,form,"
    + `fieldset,address,main,center,.fn-item,${NOTE_SELECTOR}`;
  const ATOMIC_SELECTOR = "img,svg,video,audio,iframe,canvas,input,button,select,textarea,object,embed,"
    + "sup.fn-ref,[contenteditable=false]";

  /* ---------- 껍데기(shell) ---------- */
  const shellPool = new Map();

  function internShell(tag, attrs) {
    const key = JSON.stringify([tag, attrs]);
    let shell = shellPool.get(key);
    if (!shell) {
      shell = { tag, attrs, key };
      shellPool.set(key, shell);
    }
    return shell;
  }

  function shellFromElement(el) {
    const attrs = [];
    for (const a of el.attributes) {
      if (a.name.startsWith("data-pw-")) continue;
      attrs.push([a.name, a.value]);
    }
    return internShell(el.tagName.toLowerCase(), attrs);
  }

  function escapeAttr(value) {
    return String(value).replace(/&/g, "&amp;").replace(/"/g, "&quot;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  }

  function escapeText(value) {
    return String(value).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  }

  function openTag(shell) {
    return `<${shell.tag}${shell.attrs.map(([n, v]) => ` ${n}="${escapeAttr(v)}"`).join("")}>`;
  }

  function closeTag(shell) {
    return `</${shell.tag}>`;
  }

  function blockKey(b) {
    if (b === null || b === undefined) return "";
    if (b === OPAQUE) return OPAQUE;
    return b.key;
  }

  function decodeBlock(raw) {
    if (!raw) return null;
    if (raw === OPAQUE) return OPAQUE;
    try {
      const [tag, attrs] = JSON.parse(raw);
      return internShell(tag, attrs);
    } catch (_) {
      return null;
    }
  }

  function sameChain(a, b) {
    if (a.length !== b.length) return false;
    for (let k = 0; k < a.length; k += 1) if (a[k].key !== b[k].key) return false;
    return true;
  }

  /* ---------- 런 다루기 ---------- */
  function textOf(runs) {
    let out = "";
    for (const r of runs) out += r.t;
    return out;
  }

  function mergeable(a, b) {
    return a.e == null && b.e == null && a.i === b.i && a.b === b.b && sameChain(a.ch, b.ch);
  }

  function pushRun(runs, run) {
    if (!run.t) return;
    const last = runs[runs.length - 1];
    if (last && mergeable(last, run)) last.t += run.t;
    else runs.push(run);
  }

  /** 번호(i)·블록(b)이 비어 있는 런은 이웃 값을 물려받게 하고, 이웃 런끼리 합친다. */
  function normalizeRuns(runs) {
    const filled = runs.filter((r) => r.t);
    let prev = null;
    for (const r of filled) {
      if (r.i == null || Number.isNaN(r.i)) {
        if (prev) {
          r.i = prev.i;
          r.b = prev.b;
        }
      }
      prev = r;
    }
    let next = null;
    for (let k = filled.length - 1; k >= 0; k -= 1) {
      const r = filled[k];
      if (r.i == null || Number.isNaN(r.i)) {
        r.i = next ? next.i : -1;
        r.b = next ? next.b : null;
      }
      next = r;
    }
    const out = [];
    for (const r of filled) pushRun(out, { ...r, ch: r.ch.slice() });
    return out;
  }

  function splitRuns(runs, ranges) {
    const out = ranges.map(() => []);
    let ri = 0;
    let off = 0;
    for (let p = 0; p < ranges.length; p += 1) {
      let need = ranges[p].end - ranges[p].start;
      while (need > 0 && ri < runs.length) {
        const r = runs[ri];
        const take = Math.min(r.t.length - off, need);
        out[p].push(take === r.t.length ? r : { ...r, t: r.t.slice(off, off + take) });
        off += take;
        need -= take;
        if (off >= r.t.length) {
          ri += 1;
          off = 0;
        }
      }
    }
    return out;
  }

  function deleteRange(runs, from, to) {
    const out = [];
    let pos = 0;
    for (const r of runs) {
      const start = pos;
      const end = pos + r.t.length;
      pos = end;
      if (end <= from || start >= to) {
        out.push(r);
        continue;
      }
      const head = r.t.slice(0, Math.max(0, from - start));
      const tail = r.t.slice(Math.max(0, to - start));
      if (r.e != null) continue; // 삽입물은 한 글자라 겹치면 통째로 지움
      if (head) out.push({ ...r, t: head });
      if (tail) out.push({ ...r, t: tail });
    }
    return normalizeRuns(out);
  }

  /* ---------- 본문 HTML -> 런 ---------- */
  function classifyRoot(el) {
    if (el.matches(NOTE_SELECTOR)) return "opaque";
    if (el.matches(BLOCKISH_SELECTOR)) {
      if (BLOCK_TAGS.has(el.tagName) && !el.querySelector(BLOCKISH_SELECTOR)) return "block";
      return "opaque";
    }
    if (!el.matches(ATOMIC_SELECTOR) && el.querySelector(BLOCKISH_SELECTOR)) return "opaque";
    return "inline";
  }

  function embedKindOf(el) {
    if (el.matches(NOTE_SELECTOR)) return "note";
    if (el.tagName === "IMG" || el.querySelector("img")) return "image";
    if (el.matches("sup.fn-ref")) return "footnote";
    return "other";
  }

  function parseEditor(root) {
    const embeds = [];
    const runs = [];
    let nextInst = 0;

    const newCtx = (b) => {
      nextInst += 1;
      return { i: nextInst, b, brEnd: false };
    };
    const push = (t, ch, ctx, e) => pushRun(runs, { t, ch, i: ctx.i, b: ctx.b, e });
    const closeInst = (ctx) => {
      if (ctx.brEnd) {
        const last = runs[runs.length - 1];
        last.t = last.t.slice(0, -1);
        if (!last.t) runs.pop();
      }
      push("\n", [], ctx);
    };

    const walk = (node, chain, ctx) => {
      if (node.nodeType === 3) {
        const t = node.nodeValue.replace(WS_RE, " ");
        if (!t) return;
        push(t, chain, ctx);
        ctx.brEnd = false;
        return;
      }
      if (node.nodeType !== 1) return;
      if (node.tagName === "BR") {
        push("\n", chain, ctx);
        ctx.brEnd = true;
        return;
      }
      if (node.matches(ATOMIC_SELECTOR)) {
        embeds.push({ html: node.outerHTML, block: false, kind: embedKindOf(node) });
        push(EMBED_CHAR, chain, ctx, embeds.length - 1);
        ctx.brEnd = false;
        return;
      }
      const next = chain.concat([shellFromElement(node)]);
      for (const child of node.childNodes) walk(child, next, ctx);
    };

    let bare = null;
    for (const node of root.childNodes) {
      if (node.nodeType === 3) {
        if (!bare && WS_ONLY_RE.test(node.nodeValue)) continue;
        if (!bare) bare = newCtx(null);
        walk(node, [], bare);
        continue;
      }
      if (node.nodeType !== 1) continue;
      const kind = classifyRoot(node);
      if (kind === "inline") {
        if (!bare) bare = newCtx(null);
        walk(node, [], bare);
        continue;
      }
      if (bare) {
        closeInst(bare);
        bare = null;
      }
      if (kind === "opaque") {
        const ctx = newCtx(OPAQUE);
        embeds.push({ html: node.outerHTML, block: true, kind: embedKindOf(node) === "image" ? "image" : (node.matches(NOTE_SELECTOR) ? "note" : "block") });
        push(EMBED_CHAR, [], ctx, embeds.length - 1);
        closeInst(ctx);
      } else {
        const ctx = newCtx(shellFromElement(node));
        for (const child of node.childNodes) walk(child, [], ctx);
        closeInst(ctx);
      }
    }
    if (bare) closeInst(bare);
    return { runs, embeds, text: textOf(runs) };
  }

  /* ---------- 런 -> 본문 HTML ---------- */
  function inlineHtml(runs, embeds) {
    let html = "";
    const stack = [];
    const closeTo = (n) => {
      while (stack.length > n) html += closeTag(stack.pop());
    };
    for (const r of runs) {
      let k = 0;
      while (k < stack.length && k < r.ch.length && stack[k].key === r.ch[k].key) k += 1;
      closeTo(k);
      for (let j = k; j < r.ch.length; j += 1) {
        html += openTag(r.ch[j]);
        stack.push(r.ch[j]);
      }
      if (r.e != null) html += embeds[r.e].html;
      else html += escapeText(r.t).replace(/\n/g, "<br>");
    }
    const last = runs[runs.length - 1];
    if (!last || (last.e == null && last.t.endsWith("\n"))) html += "<br>";
    closeTo(0);
    return html;
  }

  function serialize(runs, embeds) {
    const groups = [];
    for (const r of runs) {
      const last = groups[groups.length - 1];
      if (last && last.i === r.i) last.runs.push(r);
      else groups.push({ b: r.b, i: r.i, runs: [r] });
    }
    const endsWithNewline = (g) => {
      const last = g.runs[g.runs.length - 1];
      return last.e == null && last.t.endsWith("\n");
    };
    const merged = [];
    for (const g of groups) {
      const prev = merged[merged.length - 1];
      if (prev && !endsWithNewline(prev)) prev.runs = prev.runs.concat(g.runs);
      else merged.push({ b: g.b, i: g.i, runs: g.runs.slice() });
    }
    let out = "";
    let prevBare = false;
    for (const g of merged) {
      let list = g.runs;
      if (endsWithNewline(g)) {
        const last = list[list.length - 1];
        list = list.slice(0, -1);
        if (last.t.length > 1) list.push({ ...last, t: last.t.slice(0, -1) });
      }
      const isShell = g.b && g.b !== OPAQUE;
      let cur = [];
      let sawBlockEmbed = false;
      const flush = () => {
        const inner = inlineHtml(cur, embeds);
        if (isShell) {
          out += openTag(g.b) + inner + closeTag(g.b);
          prevBare = false;
        } else {
          out += (prevBare ? "<br>" : "") + inner;
          prevBare = true;
        }
        cur = [];
      };
      for (const r of list) {
        if (r.e != null && embeds[r.e].block) {
          sawBlockEmbed = true;
          if (cur.length) flush();
          out += embeds[r.e].html;
          prevBare = false;
        } else {
          cur.push(r);
        }
      }
      if (cur.length || !sawBlockEmbed) flush();
    }
    return out;
  }

  /* ---------- 쪽(page) DOM <-> 런 ---------- */
  function renderRuns(doc, runs, embeds, opts) {
    const options = opts || {};
    const frag = doc.createDocumentFragment();
    const stack = []; // { key, el }
    const GLYPHS = { image: "▣", footnote: "※", note: "✎", block: "▤", other: "◇" };
    for (const r of runs) {
      const want = [{ key: `B${r.i}|${blockKey(r.b)}`, shell: null }].concat(r.ch.map((s) => ({ key: s.key, shell: s })));
      let k = 0;
      while (k < stack.length && k < want.length && stack[k].key === want[k].key) k += 1;
      stack.length = k;
      for (let j = k; j < want.length; j += 1) {
        let el;
        if (j === 0) {
          el = doc.createElement("span");
          el.setAttribute("data-pw-inst", String(r.i));
          el.setAttribute("data-pw-blk", r.b && r.b !== OPAQUE ? JSON.stringify([r.b.tag, r.b.attrs]) : (r.b === OPAQUE ? OPAQUE : ""));
        } else {
          const shell = want[j].shell;
          el = doc.createElement(shell.tag);
          for (const [n, v] of shell.attrs) {
            if (/^on/i.test(n)) continue;
            try { el.setAttribute(n, v); } catch (_) { /* ignore invalid attr */ }
          }
        }
        (stack.length ? stack[stack.length - 1].el : frag).appendChild(el);
        stack.push({ key: want[j].key, el });
      }
      const host = stack[stack.length - 1].el;
      if (r.e != null) {
        const kind = embeds[r.e].kind || "other";
        const chip = doc.createElement("span");
        chip.className = "page-write-embed";
        chip.setAttribute("data-pw-embed", String(r.e));
        chip.setAttribute("contenteditable", "false");
        chip.setAttribute("data-glyph", GLYPHS[kind] || GLYPHS.other);
        if (typeof options.embedTitle === "function") chip.setAttribute("title", options.embedTitle(kind));
        chip.textContent = EMBED_CHAR;
        host.appendChild(chip);
      } else {
        host.appendChild(doc.createTextNode(r.t));
      }
    }
    return frag;
  }

  function readPageRuns(pageEl) {
    const runs = [];
    const lastChar = () => {
      const last = runs[runs.length - 1];
      return last ? last.t[last.t.length - 1] : "";
    };
    const walk = (node, chain, i, b) => {
      if (node.nodeType === 3) {
        const t = node.nodeValue.replace(/\r\n?/g, "\n");
        if (t) pushRun(runs, { t, ch: chain, i, b });
        return;
      }
      if (node.nodeType !== 1) return;
      if (node.hasAttribute("data-pw-embed")) {
        pushRun(runs, { t: EMBED_CHAR, ch: chain, i, b, e: Number(node.getAttribute("data-pw-embed")) });
        return;
      }
      if (node.hasAttribute("data-pw-blk")) {
        const inst = Number(node.getAttribute("data-pw-inst"));
        const blk = decodeBlock(node.getAttribute("data-pw-blk"));
        for (const child of node.childNodes) walk(child, chain, Number.isNaN(inst) ? i : inst, blk);
        return;
      }
      if (node.tagName === "BR") {
        pushRun(runs, { t: "\n", ch: chain, i, b });
        return;
      }
      if (node.tagName === "DIV" || node.tagName === "P") {
        // 브라우저가 줄바꿈으로 끼워 넣은 블록: 줄이 바뀌었음을 "\n" 으로 옮긴다
        if (runs.length && lastChar() !== "\n") pushRun(runs, { t: "\n", ch: [], i, b });
        for (const child of node.childNodes) walk(child, chain, i, b);
        return;
      }
      const next = chain.concat([shellFromElement(node)]);
      for (const child of node.childNodes) walk(child, next, i, b);
    };
    for (const child of pageEl.childNodes) walk(child, [], undefined, undefined);
    return runs;
  }

  /* ---------- 되돌림 검사(원고 보호) ---------- */
  function isBlank(text) {
    return WS_ONLY_RE.test(text);
  }

  function isAtomicEl(n) {
    return n.nodeType === 1 && n.matches(ATOMIC_SELECTOR);
  }

  function removeComments(node) {
    for (const child of Array.from(node.childNodes)) {
      if (child.nodeType === 8) child.remove();
      else if (child.nodeType === 1 && !isAtomicEl(child)) removeComments(child);
    }
  }

  function shellKeyOf(el) {
    return JSON.stringify([el.tagName, Array.from(el.attributes).map((a) => [a.name, a.value])]);
  }

  function normInline(parent) {
    for (const child of Array.from(parent.childNodes)) {
      if (child.nodeType === 1 && child.tagName !== "BR" && !isAtomicEl(child)) {
        normInline(child);
        if (!child.firstChild && !child.hasAttribute("id") && !child.hasAttribute("name")) child.remove();
      }
    }
    let k = parent.firstChild;
    while (k && k.nextSibling) {
      const nx = k.nextSibling;
      if (
        k.nodeType === 1 && nx.nodeType === 1
        && k.tagName !== "BR" && nx.tagName !== "BR"
        && !isAtomicEl(k) && !isAtomicEl(nx)
        && shellKeyOf(k) === shellKeyOf(nx)
      ) {
        while (nx.firstChild) k.appendChild(nx.firstChild);
        nx.remove();
        normInline(k);
      } else {
        k = nx;
      }
    }
    parent.normalize();
    for (const child of Array.from(parent.childNodes)) {
      if (child.nodeType === 3) {
        child.nodeValue = child.nodeValue.replace(WS_RE, " ");
        if (!child.nodeValue) child.remove();
      }
    }
  }

  function dropTrailingBr(container) {
    let n = container.lastChild;
    while (n && n.nodeType === 1 && n.tagName !== "BR" && !isAtomicEl(n) && n.lastChild) n = n.lastChild;
    if (n && n.nodeType === 1 && n.tagName === "BR") {
      n.remove();
      return true;
    }
    return false;
  }

  function canonicalFromContainer(box) {
    removeComments(box);
    // 루트: 블록 사이의 공백 문자열 제거 + 블록 안·인라인 묶음 정규화
    let prevBlockish = true;
    for (const node of Array.from(box.childNodes)) {
      if (node.nodeType === 3) {
        if (prevBlockish && isBlank(node.nodeValue)) node.remove();
        else prevBlockish = false;
        continue;
      }
      const kind = classifyRoot(node);
      prevBlockish = kind !== "inline";
    }
    box.normalize();
    // 인라인 묶음(연속된 인라인 루트 노드)을 임시 래퍼에 담아 정규화한다
    const nodes = Array.from(box.childNodes);
    let runStart = -1;
    const runs = [];
    nodes.forEach((n, idx) => {
      const inline = n.nodeType === 3 || (n.nodeType === 1 && classifyRoot(n) === "inline");
      if (inline) {
        if (runStart < 0) runStart = idx;
      } else if (runStart >= 0) {
        runs.push([runStart, idx]);
        runStart = -1;
      }
    });
    if (runStart >= 0) runs.push([runStart, nodes.length]);
    for (const [from, to] of runs) {
      const wrap = box.ownerDocument.createElement("div");
      box.insertBefore(wrap, nodes[from]);
      for (let k = from; k < to; k += 1) wrap.appendChild(nodes[k]);
      wrap.setAttribute("data-pw-bare", "1");
    }
    for (const node of Array.from(box.childNodes)) {
      if (node.nodeType !== 1) continue;
      if (node.hasAttribute("data-pw-bare") || classifyRoot(node) === "block") {
        normInline(node);
        if (dropTrailingBr(node)) normInline(node);
      }
    }
    return box.innerHTML;
  }

  function canonicalHtml(html, doc) {
    const d = doc || global.document;
    const box = d.createElement("div");
    box.innerHTML = html;
    return canonicalFromContainer(box);
  }

  /** 본문 요소를 런으로 바꿨다가 다시 HTML로 만든 결과가 원본과 같으면 { ok:true, parsed } */
  function roundTripCheck(root) {
    const d = root.ownerDocument;
    try {
      const parsed = parseEditor(root);
      const back = serialize(parsed.runs, parsed.embeds);
      const box = d.createElement("div");
      box.innerHTML = root.innerHTML;
      const before = canonicalFromContainer(box);
      const after = canonicalHtml(back, d);
      return { ok: before === after, parsed, before, after };
    } catch (err) {
      return { ok: false, parsed: null, error: String(err && err.message || err) };
    }
  }

  global.PageWriteModel = {
    EMBED_CHAR,
    parseEditor,
    serialize,
    renderRuns,
    readPageRuns,
    splitRuns,
    deleteRange,
    normalizeRuns,
    textOf,
    canonicalHtml,
    roundTripCheck,
  };
})(typeof window !== "undefined" ? window : globalThis);
