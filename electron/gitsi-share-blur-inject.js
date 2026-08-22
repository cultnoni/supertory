/**
 * Runs inside the Jitsi Meet iframe (injected by Electron).
 * Wraps getDisplayMedia: raw desktop stream → hidden video → canvas filter → captureStream(15).
 * Toggle blur with window.__gitsiShareBlur.setEnabled(true|false) — no track recreation.
 */
(function () {
  if (window.__gitsiShareBlur) return;

  const MAX_WIDTH = 1280;
  const MAX_HEIGHT = 720;
  const TARGET_FPS = 15;
  const FRAME_MS = 1000 / TARGET_FPS;
  const BLUR_ON_PX = 20;

  const state = {
    enabled: false,
    running: false,
    rafId: 0,
    timerId: 0,
    lastDraw: 0,
    drawn: 0,
    skipped: 0,
    video: null,
    canvas: null,
    ctx: null,
    rawStream: null,
    outStream: null,
    endedBound: false,
  };

  function log(handler, extra) {
    try {
      console.log("[gitsi]", Date.now(), "share-blur", handler, extra ? JSON.stringify(extra) : "");
    } catch (_) { /* ignore */ }
  }

  function even(value) {
    const n = Math.max(2, Math.round(Number(value) || 0));
    return n - (n % 2);
  }

  function canvasSize(video) {
    let width = video && video.videoWidth ? video.videoWidth : MAX_WIDTH;
    let height = video && video.videoHeight ? video.videoHeight : MAX_HEIGHT;
    if (width > MAX_WIDTH || height > MAX_HEIGHT) {
      const scale = Math.min(MAX_WIDTH / width, MAX_HEIGHT / height);
      width *= scale;
      height *= scale;
    }
    return { width: even(width), height: even(height) };
  }

  function stopTracks(stream) {
    if (!stream || typeof stream.getTracks !== "function") return;
    stream.getTracks().forEach(function (track) {
      try { track.stop(); } catch (_) { /* ignore */ }
    });
  }

  function removeEl(el) {
    if (!el) return;
    try {
      if (el.srcObject) el.srcObject = null;
    } catch (_) { /* ignore */ }
    try {
      if (typeof el.pause === "function") el.pause();
    } catch (_) { /* ignore */ }
    if (el.parentNode) {
      try { el.parentNode.removeChild(el); } catch (_) { /* ignore */ }
    }
  }

  function cleanup() {
    const wasRunning = state.running;
    state.running = false;
    if (state.rafId) {
      try { cancelAnimationFrame(state.rafId); } catch (_) { /* ignore */ }
    }
    state.rafId = 0;
    if (state.timerId) {
      try { clearInterval(state.timerId); } catch (_) { /* ignore */ }
    }
    state.timerId = 0;
    state.ctx = null;
    if (state.canvas) {
      try {
        state.canvas.width = 0;
        state.canvas.height = 0;
      } catch (_) { /* ignore */ }
    }
    removeEl(state.video);
    removeEl(state.canvas);
    state.video = null;
    state.canvas = null;
    stopTracks(state.rawStream);
    state.rawStream = null;
    stopTracks(state.outStream);
    state.outStream = null;
    state.endedBound = false;
    state.lastDraw = 0;
    if (wasRunning) {
      log("cleanup", { drawn: state.drawn, skipped: state.skipped });
    }
    state.drawn = 0;
    state.skipped = 0;
  }

  function paint(ts) {
    if (!state.running || !state.ctx || !state.video || !state.canvas) return false;
    const now = typeof ts === "number" ? ts : (performance && performance.now ? performance.now() : Date.now());
    if (now - state.lastDraw < FRAME_MS - 2) {
      state.skipped += 1;
      return false;
    }
    state.lastDraw = now;
    const ctx = state.ctx;
    const blurPx = state.enabled ? BLUR_ON_PX : 0;
    try {
      ctx.filter = "blur(" + blurPx + "px)";
      ctx.drawImage(state.video, 0, 0, state.canvas.width, state.canvas.height);
      ctx.filter = "none";
    } catch (_) { /* ignore */ }
    state.drawn += 1;
    return true;
  }

  function drawFrame(ts) {
    if (!state.running) return;
    state.rafId = requestAnimationFrame(drawFrame);
    paint(ts);
  }

  function bindEnded(raw, out) {
    if (state.endedBound) return;
    state.endedBound = true;
    const rawVideo = raw.getVideoTracks()[0] || null;
    const outVideo = out.getVideoTracks()[0] || null;
    function onEnded() {
      try { if (outVideo && outVideo.readyState !== "ended") outVideo.stop(); } catch (_) { /* ignore */ }
      cleanup();
    }
    if (rawVideo) rawVideo.addEventListener("ended", onEnded);
    if (outVideo) outVideo.addEventListener("ended", onEnded);
  }

  function startLoop(raw) {
    const video = document.createElement("video");
    video.muted = true;
    video.autoplay = true;
    video.playsInline = true;
    video.setAttribute("playsinline", "");
    video.setAttribute("aria-hidden", "true");
    video.style.cssText = "position:fixed;left:-9999px;top:0;width:2px;height:2px;opacity:0;pointer-events:none;";

    const canvas = document.createElement("canvas");
    canvas.setAttribute("aria-hidden", "true");
    canvas.style.cssText = "position:fixed;left:-9999px;top:0;width:2px;height:2px;opacity:0;pointer-events:none;";

    const host = document.documentElement || document.body;
    host.appendChild(video);
    host.appendChild(canvas);

    state.video = video;
    state.canvas = canvas;
    state.rawStream = raw;
    video.srcObject = raw;

    function begin() {
      const size = canvasSize(video);
      canvas.width = size.width;
      canvas.height = size.height;
      const ctx = canvas.getContext("2d", { alpha: false, desynchronized: true });
      if (!ctx) throw new Error("gitsi blur canvas context failed");
      state.ctx = ctx;
      state.running = true;
      state.lastDraw = 0;
      state.drawn = 0;
      state.skipped = 0;
      ctx.filter = "blur(0px)";
      try { ctx.drawImage(video, 0, 0, canvas.width, canvas.height); } catch (_) { /* ignore */ }
      state.rafId = requestAnimationFrame(drawFrame);
      state.timerId = setInterval(function () {
        if (!state.running) return;
        const now = performance && performance.now ? performance.now() : Date.now();
        if (now - state.lastDraw > FRAME_MS * 1.5) paint(now);
      }, FRAME_MS);
      const out = canvas.captureStream(TARGET_FPS);
      raw.getAudioTracks().forEach(function (track) {
        try { out.addTrack(track); } catch (_) { /* ignore */ }
      });
      state.outStream = out;
      bindEnded(raw, out);
      log("wrap", {
        srcW: video.videoWidth,
        srcH: video.videoHeight,
        outW: canvas.width,
        outH: canvas.height,
        fps: TARGET_FPS,
        blurPx: 0,
      });
      return out;
    }

    return new Promise(function (resolve, reject) {
      const fail = function () { reject(new Error("gitsi blur video failed")); };
      const playAndBegin = function () {
        Promise.resolve(video.play()).then(function () {
          try { resolve(begin()); } catch (error) { reject(error); }
        }).catch(reject);
      };
      if (video.readyState >= 2 && video.videoWidth) {
        playAndBegin();
        return;
      }
      video.addEventListener("loadedmetadata", playAndBegin, { once: true });
      video.addEventListener("error", fail, { once: true });
    });
  }

  function wrapStream(raw) {
    cleanup();
    state.enabled = false;
    if (!raw || typeof raw.getVideoTracks !== "function" || !raw.getVideoTracks().length) {
      return Promise.resolve(raw);
    }
    return startLoop(raw).catch(function (error) {
      log("wrap-fail", { error: String(error && error.message || error) });
      state.rawStream = null;
      state.outStream = null;
      state.running = false;
      if (state.rafId) {
        try { cancelAnimationFrame(state.rafId); } catch (_) { /* ignore */ }
      }
      state.rafId = 0;
      if (state.timerId) {
        try { clearInterval(state.timerId); } catch (_) { /* ignore */ }
      }
      state.timerId = 0;
      removeEl(state.video);
      removeEl(state.canvas);
      state.video = null;
      state.canvas = null;
      state.ctx = null;
      return raw;
    });
  }

  const mediaDevices = navigator.mediaDevices;
  if (mediaDevices && typeof mediaDevices.getDisplayMedia === "function") {
    const origDisplay = mediaDevices.getDisplayMedia.bind(mediaDevices);
    mediaDevices.getDisplayMedia = function (constraints) {
      return origDisplay(constraints).then(wrapStream);
    };
  }
  if (typeof navigator.getDisplayMedia === "function") {
    const origNav = navigator.getDisplayMedia.bind(navigator);
    navigator.getDisplayMedia = function (constraints) {
      return origNav(constraints).then(wrapStream);
    };
  }

  window.addEventListener("message", function (event) {
    const data = event.data;
    if (!data || data.channel !== "gitsi-share-blur") return;
    if (data.type === "set") {
      state.enabled = Boolean(data.on);
      log("setEnabled", { on: state.enabled, running: state.running, via: "message" });
    } else if (data.type === "cleanup") {
      cleanup();
    }
  });

  window.__gitsiShareBlur = {
    setEnabled: function (on) {
      state.enabled = Boolean(on);
      log("setEnabled", { on: state.enabled, running: state.running });
      return state.enabled;
    },
    isEnabled: function () { return state.enabled; },
    isRunning: function () { return state.running; },
    cleanup: cleanup,
    stats: function () {
      return {
        running: state.running,
        enabled: state.enabled,
        drawn: state.drawn,
        skipped: state.skipped,
        width: state.canvas ? state.canvas.width : 0,
        height: state.canvas ? state.canvas.height : 0,
      };
    },
  };
  log("installed", { href: String(location.href || "") });
})();
