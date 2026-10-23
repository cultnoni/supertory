/* SuperTory Gitsi (video room) UI — loaded after app.js, before app-boot.js. */
const GITSI_DISPLAY_NAME_KEY = "supertory.gitsiDisplayName";
let gitsiJoinInFlight = false;
/** @type {{ dispose?: Function, executeCommand?: Function, addListener?: Function, getIFrame?: Function } | null} */
let gitsiApi = null;
/** @type {Promise<void> | null} */
let gitsiScriptPromise = null;
/** @type {ResizeObserver | null} */
let gitsiResizeObserver = null;
let gitsiAudioMuted = true;
let gitsiVideoMuted = true;
let gitsiSharing = false;
/** @type {{ pointerId: number, startX: number, startY: number, origLeft: number, origTop: number } | null} */
let gitsiDrag = null;
let gitsiNativeInCall = false;
let gitsiNativeRoom = "";
let gitsiDefaultRoomCode = "";
let gitsiRoomsReady = false;
let gitsiIconLongPressTimer = null;
let gitsiIconLongPressFired = false;
const GITSI_ICON_LONG_PRESS_MS = 500;

function canUseGitsiWindow() {
  return typeof window.electronAPI?.openGitsiWindow === "function";
}

function loadJitsiExternalApi() {
  if (typeof window.JitsiMeetExternalAPI === "function") return Promise.resolve();
  if (gitsiScriptPromise) return gitsiScriptPromise;
  gitsiScriptPromise = new Promise((resolve, reject) => {
    const script = document.createElement("script");
    script.src = "https://meet.jit.si/external_api.js";
    script.async = true;
    script.onload = () => {
      if (typeof window.JitsiMeetExternalAPI === "function") resolve();
      else reject(new Error(i18n.t("index.룸에_입장하지_못했어요")));
    };
    script.onerror = () => reject(new Error(i18n.t("index.룸에_입장하지_못했어요")));
    document.head.appendChild(script);
  });
  return gitsiScriptPromise;
}

function gitsiDisplayName() {
  try {
    const saved = String(localStorage.getItem(GITSI_DISPLAY_NAME_KEY) || "").trim();
    if (saved) return saved.slice(0, 40);
  } catch (_) {
    /* ignore */
  }
  return i18n.t("index.짓시_표시_이름_예시");
}

function isGitsiInCall() {
  if (canUseGitsiWindow()) return Boolean(gitsiNativeInCall);
  return Boolean(gitsiApi) && !$("gitsiFloat")?.classList.contains("hidden");
}

function isGitsiPopoverOpen() {
  const popup = $("gitsiPopover");
  return Boolean(popup && !popup.hidden && popup.classList.contains("is-open"));
}

function isTitlebarChromeButton(el) {
  return Boolean(el?.closest?.(".app-titlebar"));
}

function positionGitsiPopover() {
  const popup = $("gitsiPopover");
  const button = $("gitsiPopoverButton");
  if (!popup || !button) return;
  const rect = button.getBoundingClientRect();
  const pad = 8;
  const wasOpen = popup.classList.contains("is-open");
  popup.hidden = false;
  popup.style.visibility = "hidden";
  popup.classList.add("is-open");
  const fromTitlebar = isTitlebarChromeButton(button);
  popup.classList.toggle("is-below", fromTitlebar);
  const mw = Math.min(popup.offsetWidth || 280, window.innerWidth - pad * 2);
  const mh = popup.offsetHeight || 200;
  let left = fromTitlebar ? rect.left : rect.right + 8;
  let top = fromTitlebar ? rect.bottom + 6 : rect.bottom - mh;
  if (left + mw > window.innerWidth - pad) {
    left = Math.max(pad, window.innerWidth - mw - pad);
  }
  if (top < pad) top = pad;
  if (top + mh > window.innerHeight - pad) {
    top = Math.max(pad, window.innerHeight - mh - pad);
  }
  popup.style.left = `${Math.round(left)}px`;
  popup.style.top = `${Math.round(top)}px`;
  popup.style.bottom = "auto";
  popup.style.visibility = "";
  if (!wasOpen) {
    popup.classList.remove("is-open");
    void popup.offsetWidth;
    requestAnimationFrame(() => popup.classList.add("is-open"));
  }
}

