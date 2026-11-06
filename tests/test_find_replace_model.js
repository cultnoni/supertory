// node tests/test_find_replace_model.js — web/find_replace_model.js 순수 함수 검사
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const code = fs.readFileSync(path.join(__dirname, "..", "web", "find_replace_model.js"), "utf8");
const sandbox = {};
sandbox.globalThis = sandbox;
vm.runInNewContext(code, sandbox);
const M = sandbox.FindReplaceModel;
if (!M) {
  console.error("FindReplaceModel missing");
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

// 1) 서식으로 나뉜 낱말도 같은 문단이면 찾는다
{
  const index = M.buildIndex([
    { text: "도윤은 ", block: 1 },
    { text: "천천", block: 1 },
    { text: "히 걸었다.", block: 1 },
  ]);
  const { matches } = M.findMatches(index.flat, "천천히", {});
  eq(matches.map((m) => [m.start, m.end, m.text]), [[4, 7, "천천히"]], "서식 경계를 넘는 일치");
  eq(M.locate(index, 4, "start"), { segment: 1, offset: 0 }, "시작 위치 → 둘째 조각 처음");
  eq(M.locate(index, 7, "end"), { segment: 2, offset: 1 }, "끝 위치 → 셋째 조각 1글자 뒤");
}

// 2) 다른 문단끼리는 이어 붙이지 않는다
{
  const index = M.buildIndex([
    { text: "끝", block: 1 },
    { text: "시작", block: 2 },
  ]);
  eq(index.flat, "끝\n시작", "문단 사이에 줄바꿈 구분");
  eq(M.findMatches(index.flat, "끝시작", {}).matches.length, 0, "문단을 넘는 일치는 없음");
  eq(M.findMatches(index.flat, "끝.시작", { regex: true }).matches.length, 0, "정규식도 문단을 넘지 않음");
}

// 3) 대소문자
{
  const flat = "Hunter hunter HUNTER";
  eq(M.findMatches(flat, "hunter", {}).matches.length, 3, "기본은 대소문자 무시");
  eq(M.findMatches(flat, "hunter", { caseSensitive: true }).matches.length, 1, "대소문자 구분");
  eq(M.foldCase("İa").length, "İa".length, "소문자 변환이 글자 수를 바꾸지 않음");
}

// 4) 겹치지 않게 차례대로
eq(M.findMatches("aaaa", "aa", {}).matches.map((m) => m.start), [0, 2], "겹치지 않는 일치");

// 5) 정규식: 묶음 참조·잘못된 식·빈 일치
{
  const flat = "강도윤 레벨 3, 한서진 레벨 7";
  const r = M.findMatches(flat, "레벨 (\\d+)", { regex: true });
  eq(r.matches.map((m) => m.text), ["레벨 3", "레벨 7"], "정규식 일치");
  eq(M.replacementFor("레벨 3", "레벨 (\\d+)", "Lv.$1", { regex: true }), "Lv.3", "정규식 $1 바꾸기");
  eq(M.replacementFor("레벨 3", "레벨 3", "$1원", {}), "$1원", "일반 바꾸기에서 $는 그대로");
  eq(M.findMatches(flat, "(", { regex: true }).error, "invalid_regex", "잘못된 정규식은 오류");
  eq(M.findMatches("abc", "x*", { regex: true }).matches.length, 0, "빈 일치는 건너뜀");
}

// 6) 빈 검색어·빈 조각
{
  eq(M.findMatches("본문", "", {}).matches.length, 0, "빈 검색어");
  const index = M.buildIndex([{ text: "", block: 1 }, { text: "글", block: 1 }]);
  eq(M.locate(index, 0, "start"), { segment: 1, offset: 0 }, "빈 조각은 건너뛰고 위치를 잡음");
}

if (failed) {
  console.error(`${failed} failed`);
  process.exit(1);
}
console.log("all passed");
