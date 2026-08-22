/**
 * Session frame preload: patch getDisplayMedia in every frame (including the
 * cross-origin Jitsi iframe) before page scripts run. Does not expose an API.
 */
const { webFrame } = require("electron");
const fs = require("fs");
const path = require("path");

try {
  const code = fs.readFileSync(path.join(__dirname, "gitsi-share-blur-inject.js"), "utf8");
  webFrame.executeJavaScript(code).catch(() => {});
} catch (_) {
  /* ignore */
}
