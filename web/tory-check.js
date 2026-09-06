/* Live Tory Check — client-side text analysis (no AI). */
(function (global) {
  const WINDOW_CHARS = 1000;
  const DEBOUNCE_MS = 400;
  const DEFAULT_TAB = "words";
  const TABS = [
    "words",
    "phrases",
    "starts",
    "dialogue",
    "modifiers",
    "exclaims",
    "viewpoint",
    "forbidden",
  ];
  const PRESETS = {
    strict: { word: 3, phrase: 2, startStreak: 2, dialogue: 4, modifier: 0.3, exclaim: 3 },
    normal: { word: 5, phrase: 3, startStreak: 3, dialogue: 6, modifier: 0.4, exclaim: 5 },
    loose: { word: 7, phrase: 4, startStreak: 4, dialogue: 8, modifier: 0.5, exclaim: 8 },
  };
  const PHRASE_SIMILARITY = 0.75;
  const JOSA_SUFFIXES = [
    "으로서", "으로써", "에게서", "한테서", "으로부터",
    "이라고", "이라도", "이든지", "에서부터",
    "에서", "으로", "로서", "로써", "에게", "한테", "께서",
    "부터", "까지", "처럼", "보다", "라고", "이라",
    "이나", "든지", "이며", "이고",
    "은", "는", "이", "가", "을", "를", "의", "에", "와", "과", "도", "만", "로", "요",
  ].sort((a, b) => b.length - a.length);
  const STOPWORDS = new Set([
    "이", "가", "을", "를", "의", "은", "는", "에", "도", "로", "으로",
    "와", "과", "만", "부터", "까지", "에서", "에게", "한테",
    "그", "저", "것", "수", "등", "및", "또", "좀", "잘", "더",
    "나", "내", "너", "우리", "저", "제",
    "하다", "있다", "없다", "되다", "같다", "이다", "아니다",
    "한다", "했다", "였다",
    "그리고", "그러나", "하지만", "그래서", "그런데", "그러면",
    "이런", "그런", "저런", "어떤", "무슨", "이것", "그것", "저것",
    "한", "두", "세", "네", "너무", "정말", "진짜", "아주",
    "때문", "위해", "통해", "대해", "대한",
    "없는", "있는", "하는", "된", "될",
    "yes", "no", "the", "a", "an", "and", "or", "of", "to", "in",
  ]);
  const FIRST_PERSON = [
    "나는", "내가", "나를", "나의", "나도", "내게", "나한테", "난",
    "저는", "제가", "저를", "저의", "저도", "제게", "저한테", "전",
    "우리", "우리는", "우리가", "저희", "저희는", "저희가",
  ];
  const THIRD_PERSON = [
    "그는", "그가", "그를", "그의", "그에게", "그도",
    "그녀는", "그녀가", "그녀를", "그녀의", "그녀에게", "그녀도", "그녀",
    "그들은", "그들이", "그들을", "그들의",
  ];
  const MODIFIER_SHORT = new Set([
    "큰", "작은", "많은", "적은", "좋은", "나쁜", "예쁜", "멋진",
    "새로운", "오래된", "따뜻한", "차가운", "빠른", "느린", "긴", "짧은",
    "높은", "낮은", "밝은", "어두운", "강한", "약한", "젊은", "늙은",
    "가까운", "먼", "부드러운", "거친", "뜨거운", "시원한",
  ]);
  const EXCLAIM_PATTERNS = [
    { id: "bang", re: /!{2,}/g },
    { id: "question", re: /\?{2,}/g },
    { id: "ellipsis", re: /\.{3,}|…+/g },
    { id: "ah", re: /아+[.…]+|아아+/g },
    { id: "heok", re: /헉+/g },
    { id: "at", re: /앗+/g },
    { id: "heol", re: /헐+/g },
    { id: "wow", re: /우와+|와아+/g },
    { id: "aigo", re: /아이고+|어머나*|이런/g },
    { id: "eung", re: /엥+/g },
    { id: "heuk", re: /흑+/g },
    { id: "eak", re: /으악+|악!/g },
  ];
  const DIALOGUE_START = /^[\s]*["'“‘「『]/;
  const SENTENCE_SPLIT = /(?<=[.!?。！？])(?:\s+|$)/;

  function normalizePreset(value) {
    const key = String(value || "").trim().toLowerCase();
    return PRESETS[key] ? key : "normal";
  }

  function presetThresholds(preset) {
    return PRESETS[normalizePreset(preset)];
  }

  function sliceRecent(text, n) {
    const limit = Number(n) > 0 ? Number(n) : WINDOW_CHARS;
    const chars = Array.from(String(text || ""));
    return chars.slice(-limit).join("");
  }

  function stripJosa(token) {
    const raw = String(token || "");
    for (const josa of JOSA_SUFFIXES) {
      if (raw.length > josa.length && raw.endsWith(josa)) {
        return raw.slice(0, -josa.length);
      }
    }
    return raw;
  }

  function tokenize(text) {
    const source = String(text || "");
    const raw = source.match(/[A-Za-z0-9]+|[가-힣]+/g) || [];
    const out = [];
    for (const token of raw) {
      const stem = stripJosa(token);
      const word = stem || token;
      if (!word) continue;
      if (STOPWORDS.has(word) || STOPWORDS.has(token)) continue;
      if (JOSA_SUFFIXES.includes(word) && word.length <= 2) continue;
      if (word.length < 2 && !/[A-Za-z0-9]/.test(word)) continue;
      out.push(word);
    }
    return out;
  }

  function splitSentences(text) {
    const source = String(text || "").trim();
    if (!source) return [];
    const parts = source.split(SENTENCE_SPLIT).map((s) => s.trim()).filter(Boolean);
    return parts.length ? parts : [source];
  }

  function splitLines(text) {
    return String(text || "").split(/\r?\n/);
  }

  function isDialogueLine(line) {
    return DIALOGUE_START.test(String(line || ""));
  }

  function stripDialogue(text) {
    let out = String(text || "");
    out = out.replace(/"[^"]*"/g, " ");
    out = out.replace(/'[^']*'/g, " ");
    out = out.replace(/“[^”]*”/g, " ");
    out = out.replace(/‘[^’]*’/g, " ");
    out = out.replace(/「[^」]*」/g, " ");
    out = out.replace(/『[^』]*』/g, " ");
    const lastOpen = Math.max(
      out.lastIndexOf('"'),
      out.lastIndexOf("'"),
      out.lastIndexOf("“"),
      out.lastIndexOf("「"),
      out.lastIndexOf("『"),
    );
    if (lastOpen >= 0) out = out.slice(0, lastOpen);
    return out;
  }

  function firstEojeol(sentence) {
    const match = String(sentence || "").trim().match(/[A-Za-z0-9가-힣]+/);
    return match ? match[0] : "";
  }

  function tokenSet(text) {
    return new Set(tokenize(text));
  }

  function jaccard(a, b) {
    const A = tokenSet(a);
    const B = tokenSet(b);
    if (!A.size && !B.size) return 1;
    let inter = 0;
    A.forEach((token) => {
      if (B.has(token)) inter += 1;
    });
    const union = A.size + B.size - inter;
    return union ? inter / union : 0;
  }

  function sentencesSimilar(a, b) {
    const left = String(a || "").replace(/\s+/g, " ").trim();
    const right = String(b || "").replace(/\s+/g, " ").trim();
    if (!left || !right) return false;
    if (left === right) return true;
    return jaccard(left, right) >= PHRASE_SIMILARITY;
  }

  function isModifierToken(token) {
    const word = String(token || "");
    if (!word) return false;
    if (MODIFIER_SHORT.has(word)) return true;
    if (word.length >= 2 && /(?:게|히)$/.test(word)) return true;
    if (word.length >= 3 && /(?:스러운|로운|다운|적인|같은)$/.test(word)) return true;
    if (word.length >= 2 && /(?:하게|히도)$/.test(word)) return true;
    if (word.length >= 3 && /(?:한|된)$/.test(word) && !STOPWORDS.has(word)) return true;
    return false;
  }

  function snippetAt(text, index, radius) {
    const src = String(text || "");
    const at = Math.max(0, Number(index) || 0);
    const pad = Number(radius) > 0 ? Number(radius) : 18;
    const start = Math.max(0, at - pad);
    const end = Math.min(src.length, at + pad);
    return `${start > 0 ? "…" : ""}${src.slice(start, end)}${end < src.length ? "…" : ""}`;
  }

  function analyzeWords(text, thresholds) {
    const windowText = sliceRecent(text, WINDOW_CHARS);
    const counts = new Map();
    tokenize(windowText).forEach((word) => {
      counts.set(word, (counts.get(word) || 0) + 1);
    });
    const items = [...counts.entries()]
      .sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0], "ko"))
      .slice(0, 20)
      .map(([word, count]) => ({
        word,
        count,
        flagged: count >= thresholds.word,
      }));
    return {
      tab: "words",
      windowChars: windowText.length,
      threshold: thresholds.word,
      items,
    };
  }

  function analyzePhrases(text, thresholds) {
    const windowText = sliceRecent(text, WINDOW_CHARS);
    const sentences = splitSentences(windowText);
    const clusters = [];
    sentences.forEach((sentence, index) => {
      const normalized = sentence.replace(/\s+/g, " ").trim();
      if (!normalized) return;
      let found = clusters.find((cluster) => sentencesSimilar(cluster.text, normalized));
      if (!found) {
        found = { text: normalized, count: 0, indexes: [] };
        clusters.push(found);
      }
      found.count += 1;
      found.indexes.push(index);
    });
    const items = clusters
      .filter((cluster) => cluster.count >= thresholds.phrase)
      .sort((a, b) => b.count - a.count)
      .map((cluster) => ({
        text: cluster.text,
        count: cluster.count,
        flagged: true,
      }));
    return {
      tab: "phrases",
      windowChars: windowText.length,
      threshold: thresholds.phrase,
      items,
    };
  }

  function analyzeStarts(text, thresholds) {
    const windowText = sliceRecent(text, WINDOW_CHARS);
    const sentences = splitSentences(windowText);
    const starts = sentences.map(firstEojeol);
    const streaks = [];
    let i = 0;
    while (i < starts.length) {
      const token = starts[i];
      let j = i + 1;
      while (j < starts.length && starts[j] === token && token) j += 1;
      const length = j - i;
      if (token && length >= thresholds.startStreak) {
        streaks.push({ token, count: length, flagged: true });
      }
      i = j;
    }
    return {
      tab: "starts",
      windowChars: windowText.length,
      threshold: thresholds.startStreak,
      items: streaks,
    };
  }

  function analyzeDialogue(text, thresholds) {
    const windowText = sliceRecent(text, WINDOW_CHARS);
    const lines = splitLines(windowText);
    let maxStreak = 0;
    let current = 0;
    let trailing = 0;
    let inStreak = false;
    lines.forEach((line, index) => {
      const dialogue = isDialogueLine(line);
      if (dialogue) {
        current += 1;
        inStreak = true;
        if (current > maxStreak) maxStreak = current;
      } else if (String(line).trim() === "") {
        /* blank lines between quotes still count as the same run */
      } else {
        current = 0;
        inStreak = false;
      }
      if (index === lines.length - 1) trailing = inStreak ? current : 0;
    });
    if (!windowText.trim()) trailing = 0;
    return {
      tab: "dialogue",
      windowChars: windowText.length,
      threshold: thresholds.dialogue,
      currentStreak: trailing,
      maxStreak,
      flagged: maxStreak >= thresholds.dialogue || trailing >= thresholds.dialogue,
    };
  }

  function analyzeModifiers(text, thresholds) {
    const windowText = sliceRecent(text, WINDOW_CHARS);
    const sentences = splitSentences(windowText);
    const items = sentences.map((sentence) => {
      const tokens = tokenize(sentence);
      const modifiers = tokens.filter(isModifierToken);
      const ratio = tokens.length ? modifiers.length / tokens.length : 0;
      return {
        text: sentence.replace(/\s+/g, " ").trim(),
        ratio,
        percent: Math.round(ratio * 100),
        modifiers: modifiers.length,
        tokens: tokens.length,
        flagged: ratio >= thresholds.modifier,
      };
    }).filter((item) => item.flagged);
    return {
      tab: "modifiers",
      windowChars: windowText.length,
      threshold: thresholds.modifier,
      items,
    };
  }

  function analyzeExclaims(text, thresholds) {
    const windowText = sliceRecent(text, WINDOW_CHARS);
    const items = [];
    let total = 0;
    EXCLAIM_PATTERNS.forEach((pattern) => {
      const re = new RegExp(pattern.re.source, pattern.re.flags);
      const matches = windowText.match(re) || [];
      if (matches.length) {
        total += matches.length;
        items.push({
          id: pattern.id,
          count: matches.length,
          sample: matches[0],
        });
      }
    });
    return {
      tab: "exclaims",
      windowChars: windowText.length,
      threshold: thresholds.exclaim,
      total,
      flagged: total >= thresholds.exclaim,
      items,
    };
  }

  function isWordChar(ch) {
    return /[A-Za-z0-9가-힣]/.test(ch || "");
  }

  function findPronounHits(text, needles) {
    const hits = [];
    const src = String(text || "");
    needles.forEach((word) => {
      let from = 0;
      while (from < src.length) {
        const at = src.indexOf(word, from);
        if (at < 0) break;
        const before = at > 0 ? src.charAt(at - 1) : "";
        const after = src.charAt(at + word.length) || "";
        if (!isWordChar(before) && !isWordChar(after)) {
          hits.push({
            word,
            index: at,
            snippet: snippetAt(src, at),
          });
        }
        from = at + word.length;
      }
    });
    hits.sort((a, b) => a.index - b.index);
    return hits;
  }

  function findTenseHits(text, tense) {
    const src = String(text || "");
    const hits = [];
    const pastRe = /(?:했|았|었|였)(?:다|어요|어|지|던|을|던가)?/g;
    const presentRe = /(?:고\s*있다|는\s*중이다|ㄴ다|는다)(?![가-힣])/g;
    const re = tense === "past" ? presentRe : pastRe;
    const label = tense === "past" ? "present" : "past";
    let match;
    while ((match = re.exec(src))) {
      hits.push({
        word: match[0],
        kind: label,
        index: match.index,
        snippet: snippetAt(src, match.index),
      });
    }
    return hits;
  }

  function analyzeViewpoint(text, settings) {
    const person = settings && settings.viewpoint_person;
    const tense = settings && settings.viewpoint_tense;
    const configured = (person === "first" || person === "third")
      && (tense === "past" || tense === "present");
    if (!configured) {
      return {
        tab: "viewpoint",
        configured: false,
        person: person || null,
        tense: tense || null,
        items: [],
      };
    }
    const body = stripDialogue(String(text || ""));
    const personHits = person === "first"
      ? findPronounHits(body, THIRD_PERSON)
      : findPronounHits(body, FIRST_PERSON);
    const tenseHits = findTenseHits(body, tense);
    return {
      tab: "viewpoint",
      configured: true,
      person,
      tense,
      items: [
        ...personHits.map((hit) => ({ ...hit, kind: "person" })),
        ...tenseHits.map((hit) => ({ ...hit, kind: "tense" })),
      ],
    };
  }

  function analyzeForbidden(text, settings) {
    const words = Array.isArray(settings && settings.forbidden_words)
      ? settings.forbidden_words.map((w) => String(w || "").trim()).filter(Boolean)
      : [];
    const src = String(text || "");
    const items = [];
    words.forEach((word) => {
      const hits = [];
      const lower = src.toLowerCase();
      const needle = word.toLowerCase();
      let from = 0;
      while (from < src.length) {
        const at = lower.indexOf(needle, from);
        if (at < 0) break;
        hits.push({
          index: at,
          snippet: snippetAt(src, at),
        });
        from = at + Math.max(1, word.length);
      }
      if (hits.length) {
        items.push({ word, count: hits.length, hits });
      }
    });
    return {
      tab: "forbidden",
      words,
      items,
    };
  }

  function analyze(tabId, text, settings) {
    const tab = TABS.includes(tabId) ? tabId : DEFAULT_TAB;
    const thresholds = presetThresholds(settings && settings.preset);
    switch (tab) {
      case "words":
        return analyzeWords(text, thresholds);
      case "phrases":
        return analyzePhrases(text, thresholds);
      case "starts":
        return analyzeStarts(text, thresholds);
      case "dialogue":
        return analyzeDialogue(text, thresholds);
      case "modifiers":
        return analyzeModifiers(text, thresholds);
      case "exclaims":
        return analyzeExclaims(text, thresholds);
      case "viewpoint":
        return analyzeViewpoint(text, settings || {});
      case "forbidden":
        return analyzeForbidden(text, settings || {});
      default:
        return analyzeWords(text, thresholds);
    }
  }

  global.ToryCheckEngine = {
    WINDOW_CHARS,
    DEBOUNCE_MS,
    DEFAULT_TAB,
    TABS,
    PRESETS,
    normalizePreset,
    presetThresholds,
    sliceRecent,
    tokenize,
    splitSentences,
    isDialogueLine,
    stripDialogue,
    jaccard,
    analyze,
    analyzeWords,
    analyzePhrases,
    analyzeStarts,
    analyzeDialogue,
    analyzeModifiers,
    analyzeExclaims,
    analyzeViewpoint,
    analyzeForbidden,
  };
})(typeof window !== "undefined" ? window : globalThis);