function closeGitsiPopover() {
  const popup = $("gitsiPopover");
  const button = $("gitsiPopoverButton");
  if (popup) {
    popup.classList.remove("is-open");
    popup.hidden = true;
    popup.style.visibility = "";
  }
  if (button) button.setAttribute("aria-expanded", "false");
}

function hideGitsiWorkspace() {
  closeGitsiPopover();
}

function openGitsiPopover() {
  if (typeof closeUiThemeRail === "function") closeUiThemeRail();
  if (typeof closeAmbientPopup === "function") closeAmbientPopup();
  const button = $("gitsiPopoverButton");
  const popup = $("gitsiPopover");
  if (!button || !popup) return;
  button.setAttribute("aria-expanded", "true");
  const codeInput = $("gitsiRoomCodeInput");
  if (codeInput && !String(codeInput.value || "").trim()) {
    void suggestGitsiRoomCode();
  }
  void refreshGitsiRecent();
  positionGitsiPopover();
}

function toggleGitsiPopover() {
  if (isGitsiInCall()) {
    if (canUseGitsiWindow()) {
      try { window.electronAPI.focusGitsiWindow?.(); } catch (_) { /* ignore */ }
      closeGitsiPopover();
      return;
    }
    const floatEl = $("gitsiFloat");
    if (floatEl) cycleGitsiFloatMode();
    closeGitsiPopover();
    return;
  }
  if (isGitsiPopoverOpen()) closeGitsiPopover();
  else openGitsiPopover();
}

function syncGitsiCallUi() {
  $("gitsiPopoverButton")?.classList.toggle("is-in-call", isGitsiInCall());
  const mic = $("gitsiMicButton");
  const cam = $("gitsiCamButton");
  const share = $("gitsiShareButton");
  if (mic) {
    mic.classList.toggle("is-muted", gitsiAudioMuted);
    mic.classList.toggle("is-on", !gitsiAudioMuted);
    mic.setAttribute("aria-pressed", gitsiAudioMuted ? "false" : "true");
  }
  if (cam) {
    cam.classList.toggle("is-muted", gitsiVideoMuted);
    cam.classList.toggle("is-on", !gitsiVideoMuted);
    cam.setAttribute("aria-pressed", gitsiVideoMuted ? "false" : "true");
  }
  if (share) {
    share.classList.toggle("is-on", gitsiSharing);
    share.setAttribute("aria-pressed", gitsiSharing ? "true" : "false");
  }
}

