/**
 * SuperTory Electron main process.
 * Starts the bundled PyInstaller backend (or local Python in dev), then loads the UI.
 * Packaged builds check for updates via electron-updater (GitHub Releases).
 */
const { app, BrowserWindow, shell, ipcMain, dialog, Menu, session, desktopCapturer, screen, clipboard, webFrameMain } = require("electron");
const { autoUpdater } = require("electron-updater");
const path = require("path");
const fs = require("fs");
const net = require("net");
const { spawn } = require("child_process");

app.disableHardwareAcceleration();
app.commandLine.appendSwitch("disable-lcd-text");

const HOST = "127.0.0.1";
const PORT = 8765;
const APP_URL = `http://${HOST}:${PORT}/`;
const SERVER_DIR_NAME = "supertory-server";
const SERVER_EXE_NAME = "supertory-server.exe";

/** @type {import('electron').BrowserWindow | null} */
let mainWindow = null;
/** @type {import('electron').BrowserWindow | null} */
let gitsiWindow = null;
/** @type {"strip" | "mini" | "max"} */
let gitsiWindowMode = "mini";
/** Last compact (strip/mini) bounds so maximize can return. */
let gitsiCompactBounds = null;
/** Ignore leftover clicks after a mode change (cycle + titlebar restore). */
let gitsiInputLockUntil = 0;
const GITSI_INPUT_LOCK_MS = 800;
/** Pending/current Gitsi join payload for the dedicated window. */
let gitsiJoinPayload = null;
/** @type {import('electron').BrowserWindow | null} */
let gitsiPickerWindow = null;
/** @type {((source: import('electron').DesktopCapturerSource | null) => void) | null} */
let gitsiPickerResolve = null;
/** Latest serialized sources shown in the Gitsi share picker. */
let gitsiPickerSources = [];
/** Session preload id for Jitsi getDisplayMedia blur wrap (frame context). */
let gitsiBlurPreloadId = null;
/** @type {import('child_process').ChildProcess | null} */
let backendProcess = null;
let isQuitting = false;
/** True while an update package is downloading after user confirmation. */
let updateDownloadInProgress = false;
/** Latest available version string from update-available, if any. */
let pendingUpdateVersion = null;
/** True when a usable update feed (GitHub/generic) is configured. */
let updateFeedConfigured = false;
/** True while a manual admin “업데이트 확인” is in flight (show real errors). */
let manualUpdateCheck = false;

const isDev = !app.isPackaged;

function isUpdateFeedPlaceholder(owner, repo) {
  const blob = `${owner || ""}/${repo || ""}`;
  return /깃허브|레포지토리|CHANGE_ME|YOUR_|example|placeholder|내-|sample/i.test(blob);
}

/**
 * Resolve where electron-updater should look for releases.
 * Prefer env / update-feed.json so packaged builds are not stuck on package.json placeholders.
 */
function resolveUpdateFeed() {
  const envOwner = String(
    process.env.SUPERTORY_UPDATE_OWNER || process.env.GH_OWNER || ""
  ).trim();
  const envRepo = String(
    process.env.SUPERTORY_UPDATE_REPO || process.env.GH_REPO || ""
  ).trim();
  if (envOwner && envRepo && !isUpdateFeedPlaceholder(envOwner, envRepo)) {
    return {
      provider: "github",
      owner: envOwner,
      repo: envRepo,
      releaseType: "release",
    };
  }
  const envGeneric = String(process.env.SUPERTORY_UPDATE_URL || "").trim();
  if (envGeneric) {
    return { provider: "generic", url: envGeneric };
  }

  const candidates = [];
  if (process.resourcesPath) {
    candidates.push(path.join(process.resourcesPath, "update-feed.json"));
  }
  try {
    candidates.push(path.join(path.dirname(process.execPath), "update-feed.json"));
  } catch (_) {
    /* ignore */
  }
  candidates.push(path.join(projectRoot(), "update-feed.json"));

  for (const file of candidates) {
    try {
      if (!fs.existsSync(file)) continue;
      const data = JSON.parse(fs.readFileSync(file, "utf8"));
      if (!data || typeof data !== "object") continue;
      if (data.provider === "generic" && data.url) {
        return { provider: "generic", url: String(data.url) };
      }
      const owner = String(data.owner || "").trim();
      const repo = String(data.repo || "").trim();
      if (owner && repo && !isUpdateFeedPlaceholder(owner, repo)) {
        return {
          provider: "github",
          owner,
          repo,
          releaseType: String(data.releaseType || "release"),
        };
      }
    } catch (error) {
      console.warn("[supertory] update-feed.json read failed:", error?.message || error);
    }
  }

  try {
    const pkgPath = path.join(app.getAppPath(), "package.json");
    const pkg = JSON.parse(fs.readFileSync(pkgPath, "utf8"));
    const pub = pkg?.build?.publish;
    const first = Array.isArray(pub) ? pub[0] : pub;
    if (first?.provider === "generic" && first.url) {
      return { provider: "generic", url: String(first.url) };
    }
    if (first?.provider === "github") {
      const owner = String(first.owner || "").trim();
      const repo = String(first.repo || "").trim();
      if (owner && repo && !isUpdateFeedPlaceholder(owner, repo)) {
        return {
          provider: "github",
          owner,
          repo,
          releaseType: String(first.releaseType || "release"),
        };
      }
    }
  } catch (_) {
    /* ignore */
  }
  return null;
}

function projectRoot() {
  return path.join(__dirname, "..");
}

function userDataDir() {
  return path.join(app.getPath("userData"), "data");
}

function userProjectsDir() {
  return path.join(app.getPath("userData"), "projects");
}

/**
 * Prefer live app.py in development so web/ edits apply without rebuild.
 * Packaged builds use the PyInstaller onedir bundle (no system Python).
 */
