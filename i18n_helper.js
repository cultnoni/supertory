/* ==== i18n helper (do not duplicate) ==== */
(function initI18n() {
  function readJsonSync(url) {
    try {
      var xhr = new XMLHttpRequest();
      xhr.open("GET", url, false);
      xhr.send(null);
      if (xhr.status >= 200 && xhr.status < 300 && xhr.responseText) {
        return JSON.parse(xhr.responseText);
      }
    } catch (e) {
      /* ignore: keep fallback */
    }
    return null;
  }

  var locale = "ko";
  try {
    locale = localStorage.getItem("supertory.locale") || "ko";
  } catch (e) {
    locale = "ko";
  }

  var fallback = readJsonSync("/locales/ko.json") || {};
  var strings = locale === "ko" ? fallback : (readJsonSync("/locales/" + locale + ".json") || {});

  window.i18n = {
    locale: locale,
    strings: strings,
    fallback: fallback,
    t: function (key) {
      if (!key) return "";
      var cur = this.strings && this.strings[key];
      if (cur) return cur;
      var fb = this.fallback && this.fallback[key];
      if (fb) return fb;
      return key;
    },
    apply: function (root) {
      var scope = root || document;
      if (!scope || !scope.querySelectorAll) return;
      scope.querySelectorAll("[data-i18n]").forEach(function (el) {
        var key = el.getAttribute("data-i18n");
        if (!key) return;
        var translated = window.i18n.t(key);
        if (translated && translated !== key) el.textContent = translated;
      });
      [
        ["data-i18n-placeholder", "placeholder"],
        ["data-i18n-title", "title"],
        ["data-i18n-aria-label", "aria-label"],
        ["data-i18n-alt", "alt"],
        ["data-i18n-value", "value"],
        ["data-i18n-tooltip", "data-tooltip"],
      ].forEach(function (pair) {
        var dataAttr = pair[0];
        var attr = pair[1];
        scope.querySelectorAll("[" + dataAttr + "]").forEach(function (el) {
          var key = el.getAttribute(dataAttr);
          if (!key) return;
          var translated = window.i18n.t(key);
          if (translated && translated !== key) el.setAttribute(attr, translated);
        });
      });
    },
  };

  window.t = function (key) {
    return window.i18n.t(key);
  };

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", function () {
      window.i18n.apply(document);
    });
  } else {
    window.i18n.apply(document);
  }
})();
/* ==== end i18n helper ==== */
