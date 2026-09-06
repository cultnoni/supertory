/**
 * Windows-safe backend process teardown helpers.
 * Extracted from main.js so quit sequencing can be unit-tested without Electron.
 */
const { spawn } = require("child_process");
const http = require("http");

function delay(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function rememberPid(set, pid) {
  const n = Number(pid);
  if (Number.isInteger(n) && n > 0) set.add(n);
}

function isChildProcessGone(child) {
  if (!child) return true;
  return child.exitCode != null || child.signalCode != null;
}

function waitForChildExit(child, timeoutMs) {
  return new Promise((resolve) => {
    if (isChildProcessGone(child)) {
      resolve(true);
      return;
    }
    let settled = false;
    const finish = (ok) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      child.removeListener("exit", onExit);
      resolve(ok);
    };
    const onExit = () => finish(true);
    const timer = setTimeout(() => finish(false), timeoutMs);
    child.once("exit", onExit);
  });
}

function parseListeningPids(netstatOut, port, selfPid) {
  const needle = `:${port}`;
  const pids = [];
  for (const line of String(netstatOut || "").split(/\r?\n/)) {
    if (!/LISTENING/i.test(line) || !line.includes(needle)) continue;
    const parts = line.trim().split(/\s+/);
    const local = parts[1] || "";
    if (!local.endsWith(needle)) continue;
    const pid = Number(parts[parts.length - 1]);
    if (pid > 0 && pid !== selfPid && !pids.includes(pid)) pids.push(pid);
  }
  return pids;
}

function listListeningPids(port) {
  return new Promise((resolve) => {
    if (process.platform !== "win32") {
      resolve([]);
      return;
    }
    const proc = spawn("netstat", ["-ano", "-p", "TCP"], { windowsHide: true });
    let out = "";
    let settled = false;
    const finish = (pids) => {
      if (settled) return;
      settled = true;
      resolve(pids);
    };
    proc.stdout?.on("data", (chunk) => {
      out += chunk.toString("utf8");
    });
    proc.on("close", () => {
      finish(parseListeningPids(out, port, process.pid));
    });
    proc.on("error", () => finish([]));
    setTimeout(() => {
      try { proc.kill(); } catch (_) { /* ignore */ }
      finish(parseListeningPids(out, port, process.pid));
    }, 1500);
  });
}

function listWindowsChildPids(parentPid) {
  return new Promise((resolve) => {
    if (process.platform !== "win32" || !parentPid) {
      resolve([]);
      return;
    }
    const proc = spawn(
      "powershell.exe",
      [
        "-NoProfile",
        "-Command",
        `Get-CimInstance Win32_Process -Filter "ParentProcessId=${Number(parentPid)}" | Select-Object -ExpandProperty ProcessId`,
      ],
      { windowsHide: true }
    );
    let out = "";
    let settled = false;
    const finish = (pids) => {
      if (settled) return;
      settled = true;
      resolve(pids);
    };
    proc.stdout?.on("data", (chunk) => {
      out += chunk.toString("utf8");
    });
    proc.on("close", () => {
      const pids = out
        .split(/\r?\n/)
        .map((line) => Number(String(line).trim()))
        .filter((pid) => Number.isInteger(pid) && pid > 0);
      finish(pids);
    });
    proc.on("error", () => finish([]));
    setTimeout(() => {
      try { proc.kill(); } catch (_) { /* ignore */ }
      finish([]);
    }, 1500);
  });
}

async function collectPidTree(rootPids) {
  const tracked = new Set();
  for (const pid of rootPids) rememberPid(tracked, pid);
  const queue = [...tracked];
  const seen = new Set();
  while (queue.length) {
    const pid = queue.shift();
    if (seen.has(pid)) continue;
    seen.add(pid);
    const kids = await listWindowsChildPids(pid);
    for (const kid of kids) {
      rememberPid(tracked, kid);
      if (!seen.has(kid)) queue.push(kid);
    }
  }
  return tracked;
}

function runTaskkill(pid, timeoutMs = 3000) {
  return new Promise((resolve) => {
    const n = Number(pid);
    if (!Number.isInteger(n) || n <= 0) {
      resolve(false);
      return;
    }
    if (process.platform !== "win32") {
      resolve(false);
      return;
    }
    const killer = spawn("taskkill", ["/pid", String(n), "/f", "/t"], {
      stdio: "ignore",
      windowsHide: true,
    });
    let settled = false;
    const finish = (ok) => {
      if (settled) return;
      settled = true;
      resolve(ok);
    };
    killer.once("close", () => finish(true));
    killer.once("error", (error) => {
      console.warn("[supertory] taskkill error:", error?.message || error);
      finish(false);
    });
    setTimeout(() => {
      try { killer.kill(); } catch (_) { /* ignore */ }
      finish(false);
    }, timeoutMs);
  });
}

function postBackendQuit({ host, port, timeoutMs }) {
  return new Promise((resolve) => {
    const req = http.request(
      {
        host,
        port,
        path: "/api/app/quit",
        method: "POST",
        headers: { "Content-Length": 2, "Content-Type": "application/json" },
        timeout: timeoutMs,
      },
      (res) => {
        res.resume();
        res.on("end", () => resolve(true));
      }
    );
    req.on("error", () => resolve(false));
    req.on("timeout", () => {
      req.destroy();
      resolve(false);
    });
    req.end("{}");
  });
}

module.exports = {
  collectPidTree,
  delay,
  isChildProcessGone,
  listListeningPids,
  listWindowsChildPids,
  parseListeningPids,
  postBackendQuit,
  rememberPid,
  runTaskkill,
  waitForChildExit,
};