function resolveBackendLaunch() {
  const appPy = path.join(projectRoot(), "app.py");

  function pythonLaunch() {
    if (!fs.existsSync(appPy)) return null;
    const candidates = [];
    if (process.env.SUPERTORY_PYTHON) {
      candidates.push(process.env.SUPERTORY_PYTHON);
    }
    candidates.push(
      path.join(process.env.LOCALAPPDATA || "", "Python", "bin", "python.exe")
    );
    for (const candidate of candidates) {
      if (candidate && fs.existsSync(candidate)) {
        return {
          kind: "python",
          command: candidate,
          args: [appPy],
          cwd: projectRoot(),
        };
      }
    }
    if (process.platform === "win32") {
      return {
        kind: "python",
        command: "py",
        args: ["-3", appPy],
        cwd: projectRoot(),
      };
    }
    return {
      kind: "python",
      command: "python",
      args: [appPy],
      cwd: projectRoot(),
    };
  }

  // Dev: always prefer source tree (web/styles.css, app.js stay live).
  if (isDev) {
    const live = pythonLaunch();
    if (live) {
      console.log("[supertory] backend: live app.py (dev)");
      return live;
    }
  }

  const bundledCandidates = [];
  if (!isDev) {
    bundledCandidates.push(
      path.join(process.resourcesPath, SERVER_DIR_NAME, SERVER_EXE_NAME)
    );
  }
  // Fallback when Python is unavailable: pre-built backend-dist.
  bundledCandidates.push(
    path.join(projectRoot(), "backend-dist", SERVER_DIR_NAME, SERVER_EXE_NAME)
  );

  for (const exePath of bundledCandidates) {
    if (fs.existsSync(exePath)) {
      console.log("[supertory] backend: bundled", exePath);
      return {
        kind: "bundled",
        command: exePath,
        args: [],
        cwd: path.dirname(exePath),
      };
    }
  }

  const live = pythonLaunch();
  if (live) return live;

  throw new Error(
    "백엔드를 찾을 수 없습니다.\n" +
      `번들: resources/${SERVER_DIR_NAME}/${SERVER_EXE_NAME}\n` +
      `또는 개발용: ${appPy}`
  );
}

function waitForPort(host, port, timeoutMs = 90000) {
  const start = Date.now();
  return new Promise((resolve, reject) => {
    const tryConnect = () => {
      if (isQuitting) {
        reject(new Error("앱이 종료되어 서버 대기를 중단했습니다."));
        return;
      }
      const socket = net.connect({ host, port }, () => {
        socket.end();
        resolve();
      });
      socket.on("error", () => {
        socket.destroy();
        if (Date.now() - start > timeoutMs) {
          reject(
            new Error(
              `백엔드 서버가 ${timeoutMs / 1000}초 안에 응답하지 않았습니다. (${host}:${port})`
            )
          );
          return;
        }
        setTimeout(tryConnect, 200);
      });
    };
    tryConnect();
  });
}

function startBackendServer() {
  fs.mkdirSync(userDataDir(), { recursive: true });
  fs.mkdirSync(userProjectsDir(), { recursive: true });

  const launch = resolveBackendLaunch();
  const env = {
    ...process.env,
    SUPERTORY_ELECTRON: "1",
    SUPERTORY_NO_BROWSER: "1",
    SUPERTORY_DATA_DIR: userDataDir(),
    SUPERTORY_PROJECTS_DIR: userProjectsDir(),
    PYTHONUTF8: "1",
    PYTHONUNBUFFERED: "1",
  };

  console.log(
    `[supertory] backend (${launch.kind}): ${launch.command} ${launch.args.join(" ")}`
  );
  console.log(`[supertory] cwd: ${launch.cwd}`);
  console.log(`[supertory] DATA: ${userDataDir()}`);

  backendProcess = spawn(launch.command, launch.args, {
    cwd: launch.cwd,
    env,
    stdio: ["ignore", "pipe", "pipe"],
    windowsHide: true,
  });

  backendProcess.stdout?.on("data", (chunk) => {
    process.stdout.write(`[backend] ${chunk}`);
  });
  backendProcess.stderr?.on("data", (chunk) => {
    process.stderr.write(`[backend] ${chunk}`);
  });
  backendProcess.on("error", (error) => {
    console.error("[supertory] backend spawn error:", error);
  });
  backendProcess.on("exit", (code, signal) => {
    console.log(`[supertory] backend exited code=${code} signal=${signal}`);
    backendProcess = null;
    if (!isQuitting && mainWindow && !mainWindow.isDestroyed()) {
      mainWindow.webContents
        .executeJavaScript(
          `alert("백엔드 서버가 종료되었습니다. 앱을 다시 시작해 주세요. (code=${code})")`
        )
        .catch(() => {});
    }
  });
}

function stopBackendServer() {
  if (!backendProcess || backendProcess.killed) {
    backendProcess = null;
    return;
  }
  const child = backendProcess;
  backendProcess = null;
  try {
    if (process.platform === "win32" && child.pid) {
      spawn("taskkill", ["/pid", String(child.pid), "/f", "/t"], {
        stdio: "ignore",
        windowsHide: true,
      });
    } else {
      child.kill("SIGTERM");
    }
  } catch (error) {
    console.warn("[supertory] failed to stop backend:", error);
  }
}

/** Match web `--chrome-bg` / `--ink` so OS caption buttons blend with each UI theme. */
const TITLEBAR_OVERLAY_BY_THEME = {
  light: { color: "#F8FAFC", symbolColor: "#0F172A", height: 32 },
  sand: { color: "#FEFDF8", symbolColor: "#2C2523", height: 32 },
  dark: { color: "#1E1B17", symbolColor: "#ECE6DC", height: 32 },
  cabin: { color: "#D5DDD3", symbolColor: "#2C3A2E", height: 32 },
  study: { color: "#EAE0D3", symbolColor: "#4A3B32", height: 32 },
  library: { color: "#EFEFF7", symbolColor: "#2B283B", height: 32 },
  dawn: { color: "#E1E6EB", symbolColor: "#2B323B", height: 32 },
  attic: { color: "#14181F", symbolColor: "#E6E0D4", height: 32 },
  eink: { color: "#FFFFFF", symbolColor: "#000000", height: 32 },
  "sunset-window": { color: "#F6E6C8", symbolColor: "#4D382C", height: 32 },
  "silver-fog": { color: "#F1F3F5", symbolColor: "#343A40", height: 32 },
};

