/**
 * Backend stop helpers — no Electron required.
 * Run: node tests/test_electron_backend_stop.js
 */
const assert = require("assert");
const http = require("http");
const { EventEmitter } = require("events");
const { spawn } = require("child_process");
const {
  delay,
  isChildProcessGone,
  parseListeningPids,
  postBackendQuit,
  runTaskkill,
  waitForChildExit,
} = require("../electron/backend-stop");

function fakeChild() {
  const child = new EventEmitter();
  child.exitCode = null;
  child.signalCode = null;
  return child;
}

async function testWaitForExitTimeout() {
  const child = fakeChild();
  const t0 = Date.now();
  const gone = await waitForChildExit(child, 200);
  assert.strictEqual(gone, false);
  assert.ok(Date.now() - t0 < 800, "timeout must not hang");
  assert.strictEqual(isChildProcessGone(child), false);
}

async function testWaitForExitResolves() {
  const child = fakeChild();
  const pending = waitForChildExit(child, 2000);
  child.exitCode = 0;
  child.emit("exit", 0, null);
  const gone = await pending;
  assert.strictEqual(gone, true);
}

async function testParseListeningPids() {
  const out = [
    "  TCP    127.0.0.1:8765         0.0.0.0:0              LISTENING       4321",
    "  TCP    127.0.0.1:18765        0.0.0.0:0              LISTENING       9999",
  ].join("\r\n");
  const pids = parseListeningPids(out, 8765, 1);
  assert.deepStrictEqual(pids, [4321]);
}

async function testPostQuitAndTimeout() {
  let hit = false;
  const server = http.createServer((req, res) => {
    if (req.method === "POST" && req.url === "/api/app/quit") {
      hit = true;
      res.end('{"ok":true}');
      return;
    }
    res.statusCode = 404;
    res.end();
  });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  const { port } = server.address();
  const ok = await postBackendQuit({ host: "127.0.0.1", port, timeoutMs: 1000 });
  assert.strictEqual(ok, true);
  assert.strictEqual(hit, true);
  server.close();

  const hung = http.createServer(() => {
    /* never respond */
  });
  await new Promise((resolve) => hung.listen(0, "127.0.0.1", resolve));
  const hungPort = hung.address().port;
  const t0 = Date.now();
  const timedOut = await postBackendQuit({
    host: "127.0.0.1",
    port: hungPort,
    timeoutMs: 250,
  });
  assert.strictEqual(timedOut, false);
  assert.ok(Date.now() - t0 < 1500, "hung quit POST must time out");
  hung.close();
}

async function testTaskkillWaitsForClose() {
  if (process.platform !== "win32") {
    console.log("skip taskkill (not win32)");
    return;
  }
  const child = spawn(process.execPath, ["-e", "setInterval(() => {}, 1000)"], {
    stdio: "ignore",
    windowsHide: true,
  });
  assert.ok(child.pid > 0);
  const killed = await runTaskkill(child.pid);
  assert.strictEqual(killed, true);
  const gone = await waitForChildExit(child, 4000);
  assert.strictEqual(gone, true);

  const missing = await runTaskkill(1);
  assert.strictEqual(typeof missing, "boolean");
}

async function testRaceTimeoutDoesNotHang() {
  const t0 = Date.now();
  await Promise.race([new Promise(() => {}), delay(200)]);
  assert.ok(Date.now() - t0 < 800);
}

async function main() {
  await testWaitForExitTimeout();
  await testWaitForExitResolves();
  await testParseListeningPids();
  await testPostQuitAndTimeout();
  await testTaskkillWaitsForClose();
  await testRaceTimeoutDoesNotHang();
  console.log("test_electron_backend_stop: ok");
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
