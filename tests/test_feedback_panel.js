const fs = require("fs");
const path = require("path");
const vm = require("vm");

const code = fs.readFileSync(
  path.join(__dirname, "..", "web", "feedback_panel.js"),
  "utf8"
);
const sandbox = { console };
sandbox.globalThis = sandbox;
sandbox.window = sandbox;
vm.runInNewContext(code, sandbox);
const FP = sandbox.FeedbackPanel || sandbox.globalThis.FeedbackPanel;
if (!FP) {
  console.error("FeedbackPanel missing");
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

const fixtures = JSON.parse(
  fs.readFileSync(path.join(__dirname, "fixtures", "feedback_paragraphs.json"), "utf8")
);
fixtures.filter((item) => item.fn === "html").forEach((item) => {
  const got = FP.paragraphsFromHtml(item.input);
  assert(
    JSON.stringify(got) === JSON.stringify(item.expected),
    item.id + " " + JSON.stringify(got)
  );
});

const cards = [
  { id: 3, start_para: 12, ord: 2, priority: "low" },
  { id: 1, start_para: 4, ord: 1, priority: "high" },
  { id: 2, start_para: 4, ord: 2, priority: "medium" },
  { id: 4, start_para: 1, ord: 0, priority: "ref" },
  { id: 5, start_para: 8, ord: 3, priority: null },
];
const sorted = FP.sortCards(cards);
assert(sorted.map((c) => c.id).join(",") === "4,1,2,5,3", "sort by start_para then ord");

const counts = FP.countCards(cards);
assert(counts.total === 5 && counts.high === 1 && counts.medium === 1 && counts.low === 1, "counts mix");
assert(counts.ref === 1 && counts.analyzing === 1, "ref and analyzing");

const highOnly = FP.visibleCards(cards, "high");
assert(highOnly.every((c) => c.priority === "high" || c.priority === "ref" || c.priority == null), "high reveal");
assert(highOnly.length === 3, "high+ref+analyzing");
assert(FP.visibleCards(cards, "medium").length === 4, "medium adds one");
assert(FP.visibleCards(cards, "low").length === 5, "low shows all");

assert(
  FP.formatCountLine(counts, 0, false) === "첨삭 제안 총 5개 · 중요 1 · 보통 1 · 낮음 1 · 참고 1",
  "count line"
);
assert(FP.formatCountLine(counts, 9, true) === "총 9개 중 5개 생성됨", "running count line");
assert(FP.stageLabel({ stage: "dup" }) === "문단 중복 검사", "dup stage");
assert(FP.stageLabel({ stage: "cards" }, 3, 9) === "카드 생성 3/9", "cards stage");
assert(FP.styleTypeLabel({ kind: "style", style_type: "explain_less" }) === "해설 줄이기", "style label");
assert(FP.styleTypeLabel({ kind: "consistency" }) === "설정·인물 일관성", "consistency label");
assert(FP.styleTypeLabel({ kind: "structure" }) === "구조", "structure label");
assert(FP.locationLabel({ start_para: 12, end_para: 13 }) === "문단 12~13 · 2문단", "range loc");
assert(FP.locationLabel({ start_para: 113, end_para: 118 }) === "문단 113~118 · 6문단", "range loc count");
assert(FP.locationLabel({ start_para: 4, end_para: 4 }) === "문단 4", "single loc");

function typesOf(segs) {
  return (segs || []).map((s) => s.type + ":" + s.text).join("|");
}
const delOnly = FP.diffSegments("가 나 다", "가 다");
assert(typesOf(delOnly).indexOf("del:") >= 0 && typesOf(delOnly).indexOf("add:") < 0, "delete only");
const addOnly = FP.diffSegments("가 다", "가 나 다");
assert(typesOf(addOnly).indexOf("add:") >= 0 && typesOf(addOnly).indexOf("del:") < 0, "add only");
const replaced = FP.diffSegments("가 나 다", "가 라 다");
assert(typesOf(replaced).indexOf("del:") >= 0 && typesOf(replaced).indexOf("add:") >= 0, "replace");
const same = FP.diffSegments("같은 문장", "같은 문장");
assert(same.length === 1 && same[0].type === "same" && same[0].text === "같은 문장", "identical");
const empty = FP.diffSegments("", "");
assert(Array.isArray(empty) && empty.length === 0, "empty");
const over = FP.diffSegments("가".repeat(2001), "나".repeat(2001));
assert(over === null, "over 3000 skip");

const notes = FP.referenceNotes(
  {
    weaknesses: [
      { id: "W9", title: "취향", body: "참고", fixable: "none" },
      { id: "W1", title: "카드됨", body: "있음", fixable: "sentence" },
      { id: "W2", title: "카드없음", body: "없음", fixable: "sentence" },
    ],
    consistency: [{ id: "C1", title: "호칭", body: "maybe", certainty: "maybe" }],
  },
  [{ report_ref: "W1" }, { report_ref: "W9", title: "취향" }],
  [{ id: "W2", title: "카드없음", reason: "quote_invalid" }]
);
assert(!notes.some((n) => n.id === "W9"), "carded none not duplicated");
assert(
  notes.some((n) => n.id === "W2" && n.reason === "위치를 찾지 못했어요"),
  "unowned weakness reason"
);
assert(notes.some((n) => n.id === "C1"), "maybe consistency without card stays");
assert(
  !FP.cardHasWarning({ warnings: [{ code: "note_only", message: "참고" }] }),
  "note_only excluded from warning flag"
);
assert(
  FP.isNoteOnlyCard({ warnings_json: [{ code: "note_only" }] }),
  "note_only card"
);
assert(
  FP.noSuggestionHint({ warnings: [{ code: "note_only" }] }).indexOf("참고 지적") >= 0,
  "note_only hint"
);
assert(FP.defaultExplanationLens("웹소설", "로맨스") === "normal", "webnovel lens");
assert(FP.defaultExplanationLens("문학", "에세이") === "strong", "essay lens");
assert(FP.defaultExplanationLens("문학", "순문학") === "strong", "pure literature lens");
assert(FP.resolveExplanationLens("strong", "normal") === "strong", "stored lens wins");
assert(FP.resolveExplanationLens(null, "strong") === "strong", "default strong");
assert(FP.resolveExplanationLens("off", "normal") === "normal", "invalid stored uses default");
assert(FP.lensLabel("strong") === "자세히" && FP.lensLabel("normal") === "보통", "lens labels");
assert(
  JSON.stringify(FP.analysisRequestBody(12, "strong")) === JSON.stringify({
    scene_id: 12,
    explanation_lens: "strong",
  }),
  "POST body carries lens"
);

function textsOf(mapped) {
  return (mapped || []).map((row) => ({ i: row.i, text: row.text, type: row.type }));
}

fixtures.filter((item) => item.fn === "html").forEach((item) => {
  const root = FP.htmlToDomLike(item.input);
  const mapped = FP.mapEditorParagraphs(root);
  assert(
    JSON.stringify(textsOf(mapped)) === JSON.stringify(item.expected),
    "map " + item.id + " " + JSON.stringify(textsOf(mapped))
  );
  const plain = FP.paragraphsFromEditor(root);
  assert(
    JSON.stringify(plain) === JSON.stringify(item.expected),
    "fromEditor " + item.id
  );
});

const mixedRoot = FP.htmlToDomLike(
  "<p>첫 줄입니다.</p><div>둘째 줄입니다.</div><p>세 번째 <span>조각</span>입니다.</p>"
);
const mixedMap = FP.mapEditorParagraphs(mixedRoot);
assert(mixedMap.length === 3, "mixed para count");
assert(mixedMap[2].text.indexOf("조각") >= 0, "nested span text");
assert(mixedMap[2].segments.length >= 2, "span splits text nodes: " + mixedMap[2].segments.length);

const nestedFmt = FP.htmlToDomLike(
  "<p><strong>굵게 <em>안쪽</em></strong> 바깥</p>"
);
const nestedMap = FP.mapEditorParagraphs(nestedFmt);
assert(nestedMap.length === 1 && nestedMap[0].text.indexOf("굵게") >= 0, "nested format text");
assert(nestedMap[0].segments.length >= 2, "nested format segments");

const invis = FP.htmlToDomLike("<p>안녕\u00a0하세요\u200b요</p>");
const invisMap = FP.mapEditorParagraphs(invis);
assert(invisMap[0].text === "안녕 하세요요", "nbsp zwsp normalize: " + JSON.stringify(invisMap[0].text));

const emptyLines = FP.htmlToDomLike("<p>하나</p><p><br></p><p>둘</p>");
const emptyMap = FP.mapEditorParagraphs(emptyLines);
assert(
  JSON.stringify(textsOf(emptyMap)) === JSON.stringify([
    { i: 1, text: "하나", type: "text" },
    { i: 2, text: "둘", type: "text" },
  ]),
  "empty lines skipped " + JSON.stringify(textsOf(emptyMap))
);

const locateRoot = FP.htmlToDomLike(
  "<p>첫 문장입니다.</p><p>찾아야 할 문장입니다.</p><p>찾아야 할 문장입니다.</p><p>마지막입니다.</p>"
);
const locateMap = FP.mapEditorParagraphs(locateRoot);
const exact = FP.locateCard(
  { start_para: 2, end_para: 2, original_text: "찾아야 할 문장입니다." },
  locateMap
);
assert(exact.ok && exact.startPara === 2, "locate exact");

const shifted = FP.locateCard(
  { start_para: 1, end_para: 1, original_text: "찾아야 할 문장입니다." },
  locateMap
);
assert(shifted.ok && (shifted.startPara === 2 || shifted.startPara === 3), "locate shifted window");

const dupHit = FP.locateCard(
  { start_para: 3, end_para: 3, original_text: "찾아야 할 문장입니다." },
  locateMap
);
assert(dupHit.ok && dupHit.startPara === 3, "duplicate prefers original number: " + dupHit.startPara);

const missing = FP.locateCard(
  { start_para: 1, end_para: 1, original_text: "없는 문장입니다." },
  locateMap
);
assert(!missing.ok, "locate miss");

const multi = FP.locateCard(
  { start_para: 1, end_para: 2, original_text: "첫 문장입니다.\n찾아야 할 문장입니다." },
  locateMap
);
assert(multi.ok, "multi paragraph locate");

const before = FP.snapshotEditorMarkup(locateRoot);
FP.paintCardHighlights(exact.ok ? [exact.range] : [], exact.range);
const after = FP.snapshotEditorMarkup(locateRoot);
assert(before === after, "highlight does not mutate markup");

assert(
  FP.cardTitle({ report_ref: "W1" }, { weaknesses: [{ id: "W1", title: "약초 설명이 대화를 끊음" }] })
    === "약초 설명이 대화를 끊음",
  "title from report"
);
assert(FP.cardTitle({ title: "같은 내용이 반복된 문단", kind: "structure" }, null) === "같은 내용이 반복된 문단", "stored title");
assert(FP.cardTitle({ kind: "style", style_type: "explain_less" }, null) === "해설 줄이기", "old card fallback");

assert(
  FP.noSuggestionHint({ kind: "structure" }).indexOf("구조를 바꾸는") >= 0,
  "structure hint"
);
assert(
  FP.noSuggestionHint({ kind: "consistency" }).indexOf("작가님이 직접") >= 0,
  "consistency hint"
);
assert(
  FP.noSuggestionHint({ kind: "style", style_type: "info_placement" }).indexOf("수정안 없이") >= 0,
  "info_placement hint"
);
assert(
  FP.noSuggestionHint({ warnings: [{ code: "suggestion_removed" }] }).indexOf("안전하게") >= 0,
  "removed hint"
);
assert(
  FP.formatKindCounts([
    { kind: "style", style_type: "explain_less" },
    { kind: "style", style_type: "explain_less" },
    { kind: "structure" },
    { kind: "consistency" },
  ]) === "해설 줄이기 2 · 구조 1 · 설정·인물 일관성 1",
  "kind counts " + FP.formatKindCounts([
    { kind: "style", style_type: "explain_less" },
    { kind: "style", style_type: "explain_less" },
    { kind: "structure" },
    { kind: "consistency" },
  ])
);

assert(FP.reviewPanelWidthPx(1920) === 640, "clamp max 640 at 1920");
assert(FP.reviewPanelWidthPx(1600) === 640, "clamp max at 1600");
assert(FP.reviewPanelWidthPx(1200) === 480, "40% of 1200");
assert(FP.reviewPanelWidthPx(1000) === 420, "clamp min 420 at 1000");
assert(FP.reviewPanelWidthPx(800) === 420, "clamp min at 800");
assert(FP.reviewPanelWidthPx(0) === 420, "empty viewport uses min");

assert(FP.shouldFallbackToFloat(1920) === false, "wide enough for review");
assert(FP.shouldFallbackToFloat(828) === false, "828 leaves 360px manuscript");
assert(FP.shouldFallbackToFloat(827) === true, "827 drops below 360");
assert(FP.shouldFallbackToFloat(800) === true, "800 uses float");
assert(FP.shouldFallbackToFloat(1920, { conflict: true }) === true, "conflict forces float");
assert(FP.shouldFallbackToFloat(1200, { manuscriptMinWidth: 900 }) === true, "custom min width");

const snap = FP.snapshotMainLayout({
  outlineWidth: 280,
  binderCollapsed: false,
  aiPanelWidth: 310,
  aiCollapsed: true,
});
assert(snap.outlineWidth === 280 && snap.aiPanelWidth === 310, "snapshot widths");
assert(snap.binderCollapsed === false && snap.aiCollapsed === true, "snapshot flags");

const reviewPlan = FP.planFeedbackChrome(snap, 1400);
assert(reviewPlan.mode === "review", "plan review on wide screen");
assert(reviewPlan.applied.binderCollapsed === true, "review collapses binder");
assert(reviewPlan.applied.aiCollapsed === false, "review opens tory panel");
assert(reviewPlan.applied.aiPanelWidth === 560, "review width 40% of 1400");
const restored = FP.restoredLayout(reviewPlan);
assert(
  restored.outlineWidth === 280
    && restored.binderCollapsed === false
    && restored.aiPanelWidth === 310
    && restored.aiCollapsed === true,
  "restore matches snapshot"
);

const floatPlan = FP.planFeedbackChrome(snap, 800);
assert(floatPlan.mode === "float", "plan float on narrow screen");
assert(!floatPlan.applied, "float keeps current layout");
assert(FP.restoredLayout(floatPlan).aiPanelWidth === 310, "float restore still has snapshot");

const conflictPlan = FP.planFeedbackChrome(snap, 1800, { conflict: true });
assert(conflictPlan.mode === "float", "conflict plan is float");

assert(FP.runFakeLabel({}) === "", "old run without fake flag");
assert(FP.runFakeLabel({ params: {} }) === "", "empty params");
assert(FP.runFakeLabel({ params: { fake: true } }) === "시험 기록", "fake true");
assert(FP.runFakeLabel({ params: { fake: false } }) === "실제 분석", "fake false");
assert(FP.runFakeFlag({ params: { fake: true } }) === true, "flag true");
assert(FP.runFakeFlag({ params: { fake: false } }) === false, "flag false");
assert(FP.runFakeFlag({}) === null, "flag missing");

const layoutPri = FP.visibleCardLayout(cards, "high");
assert(layoutPri.items.map((c) => c.id).join(",") === "4,1,5", "priority high group only");
assert(layoutPri.mode === "priority", "default sort mode");
const layoutMed = FP.visibleCardLayout(cards, "medium");
assert(layoutMed.items.map((c) => c.id).join(",") === "4,1,5,2", "medium group appended not interleaved");
assert(layoutMed.groups.medium.map((c) => c.id).join(",") === "2", "medium own group");
const layoutMs = FP.visibleCardLayout(cards, "medium", "manuscript");
assert(layoutMs.items.map((c) => c.id).join(",") === "4,1,2,5", "manuscript mix visible only");
assert(layoutMs.mode === "manuscript", "manuscript mode");
assert(FP.numberCards(layoutMed.items).map((r) => FP.padCardIndex(r.index)).join(",") === "01,02,03,04", "renumber visible");
assert(FP.visibleWalkOrder(cards, "medium").join(",") === "4,1,5,2", "walk follows visible");
assert(FP.visibleWalkOrder(cards, "low", "manuscript").join(",") === "4,1,2,5,3", "walk manuscript all");
const extraArriving = cards.concat([{ id: 6, start_para: 2, ord: 0, priority: "high" }]);
assert(FP.visibleCardLayout(extraArriving, "medium").groups.medium.length === 1, "polling keeps medium revealed");
assert(FP.visibleWalkOrder(extraArriving, "medium").join(",") === "4,6,1,5,2", "new high card renumbers walk");

assert(FP.shouldIgnoreCardClick({
  target: { closest: function (sel) { return sel.indexOf("button") >= 0 ? {} : null; } },
  selectionText: "",
}), "button click ignored");
assert(FP.shouldIgnoreCardClick({
  target: { closest: function (sel) { return sel.indexOf("a") === 0 ? {} : null; } },
  selectionText: "",
}), "link click ignored");
assert(!FP.shouldIgnoreCardClick({
  target: { closest: function () { return null; } },
  selectionText: "",
}), "card body click allowed");
assert(FP.shouldIgnoreCardClick({
  target: { closest: function () { return null; } },
  selectionText: "드래그한 텍스트",
}), "text selection ignored");

const overlapA = { id: 28, kind: "structure", start_para: 115, end_para: 118 };
const overlapB = { id: 30, kind: "consistency", start_para: 113, end_para: 118 };
const overlapC = { id: 31, kind: "consistency", start_para: 115, end_para: 132 };
const overlapStyle = { id: 32, kind: "style", start_para: 76, end_para: 76 };
assert(FP.shouldLinkOverlap(overlapA, overlapB), "struct/cons 50% overlap");
assert(FP.shouldLinkOverlap(overlapA, overlapC), "wide consistency overlaps structure");
assert(!FP.shouldLinkOverlap(overlapA, overlapStyle), "style elsewhere not linked");
const peerMap = FP.overlapPeerMap(FP.numberCards([overlapA, overlapB, overlapC]));
assert(peerMap[28].map((p) => p.id).sort().join(",") === "30,31", "peer ids");

assert(!FP.isInfoWarning({ code: "V8", message: "빠졌어요" }), "old V8 stays warning");
assert(FP.isInfoWarning({ code: "names_removed_by_deletion", severity: "info" }), "deletion info");
assert(
  FP.infoWarnings([
    { code: "V8", message: "빠졌어요" },
    { code: "names_removed_by_deletion", severity: "info", message: "이오나" },
  ]).length === 1,
  "info warnings filtered"
);

const wideHtml = [1, 2, 3, 4, 5, 6, 7].map(function (n) {
  return "<p>긴범위 문단" + n + " 본문입니다.</p>";
}).join("");
const wideMap = FP.mapEditorParagraphs(FP.htmlToDomLike(wideHtml));
const wideCard = {
  start_para: 1,
  end_para: 7,
  start_quote: "긴범위 문단1",
  end_quote: "긴범위 문단7",
  original_text: [1, 2, 3, 4, 5, 6, 7].map(function (n) { return "긴범위 문단" + n + " 본문입니다."; }).join("\n"),
};
const wideHit = FP.locateCard(wideCard, wideMap);
assert(wideHit.ok && wideHit.method === "quotes" && wideHit.span >= 6, "span>5 uses quotes: " + wideHit.method);

const driftedHtml = [1, 2, 3, 4, 5, 6, 7].map(function (n) {
  return "<p>긴범위 문단" + n + (n === 4 ? " 조금달라요." : " 본문입니다.") + "</p>";
}).join("");
const driftedMap = FP.mapEditorParagraphs(FP.htmlToDomLike(driftedHtml));
const driftedHit = FP.locateCard(wideCard, driftedMap);
assert(driftedHit.ok && driftedHit.method === "quotes", "middle para change still locates");
assert(FP.underlineRangesFor(wideCard, driftedHit, false, driftedMap).length === 2, "wide underline first+last");
assert(FP.underlineRangesFor(wideCard, driftedHit, true, driftedMap).length === 2, "active wide underline first+last");
assert(FP.underlineParaList(1, 7, driftedMap).join(",") === "1,7", "wide underline para list");
assert(driftedHit.joinEnd > driftedHit.joinStart, "join offsets on locate");

const divHtml = "<p>구분선 앞입니다.</p><p>***</p><p>구분선 뒤입니다.</p>"
  + "<p>이어지는 문단4.</p><p>이어지는 문단5.</p><p>이어지는 문단6.</p>";
const divMap = FP.mapEditorParagraphs(FP.htmlToDomLike(divHtml));
assert(divMap.some((row) => row.type === "divider"), "divider numbered");
const divCard = {
  start_para: 1,
  end_para: 6,
  start_quote: "구분선 앞",
  end_quote: "이어지는 문단6",
  original_text: "구분선 앞입니다.\n구분선 뒤입니다.\n이어지는 문단4.\n이어지는 문단5.\n이어지는 문단6.",
};
const divHit = FP.locateCard(divCard, divMap);
assert(divHit.ok, "divider range locates: " + (divHit.method || divHit.reason));

const quoteHtml = "<p>“안녕……하세요.”</p>";
const quoteMap = FP.mapEditorParagraphs(FP.htmlToDomLike(quoteHtml));
const quoteHit = FP.locateCard(
  { start_para: 1, end_para: 1, original_text: '"안녕...하세요."' },
  quoteMap
);
assert(quoteHit.ok, "folded quotes and ellipsis");

assert(!FP.cardHasSuggestion({ kind: "structure" }), "structure no suggestion");
assert(!FP.cardHasSuggestion({ suggestion: "" }), "empty suggestion");
assert(!FP.cardHasSuggestion({ suggestion: "  " }), "blank suggestion");
assert(FP.cardHasSuggestion({ suggestion: "고친 문장입니다." }), "has suggestion");
assert(!FP.cardHasSuggestion({ suggestion: "고친 문장", warnings: [{ code: "note_only" }] }), "note_only not suggestion");
assert(
  !FP.cardHasSuggestion({ suggestion: "고친 문장", warnings_json: [{ code: "suggestion_removed" }] }),
  "removed suggestion"
);
assert(
  !FP.cardHasSuggestion({ suggestion: "고친 문장", warnings: [{ code: "generation_failed" }] }),
  "failed suggestion"
);

const longEmp = FP.longRangeEmphasis(115, 132);
assert(longEmp.strong.join(",") === "115,132", "long strong first last");
assert(longEmp.mid.length === 16 && longEmp.mid[0] === 116 && longEmp.mid[15] === 131, "long mid paras");
const shortEmp = FP.longRangeEmphasis(50, 51);
assert(shortEmp.strong.join(",") === "50,51" && shortEmp.mid.length === 0, "short all strong");
const fourEmp = FP.longRangeEmphasis(1, 4);
assert(fourEmp.strong.join(",") === "1,2,3,4" && fourEmp.mid.length === 0, "4 paras all strong");
const fiveEmp = FP.longRangeEmphasis(10, 14);
assert(fiveEmp.strong.join(",") === "10,11,12,13,14" && fiveEmp.mid.length === 0, "5 paras all strong");
const sixEmp = FP.longRangeEmphasis(113, 118);
assert(sixEmp.strong.join(",") === "113,118" && sixEmp.mid.join(",") === "114,115,116,117", "6 paras first last");

assert(FP.normalizeUnderlineLevel("nope") === "high-medium", "default underline level");
assert(FP.shouldPaintUnderline({ id: 1, priority: "high" }, "high"), "high always");
assert(!FP.shouldPaintUnderline({ id: 2, priority: "medium" }, "high"), "medium hidden on high-only");
assert(FP.shouldPaintUnderline({ id: 2, priority: "medium" }, "high-medium"), "medium on default");
assert(!FP.shouldPaintUnderline({ id: 3, priority: "low" }, "high-medium"), "low hidden on default");
assert(FP.shouldPaintUnderline({ id: 3, priority: "low" }, "all"), "low on all");
assert(FP.shouldPaintUnderline({ id: 3, priority: "low" }, "high-medium", { hoverId: 3 }), "low on hover");
assert(FP.shouldPaintUnderline({ id: 3, priority: "ref" }, "high", { activeId: 3 }), "low on select");

assert(FP.targetScrollTop({
  scrollTop: 0, viewHeight: 600, contentHeight: 3000, firstLineOffset: 400, rangeHeight: 80, pad: 72,
}) === 328, "short range top pad 72");
assert(FP.targetScrollTop({
  scrollTop: 0, viewHeight: 600, contentHeight: 3000, firstLineOffset: 400, rangeHeight: 500, pad: 72,
}) === 328, "long range still starts at pad");
assert(FP.targetScrollTop({
  scrollTop: 0, viewHeight: 600, contentHeight: 3000, firstLineOffset: 10, rangeHeight: 80, pad: 72,
}) === 0, "document start clamp");
assert(FP.targetScrollTop({
  scrollTop: 2400, viewHeight: 600, contentHeight: 3000, firstLineOffset: 500, rangeHeight: 80, pad: 72,
}) === 2400, "document end clamp");
assert(FP.targetScrollTop({
  scrollTop: 0, viewHeight: 200, contentHeight: 2000, firstLineOffset: 100, rangeHeight: 140, pad: 72,
}) === 40, "short range reduced pad to keep visible");

const notePlan = FP.activePaintPlan(
  { suggestion: "" },
  { ok: true, span: 18, range: "full", edgeRanges: ["a", "b"], midRanges: ["m1", "m2"] }
);
assert(notePlan.replace.length === 0 && notePlan.note.join(",") === "a,b", "note uses pale strong");
assert(notePlan.mid.join(",") === "m1,m2", "mid light");
const sugPlan = FP.activePaintPlan(
  { suggestion: "고침" },
  { ok: true, span: 2, range: "full" }
);
assert(sugPlan.range.join(",") === "full" && sugPlan.note.length === 0 && sugPlan.del.length === 0, "suggestion range pink");
const fourPlan = FP.activePaintPlan(
  { suggestion: "" },
  { ok: true, span: 4, range: "one", allRanges: ["p1", "p2", "p3", "p4"], edgeRanges: ["p1", "p2", "p3", "p4"] }
);
assert(fourPlan.note.join(",") === "p1,p2,p3,p4" && fourPlan.mid.length === 0, "4 paras paint all");

function fmtMerge(rows) {
  return (rows || []).map(function (row) {
    return row.start + "-" + row.end + ":" + row.tier + ":" + row.indexes.join("+");
  }).join("|");
}
const noOverlap = FP.mergeUnderlineSpans([
  { start: 0, end: 10, tier: "high", cardId: "a", index: 3, order: 0 },
  { start: 20, end: 30, tier: "medium", cardId: "b", index: 4, order: 20 },
]);
assert(fmtMerge(noOverlap) === "0-10:high:3|20-30:medium:4", "merge no overlap");
const contained = FP.mergeUnderlineSpans([
  { start: 0, end: 100, tier: "high", cardId: "a", index: 3, order: 0 },
  { start: 20, end: 40, tier: "medium", cardId: "b", index: 4, order: 20 },
]);
assert(fmtMerge(contained) === "0-20:high:3|20-40:high:3+4|40-100:high:3", "merge contained");
const partial = FP.mergeUnderlineSpans([
  { start: 0, end: 50, tier: "high", cardId: "a", index: 3, order: 0 },
  { start: 30, end: 80, tier: "medium", cardId: "b", index: 4, order: 30 },
]);
assert(fmtMerge(partial) === "0-30:high:3|30-50:high:3+4|50-80:medium:4", "merge partial");
const sameTier = FP.mergeUnderlineSpans([
  { start: 0, end: 50, tier: "medium", cardId: "a", index: 2, order: 0 },
  { start: 30, end: 80, tier: "medium", cardId: "b", index: 4, order: 30 },
]);
assert(fmtMerge(sameTier) === "0-30:medium:2|30-50:medium:2+4|50-80:medium:4", "merge same tier earlier wins");
const triple = FP.mergeUnderlineSpans([
  { start: 0, end: 100, tier: "high", cardId: "c3", index: 3, order: 0 },
  { start: 20, end: 80, tier: "medium", cardId: "c4", index: 4, order: 20 },
  { start: 40, end: 60, tier: "low", cardId: "c2", index: 2, order: 40 },
]);
assert(fmtMerge(triple) === "0-20:high:3|20-40:high:3+4|40-60:high:3+4+2|60-80:high:3+4|80-100:high:3", "merge triple");
assert(FP.overlapTooltipLabel([2, 3, 4]) === "카드 02, 03, 04", "overlap tooltip");

const para113_132 = FP.mergeUnderlineSpans([
  { start: 113, end: 119, tier: "high", cardId: "03", index: 3, order: 113 },
  { start: 115, end: 116, tier: "medium", cardId: "04", index: 4, order: 115 },
  { start: 118, end: 133, tier: "high", cardId: "02", index: 2, order: 118 },
]);
assert(fmtMerge(para113_132) === "113-115:high:3|115-116:high:3+4|116-118:high:3|118-119:high:3+2|119-133:high:2", "113-132 three cards");

function delFmt(mapped) {
  if (mapped.skipped) return "skip";
  return (mapped.spans || []).map(function (s) { return s.start + "-" + s.end; }).join("|");
}
assert(delFmt(FP.mapDelSpansToSource("가 나 다", "가 다", "가 나 다")) === "1-3", "del only maps");
assert(delFmt(FP.mapDelSpansToSource("가 나 다", "가 라 다", "가 나 다")) === "2-3", "replace del maps");
assert(delFmt(FP.mapDelSpansToSource("hello", "hello world", "hello")) === "", "add only no del");
assert(delFmt(FP.mapDelSpansToSource("그대로", "그대로", "그대로")) === "", "no change no del");
const paraDelSrc = "첫째 문단.\n둘째 지울 문장.\n셋째 문단.";
const paraDelSug = "첫째 문단.\n둘째 고친 문장.\n셋째 문단.";
const paraDelHit = FP.mapDelSpansToSource(paraDelSrc, paraDelSug, paraDelSrc);
assert(delFmt(paraDelHit) === "10-12", "multi-para del maps: " + delFmt(paraDelHit));
assert(delFmt(FP.mapDelSpansToSource("foo  bar", "foo bar", "foo  bar")) === "3-5", "whitespace del maps");
const skipDiff = FP.mapDelSpansToSource("가".repeat(2001), "나".repeat(2001), "가".repeat(2001));
assert(skipDiff.skipped, "over 3000 skip del");

const kindCards = [
  { id: 1, kind: "structure", start_para: 1, ord: 0, priority: "high" },
  { id: 2, kind: "consistency", start_para: 2, ord: 0, priority: "medium" },
  { id: 3, kind: "style", style_type: "explain_less", start_para: 3, ord: 0, priority: "low" },
  { id: 4, kind: "style", style_type: "explain_less", start_para: 4, ord: 0, priority: "high" },
  { id: 5, kind: "style", style_type: "info_placement", start_para: 5, ord: 0, priority: "medium" },
];
assert(FP.applyKindFilter(kindCards, "구조").map((c) => c.id).join(",") === "1", "kind filter structure");
assert(FP.applyKindFilter(kindCards, "").map((c) => c.id).join(",") === "1,2,3,4,5", "kind filter all");
const chips = FP.kindChipList(kindCards);
assert(chips.total === 5, "chip total");
assert(chips.items.map((item) => item.key + ":" + item.count).join("|")
  === "구조:1|설정·인물 일관성:1|해설 줄이기:2|정보 배치:1", "chip labels");
assert(FP.applyKindFilter(kindCards, ["구조", "해설 줄이기"]).map((c) => c.id).join(",") === "1,3,4", "kind filter multi");
assert(FP.applyKindFilter(kindCards, []).map((c) => c.id).join(",") === "1,2,3,4,5", "kind filter empty array");
assert(FP.toggleKindFilter([], "구조", true).join(",") === "구조", "toggle add");
assert(FP.toggleKindFilter(["구조"], "구조", false).join(",") === "", "toggle remove");
const moreAll = FP.moreSeeCounts(kindCards, "high", "");
assert(moreAll.moreMedium && moreAll.medium === 2, "more medium unfiltered");
const moreExplain = FP.moreSeeCounts(kindCards, "high", "해설 줄이기");
assert(!moreExplain.moreMedium && moreExplain.medium === 0 && moreExplain.low === 1, "more see uses filter");
assert(moreExplain.moreLow, "filtered more low");
const layoutKind = FP.visibleCardLayout(kindCards, "low", "priority", "해설 줄이기");
assert(layoutKind.items.map((c) => c.id).join(",") === "4,3", "priority filter order");
const layoutKindMs = FP.visibleCardLayout(kindCards, "low", "manuscript", "해설 줄이기");
assert(layoutKindMs.items.map((c) => c.id).join(",") === "3,4", "manuscript filter order");
const listSum = FP.formatListSummary(FP.countCards(kindCards), 0, false);
assert(listSum.title === "5개" && listSum.high === 2 && listSum.medium === 2 && listSum.low === 1, "list summary " + JSON.stringify(listSum));
const listRun = FP.formatListSummary(FP.countCards(kindCards), 9, true);
assert(listRun.title === "9개 중 5개 생성됨", "list summary running");
const viewDefault = FP.viewOptionsState({
  sortMode: "priority", showUnderline: true, underlineLevel: "high-medium", hideDict: true, kindFilter: [],
});
assert(viewDefault.sortLabel === "중요도순" && !viewDefault.filterOn && !viewDefault.displayChanged && viewDefault.filterLabel === "필터", "view default");
assert(viewDefault.underlineTargets.join(",") === "high,medium", "default underline targets");
const viewMs = FP.viewOptionsState({
  sortMode: "manuscript", showUnderline: true, underlineLevel: "high-medium", hideDict: true, kindFilter: [],
});
assert(viewMs.sortLabel === "원고순" && !viewMs.filterOn && !viewMs.displayChanged, "sort not a display change");
const viewFilter = FP.viewOptionsState({
  sortMode: "priority", showUnderline: true, underlineLevel: "high-medium", hideDict: true, kindFilter: ["구조", "해설 줄이기"],
});
assert(viewFilter.filterOn && viewFilter.filterCount === 2 && viewFilter.filterLabel === "필터 2" && !viewFilter.displayChanged, "filter count only");
const viewDisplay = FP.viewOptionsState({
  sortMode: "priority",
  showUnderline: false,
  underlineLevel: "all",
  hideDict: false,
  kindFilter: [],
});
assert(viewDisplay.displayChanged && !viewDisplay.filterOn && viewDisplay.underlineOn === false, "display extras");
assert(FP.underlinePaintTargets("high").join(",") === "high", "targets high");
assert(FP.underlinePaintTargets("high-medium").join(",") === "high,medium", "targets default");
assert(FP.underlinePaintTargets("all").join(",") === "high,medium,low,ref", "targets all");
assert(FP.underlinePaintTargets("high").indexOf("medium") < 0, "high omits medium");
const viewBoth = FP.viewOptionsState({
  sortMode: "manuscript", showUnderline: true, underlineLevel: "high", hideDict: true, kindFilter: ["구조"],
});
assert(viewBoth.filterLabel === "필터 1" && viewBoth.displayChanged && viewBoth.sortLabel === "원고순", "filter + level");

const sortPri = FP.controlDisplayState({ sortMode: "priority" });
assert(sortPri.sort.filter((s) => s.selected).map((s) => s.value).join(",") === "priority", "sort paint priority");
assert(sortPri.sort.filter((s) => s.selected).length === 1, "one sort selected");
const sortMs = FP.controlDisplayState({ sortMode: "manuscript" });
assert(sortMs.sortMode === "manuscript" && sortMs.sortLabel === "원고순", "sort state after change");
assert(sortMs.sort.filter((s) => s.selected).map((s) => s.value).join(",") === "manuscript", "toolbar sort matches manuscript");
assert(sortMs.sort.every((s) => s.ariaChecked === (s.selected ? "true" : "false")), "sort aria matches selected");
const mockBtns = ["priority", "manuscript"].map((value) => {
  const attrs = { "data-sort": value, "aria-checked": "false" };
  const cls = new Set();
  return {
    getAttribute: (k) => attrs[k],
    setAttribute: (k, v) => { attrs[k] = v; },
    classList: {
      toggle: (name, on) => { if (on) cls.add(name); else cls.delete(name); },
      contains: (name) => cls.has(name),
    },
  };
});
sortMs.sort.forEach((paint, i) => {
  mockBtns[i].classList.toggle("is-on", paint.selected);
  mockBtns[i].setAttribute("aria-checked", paint.ariaChecked);
});
assert(mockBtns.filter((b) => b.classList.contains("is-on")).length === 1, "dom one filled sort cell");
assert(mockBtns[1].classList.contains("is-on") && mockBtns[1].getAttribute("aria-checked") === "true", "dom manuscript filled");
assert(mockBtns[0].getAttribute("aria-checked") === "false" && !mockBtns[0].classList.contains("is-on"), "dom priority empty");

const chipPaint = FP.controlDisplayState({
  kindFilter: ["구조"],
  kindChips: [{ key: "구조", label: "구조", count: 2 }, { key: "해설 줄이기", label: "해설 줄이기", count: 2 }],
  shownCount: 2,
});
assert(chipPaint.chips[0].selected && !chipPaint.chips[1].selected, "chip selected from state");
assert(chipPaint.filterLabel === "필터 1" && chipPaint.filterOn, "filter button n");
assert(chipPaint.filterReset.enabled && chipPaint.filterReset.label === "초기화 ✕", "reset enabled");
assert(chipPaint.filterReset.hint === "1개 유형 선택됨", "reset hint on");
assert(chipPaint.banner === "구조만 보는 중 · 2개 표시", "banner single");
assert(chipPaint.summaryNote === "(필터로 2개 표시 중)", "summary note");
const chipOff = FP.controlDisplayState({ kindFilter: [], kindChips: chipPaint.chips, shownCount: 5 });
assert(!chipOff.filterReset.enabled && chipOff.filterReset.label === "초기화" && chipOff.filterReset.hint === "필터 없음", "reset idle");
assert(chipOff.banner === "" && chipOff.summaryNote === "", "no banner when off");
assert(FP.formatFilterBanner(["구조", "설정·인물 일관성"], 4) === "구조, 설정·인물 일관성만 보는 중 · 4개 표시", "banner multi");
assert(FP.controlDisplayState({ kindFilter: ["구조"], shownCount: 0, hasMore: false }).emptyFilter, "empty filter list");
assert(!FP.controlDisplayState({ kindFilter: ["구조"], shownCount: 0, hasMore: true }).emptyFilter, "empty waits for more");

const ulSeg = FP.controlDisplayState({ underlineLevel: "high" });
assert(ulSeg.underline.filter((s) => s.selected).map((s) => s.value).join(",") === "high", "level segment");
assert(ulSeg.underline.filter((s) => s.selected).length === 1, "one level selected");
const lensPaint = FP.controlDisplayState({ lens: "strong" });
assert(lensPaint.lensCards.filter((s) => s.selected).map((s) => s.value).join(",") === "strong", "lens card");
assert(FP.segmentPaintState(["a", "b"], "b").filter((s) => s.selected).length === 1, "segment helper one on");

const ulCards = [
  { id: 1, kind: "structure", priority: "high" },
  { id: 2, kind: "style", style_type: "explain_less", priority: "medium" },
  { id: 3, kind: "structure", priority: "low" },
];
assert(FP.underlineTargetCards(ulCards, { reveal: "low", sortMode: "priority", kindFilter: ["구조"], underlineLevel: "high-medium" }).map((c) => c.id).join(",") === "1", "underline skips filtered and low");
assert(FP.visibleWalkOrder(ulCards, "low", "priority", ["구조"]).join(",") === "1,3", "walk skips other kinds");
assert(FP.visibleWalkOrder(ulCards, "low", "priority", []).join(",") === "1,2,3", "walk all when no filter");
assert(FP.visibleCards(ulCards, "low", "priority", ["해설 줄이기"]).map((c) => c.id).join(",") === "2", "visible filtered list");

const runDate = new Date(2026, 8, 21, 22, 0);
assert(FP.formatRunWhenShort(runDate) === "9/21 22:00", "run when short");
assert(
  FP.formatRunOption({
    created_at: runDate,
    card_counts: { open: 7 },
    params: { explanation_lens: "strong", fake: false },
    status: "ok",
  }) === "9/21 22:00 · 카드 7",
  "run option ok"
);
assert(
  FP.formatRunOption({
    created_at: runDate,
    card_counts: { open: 3 },
    params: { explanation_lens: "normal" },
    status: "running",
  }) === "9/21 22:00 · 카드 3 · 분석 중",
  "run option running"
);
assert(
  FP.formatRunOption({
    created_at: runDate,
    planned_cards: 7,
    params: { explanation_lens: "strong" },
    status: "partial",
  }) === "9/21 22:00 · 카드 7 · 일부 실패",
  "run option partial"
);
assert(
  FP.runSelectTags({ params: { explanation_lens: "strong", fake: true } }).map((t) => t.label).join(",") === "시험,자세히",
  "run tags fake lens"
);
assert(
  FP.runSelectTags({ params: { explanation_lens: "normal", fake: false } }).map((t) => t.label).join(",") === "보통",
  "run tags real lens"
);
assert(
  FP.formatRunOption({
    created_at: runDate,
    card_counts: { open: 2 },
    is_primary: 1,
    status: "ok",
  }).indexOf("기준") >= 0,
  "dropdown shows 기준"
);

const histRows = [
  { id: 3, created_at: "2026-09-22T10:00:00.000Z", is_primary: 0, status: "ok", run_kind: "analyze", card_counts: { open: 2, applied: 1, ignored: 0 }, params: { explanation_lens: "strong", fake: false } },
  { id: 1, created_at: "2026-09-20T10:00:00.000Z", is_primary: 1, status: "ok", run_kind: "analyze", card_counts: { open: 0, applied: 3, ignored: 1 }, params: { explanation_lens: "normal", fake: true } },
  { id: 2, created_at: "2026-09-21T10:00:00.000Z", is_primary: 0, status: "partial", run_kind: "legacy", card_counts: { open: 4 } },
];
assert(FP.sortHistoryRuns(histRows).map((r) => r.id).join(",") === "3,2,1", "history newest first");
assert(FP.pickDefaultRun(histRows).id === 1, "primary wins over newest");
assert(FP.pickDefaultRun(histRows.filter((r) => r.id !== 1)).id === 3, "newest when no primary");
assert(FP.formatCardCountSummary(histRows[0].card_counts) === "미결정 2 · 적용 1 · 무시 0", "count summary");
assert(FP.historyRunStatusLabel("ok") === "완료", "status ok");
assert(FP.historyRunStatusLabel("running") === "진행 중", "status running");
assert(FP.historyRunStatusLabel("partial") === "일부 실패", "status partial");
assert(FP.historyRunStatusLabel("failed") === "실패", "status failed");
const histPrimary = FP.formatHistoryRow(histRows[1]);
assert(histPrimary.primary && !histPrimary.showPrimaryBtn, "primary badge hides set-primary");
assert(histPrimary.tags.some((t) => t.label === "시험"), "fake tag");
assert(FP.formatHistoryRow(histRows[0]).tags.some((t) => t.label === "실제 분석"), "real tag");
assert(FP.formatHistoryRow(histRows[2]).tags.some((t) => t.label === "예전 기록"), "legacy tag");
const runningRow = FP.formatHistoryRow({ id: 9, status: "running", created_at: "2026-09-22T00:00:00.000Z" });
assert(!runningRow.canDelete && runningRow.deleteTitle.indexOf("실행 중") >= 0, "running cannot delete");

const localHist = [
  { id: "arh-1", mode: "analyze", modeLabel: "일반 피드백", createdAt: "2026-09-22T12:00:00.000Z", text: "a" },
  { id: "arh-2", mode: "brainstorm", modeLabel: "브레인스토밍", createdAt: "2026-09-22T08:00:00.000Z", text: "b" },
  { id: "fb-run-99", mode: "markupfeedback", kind: "markupfeedback", createdAt: "2026-09-22T20:00:00.000Z", text: "x" },
];
const markupRuns = [
  { id: 10, scene_id: 3, scene_title: "3화", created_at: "2026-09-22T11:00:00.000Z", status: "ok", card_counts: { open: 2 }, params: {} },
  { id: 11, scene_id: 4, scene_title: "4화", created_at: "2026-09-22T13:00:00.000Z", status: "running", card_counts: {}, params: { fake: true } },
  { id: 12, scene_id: 5, scene_title: "5화", created_at: "2026-09-22T07:00:00.000Z", status: "failed", planned_cards: 4, params: {} },
];
const mergedHist = FP.mergeToryHistoryItems(localHist, markupRuns, 40);
assert(mergedHist.map((item) => item.id).join(",") === "fb-run-11,arh-1,fb-run-10,arh-2,fb-run-12", "tory history mix newest first");
assert(!mergedHist.some((item) => item.id === "fb-run-99"), "drop local markup stubs");
assert(FP.isMarkupHistoryItem(mergedHist[0]) && mergedHist[0].modeLabel === "첨삭 피드백 · 시험 기록", "fake labeled");
assert(
  FP.formatMarkupHistoryLine(mergedHist[0], "오후 10:00") === "첨삭 피드백 · 시험 기록 · 4화 · 오후 10:00 · 분석 중",
  "running fake line"
);
assert(
  FP.formatMarkupHistoryLine(mergedHist[2], "오후 8:00") === "첨삭 피드백 · 3화 · 오후 8:00 · 카드 2개",
  "ok card count line"
);
assert(
  FP.formatMarkupHistoryLine(mergedHist[4], "오후 4:00") === "첨삭 피드백 · 5화 · 오후 4:00 · 실패",
  "failed line"
);
assert(FP.formatMarkupHistoryPreview(mergedHist[2]) === "3화 · 카드 2개", "preview scene and cards");
assert(FP.openHistoryRun(0) === false, "openHistoryRun rejects 0");
assert(FP.openHistoryRun(12) === true, "openHistoryRun accepts id");
const overCap = FP.mergeToryHistoryItems(
  Array.from({ length: 30 }, (_, i) => ({
    id: "arh-" + i,
    mode: "analyze",
    createdAt: "2026-09-21T00:00:00.000Z",
  })),
  Array.from({ length: 20 }, (_, i) => ({
    id: 100 + i,
    scene_id: 1,
    scene_title: "1화",
    created_at: "2026-09-22T00:00:00.000Z",
    status: "ok",
    card_counts: { open: 1 },
    params: {},
  })),
  40
);
assert(overCap.length === 40, "history cap 40");
assert(overCap.every((item) => item.kind === "markupfeedback") === false, "cap mixes kinds");
assert(overCap.filter((item) => item.kind === "markupfeedback").length === 20, "newer markup kept under cap");
assert(FP.nextRunAfterDelete(histRows, 1, 1).id === 3, "deleted current primary -> newest");
assert(FP.nextRunAfterDelete(histRows, 3, 1).id === 1, "keep current if not deleted");
assert(FP.nextRunAfterDelete(histRows, 1, 3).id === 3, "keep current when primary deleted");
assert(FP.nextRunAfterDelete([{ id: 1 }], 1, 1) == null, "empty after last delete");
assert(
  FP.deleteRunConfirmMessage(7) === "이 분석 기록을 삭제할까요? 카드 7개가 함께 지워지고 되돌릴 수 없어요",
  "delete confirm"
);
assert(FP.formatLegacyImportMessage(5, 2) === "5개 가져왔어요(2개는 이미 있어서 건너뜀)", "import mix");
assert(FP.formatLegacyImportMessage(5, 0) === "5개 가져왔어요", "import only");
assert(FP.formatLegacyImportMessage(0, 3).indexOf("3개") >= 0, "import skipped only");
assert(
  FP.legacyHistoryEntries([
    { id: "a", mode: "analyze", text: "x" },
    { id: "b", mode: "rewrite", text: "y" },
    { id: "c", mode: "analyze_multi", text: "z" },
    { mode: "analyze", text: "no-id" },
  ]).map((i) => i.id).join(",") === "a,c",
  "legacy filter"
);
const importEmpty = FP.legacyImportButtonState([
  { id: "b", mode: "rewrite", text: "y" },
]);
assert(importEmpty.disabled && importEmpty.label.indexOf("없어요") >= 0, "import disabled when no analyze");
const importHas = FP.legacyImportButtonState([
  { id: "a", mode: "analyze", text: "x" },
]);
assert(!importHas.disabled && importHas.label.indexOf("가져오기") >= 0, "import enabled when analyze exists");
assert(FP.formatReportCollectText({ report: { summary: "요약문", scores: [{ item: " ent", score: 3, comment: "ok" }] } }).indexOf("요약문") >= 0, "report collect");
assert(FP.formatCardCollectText({ title: "제목", reason: "이유", suggestion: "고침" }).indexOf("고침") >= 0, "card collect");

function dummyParas(from, to) {
  const rows = [];
  for (let n = from; n <= to; n += 1) {
    rows.push({ i: n, text: "문단" + n + ".", type: "text", chars: [] });
  }
  return rows;
}
function htmlParas(texts) {
  const html = texts.map(function (t) {
    return "<p>" + t + "</p>";
  }).join("");
  return FP.mapEditorParagraphs(FP.htmlToDomLike(html));
}
function sliceKey(mapped) {
  return (mapped.slices || []).map(function (s) {
    const body = String(s.para.text || "").slice(s.localStart, s.localEnd);
    return s.i + ":" + s.localStart + "-" + s.localEnd + ":" + body;
  }).join("|");
}

const abParas = [
  { i: 1, text: "가나다.", type: "text" },
  { i: 2, text: "라마바 사아자.", type: "text" },
];
const abOrig = "가나다.\n라마바 사아자.";
const abSug = "라마바 사아자, 차카.";
const abMapped = FP.mapDelSpansToParaSlices(abOrig, abSug, abParas);
assert(!abMapped.skipped && abMapped.slices.length, "A/B del slices exist");
const abKeys = sliceKey(abMapped);
assert(abKeys.indexOf("1:0-4:가나다.") >= 0, "A/B del covers paragraph A: " + abKeys);
assert(abKeys.indexOf("라") < 0, "A/B del does not include 라: " + abKeys);
assert(abMapped.slices.every(function (s) {
  if (Number(s.i) !== 2) return true;
  return s.localStart > 0;
}), "A/B del does not start at 라");
assert(abMapped.slices.some(function (s) {
  return Number(s.i) === 2 && String(s.para.text).slice(s.localEnd - 1, s.localEnd) === ".";
}), "A/B del includes last period of B");

const endParas = [
  { i: 1, text: "가나다.", type: "text" },
  { i: 2, text: "라마바", type: "text" },
];
const endMapped = FP.mapDelSpansToParaSlices("가나다.\n라마바", "라마바", endParas);
assert(endMapped.slices.length === 1 && endMapped.slices[0].i === 1, "del ending at para end stays in A");
assert(sliceKey(endMapped).indexOf("라") < 0, "del at para end does not leak into B");

const startParas = [
  { i: 1, text: "가나다.", type: "text" },
  { i: 2, text: "라마바", type: "text" },
];
const startMapped = FP.mapDelSpansToParaSlices("가나다.\n라마바", "가나다.\n마바", startParas);
assert(startMapped.slices.some(function (s) {
  return Number(s.i) === 2 && s.localStart === 0 && String(s.para.text).slice(s.localStart, s.localEnd).indexOf("라") >= 0;
}), "del starting at para start of B: " + sliceKey(startMapped));

const tripleParas = [
  { i: 1, text: "첫째문단.", type: "text" },
  { i: 2, text: "둘째문단.", type: "text" },
  { i: 3, text: "셋째문단.", type: "text" },
];
const tripleMapped = FP.mapDelSpansToParaSlices(
  "첫째문단.\n둘째문단.\n셋째문단.",
  "첫째문단.\n셋째문단.",
  tripleParas
);
const tripleIds = tripleMapped.slices.map(function (s) { return s.i; }).join(",");
assert(tripleIds.indexOf("2") >= 0, "three-para del includes middle: " + sliceKey(tripleMapped));
assert(tripleMapped.slices.every(function (s) { return s.i !== 0; }), "three-para slices have para ids");

const wsMapped = FP.mapDelSpansToParaSlices("가 나 다", "가 다", [{ i: 1, text: "가 나 다", type: "text" }]);
assert(sliceKey(wsMapped).indexOf("나") >= 0, "whitespace-adjacent del maps: " + sliceKey(wsMapped));

const spanHtml = "<p>가나<span>다.</span></p><p>라마바 사아자.</p>";
const spanMap = FP.mapEditorParagraphs(FP.htmlToDomLike(spanHtml));
const spanSlices = FP.mapDelSpansToParaSlices("가나다.\n라마바 사아자.", "라마바 사아자, 차카.", spanMap);
assert(spanSlices.slices.some(function (s) { return s.i === 1 && s.localStart === 0; }), "span-split para A still maps");
assert(spanSlices.slices.every(function (s) {
  if (Number(s.i) !== 2) return true;
  return s.localStart > 0;
}), "span-split does not paint 라");

const nlLayout = FP.joinedParaLayout(abParas);
const nlOnly = FP.sliceJoinedSpanToParas(nlLayout, 4, 5);
assert(nlOnly.length === 0, "newline-only span maps to no DOM chars");
assert(nlLayout.rows[0].textEnd === 4 && nlLayout.rows[1].textStart === 5, "layout keeps virtual sep");

const card05 = JSON.parse(fs.readFileSync(path.join(__dirname, "fixtures", "feedback_del_card05.json"), "utf8"));
assert(card05.paras[0].length === 37, "card05 para50 length 37");
assert(card05.paras[1] === "뿐.", "card05 para51 is 뿐.");
assert((card05.paras[0] + "\n" + card05.paras[1]).length === 40, "card05 joined 40");
assert(card05.suggestion.length === 25, "card05 suggestion 25");
assert(card05.paras[2].charAt(0) === "백", "card05 next para starts with 백");
const card05Map = htmlParas(card05.paras);
const card05Slices = FP.mapDelSpansToParaSlices(card05.original_text, card05.suggestion, card05Map.slice(0, 2));
assert(card05Slices.slices.every(function (s) { return s.i === 1 || s.i === 2; }), "card05 del stays in 50-51");
assert(card05Slices.slices.every(function (s) {
  return String(s.para.text || "").slice(s.localStart, s.localEnd).indexOf("백") < 0;
}), "card05 del does not include 백");
const card05Found = FP.locateCard({
  start_para: 1,
  end_para: 2,
  original_text: card05.original_text,
  suggestion: card05.suggestion,
}, card05Map);
assert(card05Found.ok, "card05 locates");
const card05Dels = FP.delRangesForCard({
  start_para: 1,
  end_para: 2,
  original_text: card05.original_text,
  suggestion: card05.suggestion,
}, card05Found, card05Map);
assert(!card05Dels.skipped && card05Dels.ranges.length, "card05 del ranges");
const baekNode = (card05Map[2].chars && card05Map[2].chars[0] && card05Map[2].chars[0].node);
assert(baekNode, "card05 백 node exists");
assert(card05Dels.ranges.every(function (range) {
  return range.startContainer !== baekNode && range.endContainer !== baekNode;
}), "card05 ranges do not use 백 node");
const card05Html = FP.renderSegHtml(FP.diffSegments(card05.original_text, card05.suggestion));
assert(card05Html.indexOf("줄바꿈 삭제") >= 0, "card05 card diff marks newline delete");
assert(card05.original_text.length === 40 && card05.suggestion.length === 25, "card05 orig 40 / sug 25 like screenshot");

function densLabel(rows) {
  return rows.filter(function (r) { return r.text; }).map(function (r) {
    return r.i + ":" + r.density;
  }).join(",");
}
const map3 = htmlParas(["하나 문단.", "가운데 문단.", "끝 문단."]);
const map5 = htmlParas(["문단1.", "문단2.", "문단3.", "문단4.", "문단5."]);
const map6 = htmlParas(["문단1.", "문단2.", "문단3.", "문단4.", "문단5.", "문단6."]);
const map18 = [];
for (let n = 1; n <= 18; n += 1) map18.push("문단" + n + " 본문.");
const map18d = htmlParas(map18);
assert(densLabel(FP.paraBandDensities(1, 3, map3)) === "1:1,2:1,3:1", "3 paras all strong");
assert(densLabel(FP.paraBandDensities(1, 5, map5)) === "1:1,2:1,3:1,4:1,5:1", "5 paras all strong");
const dens6 = densLabel(FP.paraBandDensities(1, 6, map6));
assert(dens6 === "1:1,2:0.55,3:0.55,4:0.55,5:0.55,6:1", "6 paras first last strong mid 0.55: " + dens6);
const dens18 = FP.paraBandDensities(1, 18, map18d);
assert(dens18[0].density === 1 && dens18[17].density === 1, "18 first last strong");
assert(dens18.slice(1, 17).every(function (r) { return r.density === 0.55; }), "18 mids 0.55");
const mapDiv = htmlParas(["앞 문단.", "***", "뒤 문단."]);
if (mapDiv[1]) mapDiv[1].type = "divider";
const densDiv = FP.paraBandDensities(1, 3, mapDiv);
assert(densDiv[1].density === 0 && densDiv[0].density === 1 && densDiv[2].density === 1, "divider skipped");

const cov3 = FP.activeCoveragePlan({ ok: true, startPara: 1, endPara: 3 }, map3);
assert(cov3.strongParas.join(",") === "1,2,3" && cov3.strong.length === 3 && !cov3.mid.length, "3 para coverage ranges");
const cov6 = FP.activeCoveragePlan({ ok: true, startPara: 1, endPara: 6 }, map6);
assert(cov6.strongParas.join(",") === "1,6" && cov6.midParas.join(",") === "2,3,4,5", "6 para coverage ids");
assert(cov6.strong.length === 2 && cov6.mid.length === 4, "6 para coverage range counts");
const cov18 = FP.activeCoveragePlan({ ok: true, startPara: 1, endPara: 18 }, map18d);
assert(cov18.strong.length === 2 && cov18.mid.length === 16, "18 para coverage all text paras");

assert(FP.underlineParaList(1, 3, map3).join(",") === "1,2,3", "<=5 underline all text paras");
assert(FP.underlineParaList(1, 5, map5).join(",") === "1,2,3,4,5", "5 underline all");
assert(FP.underlineParaList(1, 6, map6).join(",") === "1,6", ">5 underline first last");
assert(FP.underlineParaList(1, 18, map18d).join(",") === "1,18", "18 underline first last");

const map113 = dummyParas(113, 132);
const ul02 = FP.underlineJoinSpans({ startPara: 113, endPara: 118 }, map113);
const ul03 = FP.underlineJoinSpans({ startPara: 115, endPara: 118 }, map113);
const ul04 = FP.underlineJoinSpans({ startPara: 115, endPara: 132 }, map113);
assert(ul02.map(function (s) { return s.para; }).join(",") === "113,118", "card02 underline paras");
assert(ul03.map(function (s) { return s.para; }).join(",") === "115,116,117,118", "card03 underline paras");
assert(ul04.map(function (s) { return s.para; }).join(",") === "115,132", "card04 underline paras");
function asUl(spans, tier, id, index) {
  return spans.map(function (s) {
    return { start: s.start, end: s.end, tier: tier, cardId: id, index: index, order: s.start };
  });
}
const merged113 = FP.mergeUnderlineSpans([].concat(
  asUl(ul02, "high", "02", 2),
  asUl(ul03, "high", "03", 3),
  asUl(ul04, "medium", "04", 4)
));
function paraStyleAt(mapping, paraI, merged) {
  const g = mapping.filter(function (p) { return Number(p.i) === paraI; })[0];
  if (!g) return "";
  const start = FP.underlineJoinSpans({ startPara: paraI, endPara: paraI }, mapping)[0];
  if (!start) return "";
  for (let i = 0; i < merged.length; i += 1) {
    if (merged[i].start <= start.start && merged[i].end >= start.end) {
      return merged[i].tier + ":" + merged[i].indexes.join("+");
    }
  }
  return "";
}
assert(paraStyleAt(map113, 113, merged113).indexOf("high") === 0, "113 high from 02");
assert(paraStyleAt(map113, 114, merged113) === "", "114 no underline");
assert(paraStyleAt(map113, 115, merged113).indexOf("high") === 0, "115 high from 03 over 04");
assert(paraStyleAt(map113, 118, merged113).indexOf("high") === 0, "118 high");
assert(paraStyleAt(map113, 119, merged113) === "", "119 no underline (not first 5 of 04)");
assert(paraStyleAt(map113, 132, merged113) === "medium:4", "132 medium from 04");

const nlMerge = FP.mapDelSpansToParaSlices("가나다.\n라마바", "가나다. 라마바", endParas);
assert(nlMerge.newlineOnly, "separator-only del is newlineOnly");
assert(!nlMerge.slices.length, "separator-only del paints nothing on manuscript");

function textsToMap(texts) {
  return texts.map(function (t, i) {
    return { i: i + 1, text: t, type: "text", chars: [] };
  });
}
function foundSpan(mapping, startPara, endPara, localStart, localEnd) {
  const layout = FP.joinedParaLayout(mapping);
  const rows = layout.rows.filter(function (r) {
    return r.i >= startPara && r.i <= endPara;
  });
  const first = rows[0];
  const last = rows[rows.length - 1];
  const joinStart = first.textStart + (localStart || 0);
  const joinEnd = localEnd != null ? last.textStart + localEnd : last.textEnd;
  return {
    ok: true,
    startPara: startPara,
    endPara: endPara,
    joinStart: joinStart,
    joinEnd: joinEnd,
  };
}

const mapAB = textsToMap(["첫번째 문단", "두번째 문단"]);
const cardAB = {
  original_text: "첫번째 문단\n두번째 문단",
  suggestion: "하나\n둘",
  start_para: 1,
  end_para: 2,
};
const planEach = FP.planCardApply(cardAB, mapAB, foundSpan(mapAB, 1, 2));
assert(planEach.ok && planEach.mode === "each" && !planEach.needsConfirm, "M=N each mode");
assert(planEach.steps.length === 2 && planEach.steps[0].kind === "replace", "each replace both");

const planSkip = FP.planCardApply(
  { original_text: "첫번째 문단\n두번째 문단", suggestion: "첫번째 문단\n바꿈" },
  mapAB,
  foundSpan(mapAB, 1, 2)
);
assert(planSkip.steps[0].kind === "skip" && planSkip.steps[1].kind === "replace", "same line skipped");

const planMismatch = FP.planCardApply(
  { original_text: "다른 원문", suggestion: "하나" },
  mapAB,
  foundSpan(mapAB, 1, 1, 0, mapAB[0].text.length)
);
assert(!planMismatch.ok && planMismatch.reason === "mismatch", "normalize mismatch blocks apply");
assert(planMismatch.copyOnly, "mismatch offers copy");

const planLocate = FP.planCardApply(cardAB, mapAB, { ok: false });
assert(!planLocate.ok && planLocate.reason === "locate", "locate fail blocks apply");

const planAll = FP.planCardApply(
  { original_text: "첫번째 문단\n두번째 문단", suggestion: "한 줄로" },
  mapAB,
  foundSpan(mapAB, 1, 2)
);
assert(planAll.ok && planAll.mode === "all" && planAll.needsConfirm, "M≠N needs confirm");
assert(planAll.steps[0].kind === "replace-all", "all replace-all");

const map3l = textsToMap(["가나다", "라마바", "사아자"]);
const planDel = FP.planCardApply(
  { original_text: "가나다\n라마바\n사아자", suggestion: "가나다\n\n사아자" },
  map3l,
  foundSpan(map3l, 1, 3)
);
assert(planDel.ok && planDel.mode === "each", "empty line still M=N");
assert(planDel.steps[1].kind === "delete" && planDel.steps[1].text === "", "empty suggestion line deletes");
assert(FP.expectedLiveAfterPlan(planDel) === "가나다\n사아자", "delete drops empty line from expected live");
const afterDelMap = textsToMap(["가나다", "사아자"]);
assert(FP.applyVerifyOk(afterDelMap, planDel), "verify ok when mapper skips empty para");

const mapOne = textsToMap(["한 문단 원문"]);
const planOneMany = FP.planCardApply(
  { original_text: "한 문단 원문", suggestion: "첫째 줄\n둘째 줄" },
  mapOne,
  foundSpan(mapOne, 1, 1)
);
assert(planOneMany.mode === "all" && planOneMany.needsConfirm, "N=1 multi-line uses all");

const mapPartial = textsToMap(["AAAA BBBB CCCC"]);
const planPart = FP.planCardApply(
  { original_text: "BBBB", suggestion: "XXXX" },
  mapPartial,
  foundSpan(mapPartial, 1, 1, 5, 9)
);
assert(planPart.ok && planPart.live === "BBBB", "partial slice live text");
assert(planPart.steps[0].kind === "replace" && planPart.steps[0].text === "XXXX", "partial replace");

const wsCard = {
  original_text: "첫번째 문단\r\n두번째 문단",
  suggestion: "하나\n둘",
};
const planWs = FP.planCardApply(wsCard, mapAB, foundSpan(mapAB, 1, 2));
assert(planWs.ok, "CRLF original still matches after normalize");

const walkCards = [
  { id: 1, status: "open", priority: "high", kind: "style", style_type: "redundancy", start_para: 1, ord: 0 },
  { id: 2, status: "applied", priority: "high", kind: "style", style_type: "redundancy", start_para: 2, ord: 1 },
  { id: 3, status: "open", priority: "medium", kind: "structure", start_para: 3, ord: 2 },
  { id: 4, status: "ignored", priority: "low", kind: "style", style_type: "explain_less", start_para: 4, ord: 3 },
  { id: 5, status: "open", priority: "low", kind: "style", style_type: "explain_less", start_para: 5, ord: 4 },
  { id: 6, status: "applied_edited", priority: "high", kind: "consistency", start_para: 6, ord: 5 },
];
const walkAll = FP.openWalkIds(walkCards, "low", "manuscript");
assert(walkAll.join(",") === "1,3,5", "walk open only manuscript order");
const walkPri = FP.openWalkIds(walkCards, "low", "priority");
assert(walkPri[0] === 1, "priority walk starts with high open");
assert(walkPri.indexOf(2) < 0 && walkPri.indexOf(6) < 0, "settled excluded");
const walkFilt = FP.openWalkIds(walkCards, "low", "manuscript", ["구조"]);
assert(walkFilt.join(",") === "3", "kind filter + open only");
const st = FP.openWalkState(walkAll, 1);
assert(st.label === "1 / 3" && st.nextId === 3 && st.prevId == null && st.remaining === 3, "walk state first");
const stMid = FP.openWalkState(walkAll, 3);
assert(stMid.index === 2 && stMid.prevId === 1 && stMid.nextId === 5, "walk state mid");

const mem = { expectedText: "XXXX", originalText: "BBBB", status: "applied", appliedStatus: "applied" };
assert(FP.historySyncDecision(mem, { expected: "BBBB", original: "BBBB" }, "historyUndo").action === "reopen", "undo reopens");
assert(FP.historySyncDecision(mem, { expected: "XXXX", original: "XXXX" }, "historyUndo").action === "none", "undo ignored if expected remains");
const memOpen = { expectedText: "XXXX", originalText: "BBBB", status: "open", appliedStatus: "applied" };
assert(FP.historySyncDecision(memOpen, { expected: "XXXX", original: "XXXX" }, "historyRedo").action === "reapply", "redo reapplies");
assert(FP.historySyncDecision(mem, { expected: "XXXX", original: "XXXX" }, "historyRedo").action === "none", "redo skipped if already applied");
assert(FP.historySyncDecision(mem, { expected: "????", original: "????" }, "historyUndo").action === "none", "unrelated edit no sync");

const placeBelow = FP.inlineBoxPlacement({
  view: { scrollTop: 0, viewHeight: 400, viewWidth: 600 },
  lastLine: { top: 40, bottom: 60, left: 20, width: 500 },
  barVisible: true,
  barHeight: 44,
  desiredHeight: 120,
});
assert(placeBelow.mode === "below" && placeBelow.top === 66, "box below last line");
const placeBar = FP.inlineBoxPlacement({
  view: { scrollTop: 0, viewHeight: 200, viewWidth: 600 },
  lastLine: { top: 150, bottom: 170, left: 20, width: 500 },
  barVisible: true,
  barHeight: 44,
  desiredHeight: 120,
});
assert(placeBar.mode === "bar-top", "box above action bar when no space");
const placeScroll = FP.inlineBoxPlacement({
  view: { scrollTop: 200, viewHeight: 300, viewWidth: 500 },
  lastLine: { top: 480, bottom: 500, left: 16, width: 400 },
  barVisible: true,
  barHeight: 40,
  desiredHeight: 100,
});
assert(placeScroll.mode === "below" || placeScroll.top >= 200, "scrolled editor uses content coords");
const placeOff = FP.inlineBoxPlacement({
  view: { scrollTop: 0, viewHeight: 240, viewWidth: 500 },
  lastLine: { top: 260, bottom: 280, left: 16, width: 400 },
  barVisible: true,
  barHeight: 44,
  desiredHeight: 100,
});
assert(placeOff.mode === "bar-top", "last line below viewport pins to bar-top");
const barPlace = FP.actionBarPlacement({
  scrollTop: 80,
  viewHeight: 400,
  viewWidth: 500,
  barHeight: 44,
  barWidth: 420,
});
assert(barPlace.top === 80 + 400 - 12 - 44, "action bar 12px above editor bottom");
assert(barPlace.pad === 56, "pad = bar + gap");

assert(FP.locateTextForCard({ status: "open", original_text: "원문", suggestion: "결과" }) === "원문", "open locate uses original");
assert(FP.locateTextForCard({ status: "applied", original_text: "원문", suggestion: "결과" }) === "결과", "applied locate uses suggestion");
assert(FP.locateTextForCard({ status: "applied_edited", original_text: "원문", suggestion: "결과", final_text: "최종" }) === "최종", "applied_edited locate uses final_text");
assert(FP.locateTextForCard({ status: "applied", original_text: "원문", suggestion: "  " }) === "원문", "applied empty suggestion falls back");
assert(FP.isAppliedStatus("applied_edited") && !FP.isAppliedStatus("open"), "applied status helper");

const appliedMap = FP.mapEditorParagraphs(FP.htmlToDomLike(
  "<p>첫 문장입니다.</p><p>고친 문장입니다.</p><p>마지막입니다.</p>"
));
const appliedHit = FP.locateCard({
  status: "applied",
  start_para: 2,
  end_para: 2,
  original_text: "찾아야 할 문장입니다.",
  suggestion: "고친 문장입니다.",
}, appliedMap);
assert(appliedHit.ok && appliedHit.startPara === 2, "applied locate finds suggestion not original");
const appliedEditedHit = FP.locateCard({
  status: "applied_edited",
  start_para: 2,
  end_para: 2,
  original_text: "찾아야 할 문장입니다.",
  suggestion: "다른 초안",
  final_text: "고친 문장입니다.",
}, appliedMap);
assert(appliedEditedHit.ok && appliedEditedHit.startPara === 2, "applied_edited locate finds final_text");
const openMissesResult = FP.locateCard({
  status: "open",
  start_para: 2,
  end_para: 2,
  original_text: "찾아야 할 문장입니다.",
  suggestion: "고친 문장입니다.",
}, appliedMap);
assert(!openMissesResult.ok, "open locate still uses original_text");
const appliedGone = FP.locateCard({
  status: "applied",
  start_para: 2,
  end_para: 2,
  original_text: "원문",
  suggestion: "없는 결과 텍스트",
}, appliedMap);
assert(!appliedGone.ok, "applied miss when result edited away");
assert(
  FP.locateFailMessage({ status: "applied" }, appliedGone) === "적용된 위치를 찾을 수 없어요. 원고가 그 뒤에 바뀐 것 같아요",
  "applied miss copy"
);
assert(
  FP.locateFailMessage({ status: "applied_edited" }, { ok: false, reason: "인용문을 찾지 못했어요" }) === "적용된 위치를 찾을 수 없어요. 원고가 그 뒤에 바뀐 것 같아요",
  "applied_edited miss copy overrides generic"
);

assert(FP.commentButtonLabel(0) === "질문하기", "comment empty label");
assert(FP.commentButtonLabel(3) === "질문 3", "comment count label");
assert(FP.commentChipLabel(2) === "2", "collapsed chip count");
assert(!FP.commentLimitReached(19) && FP.commentLimitReached(20), "20 turn limit");
const talkHtml = FP.renderTalkThread([
  { role: "user", body: "왜 구조인가요?" },
  { role: "assistant", body: "장면이 멈춰 보여요." },
]);
assert(talkHtml.indexOf("is-user") >= 0 && talkHtml.indexOf("왜 구조인가요?") >= 0, "user bubble");
assert(talkHtml.indexOf("is-ai") >= 0 && talkHtml.indexOf("장면이 멈춰 보여요.") >= 0, "ai bubble");
const openTalk = FP.renderCommentThread({ id: 1, comment_count: 2 }, {
  open: true,
  sending: false,
  error: true,
  draft: "다시 물어볼게요",
  comments: [{ role: "user", body: "왜요?" }],
  limited: false,
});
assert(openTalk.indexOf("질문 2") >= 0, "open uses count");
assert(openTalk.indexOf("이 지적에 대해 궁금한 점이나 다른 의견을 물어보세요") >= 0, "placeholder copy");
assert(openTalk.indexOf("답을 받지 못했어요") >= 0 && openTalk.indexOf("다시 시도") >= 0, "retry copy");
assert(openTalk.indexOf("다시 물어볼게요") >= 0, "failed draft kept");
const limitedTalk = FP.renderCommentThread({ id: 2, comment_count: 20 }, {
  open: true,
  comments: [],
  limited: true,
});
assert(limitedTalk.indexOf("대화를 너무 많이") >= 0, "limit copy");
assert(limitedTalk.indexOf("fb-comment-input") < 0, "limit hides composer");

if (failed) {
  console.error("test_feedback_panel: failed", failed);
  process.exit(1);
}
console.log("test_feedback_panel: ok");