function normalizeHexColor(value, fallback) {
  const raw = String(value || "").trim();
  if (/^#[0-9A-Fa-f]{6}$/.test(raw)) return raw.toUpperCase();
  if (/^#[0-9A-Fa-f]{3}$/.test(raw)) {
    return `#${raw[1]}${raw[1]}${raw[2]}${raw[2]}${raw[3]}${raw[3]}`.toUpperCase();
  }
  return fallback;
}

function titleBarOverlayOptions(themeOrOpts = "light") {
  if (themeOrOpts && typeof themeOrOpts === "object") {
    const themeKey = String(themeOrOpts.theme || themeOrOpts.id || "light");
    const preset = TITLEBAR_OVERLAY_BY_THEME[themeKey]
      || (themeOrOpts.scheme === "dark" ? TITLEBAR_OVERLAY_BY_THEME.dark : TITLEBAR_OVERLAY_BY_THEME.light);
    return {
      color: normalizeHexColor(themeOrOpts.color, preset.color),
      symbolColor: normalizeHexColor(themeOrOpts.symbolColor, preset.symbolColor),
      height: Number(themeOrOpts.height) > 0 ? Number(themeOrOpts.height) : 32,
    };
  }
  const key = String(themeOrOpts || "light");
  if (TITLEBAR_OVERLAY_BY_THEME[key]) return { ...TITLEBAR_OVERLAY_BY_THEME[key] };
  if (key === "dark") return { ...TITLEBAR_OVERLAY_BY_THEME.dark };
  return { ...TITLEBAR_OVERLAY_BY_THEME.light };
}

function createMainWindow() {
  const iconPath = path.join(projectRoot(), "assets", "icon.ico");
  const win32Chrome =
    process.platform === "win32"
      ? {
          titleBarStyle: "hidden",
          titleBarOverlay: titleBarOverlayOptions("light"),
        }
      : process.platform === "darwin"
        ? {
            titleBarStyle: "hiddenInset",
            trafficLightPosition: { x: 14, y: 10 },
          }
        : {};
  mainWindow = new BrowserWindow({
    width: 1400,
    height: 900,
    minWidth: 960,
    minHeight: 640,
    show: false,
    title: "SuperTory",
    autoHideMenuBar: true,
    backgroundColor: TITLEBAR_OVERLAY_BY_THEME.light.color,
    icon: fs.existsSync(iconPath) ? iconPath : undefined,
    ...win32Chrome,
    webPreferences: {
      preload: path.join(__dirname, "preload.js"),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: false,
    },
  });

  mainWindow.once("ready-to-show", () => {
    mainWindow?.show();
  });

  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    try {
      const host = new URL(url).hostname;
      if (
        host === "meet.jit.si"
        || host.endsWith(".jit.si")
        || host.endsWith(".8x8.vc")
        || host.endsWith(".jitsi.net")
      ) {
        return {
          action: "allow",
          overrideBrowserWindowOptions: {
            parent: mainWindow || undefined,
            autoHideMenuBar: true,
            webPreferences: {
              preload: path.join(__dirname, "preload.js"),
              contextIsolation: true,
              nodeIntegration: false,
              sandbox: false,
            },
          },
        };
      }
    } catch (_) {
      /* fall through */
    }
    shell.openExternal(url);
    return { action: "deny" };
  });

  // File drag-drop onto the window must not navigate away from the SPA
  // (otherwise 흥행 공식 분석 드롭존 등이 동작하지 않음).
  mainWindow.webContents.on("will-navigate", (event, url) => {
    try {
      const current = mainWindow?.webContents?.getURL?.() || "";
      if (url && current && url !== current) {
        event.preventDefault();
      }
    } catch (_) {
      event.preventDefault();
    }
  });

  mainWindow.on("closed", () => {
    mainWindow = null;
    if (gitsiWindow && !gitsiWindow.isDestroyed()) {
      try { gitsiWindow.close(); } catch (_) { /* ignore */ }
    }
  });

  // Packaged upgrades must not reuse Chromium's cached SPA from a previous install
  // (version IPC can be new while index.html/app.js stay old → missing admin tabs, prompt bugs).
  const loadUi = async () => {
    try {
      await session.defaultSession.clearCache();
      await session.defaultSession.clearStorageData({
        storages: ["shadercache", "cachestorage"],
      });
    } catch (error) {
      console.warn("[supertory] clearCache failed:", error?.message || error);
    }
    return mainWindow.loadURL(APP_URL);
  };
  return loadUi();
}

/**
 * Push update UI state to the renderer (toast / banner). Safe no-op if window is gone.
 * @param {{ phase: string, version?: string|null, percent?: number, message?: string, error?: string }} payload
 */
function sendUpdateStatus(payload) {
  try {
    if (mainWindow && !mainWindow.isDestroyed()) {
      mainWindow.webContents.send("supertory:update-status", payload);
    }
  } catch (error) {
    console.warn("[supertory] sendUpdateStatus failed:", error?.message || error);
  }
}

/**
 * Ask the user whether to install a found update. Returns true if they accept.
 * @param {{ version?: string }} info
 */
async function promptInstallUpdate(info) {
  const version = info?.version ? String(info.version) : "";
  pendingUpdateVersion = version || null;
  const current = app.getVersion();

  sendUpdateStatus({
    phase: "available",
    version,
    message: "새로운 업데이트가 있습니다. 업데이트하시겠습니까?",
    currentVersion: current,
  });

  const boxOptions = {
    type: "info",
    buttons: ["업데이트", "나중에"],
    defaultId: 0,
    cancelId: 1,
    noLink: true,
    title: "SuperTory 업데이트",
    message: "새로운 업데이트가 있습니다. 업데이트하시겠습니까?",
    detail: version
      ? `새 버전: ${version}\n현재 버전: ${current}\n\n확인하면 백그라운드에서 받은 뒤 앱을 다시 시작해 설치합니다.`
      : `현재 버전: ${current}\n\n확인하면 백그라운드에서 받은 뒤 앱을 다시 시작해 설치합니다.`,
  };

  try {
    const result =
      mainWindow && !mainWindow.isDestroyed()
        ? await dialog.showMessageBox(mainWindow, boxOptions)
        : await dialog.showMessageBox(boxOptions);
    return result.response === 0;
  } catch (error) {
    console.warn("[supertory] update dialog failed:", error?.message || error);
    return false;
  }
}

async function startUpdateDownload() {
  if (updateDownloadInProgress) return;
  updateDownloadInProgress = true;
  sendUpdateStatus({
    phase: "downloading",
    version: pendingUpdateVersion,
    percent: 0,
    message: "업데이트를 다운로드하는 중…",
  });
  try {
    await autoUpdater.downloadUpdate();
  } catch (error) {
    updateDownloadInProgress = false;
    console.warn("[supertory] downloadUpdate failed:", error?.message || error);
    sendUpdateStatus({
      phase: "error",
      version: pendingUpdateVersion,
      message: "업데이트 다운로드에 실패했습니다.",
      error: String(error?.message || error),
    });
    if (mainWindow && !mainWindow.isDestroyed()) {
      dialog
        .showMessageBox(mainWindow, {
          type: "warning",
          buttons: ["확인"],
          title: "SuperTory 업데이트",
          message: "업데이트 다운로드에 실패했습니다.",
          detail: String(error?.message || error),
        })
        .catch(() => {});
    }
  }
}

function installDownloadedUpdate() {
  console.log("[supertory] quitting to install update…");
  sendUpdateStatus({
    phase: "installing",
    version: pendingUpdateVersion,
    message: "업데이트를 설치하기 위해 앱을 다시 시작합니다…",
  });
  // Brief delay so the renderer can show status / flush drafts.
  setTimeout(() => {
    isQuitting = true;
    stopBackendServer();
    // isSilent=false shows NSIS progress when needed; isForceRunAfter=true relaunches app.
    try {
      autoUpdater.quitAndInstall(false, true);
    } catch (error) {
      console.warn("[supertory] quitAndInstall failed:", error?.message || error);
      updateDownloadInProgress = false;
      sendUpdateStatus({
        phase: "error",
        message: "업데이트 설치를 시작하지 못했습니다. 앱을 종료한 뒤 다시 실행해 주세요.",
        error: String(error?.message || error),
      });
    }
  }, 1200);
}

