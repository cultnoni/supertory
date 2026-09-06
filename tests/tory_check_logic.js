const fs = require("fs");
const path = require("path");
const vm = require("vm");

const code = fs.readFileSync(path.join(__dirname, "..", "web", "tory-check.js"), "utf8");
const window = {};
vm.runInNewContext(code, { window, console });
const T = window.ToryCheckEngine;
if (!T) {
  console.error("ToryCheckEngine missing");
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

assert(T.DEBOUNCE_MS === 400, "debounce 400");
assert(T.WINDOW_CHARS === 1000, "window 1000");
assert(T.DEFAULT_TAB === "words", "default tab words");
assert(T.TABS.length === 8, "eight tabs");
assert(T.PRESETS.strict.word === 3 && T.PRESETS.normal.word === 5 && T.PRESETS.loose.word === 7, "word thresholds");
assert(T.PRESETS.strict.phrase === 2 && T.PRESETS.normal.phrase === 3 && T.PRESETS.loose.phrase === 4, "phrase thresholds");

const repeated = Array(8).fill("하늘").join(" ") + " 구름 바람";
const words = T.analyze("words", repeated, { preset: "strict" });
const sky = words.items.find((item) => item.word === "하늘");
assert(sky && sky.count >= 8 && sky.flagged, "repeat word flagged at strict 3");
const wordsLoose = T.analyze("words", repeated, { preset: "loose" });
const skyLoose = wordsLoose.items.find((item) => item.word === "하늘");
assert(skyLoose && skyLoose.flagged, "repeat word still flagged at loose 7");

const stopText = "나는 학교에 가고 나는 집으로 간다. 이 가 을 를 의 하늘";
const filtered = T.tokenize(stopText);
assert(!filtered.includes("이") && !filtered.includes("가") && !filtered.includes("을"), "josa/stopwords filtered");

const phraseText = "문이 열렸다. 문이 열렸다. 문이 열렸다. 다른 문장이다.";
const phrases = T.analyze("phrases", phraseText, { preset: "normal" });
assert(phrases.items.some((item) => item.count >= 3 && item.flagged), "exact phrase repeat");

const startText = "그는 웃었다. 그는 걸었다. 그는 멈췄다. 바람이 불었다.";
const starts = T.analyze("starts", startText, { preset: "strict" });
assert(starts.items.some((item) => item.token.startsWith("그") && item.count >= 3), "sentence start streak");

const dialogueText = '"안녕."\n"뭐야."\n"몰라."\n"그래."\n"좋아."';
const dialogue = T.analyze("dialogue", dialogueText, { preset: "strict" });
assert(dialogue.currentStreak === 5 && dialogue.flagged, "dialogue streak current 5");

const modifierText = "아주 빠르게 아름답게 천천히 완전히 부드럽게 걸어가는 문장이다.";
const modifiers = T.analyze("modifiers", modifierText, { preset: "strict" });
assert(modifiers.items.length >= 1, "modifier-heavy sentence flagged");

const exclaimText = "헉!! 아... 뭐야?? 헐...";
const exclaims = T.analyze("exclaims", exclaimText, { preset: "strict" });
assert(exclaims.total >= 3 && exclaims.flagged, "exclaim count");

const pov = T.analyze("viewpoint", '나는 걸었다. 그는 말했다. "나는 괜찮아."', {
  viewpoint_person: "third",
  viewpoint_tense: "past",
});
assert(pov.configured, "viewpoint configured");
assert(pov.items.some((item) => item.word.includes("나")), "first person in third-person work");
assert(!pov.items.some((item) => item.snippet && item.snippet.includes("괜찮아")), "quoted dialogue excluded");

const banned = T.analyze("forbidden", "여기는 비밀이 있다.", { forbidden_words: ["비밀"] });
assert(banned.items.some((item) => item.word === "비밀" && item.count >= 1), "forbidden word hit");

const onlyWords = T.analyze("words", "하늘 하늘 하늘 하늘 하늘", { preset: "normal" });
assert(onlyWords.tab === "words", "analyze returns only requested tab");

if (failed) process.exit(1);
console.log("all tory-check logic tests passed");
