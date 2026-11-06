// node tests/test_status_window_model.js — web/status_window_model.js 순수 함수 검사
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const code = fs.readFileSync(path.join(__dirname, "..", "web", "status_window_model.js"), "utf8");
const sandbox = {};
sandbox.globalThis = sandbox;
vm.runInNewContext(code, sandbox);
const M = sandbox.StatusWindowModel;
if (!M) {
  console.error("StatusWindowModel missing");
  process.exit(1);
}

let failed = 0;
function eq(actual, expected, msg) {
  const a = JSON.stringify(actual);
  const e = JSON.stringify(expected);
  if (a !== e) {
    console.error("FAIL", msg, "\n  got     ", a, "\n  expected", e);
    failed += 1;
  } else {
    console.log("ok", msg);
  }
}
const texts = (lines) => lines.map((l) => l.text);

const basic = {
  id: "basic",
  name: "기본",
  title: "상태창",
  frame: "divider",
  label_style: "colon",
  items: [
    { name: "이름", default: "" },
    { name: "레벨", default: "1", type: "number" },
    { name: "체력", default: "100/100", type: "ratio", group: "상태" },
    { name: "마나", default: "50/50", type: "ratio", group: "상태" },
    { name: "힘", default: "10", type: "number", group: "능력치" },
  ],
};

// 1) 구분선형: 위아래 선 + 제목 + 묶음 머리줄 + 기본값
{
  const lines = M.renderLines(basic);
  eq(texts(lines), [
    "━━━━━━━━━━━━━━━━━━",
    "[상태창]",
    "이름:",
    "레벨: 1",
    "〈상태〉",
    "체력: 100/100",
    "마나: 50/50",
    "〈능력치〉",
    "힘: 10",
    "━━━━━━━━━━━━━━━━━━",
  ], "구분선형 기본 줄");
  eq(lines.map((l) => l.role), ["frame", "title", "item", "item", "group", "item", "item", "group", "item", "frame"], "줄 역할");
}

// 2) 짧은 입력 정리: "레벨 3", "체력 120 / 150", 이름 사이 공백, 모르는 줄은 남김
{
  const lines = M.tidyLines(basic, ["레벨 3", "체력 120 / 150", "이 름 : 김독자", "칭호: 멸망의 계승자"]);
  eq(texts(lines), [
    "━━━━━━━━━━━━━━━━━━",
    "[상태창]",
    "이름: 김독자",
    "레벨: 3",
    "〈상태〉",
    "체력: 120/150",
    "마나:",
    "〈능력치〉",
    "힘:",
    "칭호: 멸망의 계승자",
    "━━━━━━━━━━━━━━━━━━",
  ], "짧은 입력 정리, 빠진 항목은 비움, 모르는 줄은 아래에 남김");
}

// 3) 다시 정리해도 같은 결과(멱등)
{
  const once = texts(M.tidyLines(basic, ["레벨 3", "체력 120/150", "메모 한 줄"]));
  const twice = texts(M.tidyLines(basic, once));
  eq(twice, once, "정리 결과를 다시 정리해도 같다");
}

// 4) 다른 낱말로 시작하는 줄은 항목으로 잡지 않는다 ("체력회복" ≠ "체력")
{
  const parsed = M.parseLines(basic, ["체력회복 포션 3개", "힘= 12"]);
  eq(parsed.values, { 힘: "12" }, "체력회복은 체력이 아님, 힘= 12 는 힘");
  eq(parsed.extras, ["체력회복 포션 3개"], "체력회복 줄은 그대로 남음");
}

// 5) 대괄호형 틀 + 대괄호 표기, 상자형 틀
{
  const bracket = { ...basic, frame: "bracket", label_style: "colon", items: basic.items.slice(0, 2) };
  eq(texts(M.renderLines(bracket, { 이름: "진우", 레벨: "7" })), ["【상태창】", "[이름: 진우]", "[레벨: 7]"], "대괄호형 틀");
  const tag = { ...bracket, label_style: "bracket" };
  eq(texts(M.renderLines(tag, { 이름: "진우" })), ["【상태창】", "[이름] 진우", "[레벨] 1"], "대괄호 표기는 겹쳐 감싸지 않음");
  eq(texts(M.tidyLines(tag, ["【상태창】", "[이름] 진우", "[레벨]"])), ["【상태창】", "[이름] 진우", "[레벨]"], "대괄호 표기 다시 정리");
  const box = { ...basic, frame: "box", items: basic.items.slice(0, 1) };
  eq(texts(M.renderLines(box, { 이름: "A" })), [
    "┌─────────────────",
    "│ 상태창",
    "├─────────────────",
    "│ 이름: A",
    "└─────────────────",
  ], "상자형은 오른쪽 벽 없음");
  eq(texts(M.tidyLines(box, texts(M.renderLines(box, { 이름: "A" })))), texts(M.renderLines(box, { 이름: "A" })), "상자형 다시 정리");
}

// 6) 틀 없음 + 제목 없음, 다른 틀에서 옮겨 정리
{
  const plain = { ...basic, frame: "none", title: "", items: basic.items.slice(0, 2) };
  eq(texts(M.renderLines(plain, { 이름: "B", 레벨: "2" })), ["이름: B", "레벨: 2"], "틀·제목 없음");
  const fromBox = ["┌──────", "│ 이름: B", "│ 레벨: 2", "└──────"];
  eq(texts(M.tidyLines(plain, fromBox)), ["이름: B", "레벨: 2"], "상자형 줄을 틀 없음으로");
}

// 7) 텍스트 안전 변환(내보내기와 같은 규칙)
{
  eq(M.textSafeLines(["┌────", "│ 상태창", "├────", "│ 이름: A", "└────", "━━━━", "", "레벨: 2 │"]),
    ["상태창", "이름: A", "레벨: 2"], "틀 줄을 빼고 옆 벽을 뗀다");
}

// 8) 프리셋 정리: 잘못된 값은 첫 선택지로, 이름 없는 항목은 버림
{
  const p = M.normalizePreset({ id: "bad id!", frame: "zigzag", items: [{ name: "" }, { name: " 힘 ", type: "x" }] }, 2);
  eq([p.id, p.frame, p.align, p.label_style, p.export_mode, p.items], ["p3", "divider", "left", "colon", "keep", [{ name: "힘", group: "", default: "", type: "text" }]], "프리셋 정리");
}

if (failed) {
  console.error(`${failed} failed`);
  process.exit(1);
}
console.log("all ok");