function setupAutoUpdater() {
  // Packaged only — electron-updater needs a real publish feed.
  if (isDev) {
    console.log("[supertory] autoUpdater skipped (dev / unpackaged)");
    updateFeedConfigured = false;
    return;
  }

  // User must confirm before download; install only after download completes.
  autoUpdater.autoDownload = false;
  autoUpdater.autoInstallOnAppQuit = true;
  autoUpdater.logger = {
    info: (...args) => console.log("[autoUpdater]", ...args),
    warn: (...args) => console.warn("[autoUpdater]", ...args),
    error: (...args) => console.error("[autoUpdater]", ...args),
    debug: (...args) => console.log("[autoUpdater:debug]", ...args),
  };

  autoUpdater.on("checking-for-update", () => {
    console.log("[supertory] checking for update…");
    // 수동(관리자) 확인일 때만 UI에 표시 — 백그라운드 자동 확인은 하지 않음.
    if (!manualUpdateCheck) return;
    sendUpdateStatus({
      phase: "checking",
      message: "확인 요청을 보냈어요. 새 버전이 있으면 안내가 떠요.",
    });
  });

  autoUpdater.on("update-available", (info) => {
    console.log("[supertory] update available:", info?.version);
    // 베타 기간: 자동 알림 없음. 관리자 「업데이트 확인」으로만 안내.
    if (!manualUpdateCheck) {
      console.log("[supertory] update available ignored (auto-check disabled)");
      return;
    }
    // Ask user; only then download.
    promptInstallUpdate(info)
      .then((accepted) => {
        if (!accepted) {
          console.log("[supertory] user deferred update");
          sendUpdateStatus({
            phase: "deferred",
            version: info?.version,
            message: "업데이트를 나중에 설치할 수 있습니다.",
          });
          return;
        }
        return startUpdateDownload();
      })
      .catch((error) => {
        console.warn("[supertory] update prompt failed:", error?.message || error);
      });
  });

  autoUpdater.on("update-not-available", (info) => {
    console.log("[supertory] no update available", info?.version || "");
    if (!manualUpdateCheck) return;
    sendUpdateStatus({
      phase: "none",
      version: info?.version,
      message: "현재 최신 버전이네요. 새 버전이 있으면 안내가 떠요.",
    });
  });

  autoUpdater.on("error", (error) => {
    console.warn("[supertory] autoUpdater error:", error?.message || error);
    const wasDownloading = updateDownloadInProgress;
    updateDownloadInProgress = false;
    const msg = String(error?.message || error || "");
    // Background checks: stay quiet on transient network noise.
    // Manual admin checks and download failures always surface as errors.
    const softNetwork =
      /ENOTFOUND|ECONNREFUSED|ETIMEDOUT|net::ERR_|offline|getaddrinfo/i.test(msg);
    const quiet = !wasDownloading && !manualUpdateCheck && softNetwork;
    const configHint =
      /404|403|401|Not Found|Bad credentials|rate limit/i.test(msg)
        ? " (업데이트 서버·GitHub Release·권한을 확인해 주세요.)"
        : "";
    sendUpdateStatus({
      phase: quiet ? "unavailable" : "error",
      message: quiet
        ? "업데이트를 지금은 확인할 수 없습니다."
        : (wasDownloading
          ? "업데이트 다운로드 중 문제가 생겼습니다."
          : `업데이트 확인 중 문제가 생겼습니다.${configHint}`),
      error: msg,
    });
  });

  autoUpdater.on("download-progress", (progress) => {
    const pct = Math.round(progress.percent || 0);
    console.log(`[supertory] download ${pct}%`);
    sendUpdateStatus({
      phase: "downloading",
      version: pendingUpdateVersion,
      percent: pct,
      message: `업데이트 다운로드 중… ${pct}%`,
    });
  });

  autoUpdater.on("update-downloaded", (info) => {
    console.log("[supertory] update downloaded:", info?.version);
    updateDownloadInProgress = false;
    pendingUpdateVersion = info?.version ? String(info.version) : pendingUpdateVersion;
    sendUpdateStatus({
      phase: "downloaded",
      version: pendingUpdateVersion,
      percent: 100,
      message: "다운로드 완료. 설치를 위해 앱을 다시 시작합니다…",
    });
    // User already confirmed — install + relaunch automatically.
    installDownloadedUpdate();
  });

  if (!applyUpdateFeed()) {
    console.warn(
      "[supertory] update feed not configured yet. "
      + "Set SUPERTORY_UPDATE_OWNER/REPO or place update-feed.json next to the app."
    );
    return;
  }

  // 베타: 시작/주기 자동 확인·알림 끔. 관리는 관리자 「업데이트 확인」만.
  console.log("[supertory] auto update check disabled; use admin「업데이트 확인」");
}

/** Apply feed URL from env / update-feed.json / package.json. */
function applyUpdateFeed() {
  const feed = resolveUpdateFeed();
  if (!feed) {
    updateFeedConfigured = false;
    return false;
  }
  try {
    autoUpdater.setFeedURL(feed);
    updateFeedConfigured = true;
    console.log(
      "[supertory] autoUpdater feed:",
      feed.provider === "generic"
        ? feed.url
        : `github:${feed.owner}/${feed.repo}`
    );
    return true;
  } catch (error) {
    updateFeedConfigured = false;
    console.warn("[supertory] setFeedURL failed:", error?.message || error);
    return false;
  }
}

function serializeGitsiSources(sources) {
  return (sources || []).map((source) => ({
    id: source.id,
    name: source.name,
    isScreen: String(source.id || "").startsWith("screen:"),
    thumbnail: source.thumbnail && !source.thumbnail.isEmpty()
      ? source.thumbnail.toDataURL()
      : "",
    appIcon: source.appIcon && !source.appIcon.isEmpty()
      ? source.appIcon.toDataURL()
      : "",
  }));
}

function closeGitsiPicker(result) {
  const resolve = gitsiPickerResolve;
  gitsiPickerResolve = null;
  gitsiPickerSources = [];
  const win = gitsiPickerWindow;
  gitsiPickerWindow = null;
  if (win && !win.isDestroyed()) {
    try {
      win.close();
    } catch (_) {
      /* ignore */
    }
  }
  if (typeof resolve === "function") {
    resolve(result);
  }
}

/**
 * Show a window/screen picker so Gitsi screen share can choose a window
 * (Windows has no Chromium system picker in Electron).
 * @param {import('electron').DesktopCapturerSource[]} sources
 * @returns {Promise<import('electron').DesktopCapturerSource | null>}
 */
