const fs = require("fs");
const path = require("path");
const vm = require("vm");

const code = fs.readFileSync(
  path.join(__dirname, "..", "web", "smart-punctuation.js"),
  "utf8"
);
const sandbox = { globalThis: {} };
sandbox.globalThis = sandbox;
vm.runInNewContext(code, sandbox);
const SP = sandbox.SmartPunctuation || sandbox.globalThis.SmartPunctuation;
if (!SP) {
  console.error("SmartPunctuation missing");
  process.exit(1);
}

let failed = 0;
function assert(cond, msg) {
  if (!cond) {
    console.error("FAIL", msg);
    failed += 1;
  } else {
    console.log("ok", msg);
  }
}

function memoryStorage(init) {
  const data = { ...(init || {}) };
  return {
    getItem(key) {
      return Object.prototype.hasOwnProperty.call(data, key) ? data[key] : null;
    },
    setItem(key, value) {
      data[key] = String(value);
    },
    _data: data,
  };
}

const allOnPairs = {};
SP.EXTRA_PAIRS.forEach((pair) => {
  allOnPairs[pair.id] = true;
});
const allOn = { quotes: true, pairs: allOnPairs };
const quotesOnly = { quotes: true, pairs: SP.defaultPairMap(false) };

assert(SP.EXTRA_PAIRS.length === 8, "eight extra pairs");
assert(
  !SP.EXTRA_PAIRS.some((pair) => pair.open === "\u3010" || pair.close === "\u3011"),
  "【】 not in extra pairs"
);
assert(SP.RESERVED["\u3010"] && SP.RESERVED["\u3011"], "【】 reserved");

assert(
  SP.decide({ inputType: "insertText", data: '"', collapsed: true, prev: "", next: "", prefs: allOn }).action === "wrap",
  "straight double wrap"
);
assert(
  SP.decide({ inputType: "insertText", data: "'", collapsed: true, prev: "n", next: "", prefs: allOn }).action === "ignore",
  "latin apostrophe skip wrap"
);
assert(
  SP.decide({ inputType: "insertText", data: "'", collapsed: true, prev: "가", next: "", prefs: allOn }).action === "wrap",
  "hangul then single quote wraps"
);

assert(
  SP.decide({ inputType: "insertText", data: "(", collapsed: true, prev: "", next: "", prefs: allOn }).action === "wrap"
    && SP.decide({ inputType: "insertText", data: "(", collapsed: true, prev: "", next: "", prefs: allOn }).close === ")",
  "paren wrap"
);
assert(
  SP.decide({ inputType: "insertText", data: "(", collapsed: true, prev: "", next: "", prefs: quotesOnly }).action === "ignore",
  "disabled paren types as-is"
);
assert(
  SP.decide({ inputType: "insertText", data: "[", collapsed: true, prev: "", next: "", prefs: allOn }).close === "]",
  "square wrap"
);
assert(
  SP.decide({ inputType: "insertText", data: "\u300C", collapsed: true, prev: "", next: "", prefs: allOn }).close === "\u300D",
  "corner wrap"
);
assert(
  SP.decide({ inputType: "insertText", data: "\u300E", collapsed: true, prev: "", next: "", prefs: allOn }).close === "\u300F",
  "white corner wrap"
);
assert(
  SP.decide({ inputType: "insertText", data: "\u300A", collapsed: true, prev: "", next: "", prefs: allOn }).close === "\u300B",
  "title wrap"
);
assert(
  SP.decide({ inputType: "insertText", data: "\u3008", collapsed: true, prev: "", next: "", prefs: allOn }).close === "\u3009",
  "angle wrap"
);
assert(
  SP.decide({ inputType: "insertText", data: "\u2018", collapsed: true, prev: "", next: "", prefs: allOn }).close === "\u2019",
  "curly single wrap"
);
assert(
  SP.decide({ inputType: "insertText", data: "\u201C", collapsed: true, prev: "", next: "", prefs: allOn }).close === "\u201D",
  "curly double wrap"
);

assert(
  SP.decide({ inputType: "insertText", data: "\u3010", collapsed: true, prev: "", next: "", prefs: allOn }).action === "ignore",
  "【 does not wrap"
);
assert(
  SP.decide({ inputType: "insertText", data: ")", collapsed: true, prev: "", next: ")", prefs: allOn }).action === "skip",
  "skip over existing closer"
);
assert(
  SP.decide({ inputType: "insertText", data: '"', collapsed: true, prev: "", next: '"', prefs: allOn }).action === "skip",
  "skip over existing quote"
);
assert(
  SP.decide({
    inputType: "deleteContentBackward",
    collapsed: true,
    prev: "(",
    next: ")",
    prefs: allOn,
  }).action === "deletePair",
  "backspace empty paren pair"
);
assert(
  SP.decide({
    inputType: "deleteContentBackward",
    collapsed: true,
    prev: "(",
    next: ")",
    prefs: quotesOnly,
  }).action === "ignore",
  "disabled pair backspace is normal"
);

const offStore = memoryStorage({ [SP.QUOTES_KEY]: "0" });
const migratedOff = SP.readPrefs(offStore);
assert(migratedOff.quotes === false, "quotes pref 0 stays off");
assert(
  SP.EXTRA_PAIRS.every((pair) => migratedOff.pairs[pair.id] === false),
  "new pairs default off when quotes were off"
);
assert(offStore.getItem(SP.PAIRS_KEY), "pair prefs written on migrate");

const onStore = memoryStorage({ [SP.QUOTES_KEY]: "1" });
const migratedOn = SP.readPrefs(onStore);
assert(migratedOn.quotes === true, "quotes pref 1 stays on");
assert(
  SP.EXTRA_PAIRS.every((pair) => migratedOn.pairs[pair.id] === false),
  "new pairs default off so existing quote users are not surprised"
);

const keepQuotes = memoryStorage({
  [SP.QUOTES_KEY]: "1",
  [SP.PAIRS_KEY]: JSON.stringify({ paren: false, square: true }),
});
const mixed = SP.readPrefs(keepQuotes);
assert(mixed.quotes === true, "existing quotes key untouched");
assert(mixed.pairs.paren === false && mixed.pairs.square === true, "saved pair flags kept");
assert(mixed.pairs.corner === false, "missing extra pair defaults off");

const wrapSel = SP.decide({
  inputType: "insertText",
  data: "(",
  collapsed: false,
  selected: "안녕",
  prefs: allOn,
});
assert(wrapSel.action === "wrap" && wrapSel.selected === "안녕", "wrap selection");

if (failed) {
  process.exit(1);
}
console.log("test_smart_punctuation: ok");
