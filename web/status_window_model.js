/**
 * 게임물 상태창 — 프리셋으로 줄을 만들고, 작가가 적은 줄을 프리셋 모양으로 정리하는 순수 함수(DOM 없음).
 *
 * - 원고 안 상태창은 줄마다 <div> 하나인 블록이다. 이 파일은 "줄 글자"만 다룬다.
 * - 정리(tidy)는 글을 잃지 않는다: 프리셋 항목에 맞는 줄은 값으로 옮기고, 맞지 않는 줄은
 *   그대로 아래쪽에 남긴다. 틀 글자만 있는 줄·창 제목·묶음 머리줄만 다시 만든다.
 * - 정렬 비의존이 기본: 구분선형은 위아래 선 + 줄 단위라 글꼴이 달라도 어긋나지 않는다.
 *   상자형도 오른쪽 벽을 두지 않는다(글꼴별 폭 계산을 하지 않음).
 *
 * 저장 형식과 내보내기 변환은 status_window.py. 설계: docs/design-ai-context-and-style-2026-11.md §13.
 */
(function (global) {
  "use strict";

  const FRAMES = ["divider", "bracket", "box", "none"];
  const ALIGNS = ["left", "center"];
  const LABEL_STYLES = ["colon", "spaced", "bracket"];
  const VALUE_TYPES = ["text", "number", "ratio"];
  const BACKGROUNDS = ["none", "soft"];
  const FONTS = ["body", "mono"];
  const EXPORT_MODES = ["keep", "text_safe"];
  const LIMITS = { presets: 30, items: 40, name: 40, title: 40, group: 20, value: 80 };

  const RULE_LEN = 18;
  const DIVIDER_LINE = "━".repeat(RULE_LEN);
  const BOX_TOP = `┌${"─".repeat(RULE_LEN - 1)}`;
  const BOX_MID = `├${"─".repeat(RULE_LEN - 1)}`;
  const BOX_BOTTOM = `└${"─".repeat(RULE_LEN - 1)}`;
  const BOX_SIDE = "│ ";
  const FRAME_ONLY_RE = /^[\s━─═┄┅┈┉╌╍┌┐└┘├┤┬┴┼┏┓┗┛┣┫│┃║╔╗╚╝╠╣\-=_~·•*]*$/;
  const SIDE_CHARS = "│┃║";

  function text(value, limit) {
    return String(value == null ? "" : value).replace(/\s+/g, " ").trim().slice(0, limit);
  }

  function choice(value, options) {
    const v = String(value || "").trim();
    return options.includes(v) ? v : options[0];
  }

  function normalizeItem(raw) {
    if (!raw || typeof raw !== "object") return null;
    const name = text(raw.name, LIMITS.name);
    if (!name) return null;
    return {
      name,
      group: text(raw.group, LIMITS.group),
      default: text(raw.default, LIMITS.value),
      type: choice(raw.type, VALUE_TYPES),
    };
  }

  function normalizePreset(raw, index = 0) {
    if (!raw || typeof raw !== "object") return null;
    let id = String(raw.id || "").trim();
    if (!/^[A-Za-z0-9_-]{1,40}$/.test(id)) id = `p${index + 1}`;
    const items = [];
    (Array.isArray(raw.items) ? raw.items : []).forEach((item) => {
      if (items.length >= LIMITS.items) return;
      const n = normalizeItem(item);
      if (n) items.push(n);
    });
    const display = raw.display && typeof raw.display === "object" ? raw.display : {};
    return {
      id,
      name: text(raw.name, LIMITS.name) || text(raw.title, LIMITS.name) || `#${index + 1}`,
      title: text(raw.title, LIMITS.title),
      frame: choice(raw.frame, FRAMES),
      align: choice(raw.align, ALIGNS),
      label_style: choice(raw.label_style, LABEL_STYLES),
      items,
      display: {
        background: choice(display.background, BACKGROUNDS),
        font: choice(display.font, FONTS),
      },
      export_mode: choice(raw.export_mode, EXPORT_MODES),
    };
  }

  /** 값 다듬기: 현재÷최대는 "120 / 150" → "120/150". 그 밖에는 앞뒤 공백만 뺀다(작가 글을 고치지 않는다). */
  function formatValue(item, raw) {
    const value = String(raw == null ? "" : raw).trim();
    if (item && item.type === "ratio") {
      const m = value.match(/^([-+]?\d[\d,.]*)\s*[/／]\s*([-+]?\d[\d,.]*)(.*)$/);
      if (m) return `${m[1]}/${m[2]}${m[3] ? ` ${m[3].trim()}` : ""}`.trim();
    }
    return value;
  }

  function itemLine(preset, name, value) {
    const v = String(value || "").trim();
    if (preset.label_style === "bracket") return v ? `[${name}] ${v}` : `[${name}]`;
    if (preset.label_style === "spaced") return v ? `${name} : ${v}` : `${name} :`;
    return v ? `${name}: ${v}` : `${name}:`;
  }

  function wrapLine(preset, line, role) {
    if (preset.frame === "box") return `${BOX_SIDE}${line}`;
    if (preset.frame === "bracket" && role === "item" && preset.label_style !== "bracket") return `[${line}]`;
    return line;
  }

  function titleLine(preset) {
    const t = String(preset.title || "").trim();
    if (!t) return "";
    if (preset.frame === "bracket") return `【${t}】`;
    if (preset.frame === "box") return t;
    return `[${t}]`;
  }

  function groupLine(group) {
    return `〈${group}〉`;
  }

  /**
   * values: { 항목이름: 값 } (없으면 기본값), extras: 프리셋에 없는 줄(그대로 남김)
   * → [{ text, role: "frame"|"title"|"group"|"item"|"extra" }]
   */
  function renderLines(presetRaw, values = null, extras = null) {
    const preset = normalizePreset(presetRaw) || normalizePreset({});
    const lines = [];
    const push = (t, role) => lines.push({ text: t, role });
    if (preset.frame === "divider") push(DIVIDER_LINE, "frame");
    if (preset.frame === "box") push(BOX_TOP, "frame");
    const title = titleLine(preset);
    if (title) {
      push(wrapLine(preset, title, "title"), "title");
      if (preset.frame === "box") push(BOX_MID, "frame");
    }
    let lastGroup = "";
    preset.items.forEach((item) => {
      if (item.group && item.group !== lastGroup) push(wrapLine(preset, groupLine(item.group), "group"), "group");
      lastGroup = item.group || "";
      const has = values && Object.prototype.hasOwnProperty.call(values, item.name);
      const value = formatValue(item, has ? values[item.name] : item.default);
      push(wrapLine(preset, itemLine(preset, item.name, value), "item"), "item");
    });
    (Array.isArray(extras) ? extras : []).forEach((extra) => {
      const t = String(extra || "").trim();
      if (t) push(wrapLine(preset, t, "extra"), "extra");
    });
    if (preset.frame === "divider") push(DIVIDER_LINE, "frame");
    if (preset.frame === "box") push(BOX_BOTTOM, "frame");
    return lines;
  }

  /** 틀 글자를 뗀 줄. 틀만 있는 줄이면 null. */
  function stripFrame(line) {
    let s = String(line == null ? "" : line).replace(/\u00a0/g, " ").trim();
    if (!s || FRAME_ONLY_RE.test(s)) return null;
    if (SIDE_CHARS.includes(s[0])) s = s.slice(1).trim();
    if (s && SIDE_CHARS.includes(s[s.length - 1])) s = s.slice(0, -1).trim();
    if (!s) return null;
    // [레벨: 3] 처럼 줄 전체를 감싼 대괄호(대괄호형 틀)는 벗긴다. "[이름] 값" 은 그대로 둔다.
    const wrapped = s.match(/^\[(.*)\]$/);
    if (wrapped && !wrapped[1].includes("]") && !wrapped[1].includes("[")) s = wrapped[1].trim();
    return s || null;
  }

  function compact(s) {
    return String(s || "").replace(/\s+/g, "").toLowerCase();
  }

  function isTitleOrGroup(line, preset) {
    const s = line.trim();
    if (/^〈[^〉]*〉$/.test(s)) return true;
    const title = String(preset.title || "").trim();
    if (!title) return false;
    const inner = s.replace(/^[【\[〔「『<《\s]+|[】\]〕」』>》\s]+$/g, "");
    return compact(inner) === compact(title);
  }

  /** 줄 하나가 프리셋 항목인지: 이름 바로 뒤에 구분(공백·:·：·=·]·-)이 오고 값이 따라온다. */
  function matchItem(line, items) {
    const sorted = items.slice().sort((a, b) => b.name.length - a.name.length);
    for (const item of sorted) {
      const name = item.name;
      let rest = null;
      if (line.startsWith(`[${name}]`)) rest = line.slice(name.length + 2);
      else if (line.startsWith(name)) {
        rest = line.slice(name.length);
        if (rest && !/^[\s:：=\]\-–—]/.test(rest)) rest = null; // "체력회복" 같은 다른 낱말
      } else if (compact(line).startsWith(compact(name))) {
        // "레 벨 : 3" 처럼 이름 사이 공백이 다른 경우
        let i = 0;
        let k = 0;
        const target = compact(name);
        while (i < line.length && k < target.length) {
          if (/\s/.test(line[i])) { i += 1; continue; }
          if (line[i].toLowerCase() !== target[k]) break;
          i += 1;
          k += 1;
        }
        if (k === target.length) {
          rest = line.slice(i);
          if (rest && !/^[\s:：=\]\-–—]/.test(rest)) rest = null;
        }
      }
      if (rest == null) continue;
      const value = rest.replace(/^[\s:：=\]\-–—]+/, "").trim();
      return { item, value };
    }
    return null;
  }

  /** 작가가 적은 줄들 → { values, extras } */
  function parseLines(presetRaw, rawLines) {
    const preset = normalizePreset(presetRaw) || normalizePreset({});
    const values = {};
    const extras = [];
    (Array.isArray(rawLines) ? rawLines : []).forEach((raw) => {
      const line = stripFrame(raw);
      if (line == null) return;
      if (isTitleOrGroup(line, preset)) return;
      const hit = matchItem(line, preset.items);
      if (hit && !Object.prototype.hasOwnProperty.call(values, hit.item.name)) {
        values[hit.item.name] = hit.value;
        return;
      }
      if (hit && !hit.value) return; // 같은 항목의 빈 줄이 또 있으면 버려도 글이 줄지 않는다
      extras.push(line);
    });
    return { values, extras };
  }

  /** 정리: 적은 줄을 프리셋 모양으로. 빠진 항목은 기본값 대신 비워 둔다. */
  function tidyLines(presetRaw, rawLines) {
    const preset = normalizePreset(presetRaw) || normalizePreset({});
    const parsed = parseLines(preset, rawLines);
    const values = {};
    preset.items.forEach((item) => {
      values[item.name] = Object.prototype.hasOwnProperty.call(parsed.values, item.name)
        ? parsed.values[item.name]
        : "";
    });
    return renderLines(preset, values, parsed.extras);
  }

  /** 내보내기 「텍스트 안전」과 같은 규칙(status_window.py text_safe_lines). */
  function textSafeLines(rawLines) {
    const out = [];
    (Array.isArray(rawLines) ? rawLines : []).forEach((raw) => {
      let s = String(raw == null ? "" : raw).trim();
      if (!s || FRAME_ONLY_RE.test(s)) return;
      if (SIDE_CHARS.includes(s[0])) s = s.slice(1).trim();
      if (s && SIDE_CHARS.includes(s[s.length - 1])) s = s.slice(0, -1).trim();
      if (s) out.push(s);
    });
    return out;
  }

  global.StatusWindowModel = {
    FRAMES,
    ALIGNS,
    LABEL_STYLES,
    VALUE_TYPES,
    BACKGROUNDS,
    FONTS,
    EXPORT_MODES,
    LIMITS,
    normalizePreset,
    formatValue,
    renderLines,
    stripFrame,
    parseLines,
    tidyLines,
    textSafeLines,
  };
})(typeof window !== "undefined" ? window : globalThis);
