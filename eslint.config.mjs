/**
 * SuperTory front-end: catch bare undefined identifiers (no-undef).
 * Cross-file / CDN globals: scripts/web_js_globals.mjs
 */
import globals from "globals";
import {
  browserExtras,
  cdnGlobals,
  crossFileGlobalsFor,
  lintFiles,
} from "./scripts/web_js_globals.mjs";

const sharedRules = {
  "no-undef": "error",
  "no-unused-vars": "off",
};

export default [
  {
    ignores: [
      "web/vendor/**",
      "web/dev/**",
      "node_modules/**",
      "electron/**",
      "dist/**",
      "backend-dist/**",
      "scripts/**",
      "tools/**",
    ],
  },
  {
    linterOptions: {
      reportUnusedDisableDirectives: "off",
    },
  },
  ...lintFiles.map((filePath) => ({
    files: [filePath],
    languageOptions: {
      ecmaVersion: 2022,
      sourceType: "script",
      globals: {
        ...globals.browser,
        ...browserExtras,
        ...cdnGlobals,
        ...crossFileGlobalsFor(filePath),
      },
    },
    rules: sharedRules,
  })),
];