function promptGitsiShareSource(sources) {
  if (gitsiPickerResolve) {
    closeGitsiPicker(null);
  }
  return new Promise((resolve) => {
    gitsiPickerResolve = resolve;
    gitsiPickerSources = sources || [];
    const iconPath = path.join(projectRoot(), "assets", "icon.ico");
    const parentWin = (gitsiWindow && !gitsiWindow.isDestroyed())
      ? gitsiWindow
      : (mainWindow && !mainWindow.isDestroyed() ? mainWindow : undefined);
    const picker = new BrowserWindow({
      width: 760,
      height: 560,
      minWidth: 520,
      minHeight: 400,
      parent: parentWin,
      modal: Boolean(parentWin),
      show: false,
      alwaysOnTop: true,
      autoHideMenuBar: true,
      title: "짓시 화면 공유",
      backgroundColor: "#F8FAFC",
      icon: fs.existsSync(iconPath) ? iconPath : undefined,
      webPreferences: {
        preload: path.join(__dirname, "preload.js"),
        contextIsolation: true,
        nodeIntegration: false,
        sandbox: false,
      },
    });
    gitsiPickerWindow = picker;
    picker.once("ready-to-show", () => picker.show());
    picker.on("closed", () => {
      if (gitsiPickerWindow === picker) {
        closeGitsiPicker(null);
      }
    });
    picker.webContents.on("did-finish-load", () => {
      try {
        picker.webContents.send(
          "supertory:gitsi-share-sources",
          serializeGitsiSources(gitsiPickerSources)
        );
      } catch (_) {
        /* ignore */
      }
    });
    picker.loadURL(`${APP_URL}gitsi-picker.html`).catch((error) => {
      console.warn("[supertory] gitsi picker load failed:", error?.message || error);
      closeGitsiPicker(null);
    });
  });
}

const GITSI_STRIP = { width: 360, height: 40 };
const GITSI_MINI = { width: 320, height: 240 };
const GITSI_MAX_PAD = 16;
const GITSI_DEBUG_LOG = path.join(__dirname, "..", "data", "_gitsi_debug.log");
let gitsiShareBlurInject = "";
try {
  gitsiShareBlurInject = fs.readFileSync(
    path.join(__dirname, "gitsi-share-blur-inject.js"),
    "utf8"
  );
} catch (error) {
  console.warn("[supertory] gitsi share-blur inject missing:", error?.message || error);
}

function gitsiDebugLog(entry) {
  const line = JSON.stringify({ t: Date.now(), ...entry }) + "\n";
  try {
    fs.mkdirSync(path.dirname(GITSI_DEBUG_LOG), { recursive: true });
    fs.appendFileSync(GITSI_DEBUG_LOG, line);
  } catch (_) {
    /* ignore */
  }
  console.log("[gitsi:main]", entry.src || "main", entry.handler || entry.action || "", entry);
}

function visitGitsiFrames(fn, win) {
  const target = win || gitsiWindow;
  if (!target || target.isDestroyed()) return;
  const visit = (frame) => {
    if (!frame) return;
    try {
      if (typeof frame.isDestroyed === "function" && frame.isDestroyed()) return;
    } catch (_) {
      return;
    }
    try { fn(frame); } catch (_) { /* ignore */ }
    let children = [];
    try { children = frame.frames || []; } catch (_) { children = []; }
    for (const child of children) visit(child);
  };
  try { visit(target.webContents.mainFrame); } catch (_) { /* ignore */ }
}

function injectGitsiShareBlur(frame) {
  if (!gitsiShareBlurInject || !frame) return;
  try {
    if (typeof frame.isDestroyed === "function" && frame.isDestroyed()) return;
  } catch (_) {
    return;
  }
  frame.executeJavaScript(gitsiShareBlurInject).catch((error) => {
    console.warn("[gitsi] share-blur inject failed:", error?.message || error);
  });
}

function attachGitsiShareBlurInjection(win) {
  if (!win || win.isDestroyed()) return;
  const onFrameReady = (_event, isMainFrame, processId, routingId) => {
    if (isMainFrame) return;
    const frame = webFrameMain.fromId(processId, routingId);
    if (frame) injectGitsiShareBlur(frame);
  };
  win.webContents.on("did-frame-finish-load", onFrameReady);
  win.webContents.on("frame-created", (_event, details) => {
    const frame = details && details.frame;
    if (!frame) return;
    const inject = () => injectGitsiShareBlur(frame);
    try { frame.once("dom-ready", inject); } catch (_) { inject(); }
  });
  win.webContents.on("did-finish-load", () => {
    visitGitsiFrames((frame) => {
      if (frame === win.webContents.mainFrame) return;
      injectGitsiShareBlur(frame);
    });
  });
}

function setGitsiShareBlur(enabled) {
  const on = Boolean(enabled);
  const script = "window.__gitsiShareBlur && window.__gitsiShareBlur.setEnabled(" + (on ? "true" : "false") + ")";
  visitGitsiFrames((frame) => {
    frame.executeJavaScript(script).catch(() => {});
  });
  return on;
}

function cleanupGitsiShareBlur(win) {
  visitGitsiFrames((frame) => {
    frame.executeJavaScript(
      "window.__gitsiShareBlur && window.__gitsiShareBlur.cleanup && window.__gitsiShareBlur.cleanup()"
    ).catch(() => {});
  }, win);
}

function notifyGitsiStatus(payload) {
  if (mainWindow && !mainWindow.isDestroyed()) {
    try {
      mainWindow.webContents.send("supertory:gitsi-status", payload);
    } catch (_) {
      /* ignore */
    }
  }
}

function gitsiMaxBounds() {
  const area = screen.getPrimaryDisplay().workArea;
  return {
    x: area.x + GITSI_MAX_PAD,
    y: area.y + GITSI_MAX_PAD,
    width: Math.max(640, area.width - GITSI_MAX_PAD * 2),
    height: Math.max(400, area.height - GITSI_MAX_PAD * 2),
  };
}

function clampGitsiBounds(bounds, size) {
  const area = screen.getPrimaryDisplay().workArea;
  const width = size.width;
  const height = size.height;
  const x = Math.min(
    Math.max(area.x, bounds.x),
    area.x + Math.max(0, area.width - width)
  );
  const y = Math.min(
    Math.max(area.y, bounds.y),
    area.y + Math.max(0, area.height - height)
  );
  return { x, y, width, height };
}

function defaultGitsiMiniBounds() {
  const area = screen.getPrimaryDisplay().workArea;
  return {
    x: area.x + area.width - GITSI_MINI.width - GITSI_MAX_PAD,
    y: area.y + area.height - GITSI_MINI.height - GITSI_MAX_PAD,
    width: GITSI_MINI.width,
    height: GITSI_MINI.height,
  };
}

