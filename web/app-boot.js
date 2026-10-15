/* SuperTory boot: wire DOM after app.js (+ domain scripts) have loaded. */
/** Bind click/change only when the node exists — missing DOM must not abort boot. */
function onEl(id, eventName, handler) {
  const el = $(id);
  if (!el || typeof el.addEventListener !== "function") return;
  el.addEventListener(eventName, handler);
}
function safeSetup(name, fn) {
  try {
    fn();
  } catch (err) {
    console.error(`[SuperTORY] setup failed: ${name}`, err);
  }
}

onEl("newChapterButton", "click", () => {
  if (suppressNextNewChapterClick) {
    suppressNextNewChapterClick = false;
    return;
  }
  createChapter().catch(handleError);
});
safeSetup("setupMobileDraftUi", setupMobileDraftUi);
safeSetup("setupRenumberChaptersModal", setupRenumberChaptersModal);
safeSetup("setupTextPromptModal", setupTextPromptModal);
safeSetup("setupOutlineBinderChrome", setupOutlineBinderChrome);
safeSetup("setupGuideTips", setupGuideTips);
safeSetup("setupOutlineOverview", setupOutlineOverview);
safeSetup("setupFolderUndoUi", setupFolderUndoUi);
onEl("newCharacterButton", "click", () => createCharacter().catch(handleError));
onEl("newItemButton", "click", () => createItem().catch(handleError));
onEl("itemEditor", "submit", (event) => saveItem(event).catch(handleError));
onEl("addItemAliasButton", "click", () => addItemAlias().catch(handleError));
onEl("closeSplitButton", "click", () => closeSplitView().catch(handleError));
onEl("splitSceneSelect", "change", () => selectSplitScene().catch(handleError));
safeSetup("setupPopupDragging", setupPopupDragging);
safeSetup("setupRichEditor", setupRichEditor);
safeSetup("setupIllustrationPanel", setupIllustrationPanel);
safeSetup("setupReferenceLinks", setupReferenceLinks);
safeSetup("setupSceneStats", setupSceneStats);
safeSetup("setupSynopsisList", setupSynopsisList);
safeSetup("setupIdeaBank", setupIdeaBank);
safeSetup("setupSettingsCodex", setupSettingsCodex);
safeSetup("setupSettingsCollectionBoard", setupSettingsCollectionBoard);
safeSetup("setupReadingInvite", setupReadingInvite);
safeSetup("setupAiAssist", setupAiAssist);
safeSetup("setupDesktopThemeMenu", setupDesktopThemeMenu);
safeSetup("setupBaitCollection", setupBaitCollection);
safeSetup("setupSourceCollection", setupSourceCollection);
safeSetup("setupToryVault", setupToryVault);
safeSetup("setupAiPanelToggle", setupAiPanelToggle);
safeSetup("setupBinderPanelToggle", setupBinderPanelToggle);
safeSetup("setupPanelDock", setupPanelDock);
safeSetup("setupNarrowLayoutAutoCollapse", setupNarrowLayoutAutoCollapse);
// Theme controls already bound earlier (setupUiThemeToggle); keep a safe re-entry.
safeSetup("setupUiThemeToggle", setupUiThemeToggle);
safeSetup("setupAmbientSound", setupAmbientSound);
safeSetup("setupBinderContextMenu", setupBinderContextMenu);
safeSetup("setupSettingsContextMenu", setupSettingsContextMenu);
safeSetup("setupSceneFeatureBar", setupSceneFeatureBar);
safeSetup("setupAnalyzeMenu", setupAnalyzeMenu);
safeSetup("setupFocusWrite", setupFocusWrite);
safeSetup("setupHoverHeadPins", setupHoverHeadPins);
safeSetup("setupPageWrite", setupPageWrite);
safeSetup("setupViewModeShortcuts", setupViewModeShortcuts);
safeSetup("setupViewerMode", setupViewerMode);
safeSetup("setupGenrePicker", setupGenrePicker);
safeSetup("setupPurposePicker", setupPurposePicker);
safeSetup("setupKeywordBox", setupKeywordBox);
safeSetup("setupProofReportModal", setupProofReportModal);
safeSetup("setupCharacterAnalysisModal", setupCharacterAnalysisModal);
safeSetup("setupTraitChronicle", setupTraitChronicle);
safeSetup("setupWritingLog", setupWritingLog);
safeSetup("setupHeaderNotices", setupHeaderNotices);
safeSetup("setupBookmarkListPanel", setupBookmarkListPanel);
safeSetup("renderBookmarkBar", () => renderBookmarkBar());
safeSetup("setupSceneAutoSave", setupSceneAutoSave);
safeSetup("setupSceneCompleteLock", setupSceneCompleteLock);
safeSetup("setupCompletionGuideCard", setupCompletionGuideCard);
safeSetup("setupOfflineModeUi", setupOfflineModeUi);
safeSetup("setupAuthorNoteUi", setupAuthorNoteUi);
safeSetup("setupCharacterEditorAutosave", setupCharacterEditorAutosave);
safeSetup("setupItemEditorAutosave", setupItemEditorAutosave);
safeSetup("setupBinderSwitch", setupBinderSwitch);
safeSetup("setupGitsi", setupGitsi);
safeSetup("setupOutlineResizer", setupOutlineResizer);
safeSetup("setupAiPanelResizer", setupAiPanelResizer);
safeSetup("setupSplitPaneResizer", setupSplitPaneResizer);
safeSetup("setupFocusWriteSplitResizer", setupFocusWriteSplitResizer);
safeSetup("setupSplitEditMode", setupSplitEditMode);
safeSetup("setupCompareSplitView", setupCompareSplitView);
onEl("projectSelect", "change", (event) => {
  const nextId = Number(event.target.value);
  if (!Number.isFinite(nextId) || nextId <= 0) return;
  const switchGen = bumpProjectLoadGen();
  const applyProjectSwitch = () => {
    if (!isCurrentProjectLoadGen(switchGen)) return;
    const previousId = Number(state.projectId);
    if (Number.isFinite(previousId) && previousId > 0 && previousId !== nextId) {
      detachTranslationWorkspaceForProjectChange();
    }
    if (Number.isFinite(previousId) && previousId > 0) {
      fetch(`/api/projects/${previousId}/checkin`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: "{}",
        keepalive: true,
      }).catch(() => {});
    }
    state.projectId = nextId;
    const nextProject = state.projects.find((p) => Number(p.id) === nextId);
    state.completionGuideShown = Boolean(nextProject?.completion_guide_shown)
      || completionGuideShownThisSession.has(nextId);
    if (typeof renderHiddenGuideTipsList === "function") renderHiddenGuideTipsList();
    // Detach previous work's outline so bookmark sanitize cannot wipe the new work's list.
    state.outline = [];
    state.parts = [];
    state.ungroupedChapters = [];
    state.folders = [];
    state.forceFoldersOutline = false;
    state.outlineProjectId = null;
    state.mainGenre = "";
    state.subGenre = "";
    state.keywords = [];
    state.toryPriorityMd = "";
    syncToryPriorityInput();
    state.projectPurpose = state.projects.find((p) => p.id === state.projectId)?.purpose || null;
    loadEpisodeTabsFromStorage();
    syncPurposePickerFromState();
    syncGenrePickerFromState();
    renderKeywordBox();
    showWelcome();
    renderBookmarkBar({ persistSanitize: false });
    renderForeshadowSelect("");
    fillForeshadowForm(null);
    loadProject().catch((error) => {
      if (isAbortError(error) || !isCurrentProjectLoadGen(switchGen)) return;
      handleError(error);
    });
  };
  flushPendingCodexSaves().catch(() => {}).finally(applyProjectSwitch);
});
safeSetup("setupEpisodeChrome", setupEpisodeChrome);
safeSetup("setupProjectListContextMenu", setupProjectListContextMenu);
safeSetup("setupProjectLibraryMenu", setupProjectLibraryMenu);
onEl("sceneEditor", "submit", (event) => saveScene(event).catch(handleError));
onEl("characterEditor", "submit", (event) => saveCharacter(event).catch(handleError));
onEl("addAliasButton", "click", () => addAlias().catch(handleError));
onEl("newAlias", "keydown", (event) => {
  if (event.key === "Enter") {
    event.preventDefault();
    addAlias().catch(handleError);
  }
});

window.addEventListener("beforeunload", () => {
  if (state.projectId) postProjectCheckinKeepalive(state.projectId);
});
window.addEventListener("pagehide", () => {
  if (state.projectId) postProjectCheckinKeepalive(state.projectId);
});

loadProjects().catch(handleError);
try {
  maybeStartOnboardingTutorial();
} catch (err) {
  console.error("[SuperTORY] onboarding failed", err);
}
