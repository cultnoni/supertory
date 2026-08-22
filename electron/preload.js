/**
 * Preload bridge: expose a minimal, safe API to the renderer (web/app.js).
 */
const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("electronAPI", {
  minimize: () => ipcRenderer.invoke("supertory:minimize"),
  getVersion: () => ipcRenderer.invoke("supertory:get-version"),
  isPackaged: () => ipcRenderer.invoke("supertory:is-packaged"),
  /** Sync Windows title-bar overlay with current UI theme chrome colors. */
  setTitleBarTheme: (theme) => ipcRenderer.invoke("supertory:set-titlebar-theme", theme),

  /** Subscribe to auto-update status events from the main process. */
  onUpdateStatus: (callback) => {
    if (typeof callback !== "function") return () => {};
    const handler = (_event, payload) => {
      try {
        callback(payload);
      } catch (_) {
        /* ignore renderer errors */
      }
    };
    ipcRenderer.on("supertory:update-status", handler);
    return () => {
      ipcRenderer.removeListener("supertory:update-status", handler);
    };
  },

  /** Optional manual update check (e.g. from admin info panel). */
  checkForUpdates: () => ipcRenderer.invoke("supertory:check-for-updates"),

  /** Pick a folder for manuscript export. Returns path string or null if cancelled. */
  pickExportDirectory: () => ipcRenderer.invoke("supertory:pick-export-directory"),

  /** Gitsi (Jitsi) screen-share picker — used only by gitsi-picker.html. */
  gitsiPickerReady: () => ipcRenderer.invoke("supertory:gitsi-picker-ready"),
  onGitsiShareSources: (callback) => {
    if (typeof callback !== "function") return () => {};
    const handler = (_event, payload) => {
      try {
        callback(payload);
      } catch (_) {
        /* ignore renderer errors */
      }
    };
    ipcRenderer.on("supertory:gitsi-share-sources", handler);
    return () => {
      ipcRenderer.removeListener("supertory:gitsi-share-sources", handler);
    };
  },
  chooseGitsiShareSource: (sourceId) => ipcRenderer.invoke("supertory:gitsi-picker-choose", sourceId),
  cancelGitsiShareSource: () => ipcRenderer.invoke("supertory:gitsi-picker-cancel"),

  logGitsi: (payload) => ipcRenderer.send("supertory:gitsi-debug", payload),
  copyText: (text) => ipcRenderer.invoke("supertory:gitsi-copy", text),
  openGitsiWindow: (opts) => ipcRenderer.invoke("supertory:gitsi-window-open", opts || {}),
  closeGitsiWindow: () => ipcRenderer.invoke("supertory:gitsi-window-close"),
  focusGitsiWindow: () => ipcRenderer.invoke("supertory:gitsi-window-focus"),
  cycleGitsiWindowMode: () => ipcRenderer.invoke("supertory:gitsi-window-cycle"),
  setGitsiWindowMode: (mode, opts) => ipcRenderer.invoke("supertory:gitsi-window-mode", mode, opts || {}),
  gitsiWindowReady: () => ipcRenderer.invoke("supertory:gitsi-window-ready"),
  setGitsiShareBlur: (on) => ipcRenderer.invoke("supertory:gitsi-share-blur", Boolean(on)),
  cleanupGitsiShareBlur: () => ipcRenderer.invoke("supertory:gitsi-share-blur-cleanup"),
  onGitsiJoin: (callback) => {
    if (typeof callback !== "function") return () => {};
    const handler = (_event, payload) => {
      try { callback(payload); } catch (_) { /* ignore */ }
    };
    ipcRenderer.on("supertory:gitsi-join", handler);
    return () => ipcRenderer.removeListener("supertory:gitsi-join", handler);
  },
  onGitsiMode: (callback) => {
    if (typeof callback !== "function") return () => {};
    const handler = (_event, mode) => {
      try { callback(mode); } catch (_) { /* ignore */ }
    };
    ipcRenderer.on("supertory:gitsi-mode", handler);
    return () => ipcRenderer.removeListener("supertory:gitsi-mode", handler);
  },
  onGitsiWindowStatus: (callback) => {
    if (typeof callback !== "function") return () => {};
    const handler = (_event, payload) => {
      try { callback(payload); } catch (_) { /* ignore */ }
    };
    ipcRenderer.on("supertory:gitsi-status", handler);
    return () => ipcRenderer.removeListener("supertory:gitsi-status", handler);
  },
});