function normalizeGitsiWindowMode(mode) {
  const value = String(mode || "");
  if (value === "strip" || value === "minimized") return "strip";
  if (value === "max" || value === "maximized") return "max";
  return "mini";
}

function nextGitsiWindowMode(current) {
  switch (normalizeGitsiWindowMode(current)) {
    case "strip":
      return "mini";
    case "mini":
      return "max";
    case "max":
    default:
      return "strip";
  }
}

function applyGitsiWindowMode(mode, options = {}) {
  const win = gitsiWindow;
  if (!win || win.isDestroyed()) return gitsiWindowMode;
  const next = normalizeGitsiWindowMode(mode);
  const locked = Date.now() < gitsiInputLockUntil;
  const force = Boolean(options.force);
  console.log("[gitsi:main]", Date.now(), "apply", {
    from: gitsiWindowMode,
    to: next,
    force,
    locked,
    remainMs: locked ? gitsiInputLockUntil - Date.now() : 0,
  });
  gitsiDebugLog({
    src: "main",
    handler: "apply",
    from: gitsiWindowMode,
    to: next,
    force,
    locked,
  });
  if (
    next === "mini"
    && gitsiWindowMode === "strip"
    && Date.now() < gitsiInputLockUntil
    && !force
  ) {
    console.log("[gitsi:main]", Date.now(), "apply-blocked-restore", { from: gitsiWindowMode, to: next });
    gitsiDebugLog({ src: "main", handler: "apply-blocked-restore", from: gitsiWindowMode, to: next });
    return gitsiWindowMode;
  }
  const animate = options.animate !== false;
  if (gitsiWindowMode !== "max") {
    try {
      gitsiCompactBounds = win.getBounds();
    } catch (_) {
      /* ignore */
    }
  }
  const prev = gitsiWindowMode;
  gitsiWindowMode = next;
  if (prev !== next && next === "strip") {
    gitsiInputLockUntil = Date.now() + GITSI_INPUT_LOCK_MS;
    console.log("[gitsi:main]", Date.now(), "mode-change", { from: prev, to: next, lockMs: GITSI_INPUT_LOCK_MS });
    gitsiDebugLog({ src: "main", handler: "mode-change", from: prev, to: next, lockMs: GITSI_INPUT_LOCK_MS });
  } else if (prev !== next) {
    console.log("[gitsi:main]", Date.now(), "mode-change", { from: prev, to: next, lockMs: 0 });
    gitsiDebugLog({ src: "main", handler: "mode-change", from: prev, to: next, lockMs: 0 });
  }
  try {
    win.webContents.send("supertory:gitsi-mode", next);
  } catch (_) {
    /* ignore */
  }
  let bounds;
  if (next === "max") {
    bounds = gitsiMaxBounds();
  } else {
    const size = next === "strip" ? GITSI_STRIP : GITSI_MINI;
    const origin = gitsiCompactBounds || win.getBounds();
    bounds = clampGitsiBounds(origin, size);
  }
  try { win.setResizable(true); } catch (_) { /* ignore */ }
  try { win.setMinimumSize(1, 1); } catch (_) { /* ignore */ }
  try {
    const area = screen.getPrimaryDisplay().workArea;
    win.setMaximumSize(Math.max(area.width, 800), Math.max(area.height, 600));
  } catch (_) {
    /* ignore */
  }
  try {
    win.setAlwaysOnTop(next !== "max", "floating");
  } catch (_) {
    try { win.setAlwaysOnTop(next !== "max"); } catch (_2) { /* ignore */ }
  }
  try {
    win.setSize(bounds.width, bounds.height, animate);
    win.setPosition(bounds.x, bounds.y);
  } catch (_) {
    try {
      win.setBounds(bounds, animate);
    } catch (_2) {
      /* ignore */
    }
  }
  try {
    if (next === "max") {
      win.setMinimumSize(320, 40);
      win.setResizable(true);
    } else {
      win.setMinimumSize(bounds.width, bounds.height);
      win.setMaximumSize(bounds.width, bounds.height);
      win.setResizable(false);
    }
  } catch (_) {
    /* ignore */
  }
  if (next !== "max") gitsiCompactBounds = bounds;
  return next;
}

function cycleGitsiWindowMode() {
  const next = nextGitsiWindowMode(gitsiWindowMode);
  console.log("[gitsi:main]", Date.now(), "cycle", {
    from: gitsiWindowMode,
    to: next,
  });
  gitsiDebugLog({ src: "main", handler: "cycle", from: gitsiWindowMode, to: next });
  return applyGitsiWindowMode(next);
}

function closeGitsiMeetingWindow() {
  const win = gitsiWindow;
  cleanupGitsiShareBlur(win);
  gitsiWindow = null;
  gitsiJoinPayload = null;
  gitsiWindowMode = "mini";
  gitsiCompactBounds = null;
  notifyGitsiStatus({ inCall: false, room: "", closed: true });
  if (win && !win.isDestroyed()) {
    try { win.close(); } catch (_) { /* ignore */ }
  }
  return true;
}

function createGitsiMeetingWindow() {
  const iconPath = path.join(projectRoot(), "assets", "icon.ico");
  const start = defaultGitsiMiniBounds();
  const win = new BrowserWindow({
    ...start,
    minWidth: GITSI_MINI.width,
    minHeight: GITSI_STRIP.height,
    useContentSize: true,
    thickFrame: false,
    show: false,
    frame: false,
    autoHideMenuBar: true,
    fullscreenable: false,
    maximizable: false,
    minimizable: false,
    resizable: false,
    alwaysOnTop: true,
    skipTaskbar: false,
    backgroundColor: "#1c1917",
    title: "짓시",
    icon: fs.existsSync(iconPath) ? iconPath : undefined,
    webPreferences: {
      preload: path.join(__dirname, "preload.js"),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: false,
      backgroundThrottling: false,
    },
  });
  try { win.setAlwaysOnTop(true, "floating"); } catch (_) { /* ignore */ }
  attachGitsiShareBlurInjection(win);
  gitsiWindow = win;
  gitsiWindowMode = "mini";
  gitsiCompactBounds = start;
  win.once("ready-to-show", () => {
    try { win.show(); } catch (_) { /* ignore */ }
  });
  win.on("closed", () => {
    if (gitsiWindow === win) {
      gitsiWindow = null;
      gitsiJoinPayload = null;
      gitsiWindowMode = "mini";
      gitsiCompactBounds = null;
      notifyGitsiStatus({ inCall: false, room: "", closed: true });
    }
  });
  win.webContents.setWindowOpenHandler(({ url }) => {
    shell.openExternal(url);
    return { action: "deny" };
  });
  win.webContents.on("console-message", (_event, level, message, line, sourceId) => {
    const text = String(message || "");
    if (!text.includes("[gitsi]")) return;
    gitsiDebugLog({
      src: "renderer-console",
      level,
      message: text,
      line,
      sourceId: String(sourceId || ""),
    });
  });
  win.loadURL(`${APP_URL}gitsi-meet.html?v=1.4.8`).catch((error) => {
    console.warn("[supertory] gitsi window load failed:", error?.message || error);
    closeGitsiMeetingWindow();
  });
  win.webContents.on("did-finish-load", () => {
    try {
      win.webContents.send("supertory:gitsi-mode", gitsiWindowMode);
      if (gitsiJoinPayload) {
        win.webContents.send("supertory:gitsi-join", {
          ...gitsiJoinPayload,
          mode: gitsiWindowMode,
        });
      }
    } catch (_) {
      /* ignore */
    }
  });
  return win;
}

