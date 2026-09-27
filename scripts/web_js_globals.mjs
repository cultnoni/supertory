/**
 * Shared globals for web/*.js no-undef lint.
 * - browserExtras: host APIs the browser env may miss
 * - cdnGlobals: vendor / CDN IIFEs loaded from index.html
 * - crossFileGlobalsFor: symbols other scripts expose (function / const / window.*)
 * - manualAllowlist: intentional shared names not auto-detected (keep tiny)
 */
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");

/** App scripts loaded by index.html (vendor excluded). */
export const lintFiles = [
  "web/typeset_metrics.js",
  "web/tory-check.js",
  "web/smart-punctuation.js",
  "web/app.js",
  "web/feedback_panel.js",
];

export const browserExtras = {
  require: "readonly",
  module: "readonly",
  exports: "readonly",
  process: "readonly",
  __dirname: "readonly",
  __filename: "readonly",
};

export const cdnGlobals = {
  // web/vendor/driver.js/driver.js.iife.js
  driver: "readonly",
};

/**
 * Rare shared names that auto-scan misses. Prefer auto-scan of sibling scripts.
 */
export const manualAllowlist = Object.create(null);

const FN_RE = /(?:^|\n)(?:async\s+)?function\s*\*?\s*([A-Za-z_$][\w$]*)\s*\(/g;
const VAR_RE = /(?:^|\n)(?:const|let|var)\s+([A-Za-z_$][\w$]*)\b/g;
const GLOBAL_ASSIGN_RE =
  /(?:window|globalThis|global)\.([A-Za-z_$][\w$]*)\s*=/g;

function collectExports(absPath) {
  const text = fs.readFileSync(absPath, "utf8");
  const names = new Set();
  for (const re of [FN_RE, VAR_RE, GLOBAL_ASSIGN_RE]) {
    re.lastIndex = 0;
    let match;
    while ((match = re.exec(text)) !== null) {
      names.add(match[1]);
    }
  }
  return names;
}

const exportByBase = new Map();
for (const rel of lintFiles) {
  exportByBase.set(path.basename(rel), collectExports(path.join(root, rel)));
}

/** Globals from sibling scripts when linting `filePath`. */
export function crossFileGlobalsFor(filePath) {
  const base = path.basename(String(filePath));
  const out = Object.create(null);
  for (const [otherBase, names] of exportByBase) {
    if (otherBase === base) continue;
    for (const name of names) out[name] = "readonly";
  }
  for (const [name, writable] of Object.entries(manualAllowlist)) {
    out[name] = writable;
  }
  return out;
}

export function absoluteLintFiles() {
  return lintFiles.map((rel) => path.join(root, rel));
}
