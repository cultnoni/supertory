/**
 * SuperTory Electron main process.
 * Starts the bundled PyInstaller backend (or local Python in dev), then loads the UI.
 * Packaged builds check for updates via electron-updater (GitHub Releases).
 */
const { app, BrowserWindow, shell, ipcMain, dialog, Menu, session } = require("electron");
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

async function bootstrap() {
  setupIpc();
  // Remove default File / Edit / View / Window / Help menu bar.
  Menu.setApplicationMenu(null);

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