async function openGitsiMeetingWindow(payload) {
  const room = String(payload?.room || "").trim();
  if (!room) return { ok: false, error: "missing_room" };
  gitsiJoinPayload = {
    room,
    name: String(payload?.name || "").trim(),
    lang: String(payload?.lang || "ko").slice(0, 8),
    mode: gitsiWindowMode || "mini",
  };
  if (!gitsiWindow || gitsiWindow.isDestroyed()) {
    createGitsiMeetingWindow();
  }
  const win = gitsiWindow;
  if (!win || win.isDestroyed()) return { ok: false, error: "window" };
  if (win.isMinimized()) {
    try { win.restore(); } catch (_) { /* ignore */ }
  }
  try { win.show(); win.focus(); } catch (_) { /* ignore */ }
  if (gitsiWindowMode === "max") {
    applyGitsiWindowMode("mini", { animate: false });
  } else if (gitsiWindowMode !== "mini") {
    applyGitsiWindowMode("mini");
  }
  gitsiJoinPayload.mode = gitsiWindowMode;
  try {
    win.webContents.send("supertory:gitsi-join", gitsiJoinPayload);
  } catch (_) {
    /* page may still be loading; gitsi-window-ready will pick it up */
  }
  notifyGitsiStatus({ inCall: true, room, closed: false });
  return { ok: true, room, mode: gitsiWindowMode };
}

function setupGitsiMedia() {
  const ses = session.defaultSession;
  const allowPermission = (permission) => {
    const name = String(permission || "");
    return (
      name === "media"
      || name === "display-capture"
      || name === "fullscreen"
      || name === "clipboard-sanitized-write"
      || name === "notifications"
    );
  };
  ses.setPermissionRequestHandler((_webContents, permission, callback) => {
    callback(allowPermission(permission));
  });
  ses.setPermissionCheckHandler((_webContents, permission) => allowPermission(permission));
  ses.setDisplayMediaRequestHandler(async (request, callback) => {
    try {
      const sources = await desktopCapturer.getSources({
        types: ["window", "screen"],
        thumbnailSize: { width: 320, height: 180 },
        fetchWindowIcons: true,
      });
      const chosen = await promptGitsiShareSource(sources);
      if (!chosen) {
        callback({});
        return;
      }
      callback({
        video: chosen,
        audio: request.audioRequested ? "loopback" : undefined,
      });
    } catch (error) {
      console.warn("[supertory] Gitsi display-capture failed:", error?.message || error);
      callback({});
    }
  });
  if (!gitsiBlurPreloadId) {
    try {
      gitsiBlurPreloadId = ses.registerPreloadScript({
        type: "frame",
        id: "gitsi-share-blur",
        filePath: path.join(__dirname, "gitsi-frame-preload.js"),
      });
    } catch (error) {
      console.warn("[supertory] gitsi blur preload failed:", error?.message || error);
    }
  }
}

function setupIpc() {
  ipcMain.handle("supertory:minimize", () => {
    mainWindow?.minimize();
  });
  ipcMain.handle("supertory:get-version", () => app.getVersion());
  ipcMain.handle("supertory:is-packaged", () => app.isPackaged);
  ipcMain.handle("supertory:set-titlebar-theme", (_event, theme) => {
    if (!mainWindow || process.platform !== "win32") return false;
    if (typeof mainWindow.setTitleBarOverlay !== "function") return false;
    try {
      const opts = titleBarOverlayOptions(theme);
      mainWindow.setTitleBarOverlay(opts);
      try {
        mainWindow.setBackgroundColor?.(opts.color);
      } catch (_) {
        /* ignore */
      }
      return true;
    } catch (error) {
      console.warn("[supertory] setTitleBarOverlay failed:", error?.message || error);
      return false;
    }
  });
  /** Folder picker for export path (returns absolute path or null). */
  ipcMain.handle("supertory:pick-export-directory", async () => {
    const result = await dialog.showOpenDialog(mainWindow || undefined, {
      title: "내보내기 폴더 선택",
      properties: ["openDirectory", "createDirectory"],
    });
    if (result.canceled || !result.filePaths?.length) return null;
    return result.filePaths[0];
  });
  ipcMain.handle("supertory:gitsi-picker-ready", () => {
    return serializeGitsiSources(gitsiPickerSources);
  });
  ipcMain.handle("supertory:gitsi-picker-choose", (_event, sourceId) => {
    const id = String(sourceId || "");
    const chosen = gitsiPickerSources.find((source) => source.id === id) || null;
    closeGitsiPicker(chosen);
    return Boolean(chosen);
  });
  ipcMain.handle("supertory:gitsi-picker-cancel", () => {
    closeGitsiPicker(null);
    return true;
  });
  ipcMain.on("supertory:gitsi-debug", (_event, payload) => {
    gitsiDebugLog({ src: "renderer-ipc", ...(payload || {}) });
  });
  ipcMain.handle("supertory:gitsi-window-open", (_event, payload) => {
    return openGitsiMeetingWindow(payload || {});
  });
  ipcMain.handle("supertory:gitsi-window-close", () => {
    closeGitsiMeetingWindow();
    return true;
  });
  ipcMain.handle("supertory:gitsi-window-focus", () => {
    if (!gitsiWindow || gitsiWindow.isDestroyed()) return false;
    try {
      if (gitsiWindow.isMinimized()) gitsiWindow.restore();
      gitsiWindow.show();
      gitsiWindow.focus();
    } catch (_) {
      /* ignore */
    }
    return true;
  });
  ipcMain.handle("supertory:gitsi-window-cycle", () => {
    return { ok: true, mode: cycleGitsiWindowMode() };
  });
  ipcMain.handle("supertory:gitsi-window-mode", (_event, mode, options) => {
    return {
      ok: true,
      mode: applyGitsiWindowMode(String(mode || "mini"), options && typeof options === "object" ? options : {}),
    };
  });
  ipcMain.handle("supertory:gitsi-window-ready", () => {
    return {
      ...(gitsiJoinPayload || {}),
      mode: gitsiWindowMode,
    };
  });
  ipcMain.handle("supertory:gitsi-share-blur", (_event, enabled) => {
    return { ok: true, on: setGitsiShareBlur(enabled) };
  });
  ipcMain.handle("supertory:gitsi-share-blur-cleanup", () => {
    cleanupGitsiShareBlur();
    return true;
  });
  ipcMain.handle("supertory:gitsi-copy", (_event, text) => {
    const value = String(text || "");
    if (!value) return false;
    try {
      clipboard.writeText(value);
      return true;
    } catch (_) {
      return false;
    }
  });
  /** Manual re-check from UI (관리자 → 정보 등). */
  ipcMain.handle("supertory:check-for-updates", async () => {
    if (isDev) {
      return { ok: false, reason: "dev" };
    }
    if (!updateFeedConfigured && !applyUpdateFeed()) {
      return {
        ok: false,
        reason: "not_configured",
        error:
          "업데이트 서버가 설정되지 않았습니다. update-feed.json 또는 SUPERTORY_UPDATE_OWNER/REPO를 설정해 주세요.",
      };
    }
    manualUpdateCheck = true;
    try {
      sendUpdateStatus({
        phase: "checking",
        message: "확인 요청을 보냈어요. 새 버전이 있으면 안내가 떠요.",
      });
      const result = await autoUpdater.checkForUpdates();
      return {
        ok: true,
        version: result?.updateInfo?.version || null,
      };
    } catch (error) {
      const msg = String(error?.message || error);
      sendUpdateStatus({
        phase: "error",
        message: "업데이트 확인에 실패했습니다.",
        error: msg,
      });
      return { ok: false, error: msg };
    } finally {
      setTimeout(() => {
        manualUpdateCheck = false;
      }, 8000);
    }
  });
}