function renderGitsiRecent(rooms) {
  const host = $("gitsiRecentList");
  if (!host) return;
  const list = Array.isArray(rooms) ? rooms : [];
  gitsiDefaultRoomCode = "";
  for (const room of list) {
    if (room && (room.is_default === 1 || room.is_default === true)) {
      gitsiDefaultRoomCode = String(room.room_code || "").trim();
      break;
    }
  }
  gitsiRoomsReady = true;
  if (!list.length) {
    host.innerHTML = `<p class="hint gitsi-recent-empty">${i18n.t("index.아직_만든_룸이_없어요")}</p>`;
    return;
  }
  host.innerHTML = list.map((room) => {
    const code = String(room?.room_code || "").replace(/"/g, "&quot;");
    const safe = String(room?.room_code || "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;");
    const isDefault = Boolean(room?.is_default);
    const starLabel = (isDefault ? i18n.t("index.기본_룸") : i18n.t("index.기본으로_설정"))
      .replace(/&/g, "&amp;")
      .replace(/"/g, "&quot;")
      .replace(/</g, "&lt;");
    const removeLabel = i18n.t("index.목록에서_삭제")
      .replace(/&/g, "&amp;")
      .replace(/"/g, "&quot;")
      .replace(/</g, "&lt;");
    return `<div class="gitsi-recent-row">
      <button type="button" class="gitsi-recent-item" data-gitsi-room="${code}"><code>${safe}</code></button>
      <button type="button" class="gitsi-recent-star${isDefault ? " is-on" : ""}" data-gitsi-star="${code}" title="${starLabel}" aria-label="${starLabel}" aria-pressed="${isDefault ? "true" : "false"}">${isDefault ? "★" : "☆"}</button>
      <button type="button" class="gitsi-recent-remove" data-gitsi-remove="${code}" title="${removeLabel}" aria-label="${removeLabel}">×</button>
    </div>`;
  }).join("");
}

async function refreshGitsiRecent() {
  try {
    const data = await api("/api/gitsi/rooms");
    renderGitsiRecent(data?.rooms || []);
  } catch (error) {
    renderGitsiRecent([]);
    handleError?.(error);
  }
}

async function setGitsiDefaultRoom(roomCode, enabled) {
  const payload = { is_default: Boolean(enabled) };
  const code = String(roomCode || "").trim();
  if (code) payload.room_code = code;
  const room = await api("/api/gitsi/rooms/default", {
    method: "POST",
    body: JSON.stringify(payload),
  });
  const next = String(room?.room_code || "").trim();
  const input = $("gitsiRoomCodeInput");
  if (input && next && enabled) input.value = next;
  toast(enabled ? i18n.t("index.기본_룸으로_지정했어요") : i18n.t("index.기본_룸을_해제했어요"));
  await refreshGitsiRecent();
  return room;
}

async function removeGitsiRecentRoom(roomCode) {
  const code = String(roomCode || "").trim();
  if (!code) return;
  await api(`/api/gitsi/rooms/${encodeURIComponent(code)}`, { method: "DELETE" });
  toast(i18n.t("index.룸을_목록에서_지웠어요"));
  await refreshGitsiRecent();
}

async function ensureGitsiRoomsLoaded() {
  if (gitsiRoomsReady) return;
  await refreshGitsiRecent();
}

function clearGitsiIconLongPress() {
  if (gitsiIconLongPressTimer) {
    clearTimeout(gitsiIconLongPressTimer);
    gitsiIconLongPressTimer = null;
  }
}

function openGitsiPopoverForced() {
  if (isGitsiPopoverOpen()) {
    positionGitsiPopover();
    return;
  }
  openGitsiPopover();
}

async function handleGitsiIconActivate() {
  if (gitsiIconLongPressFired) return;
  if (isGitsiInCall()) {
    toggleGitsiPopover();
    return;
  }
  if (isGitsiPopoverOpen()) {
    closeGitsiPopover();
    return;
  }
  await ensureGitsiRoomsLoaded();
  if (gitsiDefaultRoomCode) {
    void joinGitsiRoom(gitsiDefaultRoomCode);
    return;
  }
  openGitsiPopover();
}

async function suggestGitsiRoomCode() {
  const input = $("gitsiRoomCodeInput");
  try {
    const data = await api("/api/gitsi/suggest-code");
    if (input && data?.room_code) input.value = data.room_code;
  } catch (error) {
    if (input && !String(input.value || "").trim()) {
      input.value = `supertory-${Math.random().toString(36).slice(2, 6)}`;
    }
    handleError?.(error);
  }
}

function gitsiMeetIframe() {
  try {
    const fromApi = gitsiApi?.getIFrame?.();
    if (fromApi) return fromApi;
  } catch (_) {
    /* ignore */
  }
  return $("gitsiMeetHost")?.querySelector("iframe") || null;
}

function syncGitsiMeetFrameSize() {
  const host = $("gitsiMeetHost");
  const floatEl = $("gitsiFloat");
  if (!host || !floatEl || floatEl.classList.contains("hidden")) return null;
  const rect = host.getBoundingClientRect();
  const w = Math.max(1, Math.round(rect.width));
  const h = Math.max(1, Math.round(rect.height));
  const iframe = gitsiMeetIframe();
  const applySize = (el) => {
    if (!el || !el.style) return;
    el.style.setProperty("width", `${w}px`, "important");
    el.style.setProperty("height", `${h}px`, "important");
    if (el.setAttribute) {
      el.setAttribute("width", String(w));
      el.setAttribute("height", String(h));
    }
  };
  applySize(iframe);
  Array.from(host.children || []).forEach((child) => applySize(child));
  return { w, h };
}

function bindGitsiMeetResize() {
  const host = $("gitsiMeetHost");
  const floatEl = $("gitsiFloat");
  if (!host) return;
  if (typeof ResizeObserver === "function") {
    if (!gitsiResizeObserver) {
      gitsiResizeObserver = new ResizeObserver(() => {
        syncGitsiMeetFrameSize();
      });
    }
    gitsiResizeObserver.disconnect();
    gitsiResizeObserver.observe(host);
    if (floatEl) gitsiResizeObserver.observe(floatEl);
  }
}

function unbindGitsiMeetResize() {
  try {
    gitsiResizeObserver?.disconnect();
  } catch (_) {
    /* ignore */
  }
}

function applyGitsiExpandedRect() {
  const el = $("gitsiFloat");
  if (!el) return;
  const work = $("workArea")?.getBoundingClientRect();
  if (work && work.width > 40 && work.height > 40) {
    el.style.setProperty("--gitsi-expand-top", `${Math.round(work.top)}px`);
    el.style.setProperty("--gitsi-expand-left", `${Math.round(work.left)}px`);
    el.style.setProperty("--gitsi-expand-width", `${Math.round(work.width)}px`);
    el.style.setProperty("--gitsi-expand-height", `${Math.round(work.height)}px`);
    return;
  }
  el.style.setProperty("--gitsi-expand-top", "12px");
  el.style.setProperty("--gitsi-expand-left", "12px");
  el.style.setProperty("--gitsi-expand-width", `${Math.max(1, window.innerWidth - 24)}px`);
  el.style.setProperty("--gitsi-expand-height", `${Math.max(1, window.innerHeight - 24)}px`);
}

function rememberGitsiMiniBox(el) {
  const r = el.getBoundingClientRect();
  el.style.left = `${Math.round(r.left)}px`;
  el.style.top = `${Math.round(r.top)}px`;
  el.style.right = "auto";
  el.style.bottom = "auto";
}

function clampGitsiMiniPosition(el) {
  if (!el || !el.classList.contains("is-mini")) return;
  const r = el.getBoundingClientRect();
  const pad = 8;
  const left = Math.min(Math.max(pad, r.left), Math.max(pad, window.innerWidth - r.width - pad));
  const top = Math.min(Math.max(pad, r.top), Math.max(pad, window.innerHeight - r.height - pad));
  el.style.left = `${Math.round(left)}px`;
  el.style.top = `${Math.round(top)}px`;
  el.style.right = "auto";
  el.style.bottom = "auto";
}

function setGitsiFloatMode(mode) {
  const el = $("gitsiFloat");
  if (!el || el.classList.contains("hidden")) return;
  const next = mode === "expanded" || mode === "strip" ? mode : "mini";
  if (next === "expanded" && !el.classList.contains("is-expanded")) {
    if (el.classList.contains("is-mini")) rememberGitsiMiniBox(el);
  }
  if (next === "expanded") {
    // 데스크톱 앱처럼 앱 밖(브라우저 전체 화면)까지 꽉 채운다. 거부되면 페이지 전체로 대신한다.
    try {
      if (!document.fullscreenElement) void el.requestFullscreen?.().catch(() => {});
    } catch (_) { /* ignore */ }
  } else if (document.fullscreenElement === el) {
    try { void document.exitFullscreen?.().catch(() => {}); } catch (_) { /* ignore */ }
  }
  el.classList.toggle("is-expanded", next === "expanded");
  el.classList.toggle("is-strip", next === "strip");
  el.classList.toggle("is-mini", next === "mini");
  el.dataset.gitsiMode = next;
  requestAnimationFrame(() => {
    syncGitsiMeetFrameSize();
    setTimeout(syncGitsiMeetFrameSize, 60);
    setTimeout(syncGitsiMeetFrameSize, 240);
  });
}

function cycleGitsiFloatMode() {
  const el = $("gitsiFloat");
  if (!el) return;
  const cur = el.dataset.gitsiMode || "mini";
  setGitsiFloatMode(cur === "strip" ? "mini" : cur === "mini" ? "expanded" : "strip");
}

function showGitsiFloatMini() {
  const el = $("gitsiFloat");
  if (!el) return;
  el.classList.remove("hidden", "is-expanded", "is-strip");
  el.classList.add("is-mini");
  el.dataset.gitsiMode = "mini";
  el.setAttribute("aria-hidden", "false");
  if (!el.dataset.gitsiDragged) {
    const work = $("workArea")?.getBoundingClientRect();
    const w = 280;
    const h = 200;
    const pad = 16;
    let left;
    let top;
    if (work && work.width > 80) {
      left = work.right - w - pad;
      top = work.bottom - h - pad;
    } else {
      left = window.innerWidth - w - pad;
      top = window.innerHeight - h - pad;
    }
    el.style.left = `${Math.round(left)}px`;
    el.style.top = `${Math.round(top)}px`;
    el.style.right = "auto";
    el.style.bottom = "auto";
  }
  state.gitsiOpen = true;
  syncGitsiCallUi();
}

function hideGitsiFloat() {
  const el = $("gitsiFloat");
  if (!el) return;
  el.classList.add("hidden", "is-mini");
  el.classList.remove("is-expanded", "is-strip", "is-dragging");
  el.dataset.gitsiMode = "mini";
  if (document.fullscreenElement === el) {
    try { void document.exitFullscreen?.().catch(() => {}); } catch (_) { /* ignore */ }
  }
  el.setAttribute("aria-hidden", "true");
  state.gitsiOpen = false;
  syncGitsiCallUi();
}

function onGitsiWindowResize() {
  if (isGitsiPopoverOpen()) positionGitsiPopover();
  const el = $("gitsiFloat");
  if (!el || el.classList.contains("hidden")) return;
  if (el.classList.contains("is-expanded")) applyGitsiExpandedRect();
  else clampGitsiMiniPosition(el);
  syncGitsiMeetFrameSize();
}

function gitsiExecute(command) {
  try {
    gitsiApi?.executeCommand?.(command);
  } catch (_) {
    /* ignore */
  }
}

function leaveGitsiMeeting() {
  if (canUseGitsiWindow()) {
    try { window.electronAPI.closeGitsiWindow?.(); } catch (_) { /* ignore */ }
    return;
  }
  disposeGitsiMeeting();
  toast(i18n.t("index.짓시_룸에서_나왔어요"));
}

function scheduleGitsiFrameSync() {
  requestAnimationFrame(() => {
    syncGitsiMeetFrameSize();
    setTimeout(syncGitsiMeetFrameSize, 50);
    setTimeout(syncGitsiMeetFrameSize, 300);
  });
}

async function showGitsiMeeting(roomCode) {
  if (canUseGitsiWindow()) {
    closeGitsiPopover();
    hideGitsiFloat();
    disposeGitsiApiOnly();
    gitsiNativeInCall = true;
    gitsiNativeRoom = roomCode;
    syncGitsiCallUi();
    const lang = i18n.getLang?.() || document.documentElement.lang || "ko";
    await window.electronAPI.openGitsiWindow({
      room: roomCode,
      name: gitsiDisplayName(),
      lang,
    });
    return;
  }
  const current = String($("gitsiActiveRoomCode")?.textContent || "").trim();
  if (gitsiApi && current === roomCode && !$("gitsiFloat")?.classList.contains("hidden")) {
    closeGitsiPopover();
    setGitsiFloatMode("mini");
    return;
  }
  disposeGitsiApiOnly();
  const host = $("gitsiMeetHost");
  const codeEl = $("gitsiActiveRoomCode");
  if (codeEl) codeEl.textContent = roomCode;
  if (!host) return;
  showGitsiFloatMini();
  closeGitsiPopover();
  await loadJitsiExternalApi();
  gitsiAudioMuted = true;
  gitsiVideoMuted = true;
  gitsiSharing = false;
  syncGitsiCallUi();
  const lang = i18n.getLang?.() || document.documentElement.lang || "ko";
  const displayName = gitsiDisplayName();
  gitsiApi = new window.JitsiMeetExternalAPI("meet.jit.si", {
    roomName: roomCode,
    parentNode: host,
    width: "100%",
    height: "100%",
    lang,
    userInfo: { displayName },
    configOverwrite: {
      startWithAudioMuted: true,
      startWithVideoMuted: true,
      disableShortcuts: true,
      prejoinConfig: { enabled: false },
      prejoinPageEnabled: false,
      disableInviteFunctions: true,
      toolbarButtons: [
        "microphone",
        "camera",
        "desktop",
        "chat",
        "raisehand",
        "participants-pane",
        "tileview",
        "settings",
        "hangup",
      ],
    },
    interfaceConfigOverwrite: {
      SHOW_CHROME_EXTENSION_BANNER: false,
      DISABLE_JOIN_LEAVE_NOTIFICATIONS: true,
      TOOLBAR_ALWAYS_VISIBLE: true,
    },
  });
  bindGitsiMeetResize();
  scheduleGitsiFrameSync();
  gitsiApi.addListener?.("videoConferenceJoined", () => {
    try {
      gitsiApi.executeCommand?.("displayName", displayName);
    } catch (_) {
      /* ignore */
    }
    scheduleGitsiFrameSync();
  });
  gitsiApi.addListener?.("audioMuteStatusChanged", (payload) => {
    gitsiAudioMuted = payload?.muted !== false;
    syncGitsiCallUi();
  });
  gitsiApi.addListener?.("videoMuteStatusChanged", (payload) => {
    gitsiVideoMuted = payload?.muted !== false;
    syncGitsiCallUi();
  });
  gitsiApi.addListener?.("screenSharingStatusChanged", (payload) => {
    gitsiSharing = Boolean(payload?.on);
    syncGitsiCallUi();
  });
  gitsiApi.addListener?.("readyToClose", () => {
    disposeGitsiMeeting();
  });
}

function disposeGitsiApiOnly() {
  unbindGitsiMeetResize();
  const api = gitsiApi;
  gitsiApi = null;
  if (api) {
    try {
      api.dispose?.();
    } catch (_) {
      /* ignore */
    }
  }
  const host = $("gitsiMeetHost");
  if (host) host.innerHTML = "";
}

function disposeGitsiMeeting() {
  disposeGitsiApiOnly();
  gitsiAudioMuted = true;
  gitsiVideoMuted = true;
  gitsiSharing = false;
  hideGitsiFloat();
  const codeEl = $("gitsiActiveRoomCode");
  if (codeEl) codeEl.textContent = "";
}

async function joinGitsiRoom(rawCode) {
  if (gitsiJoinInFlight) return;
  const input = $("gitsiRoomCodeInput");
  const roomCode = String(rawCode != null ? rawCode : (input?.value || "")).trim();
  gitsiJoinInFlight = true;
  try {
    const payload = {};
    if (roomCode) payload.room_code = roomCode;
    const room = await api("/api/gitsi/rooms", {
      method: "POST",
      body: JSON.stringify(payload),
    });
    const code = String(room?.room_code || "").trim();
    if (!code) throw new Error(i18n.t("index.룸에_입장하지_못했어요"));
    if (input) input.value = code;
    try {
      await showGitsiMeeting(code);
    } catch (meetError) {
      disposeGitsiMeeting();
      if (canUseGitsiWindow()) {
        try { window.electronAPI.closeGitsiWindow?.(); } catch (_) { /* ignore */ }
      }
      throw meetError;
    }
    toast(room?.created ? i18n.t("index.짓시_룸을_만들었어요") : i18n.t("index.짓시_룸에_입장했어요"));
    void refreshGitsiRecent();
  } catch (error) {
    handleError?.(error);
  } finally {
    gitsiJoinInFlight = false;
  }
}

function onGitsiPointerMove(event) {
  if (!gitsiDrag) return;
  const el = $("gitsiFloat");
  if (!el) return;
  const left = gitsiDrag.origLeft + (event.clientX - gitsiDrag.startX);
  const top = gitsiDrag.origTop + (event.clientY - gitsiDrag.startY);
  el.style.left = `${Math.round(left)}px`;
  el.style.top = `${Math.round(top)}px`;
}

function onGitsiPointerUp() {
  if (!gitsiDrag) return;
  const el = $("gitsiFloat");
  el?.classList.remove("is-dragging");
  if (el) clampGitsiMiniPosition(el);
  gitsiDrag = null;
}

function setupGitsi() {
  try {
    window.electronAPI?.onGitsiWindowStatus?.((payload) => {
      const inCall = Boolean(payload?.inCall) && !payload?.closed;
      const wasInCall = gitsiNativeInCall;
      gitsiNativeInCall = inCall;
      gitsiNativeRoom = inCall ? String(payload?.room || gitsiNativeRoom || "") : "";
      syncGitsiCallUi();
      if (wasInCall && !inCall) {
        toast(i18n.t("index.짓시_룸에서_나왔어요"));
      }
    });
  } catch (_) {
    /* ignore */
  }
  void refreshGitsiRecent();
  $("gitsiPopoverButton")?.addEventListener("pointerdown", (event) => {
    if (event.button !== 0) return;
    gitsiIconLongPressFired = false;
    clearGitsiIconLongPress();
    gitsiIconLongPressTimer = setTimeout(() => {
      gitsiIconLongPressFired = true;
      openGitsiPopoverForced();
    }, GITSI_ICON_LONG_PRESS_MS);
  });
  $("gitsiPopoverButton")?.addEventListener("pointerup", () => {
    clearGitsiIconLongPress();
  });
  $("gitsiPopoverButton")?.addEventListener("pointerleave", () => {
    clearGitsiIconLongPress();
  });
  $("gitsiPopoverButton")?.addEventListener("pointercancel", () => {
    clearGitsiIconLongPress();
  });
  $("gitsiPopoverButton")?.addEventListener("click", (event) => {
    event.stopPropagation();
    if (gitsiIconLongPressFired) {
      event.preventDefault();
      return;
    }
    void handleGitsiIconActivate();
  });
  $("gitsiPopoverButton")?.addEventListener("contextmenu", (event) => {
    event.preventDefault();
    event.stopPropagation();
    clearGitsiIconLongPress();
    gitsiIconLongPressFired = true;
    openGitsiPopoverForced();
  });
  $("gitsiJoinButton")?.addEventListener("click", () => {
    void joinGitsiRoom();
  });
  $("gitsiSetDefaultButton")?.addEventListener("click", () => {
    const code = String($("gitsiRoomCodeInput")?.value || "").trim();
    void setGitsiDefaultRoom(code, true).catch((error) => handleError?.(error));
  });
  $("gitsiLeaveButton")?.addEventListener("click", leaveGitsiMeeting);
  $("gitsiLeaveMiniButton")?.addEventListener("click", leaveGitsiMeeting);
  $("gitsiExpandButton")?.addEventListener("click", () => setGitsiFloatMode("expanded"));
  $("gitsiShrinkButton")?.addEventListener("click", () => setGitsiFloatMode("mini"));
  $("gitsiStripButton")?.addEventListener("click", () => setGitsiFloatMode("strip"));
  $("gitsiRestoreButton")?.addEventListener("click", () => setGitsiFloatMode("mini"));
  document.addEventListener("fullscreenchange", () => {
    const el = $("gitsiFloat");
    if (el?.classList.contains("is-expanded") && !document.fullscreenElement) setGitsiFloatMode("mini");
  });
  $("gitsiMiniHit")?.addEventListener("click", () => setGitsiFloatMode("expanded"));
  $("gitsiMicButton")?.addEventListener("click", () => gitsiExecute("toggleAudio"));
  $("gitsiCamButton")?.addEventListener("click", () => gitsiExecute("toggleVideo"));
  $("gitsiShareButton")?.addEventListener("click", () => gitsiExecute("toggleShareScreen"));
  $("gitsiCopyCodeButton")?.addEventListener("click", async () => {
    const code = String($("gitsiActiveRoomCode")?.textContent || "").trim();
    if (!code) return;
    try {
      await navigator.clipboard.writeText(code);
      toast(i18n.t("index.룸_코드를_복사했어요"));
    } catch (_) {
      toast(code);
    }
  });
  $("gitsiRoomCodeInput")?.addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      event.preventDefault();
      void joinGitsiRoom();
    }
  });
  $("gitsiRecentList")?.addEventListener("click", (event) => {
    const remove = event.target.closest?.("[data-gitsi-remove]");
    if (remove) {
      event.preventDefault();
      event.stopPropagation();
      const code = String(remove.getAttribute("data-gitsi-remove") || "").trim();
      if (!code) return;
      void removeGitsiRecentRoom(code).catch((error) => handleError?.(error));
      return;
    }
    const star = event.target.closest?.("[data-gitsi-star]");
    if (star) {
      event.preventDefault();
      event.stopPropagation();
      const code = String(star.getAttribute("data-gitsi-star") || "").trim();
      if (!code) return;
      const nextOn = star.getAttribute("aria-pressed") !== "true";
      void setGitsiDefaultRoom(code, nextOn).catch((error) => handleError?.(error));
      return;
    }
    const button = event.target.closest?.("[data-gitsi-room]");
    if (!button) return;
    const code = String(button.getAttribute("data-gitsi-room") || "").trim();
    const input = $("gitsiRoomCodeInput");
    if (input && code) input.value = code;
    void joinGitsiRoom(code);
  });
  $("gitsiFloatDrag")?.addEventListener("pointerdown", (event) => {
    if (event.button !== 0) return;
    if (event.target.closest?.("button")) return;
    const el = $("gitsiFloat");
    if (!el || !el.classList.contains("is-mini")) return;
    event.preventDefault();
    rememberGitsiMiniBox(el);
    el.classList.add("is-dragging");
    el.dataset.gitsiDragged = "1";
    gitsiDrag = {
      pointerId: event.pointerId,
      startX: event.clientX,
      startY: event.clientY,
      origLeft: el.getBoundingClientRect().left,
      origTop: el.getBoundingClientRect().top,
    };
  });
  window.addEventListener("pointermove", onGitsiPointerMove);
  window.addEventListener("pointerup", onGitsiPointerUp);
  window.addEventListener("pointercancel", onGitsiPointerUp);
  window.addEventListener("resize", onGitsiWindowResize);
  document.addEventListener("click", (event) => {
    if (!isGitsiPopoverOpen()) return;
    if (event.target?.closest?.("#gitsiPopover, #binderGitsiControl")) return;
    closeGitsiPopover();
  });
  window.addEventListener("message", (event) => {
    const data = event.data;
    if (!data || data.source !== "supertory-gitsi") return;
    if (data.type === "left") {
      disposeGitsiMeeting();
    } else if (data.type === "error" && data.message) {
      toast(String(data.message));
    }
  });
}
