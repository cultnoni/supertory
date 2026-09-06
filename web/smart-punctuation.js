/* Smart punctuation — pair catalog, prefs, and wrap/skip/delete decisions (no DOM). */
(function (global) {
  const QUOTES_KEY = "supertory.smartPunctuation";
  const PAIRS_KEY = "supertory.smartPunctuation.pairs";
  const RESERVED = { "\u3010": true, "\u3011": true };

  const EXTRA_PAIRS = [
    { id: "paren", open: "(", close: ")" },
    { id: "square", open: "[", close: "]" },
    { id: "corner", open: "\u300C", close: "\u300D" },
    { id: "whiteCorner", open: "\u300E", close: "\u300F" },
    { id: "title", open: "\u300A", close: "\u300B" },
    { id: "angle", open: "\u3008", close: "\u3009" },
    { id: "smartSingle", open: "\u2018", close: "\u2019" },
    { id: "smartDouble", open: "\u201C", close: "\u201D" },
  ];

  function defaultPairMap(_quotesOn) {
    const pairs = {};
    EXTRA_PAIRS.forEach((pair) => {
      pairs[pair.id] = false;
    });
    return pairs;
  }

  function readQuotesPref(storage) {
    try {
      const raw = storage.getItem(QUOTES_KEY);
      if (raw === null) return true;
      return raw !== "0";
    } catch (_) {
      return true;
    }
  }

  function writeQuotesPref(storage, on) {
    try {
      storage.setItem(QUOTES_KEY, on ? "1" : "0");
    } catch (_) {
      /* ignore */
    }
  }

  function normalizePairMap(parsed, quotesOn) {
    const fallback = defaultPairMap(quotesOn);
    if (!parsed || typeof parsed !== "object") return fallback;
    const pairs = {};
    EXTRA_PAIRS.forEach((pair) => {
      if (typeof parsed[pair.id] === "boolean") pairs[pair.id] = parsed[pair.id];
      else if (parsed[pair.id] === 0 || parsed[pair.id] === "0") pairs[pair.id] = false;
      else if (parsed[pair.id] === 1 || parsed[pair.id] === "1") pairs[pair.id] = true;
      else pairs[pair.id] = fallback[pair.id];
    });
    return pairs;
  }

  function readPairPrefs(storage, quotesOn) {
    try {
      const raw = storage.getItem(PAIRS_KEY);
      if (raw === null || raw === "") {
        const migrated = defaultPairMap(quotesOn);
        try {
          storage.setItem(PAIRS_KEY, JSON.stringify(migrated));
        } catch (_) {
          /* ignore */
        }
        return migrated;
      }
      return normalizePairMap(JSON.parse(raw), quotesOn);
    } catch (_) {
      return defaultPairMap(quotesOn);
    }
  }

  function writePairPrefs(storage, pairs) {
    try {
      storage.setItem(PAIRS_KEY, JSON.stringify(normalizePairMap(pairs, true)));
    } catch (_) {
      /* ignore */
    }
  }

  function readPrefs(storage) {
    const quotes = readQuotesPref(storage);
    return { quotes, pairs: readPairPrefs(storage, quotes) };
  }

  function pairByOpen(ch) {
    return EXTRA_PAIRS.find((pair) => pair.open === ch) || null;
  }

  function pairByClose(ch) {
    return EXTRA_PAIRS.find((pair) => pair.close === ch) || null;
  }

  function closerForOpener(open, prefs) {
    if (!open || RESERVED[open]) return null;
    if (prefs?.quotes && (open === '"' || open === "'")) return open;
    const pair = pairByOpen(open);
    if (pair && prefs?.pairs?.[pair.id]) return pair.close;
    return null;
  }

  function isCloserEnabled(ch, prefs) {
    if (!ch || RESERVED[ch]) return false;
    if (prefs?.quotes && (ch === '"' || ch === "'")) return true;
    const pair = pairByClose(ch);
    return Boolean(pair && prefs?.pairs?.[pair.id]);
  }

  function isEnabledPair(open, close, prefs) {
    if (!open || !close || RESERVED[open] || RESERVED[close]) return false;
    if (prefs?.quotes && open === close && (open === '"' || open === "'")) return true;
    return EXTRA_PAIRS.some(
      (pair) => pair.open === open && pair.close === close && prefs?.pairs?.[pair.id]
    );
  }

  function decide(opts) {
    const inputType = String(opts?.inputType || "");
    const data = String(opts?.data || "");
    const prev = String(opts?.prev || "");
    const next = String(opts?.next || "");
    const collapsed = opts?.collapsed !== false;
    const selected = String(opts?.selected || "");
    const prefs = opts?.prefs || { quotes: false, pairs: {} };

    if (inputType === "deleteContentBackward") {
      if (!collapsed) return { action: "ignore" };
      if (isEnabledPair(prev, next, prefs)) return { action: "deletePair" };
      return { action: "ignore" };
    }

    if (inputType !== "insertText" && inputType !== "insertCompositionText") {
      return { action: "ignore" };
    }
    if (!data || data.length !== 1 || RESERVED[data]) return { action: "ignore" };

    if (collapsed && next === data && isCloserEnabled(data, prefs)) {
      return { action: "skip" };
    }

    const close = closerForOpener(data, prefs);
    if (!close) return { action: "ignore" };
    if (data === "'" && collapsed && /[A-Za-z0-9]/.test(prev)) {
      return { action: "ignore" };
    }
    return {
      action: "wrap",
      open: data,
      close,
      selected: collapsed ? "" : selected,
    };
  }

  global.SmartPunctuation = {
    QUOTES_KEY,
    PAIRS_KEY,
    RESERVED,
    EXTRA_PAIRS,
    defaultPairMap,
    readQuotesPref,
    writeQuotesPref,
    readPairPrefs,
    writePairPrefs,
    readPrefs,
    closerForOpener,
    isCloserEnabled,
    isEnabledPair,
    decide,
  };
})(typeof window !== "undefined" ? window : globalThis);