async function clickGitsiElement(win, elementId) {
  const info = await win.webContents.executeJavaScript(`
    (() => {
      const el = document.getElementById(${JSON.stringify(elementId)});
      if (!el) return { missing: true };
      el.click();
      return {
        clicked: true,
        id: el.id,
        mode: document.documentElement.dataset.mode || "",
      };
    })()
  `);
  gitsiDebugLog({
    src: "repro",
    action: "dom-click",
    elementId,
    info,
    mode: gitsiWindowMode,
    bounds: win && !win.isDestroyed() ? win.getBounds() : null,
  });
  return info;
}

async function runGitsiReproSequence() {
  const win = gitsiWindow;
  const outPath = path.join(__dirname, "..", "data", "_gitsi_repro.json");
  const snapshot = (label) => ({
    label,
    t: Date.now(),
    mode: gitsiWindowMode,
    bounds: win && !win.isDestroyed() ? win.getBounds() : null,
  });
  const result = { steps: [] };
  try {
    await new Promise((r) => setTimeout(r, 1500));
    result.steps.push(snapshot("start"));
    await clickGitsiElement(win, "gitsiCycleBtn");
    await new Promise((r) => setTimeout(r, 400));
    result.steps.push(snapshot("after-click-1"));
    await clickGitsiElement(win, "gitsiCycleBtn");
    const waitFrom = Date.now();
    await new Promise((r) => setTimeout(r, 1100));
    result.steps.push({
      ...snapshot("after-click-2-wait-1100ms"),
      waitedMs: Date.now() - waitFrom,
    });
    await clickGitsiElement(win, "gitsiRestoreHit");
    await new Promise((r) => setTimeout(r, 400));
    result.steps.push(snapshot("after-restore-click"));
  } catch (error) {
    result.error = String(error && error.stack || error);
    gitsiDebugLog({ src: "repro", handler: "error", error: result.error });
  }
  result.passStayStrip = result.steps[2] && result.steps[2].mode === "strip";
  result.passRestoreMini = result.steps[3] && result.steps[3].mode === "mini";
  try {
    fs.writeFileSync(outPath, JSON.stringify(result, null, 2));
  } catch (_) {
    /* ignore */
  }
  gitsiDebugLog({ src: "repro", handler: "done", result });
  console.log("[gitsi:repro]", JSON.stringify(result, null, 2));
  isQuitting = true;
  closeGitsiMeetingWindow();
  app.quit();
}

async function bootstrap() {
  setupIpc();
  // Remove default File / Edit / View / Window / Help menu bar.
  Menu.setApplicationMenu(null);

  if (process.env.GITSI_REPRO === "1") {
    try {
      try { fs.unlinkSync(GITSI_DEBUG_LOG); } catch (_) { /* ignore */ }
      gitsiDebugLog({ src: "repro", handler: "start", href: APP_URL });
      await waitForPort(HOST, PORT, 8000);
      setupGitsiMedia();
      await openGitsiMeetingWindow({ room: "repro-room", name: "repro", lang: "ko" });
      await runGitsiReproSequence();
    } catch (error) {
      console.error("[gitsi:repro] failed:", error);
      gitsiDebugLog({ src: "repro", handler: "fatal", error: String(error && error.stack || error) });
      isQuitting = true;
      app.quit();
    }
    return;
  }

  const gotLock = app.requestSingleInstanceLock();
  if (!gotLock) {
    app.quit();
    return;
  }
  app.on("second-instance", () => {
    if (mainWindow) {
      if (mainWindow.isMinimized()) mainWindow.restore();
      mainWindow.focus();
    }
  });

  try {
    startBackendServer();
    await waitForPort(HOST, PORT);
    setupGitsiMedia();
    await createMainWindow();
    setupAutoUpdater();
  } catch (error) {
    console.error("[supertory] bootstrap failed:", error);
    dialog.showErrorBox(
      "SuperTory 시작 실패",
      String(error?.message || error) +
        "\n\n설치 파일이 손상되었거나 백엔드가 포함되지 않았을 수 있습니다.\n" +
        "개발 중이라면: python scripts/build_backend.py 후 다시 실행해 주세요."
    );
    isQuitting = true;
    stopBackendServer();
    app.quit();
  }
}

app.whenReady().then(() => {
  if (process.platform === "win32") {
    app.setAppUserModelId("com.supertory.app");
  }
  return bootstrap();
});

app.on("before-quit", () => {
  isQuitting = true;
  stopBackendServer();
});

app.on("window-all-closed", () => {
  isQuitting = true;
  stopBackendServer();
  app.quit();
});

app.on("activate", () => {
  if (BrowserWindow.getAllWindows().length === 0 && app.isReady()) {
    createMainWindow().catch(console.error);
  }
});
