/**
 * Preload bridge: expose a minimal, safe API to the renderer (web/app.js).
 */
const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("electronAPI", {
  minimize: () => ipcRenderer.invoke("supertory:minimize"),
  getVersion: () => ipcRenderer.invoke("supertory:get-version"),
  isPackaged: () => ipcRenderer.invoke("supertory:is-packaged"),
  /** Sync Windows title-bar overlay with day/dark chrome. */
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
});