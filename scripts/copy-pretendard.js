/**
 * Copy Pretendard Variable from node_modules into web/fonts for offline serving.
 * Runtime (Python / Electron / PyInstaller) only reads web/ — never node_modules.
 */
const fs = require("fs");
const path = require("path");

const root = path.resolve(__dirname, "..");
const src = path.join(
  root,
  "node_modules",
  "pretendard",
  "dist",
  "web",
  "variable",
  "woff2",
  "PretendardVariable.woff2"
);
const destDir = path.join(root, "web", "fonts");
const dest = path.join(destDir, "PretendardVariable.woff2");

if (!fs.existsSync(src)) {
  console.error("Missing pretendard package. Run: npm install pretendard");
  process.exit(1);
}
fs.mkdirSync(destDir, { recursive: true });
fs.copyFileSync(src, dest);
console.log(`Copied ${path.relative(root, src)} -> ${path.relative(root, dest)}`);
