/**
 * 원고 찾아 바꾸기 — 일치 계산만 하는 순수 함수(DOM 없음).
 *
 * 편집기는 본문을 텍스트 조각(segment)으로 나눠 넘긴다. 굵게·기울임 같은 서식 때문에
 * 한 낱말이 여러 조각에 걸쳐 있어도 찾을 수 있게, 같은 문단(block)의 조각은 이어 붙여
 * 검색한다. 다른 문단끼리는 "\n"으로 갈라서 문단을 넘는 일치는 만들지 않는다.
 *
 * 설계: docs/design-ai-context-and-style-2026-11.md §7-2.
 */
(function (global) {
  "use strict";

  const BLOCK_BREAK = "\n";

  /** segments: [{ text, block }] (문서 순서) → { flat, starts } */
  function buildIndex(segments) {
    const list = Array.isArray(segments) ? segments : [];
    const starts = [];
    let flat = "";
    let lastBlock = null;
    list.forEach((segment, i) => {
      const block = segment && segment.block != null ? segment.block : i;
      if (lastBlock !== null && block !== lastBlock) flat += BLOCK_BREAK;
      starts.push(flat.length);
      flat += String((segment && segment.text) || "");
      lastBlock = block;
    });
    return { flat, starts, lengths: list.map((s) => String((s && s.text) || "").length) };
  }

  /** 글자 수가 바뀌지 않게 한 글자씩만 소문자로 바꾼다(오프셋 유지). */
  function foldCase(text) {
    let out = "";
    for (const ch of String(text || "")) {
      const lower = ch.toLowerCase();
      out += lower.length === ch.length ? lower : ch;
    }
    return out;
  }

  function regexFlags(options, global) {
    let flags = global ? "g" : "";
    if (!options.caseSensitive) flags += "i";
    flags += "u";
    return flags;
  }

  /**
   * flat 텍스트에서 query를 찾는다.
   * options: { caseSensitive, regex }
   * 반환: { matches: [{ start, end, text }], error: "" | "invalid_regex" }
   */
  function findMatches(flat, query, options) {
    const opts = options || {};
    const text = String(flat || "");
    const q = String(query || "");
    const matches = [];
    if (!q) return { matches, error: "" };
    if (opts.regex) {
      let re;
      try {
        re = new RegExp(q, regexFlags(opts, true));
      } catch (_) {
        return { matches, error: "invalid_regex" };
      }
      let found;
      let guard = 0;
      while ((found = re.exec(text)) !== null && guard < 100000) {
        guard += 1;
        if (found[0].length === 0) {
          re.lastIndex += 1; // 빈 일치는 건너뛴다(무한 반복 방지)
          continue;
        }
        if (found[0].indexOf(BLOCK_BREAK) !== -1) continue; // 문단을 넘는 일치는 버린다
        matches.push({ start: found.index, end: found.index + found[0].length, text: found[0] });
      }
      return { matches, error: "" };
    }
    if (q.indexOf(BLOCK_BREAK) !== -1) return { matches, error: "" };
    const hay = opts.caseSensitive ? text : foldCase(text);
    const needle = opts.caseSensitive ? q : foldCase(q);
    let from = 0;
    while (from <= hay.length - needle.length) {
      const at = hay.indexOf(needle, from);
      if (at < 0) break;
      matches.push({ start: at, end: at + needle.length, text: text.slice(at, at + needle.length) });
      from = at + needle.length;
    }
    return { matches, error: "" };
  }

  /**
   * flat 오프셋 → { segment, offset }.
   * bias "start": 조각 경계에서는 뒤 조각의 처음, "end": 앞 조각의 끝.
   */
  function locate(index, offset, bias) {
    const starts = index.starts || [];
    const lengths = index.lengths || [];
    if (!starts.length) return null;
    const preferEnd = bias === "end";
    for (let i = 0; i < starts.length; i += 1) {
      const begin = starts[i];
      const end = begin + lengths[i];
      if (preferEnd) {
        if (offset > begin && offset <= end) return { segment: i, offset: offset - begin };
      } else if (offset >= begin && offset < end) {
        return { segment: i, offset: offset - begin };
      }
    }
    // 빈 조각만 있거나 경계 끝: 가장 가까운 앞 조각의 끝
    for (let i = starts.length - 1; i >= 0; i -= 1) {
      if (starts[i] <= offset) return { segment: i, offset: Math.min(offset - starts[i], lengths[i]) };
    }
    return { segment: 0, offset: 0 };
  }

  /** 바꿀 글. 정규식이면 $1 같은 묶음 참조를 쓴다. 일반 찾기에서는 $가 특별하지 않다. */
  function replacementFor(matchText, query, replacement, options) {
    const opts = options || {};
    const rep = String(replacement == null ? "" : replacement);
    if (!opts.regex) return rep;
    try {
      return String(matchText).replace(new RegExp(String(query), regexFlags(opts, false)), rep);
    } catch (_) {
      return rep;
    }
  }

  global.FindReplaceModel = {
    BLOCK_BREAK,
    buildIndex,
    foldCase,
    findMatches,
    locate,
    replacementFor,
  };
})(typeof window !== "undefined" ? window : globalThis);
