/**
 * Northstar Side Panel
 * Real-time Gemini Live audio client plus browser-task UI.
 */

import {
  LIVE_VOICE_OPTIONS,
  LIVE_SETTINGS_DEFAULTS,
  getLiveSettingsSummary,
  normalizeLiveSettings,
} from "../shared/live-settings.js";

let currentTabId = null;
let isListening = false;
let isRequestingMicrophone = false;
let voiceSessionActive = false;
let captureStream = null;
let captureContext = null;
let captureSource = null;
let captureProcessor = null;
let captureSink = null;
let playbackContext = null;
let playbackCursor = 0;
let keyboardVisible = false;
let liveConnectionStatus = "disconnected";
let microphonePermissionState = "unknown";
let heroExpanded = true;
let baseUiState = "idle";
let isAssistantSpeaking = false;
let speakingStateTimer = null;
let speechActivityActive = false;
let speechLastDetectedAt = 0;
let speechLeadInChunks = [];
let persona = null;
let personaReady = false;
let personaInputs = {};
let personaWaveformFrame = 0;
let personaWaveformLastFrameAt = 0;
let personaMicLevel = 0;
let personaSpeakerLevel = 0;
let personaWaveformLevel = 0;
let previouslyFocusedElement = null;
let liveSettings = normalizeLiveSettings(LIVE_SETTINGS_DEFAULTS);
let liveSettingsStatusTimer = null;

const SPEECH_THRESHOLD = 0.014;
const SPEECH_SILENCE_HANG_MS = 650;
const SPEECH_LEAD_IN_CHUNKS = 3;
const PERSONA_WAVE_MAX_SCALE_DELTA = 0.08;
const PERSONA_WAVE_MAX_LIFT_PX = 3;
const PERSONA_WAVE_INPUT_FLOOR = 0.008;
const PERSONA_WAVE_INPUT_CEIL = 0.05;
const PERSONA_WAVE_OUTPUT_FLOOR = 0.01;
const PERSONA_WAVE_OUTPUT_CEIL = 0.09;
const MAX_CONVERSATION_ITEMS = 6;
const hasChromeApis = Boolean(globalThis.chrome?.runtime?.onMessage && globalThis.chrome?.tabs);
const PERSONA_RIVE_SRC = "../assets/rive/obsidian-2.0.riv";
const PERSONA_STATE_MACHINE = "default";
const NO_WORD_SEPARATOR_SCRIPT_PATTERN =
  /[\p{Script=Han}\p{Script=Hiragana}\p{Script=Katakana}\p{Script=Thai}\p{Script=Lao}\p{Script=Khmer}\p{Script=Myanmar}]/u;

const transcriptBuffers = { user: "", assistant: "" };
const activeTranscriptNodes = { user: null, assistant: null };

const appShell = document.getElementById("app");
const conversation = document.getElementById("conversation");
const voiceHero = document.getElementById("voiceHero");
const voiceHeroStatus = document.getElementById("voiceHeroStatus");
const heroStateBadge = document.getElementById("heroStateBadge");
const scopeTitle = document.getElementById("scopeTitle");
const voiceOrbShell = document.querySelector(".voice-orb-shell");
const personaCanvas = document.getElementById("personaCanvas");
const startVoiceButton = document.getElementById("startVoiceButton");
const endVoiceButton = document.getElementById("endVoiceButton");
const showKeyboardButton = document.getElementById("showKeyboardButton");
const openLiveSettingsButton = document.getElementById("openLiveSettingsButton");
const textInput = document.getElementById("textInput");
const textInputWrapper = document.getElementById("textInputWrapper");
const sendButton = document.getElementById("sendButton");
const quickActions = document.getElementById("quickActions");
const liveSettingsDialog = document.getElementById("liveSettingsDialog");
const liveSettingsSummary = document.getElementById("liveSettingsSummary");
const liveSettingsStatus = document.getElementById("liveSettingsStatus");
const settingVoiceName = document.getElementById("settingVoiceName");
const settingThinkingBudget = document.getElementById("settingThinkingBudget");
const settingAllowInterruptions = document.getElementById("settingAllowInterruptions");
const settingEnableInputTranscription = document.getElementById("settingEnableInputTranscription");
const settingEnableOutputTranscription = document.getElementById("settingEnableOutputTranscription");
const statusBar = document.getElementById("statusBar");
const statusText = document.getElementById("statusText");
const taskApprovalBar = document.getElementById("taskApprovalBar");
const taskApprovalText = document.getElementById("taskApprovalText");
const continueTaskButton = document.getElementById("continueTaskButton");
const diagnosisPanel = document.getElementById("diagnosisPanel");
const diagnosisContent = document.getElementById("diagnosisContent");
const closeDiagnosis = document.getElementById("closeDiagnosis");
const closeLiveSettingsButton = document.getElementById("closeLiveSettingsButton");
const conversationEmptyState = document.getElementById("conversationEmptyState");
const panelAnnouncements = document.getElementById("panelAnnouncements");

async function init() {
  setKeyboardVisible(false);
  setVoiceHeroVisible(true);
  refreshConversationMode();
  syncVoiceButtons();
  renderUiState();
  updateVoiceHeroPrompt();
  initializePersona();
  populateVoiceOptions();
  bindLiveSettingsControls();
  await loadLiveSettingsFromExtension();

  if (!hasChromeApis) {
    return;
  }

  const tab = await getActiveTab();
  if (tab) {
    currentTabId = tab.id;
    updateScopeFromTab(tab);
    connectToBackend();
    syncMicrophonePermissionState();
  }

  chrome.tabs.onActivated.addListener(async (activeInfo) => {
    const previousTabId = currentTabId;
    if (previousTabId && previousTabId !== activeInfo.tabId) {
      disconnectFromBackend(previousTabId);
    }
    currentTabId = activeInfo.tabId;
    hideTaskApprovalBar();
    clearStreamingMessages();
    try {
      const tab = await chrome.tabs.get(activeInfo.tabId);
      updateScopeFromTab(tab);
    } catch {
      updateScopeFromTab();
    }
    connectToBackend();
    syncMicrophonePermissionState();
  });

  chrome.tabs.onUpdated.addListener((tabId, changeInfo, tab) => {
    if (tabId !== currentTabId) return;
    if (changeInfo.title || changeInfo.url || changeInfo.status === "complete") {
      updateScopeFromTab(tab);
    }
  });

  window.addEventListener("beforeunload", () => {
    disconnectFromBackend();
    cleanupPersona();
    stopListening({ notifyBackend: false, keepSessionActive: false }).catch(() => {});
  });

  window.addEventListener("focus", async () => {
    syncMicrophonePermissionState();
    try {
      const tab = await getActiveTab();
      updateScopeFromTab(tab);
    } catch {}
  });

  window.addEventListener("resize", () => {
    resizePersona();
  });
  document.addEventListener("keydown", handleGlobalKeydown);
}

function populateVoiceOptions() {
  if (!settingVoiceName || settingVoiceName.options.length) {
    return;
  }

  const defaultOption = document.createElement("option");
  defaultOption.value = "";
  defaultOption.textContent = "Model default";
  settingVoiceName.append(defaultOption);

  for (const voiceName of LIVE_VOICE_OPTIONS) {
    const option = document.createElement("option");
    option.value = voiceName;
    option.textContent = voiceName;
    settingVoiceName.append(option);
  }
}

function bindLiveSettingsControls() {
  const controls = [
    settingVoiceName,
    settingThinkingBudget,
    settingAllowInterruptions,
    settingEnableInputTranscription,
    settingEnableOutputTranscription,
  ].filter(Boolean);

  for (const control of controls) {
    control.addEventListener("change", () => {
      saveLiveSettingsFromControls().catch((error) => {
        setLiveSettingsStatus(error?.message || "Could not save Northstar settings.");
      });
    });
  }
}

function collectLiveSettingsFromControls() {
  return normalizeLiveSettings({
    voiceName: settingVoiceName?.value || "",
    thinkingBudget:
      settingThinkingBudget?.value ?? String(LIVE_SETTINGS_DEFAULTS.thinkingBudget),
    allowInterruptions: Boolean(settingAllowInterruptions?.checked),
    enableInputTranscription: Boolean(settingEnableInputTranscription?.checked),
    enableOutputTranscription: Boolean(settingEnableOutputTranscription?.checked),
  });
}

function applyLiveSettingsToControls(settings) {
  liveSettings = normalizeLiveSettings(settings);
  if (settingVoiceName) {
    settingVoiceName.value = liveSettings.voiceName;
  }
  if (settingThinkingBudget) {
    settingThinkingBudget.value = String(liveSettings.thinkingBudget);
  }
  if (settingAllowInterruptions) {
    settingAllowInterruptions.checked = liveSettings.allowInterruptions;
  }
  if (settingEnableInputTranscription) {
    settingEnableInputTranscription.checked = liveSettings.enableInputTranscription;
  }
  if (settingEnableOutputTranscription) {
    settingEnableOutputTranscription.checked = liveSettings.enableOutputTranscription;
  }
  if (liveSettingsSummary) {
    liveSettingsSummary.textContent = getLiveSettingsSummary(liveSettings);
  }
}

async function loadLiveSettingsFromExtension() {
  if (!hasChromeApis) {
    applyLiveSettingsToControls(LIVE_SETTINGS_DEFAULTS);
    return;
  }

  try {
    const response = await chrome.runtime.sendMessage({ type: "get_live_settings" });
    applyLiveSettingsToControls(response?.settings || LIVE_SETTINGS_DEFAULTS);
  } catch {
    applyLiveSettingsToControls(LIVE_SETTINGS_DEFAULTS);
  }
}

async function saveLiveSettingsFromControls() {
  if (!hasChromeApis) {
    applyLiveSettingsToControls(collectLiveSettingsFromControls());
    return;
  }

  const response = await chrome.runtime.sendMessage({
    type: "update_live_settings",
    data: collectLiveSettingsFromControls(),
  });
  if (!response?.ok) {
    throw new Error(response?.error || "Could not save Northstar settings.");
  }
  applyLiveSettingsToControls(response.settings);
  setLiveSettingsStatus("Northstar settings saved.");
}

function setLiveSettingsStatus(message) {
  if (!liveSettingsStatus) {
    return;
  }
  liveSettingsStatus.textContent = message || "";
  clearTimeout(liveSettingsStatusTimer);
  if (message) {
    liveSettingsStatusTimer = setTimeout(() => {
      liveSettingsStatus.textContent = "";
    }, 2400);
  }
}

function connectToBackend() {
  if (!currentTabId) return;
  chrome.runtime.sendMessage({ type: "refresh_context", tabId: currentTabId }).catch(() => {});
}

function disconnectFromBackend(tabId = currentTabId) {
  if (!tabId) return;
  chrome.runtime.sendMessage({ type: "disconnect_session", tabId });
}

function initializePersona() {
  if (!personaCanvas || !globalThis.rive?.Rive) {
    return;
  }

  try {
    globalThis.rive.RuntimeLoader?.setWasmUrl?.("../vendor/rive/rive.wasm");
    persona = new globalThis.rive.Rive({
      src: PERSONA_RIVE_SRC,
      canvas: personaCanvas,
      stateMachines: PERSONA_STATE_MACHINE,
      autoplay: true,
      layout: new globalThis.rive.Layout({
        fit: globalThis.rive.Fit.Contain,
        alignment: globalThis.rive.Alignment.Center,
      }),
      onLoad: () => {
        personaReady = true;
        cachePersonaInputs();
        resizePersona();
        syncPersonaState();
        voiceOrbShell?.classList.add("has-persona");
      },
      onLoadError: (event) => {
        personaReady = false;
        personaInputs = {};
        voiceOrbShell?.classList.remove("has-persona");
        console.error("Persona load failed:", event);
      },
    });
  } catch (error) {
    console.error("Persona init failed:", error);
  }
}

function cachePersonaInputs() {
  if (!persona?.stateMachineInputs) {
    return;
  }

  personaInputs = Object.fromEntries(
    persona.stateMachineInputs(PERSONA_STATE_MACHINE).map((input) => [input.name, input])
  );
}

function resizePersona() {
  if (!personaReady || !persona?.resizeDrawingSurfaceToCanvas) {
    return;
  }
  persona.resizeDrawingSurfaceToCanvas();
}

function normalizePersonaWaveformRms(channel, rms) {
  const floor = channel === "speaker" ? PERSONA_WAVE_OUTPUT_FLOOR : PERSONA_WAVE_INPUT_FLOOR;
  const ceil = channel === "speaker" ? PERSONA_WAVE_OUTPUT_CEIL : PERSONA_WAVE_INPUT_CEIL;
  const normalized = (rms - floor) / (ceil - floor);
  return Math.max(0, Math.min(1, normalized));
}

function applyPersonaWaveform(level) {
  if (!voiceOrbShell) {
    return;
  }

  const clamped = Math.max(0, Math.min(1, level));
  const scale = 1 + clamped * PERSONA_WAVE_MAX_SCALE_DELTA;
  const lift = clamped * -PERSONA_WAVE_MAX_LIFT_PX;
  const glow = 0.16 + clamped * 0.84;
  voiceOrbShell.style.setProperty("--persona-wave-scale", scale.toFixed(4));
  voiceOrbShell.style.setProperty("--persona-wave-lift", `${lift.toFixed(2)}px`);
  voiceOrbShell.style.setProperty("--persona-wave-glow", glow.toFixed(4));
}

function stopPersonaWaveformLoop() {
  if (personaWaveformFrame) {
    cancelAnimationFrame(personaWaveformFrame);
    personaWaveformFrame = 0;
  }
  personaWaveformLastFrameAt = 0;
}

function resetPersonaWaveform() {
  personaMicLevel = 0;
  personaSpeakerLevel = 0;
  personaWaveformLevel = 0;
  stopPersonaWaveformLoop();
  applyPersonaWaveform(0);
}

function decayPersonaWaveformLevel(level, deltaSeconds, ratePerSecond) {
  return Math.max(0, level - deltaSeconds * ratePerSecond);
}

function stepPersonaWaveform(timestamp) {
  if (!personaWaveformLastFrameAt) {
    personaWaveformLastFrameAt = timestamp;
  }

  const deltaSeconds = Math.min(0.12, Math.max(0.016, (timestamp - personaWaveformLastFrameAt) / 1000));
  personaWaveformLastFrameAt = timestamp;

  personaMicLevel = decayPersonaWaveformLevel(
    personaMicLevel,
    deltaSeconds,
    speechActivityActive || isListening ? 2.6 : 4.5
  );
  personaSpeakerLevel = decayPersonaWaveformLevel(
    personaSpeakerLevel,
    deltaSeconds,
    isAssistantSpeaking ? 2.2 : 4.2
  );

  const targetLevel = Math.max(personaMicLevel, personaSpeakerLevel);
  const smoothing = Math.min(1, deltaSeconds * 18);
  personaWaveformLevel += (targetLevel - personaWaveformLevel) * smoothing;
  applyPersonaWaveform(personaWaveformLevel);

  if (
    targetLevel > 0.002 ||
    personaWaveformLevel > 0.002 ||
    speechActivityActive ||
    isAssistantSpeaking
  ) {
    personaWaveformFrame = requestAnimationFrame(stepPersonaWaveform);
    return;
  }

  resetPersonaWaveform();
}

function ensurePersonaWaveformLoop() {
  if (personaWaveformFrame) {
    return;
  }
  personaWaveformLastFrameAt = 0;
  personaWaveformFrame = requestAnimationFrame(stepPersonaWaveform);
}

function updatePersonaWaveform(channel, rms) {
  const normalized = normalizePersonaWaveformRms(channel, rms);
  if (channel === "speaker") {
    personaSpeakerLevel = Math.max(personaSpeakerLevel, normalized);
  } else {
    personaMicLevel = Math.max(personaMicLevel, normalized);
  }
  ensurePersonaWaveformLoop();
}

function syncPersonaState() {
  if (!personaReady) {
    return;
  }

  const personaState = getPersonaState();
  const nextFlags = {
    listening: personaState === "listening",
    thinking: personaState === "thinking",
    speaking: personaState === "speaking",
    asleep: personaState === "asleep",
  };

  for (const [name, active] of Object.entries(nextFlags)) {
    if (personaInputs[name]) {
      personaInputs[name].value = active;
    }
  }
}

function getPersonaState() {
  const visibleState = isAssistantSpeaking ? "speaking" : baseUiState;

  if (visibleState === "speaking") {
    return "speaking";
  }
  if (visibleState === "listening") {
    return "listening";
  }
  if (visibleState === "thinking" || visibleState === "acting") {
    return "thinking";
  }
  if (liveConnectionStatus !== "connected" && !voiceSessionActive && !isListening) {
    return "asleep";
  }
  return "idle";
}

function cleanupPersona() {
  if (persona?.cleanup) {
    persona.cleanup();
  }
  persona = null;
  personaReady = false;
  personaInputs = {};
  resetPersonaWaveform();
  voiceOrbShell?.classList.remove("has-persona");
}

if (hasChromeApis) {
  chrome.runtime.onMessage.addListener((message) => {
    if (message.tabId && message.tabId !== currentTabId) return;

    switch (message.type) {
      case "connection_status":
        updateConnectionStatus(message.status);
        break;

      case "assistant_message":
        addMessage("system", message.text);
        break;

      case "status":
        updateStatus(message.data);
        break;

      case "diagnosis":
        showDiagnosis(message.text);
        break;

      case "live_audio_output":
        playAudioChunk(message.data, message.mimeType).catch((error) => {
          console.error("Audio playback failed:", error);
        });
        break;

      case "live_transcript":
        updateTranscript(message.role, message.text, message.finished);
        break;

      case "browser_task_status":
        handleBrowserTaskStatus(message.data);
        break;

      case "live_settings_updated":
        applyLiveSettingsToControls(message.settings || LIVE_SETTINGS_DEFAULTS);
        break;

      case "microphone_permission_result":
        handleMicrophonePermissionResult(message.data);
        break;
    }
  });
}

async function sendMessage(text) {
  if (!text.trim()) return;
  ensurePlaybackContext().catch(() => {});
  await syncToActiveTabContext();
  clearAssistantSpeaking();
  hideTaskApprovalBar();
  clearTranscriptRole("user");
  clearTranscriptRole("assistant");
  setVoiceHeroVisible(false);
  addMessage("user", text.trim());
  textInput.value = "";

  chrome.runtime.sendMessage({
    type: "user_message",
    tabId: currentTabId,
    text: text.trim(),
  });

  chrome.runtime.sendMessage({ type: "request_screenshot", tabId: currentTabId });
  chrome.runtime.sendMessage({ type: "request_page_map", tabId: currentTabId });
}

sendButton.addEventListener("click", () => {
  sendMessage(textInput.value).catch((error) => {
    console.error("Send message failed:", error);
    handleVoiceCaptureFailure(error?.message || "Failed to sync the current tab.", {
      reason: "context_failed",
    });
  });
});

textInput.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey) {
    event.preventDefault();
    sendMessage(textInput.value).catch((error) => {
      console.error("Send message failed:", error);
      handleVoiceCaptureFailure(error?.message || "Failed to sync the current tab.", {
        reason: "context_failed",
      });
    });
  }
});

document.querySelectorAll(".quick-action[data-command]").forEach((button) => {
  button.addEventListener("click", () => {
    sendMessage(button.dataset.command).catch((error) => {
      console.error("Quick action failed:", error);
      handleVoiceCaptureFailure(error?.message || "Failed to sync the current tab.", {
        reason: "context_failed",
      });
    });
  });
});

startVoiceButton.addEventListener("click", async () => {
  if (voiceSessionActive && isListening) {
    await stopListening({ keepSessionActive: true, awaitingResponse: false });
    return;
  }
  await startListening();
});

endVoiceButton.addEventListener("click", async () => {
  await stopListening({ keepSessionActive: false, awaitingResponse: false });
});

showKeyboardButton.addEventListener("click", () => {
  setKeyboardVisible(!keyboardVisible);
  updateVoiceHeroPrompt();
});

continueTaskButton?.addEventListener("click", () => {
  sendMessage("Continue with the current browser task.").catch((error) => {
    console.error("Continue task failed:", error);
    handleVoiceCaptureFailure(error?.message || "Failed to continue the browser task.", {
      reason: "context_failed",
    });
  });
});

openLiveSettingsButton?.addEventListener("click", () => {
  showLiveSettingsDialog();
});

closeLiveSettingsButton?.addEventListener("click", () => {
  closeLiveSettingsDialog();
});

liveSettingsDialog?.addEventListener("click", (event) => {
  if (event.target === liveSettingsDialog) {
    closeLiveSettingsDialog();
  }
});

async function startListening() {
  if (isListening || isRequestingMicrophone) return;
  clearAssistantSpeaking();

  if (liveConnectionStatus !== "connected") {
    setVoiceHeroVisible(true);
    setVoiceHeroState(
      "error",
      "Gemini Live is still connecting. Wait a second, then start voice again."
    );
    updateStatusState("error", "Live disconnected");
    return;
  }

  try {
    await ensurePlaybackContext();
    await syncToActiveTabContext();
    await syncMicrophonePermissionState();

    if (microphonePermissionState !== "granted") {
      await openMicrophonePermissionTab();
      return;
    }

    await startLocalMicrophoneCapture();
  } catch (error) {
    isRequestingMicrophone = false;
    syncVoiceButtons();
    handleVoiceCaptureFailure(error?.message || "Microphone access failed.");
  }
}

async function stopListening({
  awaitingResponse = false,
  notifyBackend = true,
  keepSessionActive = voiceSessionActive,
} = {}) {
  if (!voiceSessionActive && !isListening && !isRequestingMicrophone && !captureContext && !captureStream) {
    return;
  }

  isListening = false;
  isRequestingMicrophone = false;
  voiceSessionActive = keepSessionActive;
  clearAssistantSpeaking();
  syncVoiceButtons();

  if (awaitingResponse) {
    updateStatusState("thinking", "Waiting for Gemini...");
  } else if (voiceSessionActive) {
    updateStatusState("idle", "Mic muted");
  } else {
    updateStatusState("idle", "Ready");
  }

  try {
    if (speechActivityActive) {
      chrome.runtime.sendMessage({ type: "live_activity_end", tabId: currentTabId });
      resetSpeechActivityState();
    }
    if (notifyBackend) {
      chrome.runtime.sendMessage({
        type: keepSessionActive ? "live_end" : "live_stop",
        tabId: currentTabId,
      });
    }
  } catch {}

  if (captureProcessor) {
    captureProcessor.disconnect();
    captureProcessor.onaudioprocess = null;
    captureProcessor = null;
  }

  if (captureSource) {
    captureSource.disconnect();
    captureSource = null;
  }

  if (captureSink) {
    captureSink.disconnect();
    captureSink = null;
  }

  if (captureStream) {
    captureStream.getTracks().forEach((track) => track.stop());
    captureStream = null;
  }

  if (captureContext) {
    await captureContext.close().catch(() => {});
    captureContext = null;
  }

  if (!voiceSessionActive && !awaitingResponse) {
    resetPersonaWaveform();
  }

  if (!hasConversationMessages()) {
    setVoiceHeroVisible(true);
  }
  updateVoiceHeroPrompt();
}

async function ensurePlaybackContext() {
  if (!playbackContext) {
    playbackContext = new AudioContext();
  }
  if (playbackContext.state === "suspended") {
    await playbackContext.resume();
  }
}

async function playAudioChunk(base64, mimeType = "audio/pcm;rate=24000") {
  if (!base64) return;
  await ensurePlaybackContext();

  if (!mimeType.startsWith("audio/pcm")) {
    const blob = new Blob([base64ToArrayBuffer(base64)], { type: mimeType });
    const url = URL.createObjectURL(blob);
    const audio = new Audio(url);
    audio.onended = () => URL.revokeObjectURL(url);
    updatePersonaWaveform("speaker", PERSONA_WAVE_OUTPUT_FLOOR + (PERSONA_WAVE_OUTPUT_CEIL - PERSONA_WAVE_OUTPUT_FLOOR) * 0.35);
    markAssistantSpeaking(1400);
    await audio.play();
    return;
  }

  const sampleRate = parsePcmRate(mimeType) || 24000;
  const pcm = new Int16Array(base64ToArrayBuffer(base64));
  updatePersonaWaveform("speaker", computeRmsLevel(pcm));
  const floatData = new Float32Array(pcm.length);
  for (let i = 0; i < pcm.length; i += 1) {
    floatData[i] = pcm[i] / 32768;
  }

  const buffer = playbackContext.createBuffer(1, floatData.length, sampleRate);
  buffer.copyToChannel(floatData, 0);

  const source = playbackContext.createBufferSource();
  source.buffer = buffer;
  source.connect(playbackContext.destination);

  const now = playbackContext.currentTime;
  if (playbackCursor < now) {
    playbackCursor = now + 0.02;
  }

  markAssistantSpeaking(Math.max(700, Math.round(buffer.duration * 1000) + 180));
  source.start(playbackCursor);
  playbackCursor += buffer.duration;
}

function updateTranscript(role, text, finished) {
  const transcriptRole = role === "user" ? "user" : "assistant";
  const transcriptText = String(text || "");
  if (!transcriptText && !finished) {
    return;
  }

  const oppositeRole = transcriptRole === "user" ? "assistant" : "user";
  if (transcriptText && activeTranscriptNodes[oppositeRole]) {
    finalizeTranscriptRole(oppositeRole);
  }

  const current = transcriptBuffers[transcriptRole];
  const nextText = mergeTranscriptText(current, transcriptText);
  transcriptBuffers[transcriptRole] = nextText;

  let node = activeTranscriptNodes[transcriptRole];
  if (!node && !nextText && finished) {
    transcriptBuffers[transcriptRole] = "";
    return;
  }

  if (!node) {
    setVoiceHeroVisible(false);
    node = createMessageNode(transcriptRole);
    activeTranscriptNodes[transcriptRole] = node;
  }

  if (nextText) {
    setMessageBody(node, nextText, false);
  }
  node.classList.toggle("streaming", !finished);
  conversation.scrollTop = conversation.scrollHeight;

  if (finished) {
    finalizeTranscriptRole(transcriptRole);
  }
}

function mergeTranscriptText(current, incoming) {
  if (!incoming) {
    return current;
  }
  if (!current) {
    return incoming;
  }
  if (incoming.startsWith(current)) {
    return incoming;
  }

  const overlapLength = findTranscriptOverlap(current, incoming);
  const nextChunk = overlapLength > 0 ? incoming.slice(overlapLength) : incoming;
  if (!nextChunk) {
    return current;
  }

  if (shouldConcatenateTranscriptWordFragment(current, nextChunk)) {
    return `${current}${nextChunk}`;
  }

  if (needsTranscriptSeparator(current, nextChunk)) {
    return `${current} ${nextChunk.replace(/^\s+/, "")}`;
  }
  return `${current}${nextChunk}`;
}

function findTranscriptOverlap(current, incoming) {
  const maxLength = Math.min(current.length, incoming.length);
  for (let size = maxLength; size > 0; size -= 1) {
    if (current.slice(-size) === incoming.slice(0, size)) {
      return size;
    }
  }
  return 0;
}

function shouldConcatenateTranscriptWordFragment(current, nextChunk) {
  if (!current || !nextChunk) {
    return false;
  }
  if (/\s$/.test(current) || /^\s/.test(nextChunk)) {
    return false;
  }
  if (/^[,.;:!?%)\]}]/.test(nextChunk) || /^['’]/.test(nextChunk)) {
    return false;
  }
  if (/[(\[{/"“‘-]$/.test(current)) {
    return false;
  }

  const currentBoundary = current.at(-1) || "";
  const nextBoundary = nextChunk[0] || "";
  return /[\p{L}\p{N}]$/u.test(currentBoundary) && /^[\p{L}\p{N}]/u.test(nextBoundary);
}

function needsTranscriptSeparator(current, nextChunk) {
  if (!current || !nextChunk) {
    return false;
  }
  if (/\s$/.test(current) || /^\s/.test(nextChunk)) {
    return false;
  }
  if (/^[,.;:!?%)\]}]/.test(nextChunk) || /^['’]/.test(nextChunk)) {
    return false;
  }
  if (/[(\[{/"“‘-]$/.test(current)) {
    return false;
  }

  const currentBoundary = current.at(-1) || "";
  const nextBoundary = nextChunk[0] || "";
  if (
    NO_WORD_SEPARATOR_SCRIPT_PATTERN.test(currentBoundary) ||
    NO_WORD_SEPARATOR_SCRIPT_PATTERN.test(nextBoundary)
  ) {
    return false;
  }

  const currentEndsWord = /[\p{L}\p{N}]$/u.test(currentBoundary);
  const nextStartsWord = /^[\p{L}\p{N}]/u.test(nextBoundary);
  if (currentEndsWord && nextStartsWord) {
    return true;
  }

  return /[,.!?;:]$/.test(current) && nextStartsWord;
}

function clearStreamingMessages() {
  clearTranscriptRole("user");
  clearTranscriptRole("assistant");
}

function clearTranscriptRole(role) {
  finalizeTranscriptRole(role);
}

function finalizeTranscriptRole(role) {
  const node = activeTranscriptNodes[role];
  if (node) {
    node.classList.remove("streaming");
  }
  transcriptBuffers[role] = "";
  activeTranscriptNodes[role] = null;
}

function createMessageNode(role) {
  setVoiceHeroVisible(false);

  const node = document.createElement("article");
  node.className = `message ${role}`;
  node.setAttribute("role", "article");

  if (role !== "system") {
    const meta = document.createElement("div");
    meta.className = "message-meta";
    meta.textContent = role === "user" ? "You" : "Northstar";
    node.appendChild(meta);
  }

  const body = document.createElement("div");
  body.className = "message-body";
  node.appendChild(body);
  conversation.appendChild(node);
  trimConversationHistory();
  refreshConversationMode();
  conversation.scrollTop = conversation.scrollHeight;
  return node;
}

function trimConversationHistory(limit = MAX_CONVERSATION_ITEMS) {
  const nodes = conversation.querySelectorAll(".message");
  if (nodes.length <= limit) {
    return;
  }

  const excess = nodes.length - limit;
  for (let i = 0; i < excess; i += 1) {
    nodes[i].remove();
  }
}

function addMessage(role, text) {
  const node = createMessageNode(role);
  setMessageBody(node, escapeHtml(text).replace(/\n/g, "<br>"), true);
}

function updateConnectionStatus(status) {
  const statusEl = document.getElementById("connectionStatus");
  const textEl = statusEl.querySelector(".status-text");
  liveConnectionStatus = status;

  if (status === "connected") {
    statusEl.classList.add("connected");
    textEl.textContent = "Connected";
    statusEl.setAttribute("aria-label", "Connection status: connected");
  } else {
    statusEl.classList.remove("connected");
    textEl.textContent = "Disconnected";
    statusEl.setAttribute("aria-label", "Connection status: disconnected");
  }

  syncVoiceButtons();
  if (status === "connected") {
    if (isListening) {
      updateStatusState("listening", "Listening to this tab");
    } else if (voiceSessionActive) {
      updateStatusState("idle", "Mic muted");
    } else {
      updateStatusState("connected", "Live ready");
    }
    updateVoiceHeroPrompt();
    return;
  }

  hideTaskApprovalBar();
  updateStatusState("error", "Live disconnected");
  if (!hasConversationMessages()) {
    setVoiceHeroVisible(true);
  }
  setVoiceHeroState(
    "error",
    "Northstar could not reach Gemini Live. Reload the page or extension, then try again."
  );
}

function updateStatus(data = {}) {
  const state = data.state;
  const labels = {
    idle: voiceSessionActive && !isListening ? "Mic muted" : "Ready",
    thinking: "Thinking...",
    acting: `Acting: ${data.action || ""}`.trim(),
    listening: "Listening...",
    page_received: "Current tab synced",
    connected: "Live ready",
    error: data.message || "Error",
  };
  updateStatusState(state || "idle", labels[state] || data.message || "Ready");
}

function updateStatusState(state, text) {
  baseUiState = normalizeUiState(state);
  if (statusText) {
    statusText.textContent = text;
  }
  if (voiceHeroStatus && voiceHero.classList.contains("compact")) {
    voiceHeroStatus.textContent = text;
  }
  renderUiState();
  announce(text, state === "error" ? "assertive" : "polite");
}

function restoreLiveStatusState() {
  if (liveConnectionStatus !== "connected") {
    updateStatusState("error", "Live disconnected");
    return;
  }
  if (isListening) {
    updateStatusState("listening", "Listening to this tab");
    return;
  }
  if (voiceSessionActive) {
    updateStatusState("idle", "Mic muted");
    return;
  }
  updateStatusState("connected", "Live ready");
}

function handleBrowserTaskStatus(data = {}) {
  const taskStatus = String(data.status || "").trim();
  const message = String(data.message || "").trim();
  const userQuestion = String(data.user_question || "").trim();
  const continuationAvailable = Boolean(data.continuation_available);

  if (!taskStatus) {
    hideTaskApprovalBar();
    if (message) {
      updateStatusState("acting", message);
    }
    return;
  }

  if (taskStatus === "completed" || taskStatus === "failed" || taskStatus === "cancelled") {
    hideTaskApprovalBar();
    restoreLiveStatusState();
    return;
  }

  if (taskStatus === "needs_input") {
    if (continuationAvailable) {
      showTaskApprovalBar(userQuestion || message || "Northstar needs your approval to continue.");
    } else {
      hideTaskApprovalBar();
    }
    if (liveConnectionStatus === "connected") {
      updateStatusState(
        "listening",
        continuationAvailable
          ? "Waiting for your approval to continue"
          : "Listening for your answer"
      );
    } else {
      restoreLiveStatusState();
    }
    return;
  }

  if (taskStatus === "started" || taskStatus === "in_progress" || taskStatus === "retry") {
    hideTaskApprovalBar();
    updateStatusState("acting", message || "Working on it...");
    return;
  }

  hideTaskApprovalBar();
  if (message) {
    updateStatusState("acting", message);
  } else {
    restoreLiveStatusState();
  }
}

function showTaskApprovalBar(text) {
  if (!taskApprovalBar || !taskApprovalText) {
    return;
  }
  taskApprovalText.textContent = text || "Northstar needs your approval to continue.";
  taskApprovalBar.hidden = false;
}

function hideTaskApprovalBar() {
  if (!taskApprovalBar) {
    return;
  }
  taskApprovalBar.hidden = true;
}

function refreshConversationMode() {
  const hasHistory = hasConversationMessages();
  conversation.classList.toggle("empty", !hasHistory);
  if (conversationEmptyState) {
    conversationEmptyState.hidden = hasHistory;
  }
  voiceHero.classList.toggle("compact", hasHistory || !heroExpanded || keyboardVisible || voiceSessionActive);
  appShell.classList.toggle("has-history", hasHistory);
  requestAnimationFrame(() => {
    resizePersona();
  });
}

function hasConversationMessages() {
  return Boolean(conversation.querySelector(".message"));
}

function setVoiceHeroVisible(visible) {
  heroExpanded = visible;
  refreshConversationMode();
}

function setVoiceHeroState(state, text) {
  voiceHero.dataset.tone = state;
  voiceHeroStatus.textContent = text;
}

function setKeyboardVisible(visible) {
  keyboardVisible = visible;
  appShell.classList.toggle("keyboard-open", visible);
  textInputWrapper.hidden = !visible;
  quickActions.hidden = !visible;
  showKeyboardButton.textContent = visible ? "Hide Keyboard" : "Keyboard";
  showKeyboardButton.setAttribute("aria-expanded", String(visible));
  refreshConversationMode();

  if (visible) {
    queueMicrotask(() => textInput.focus());
  }
}

function syncVoiceButtons() {
  let label = "Start Voice";
  let mode = "start";

  if (isRequestingMicrophone) {
    label = "Approve Mic";
  } else if (voiceSessionActive && isListening) {
    label = "Mute";
    mode = "mute";
  } else if (voiceSessionActive) {
    label = "Unmute";
    mode = "unmute";
  }

  startVoiceButton.textContent = label;
  startVoiceButton.dataset.mode = mode;
  startVoiceButton.setAttribute("aria-pressed", String(Boolean(voiceSessionActive)));
  startVoiceButton.disabled =
    isRequestingMicrophone ||
    (liveConnectionStatus !== "connected" && (!voiceSessionActive || !isListening));

  endVoiceButton.hidden = !voiceSessionActive;
  endVoiceButton.disabled = false;
}

async function syncMicrophonePermissionState() {
  if (!navigator.permissions?.query) {
    updateVoiceHeroPrompt();
    return;
  }

  try {
    const status = await navigator.permissions.query({ name: "microphone" });
    microphonePermissionState = status.state || "unknown";
  } catch {
    microphonePermissionState = "unknown";
  }

  if (isRequestingMicrophone && microphonePermissionState === "granted" && !isListening) {
    isRequestingMicrophone = false;
    syncVoiceButtons();
    try {
      await startLocalMicrophoneCapture();
      return;
    } catch (error) {
      handleVoiceCaptureFailure(error?.message || "Microphone access failed.", {
        reason: error?.name === "NotAllowedError" ? "blocked" : "capture_failed",
      });
      return;
    }
  }

  if (isRequestingMicrophone && microphonePermissionState === "denied") {
    isRequestingMicrophone = false;
    syncVoiceButtons();
  }

  updateVoiceHeroPrompt();
}

async function getActiveTab() {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  return tab || null;
}

function updateScopeFromTab(tab) {
  const title = tab?.title?.trim();
  scopeTitle.textContent = title || "No active page";
}

async function syncToActiveTabContext() {
  const tab = await getActiveTab();
  if (!tab?.id) {
    throw new Error("No active browser tab is available.");
  }

  const nextTabId = tab.id;
  const previousTabId = currentTabId;
  if (previousTabId && previousTabId !== nextTabId) {
    disconnectFromBackend();
    clearStreamingMessages();
  }

  currentTabId = nextTabId;
  updateScopeFromTab(tab);

  const result = await chrome.runtime.sendMessage({
    type: "refresh_context",
    tabId: currentTabId,
  });

  if (!result?.ok) {
    throw new Error(result?.error || "Failed to refresh the current tab context.");
  }

  return currentTabId;
}

function updateVoiceHeroPrompt() {
  if (isRequestingMicrophone) {
    setVoiceHeroState(
      "idle",
      "Allow the microphone in the setup tab. Northstar will bring you back here automatically."
    );
    return;
  }

  if (liveConnectionStatus !== "connected") {
    setVoiceHeroState("idle", "Connecting to Gemini Live.");
    return;
  }

  if (isListening) {
    setVoiceHeroState(
      "listening",
      "I am listening to the current tab. Ask what is here or tell me what to do."
    );
    return;
  }

  if (voiceSessionActive) {
    setVoiceHeroState(
      "idle",
      "The voice session is still open. Unmute when you are ready for the next turn."
    );
    return;
  }

  if (microphonePermissionState === "denied") {
    setVoiceHeroState(
      "error",
      "Microphone permission is blocked. Re-enable it in Chrome, then run voice setup again."
    );
    return;
  }

  if (keyboardVisible) {
    setVoiceHeroState(
      "idle",
      "Voice is ready when you want it. The keyboard is open below."
    );
    return;
  }

  if (microphonePermissionState === "granted") {
    setVoiceHeroState(
      "idle",
      "Start voice to talk hands-free with the current tab."
    );
    return;
  }

  setVoiceHeroState(
    "idle",
    "Start voice. Northstar will open a setup tab for Chrome's one-time microphone approval."
  );
}

function showDiagnosis(text) {
  if (liveSettingsDialog && !liveSettingsDialog.hidden) {
    closeLiveSettingsDialog();
  }
  renderDiagnosisContent(text);
  previouslyFocusedElement = document.activeElement instanceof HTMLElement ? document.activeElement : null;
  diagnosisPanel.hidden = false;
  syncDialogBodyState();
  announce("Accessibility diagnosis opened.", "polite");
  queueMicrotask(() => closeDiagnosis.focus());
}

closeDiagnosis.addEventListener("click", () => {
  closeDiagnosisPanel();
});

function closeDiagnosisPanel() {
  if (diagnosisPanel.hidden) {
    return;
  }

  diagnosisPanel.hidden = true;
  syncDialogBodyState();
  if (previouslyFocusedElement?.isConnected) {
    previouslyFocusedElement.focus();
  }
  previouslyFocusedElement = null;
}

function showLiveSettingsDialog() {
  if (!liveSettingsDialog || !closeLiveSettingsButton) {
    return;
  }
  if (diagnosisPanel && !diagnosisPanel.hidden) {
    closeDiagnosisPanel();
  }
  previouslyFocusedElement = document.activeElement instanceof HTMLElement ? document.activeElement : null;
  liveSettingsDialog.hidden = false;
  openLiveSettingsButton?.setAttribute("aria-expanded", "true");
  syncDialogBodyState();
  announce("Northstar settings opened.", "polite");
  queueMicrotask(() => closeLiveSettingsButton.focus());
}

function closeLiveSettingsDialog() {
  if (!liveSettingsDialog || liveSettingsDialog.hidden) {
    return;
  }

  liveSettingsDialog.hidden = true;
  openLiveSettingsButton?.setAttribute("aria-expanded", "false");
  syncDialogBodyState();
  if (previouslyFocusedElement?.isConnected) {
    previouslyFocusedElement.focus();
  }
  previouslyFocusedElement = null;
}

function getOpenDialog() {
  if (liveSettingsDialog && !liveSettingsDialog.hidden) {
    return liveSettingsDialog;
  }
  if (diagnosisPanel && !diagnosisPanel.hidden) {
    return diagnosisPanel;
  }
  return null;
}

function syncDialogBodyState() {
  document.body.classList.toggle("dialog-open", Boolean(getOpenDialog()));
}

function handleGlobalKeydown(event) {
  const openDialog = getOpenDialog();

  if (event.key === "Escape" && openDialog) {
    event.preventDefault();
    if (openDialog === liveSettingsDialog) {
      closeLiveSettingsDialog();
    } else {
      closeDiagnosisPanel();
    }
    return;
  }

  if (event.key !== "Tab" || !openDialog) {
    return;
  }

  const focusable = getFocusableElements(openDialog);
  if (!focusable.length) {
    event.preventDefault();
    return;
  }

  const first = focusable[0];
  const last = focusable[focusable.length - 1];

  if (event.shiftKey && document.activeElement === first) {
    event.preventDefault();
    last.focus();
  } else if (!event.shiftKey && document.activeElement === last) {
    event.preventDefault();
    first.focus();
  }
}

function getFocusableElements(root) {
  if (!root) {
    return [];
  }

  return Array.from(
    root.querySelectorAll(
      'button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])'
    )
  ).filter((element) => !element.hidden && !element.closest("[hidden]"));
}

function announce(text, politeness = "polite") {
  if (!panelAnnouncements || !text) {
    return;
  }

  panelAnnouncements.setAttribute("aria-live", politeness);
  panelAnnouncements.textContent = "";
  requestAnimationFrame(() => {
    panelAnnouncements.textContent = text;
  });
}

function renderDiagnosisContent(text = "") {
  diagnosisContent.replaceChildren();
  const lines = String(text).split(/\r?\n/);
  let currentList = null;

  for (const rawLine of lines) {
    const line = rawLine.trim();
    if (!line) {
      currentList = null;
      continue;
    }

    const markdownHeading = line.match(/^#{1,6}\s+(.+)$/);
    if (markdownHeading) {
      const heading = document.createElement("h3");
      heading.textContent = markdownHeading[1].trim();
      diagnosisContent.appendChild(heading);
      currentList = null;
      continue;
    }

    const emphasisHeading = line.match(/^\*\*(.+?)\*\*:?\s*$/);
    if (emphasisHeading) {
      const heading = document.createElement("h3");
      heading.textContent = emphasisHeading[1].replace(/:$/, "").trim();
      diagnosisContent.appendChild(heading);
      currentList = null;
      continue;
    }

    const bullet = line.match(/^-\s+(.+)$/);
    if (bullet) {
      if (!currentList) {
        currentList = document.createElement("ul");
        diagnosisContent.appendChild(currentList);
      }
      const item = document.createElement("li");
      appendInlineContent(item, bullet[1]);
      currentList.appendChild(item);
      continue;
    }

    const paragraph = document.createElement("p");
    appendInlineContent(paragraph, line);
    diagnosisContent.appendChild(paragraph);
    currentList = null;
  }
}

function appendInlineContent(container, text) {
  const segments = String(text).split(/(\*\*[^*]+\*\*)/g).filter(Boolean);

  for (const segment of segments) {
    const match = segment.match(/^\*\*([^*]+)\*\*$/);
    if (match) {
      const strong = document.createElement("strong");
      strong.textContent = match[1];
      container.appendChild(strong);
    } else {
      container.appendChild(document.createTextNode(segment));
    }
  }
}

function escapeHtml(text) {
  const div = document.createElement("div");
  div.textContent = text;
  return div.innerHTML;
}

function base64ToArrayBuffer(base64) {
  const binary = atob(base64);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i += 1) {
    bytes[i] = binary.charCodeAt(i);
  }
  return bytes.buffer;
}

function parsePcmRate(mimeType) {
  const match = /rate=(\d+)/i.exec(mimeType || "");
  return match ? Number(match[1]) : 0;
}

function handleVoiceCaptureFailure(errorMessage, details = {}) {
  if (details?.reason === "blocked") {
    microphonePermissionState = "denied";
    setKeyboardVisible(true);
  }

  isRequestingMicrophone = false;
  clearAssistantSpeaking();
  syncVoiceButtons();
  setVoiceHeroVisible(true);
  setVoiceHeroState(
    "error",
    errorMessage || "Microphone access failed. Check Chrome permissions and try again."
  );
  updateStatusState(
    "error",
    details?.reason === "blocked" ? "Microphone blocked" : "Microphone failed"
  );
}

async function startLocalMicrophoneCapture() {
  if (isListening || captureStream || captureContext) {
    return;
  }

  captureStream = await navigator.mediaDevices.getUserMedia({
    audio: {
      channelCount: 1,
      echoCancellation: true,
      noiseSuppression: true,
      autoGainControl: true,
    },
  });
  captureContext = new AudioContext();
  await captureContext.resume();

  captureSource = captureContext.createMediaStreamSource(captureStream);
  captureProcessor = captureContext.createScriptProcessor(4096, 1, 1);
  captureSink = captureContext.createGain();
  captureSink.gain.value = 0;
  resetSpeechActivityState();

  captureProcessor.onaudioprocess = (event) => {
    if (!isListening || !captureContext) return;
    const input = event.inputBuffer.getChannelData(0);
    const pcmBytes = downsampleToPcm16(input, captureContext.sampleRate, 16000);
    if (!pcmBytes.byteLength) return;

    handleMicrophoneChunk(pcmBytes);
  };

  captureSource.connect(captureProcessor);
  captureProcessor.connect(captureSink);
  captureSink.connect(captureContext.destination);

  isRequestingMicrophone = false;
  isListening = true;
  voiceSessionActive = true;
  microphonePermissionState = "granted";
  clearTranscriptRole("user");
  syncVoiceButtons();
  setVoiceHeroVisible(false);
  updateStatusState("listening", "Listening to this tab");
  updateVoiceHeroPrompt();
}

async function openMicrophonePermissionTab() {
  isRequestingMicrophone = true;
  syncVoiceButtons();
  setVoiceHeroVisible(true);
  setVoiceHeroState(
    "idle",
    "Allow the microphone in the setup tab. Northstar will bring you back here automatically."
  );
  updateStatusState("thinking", "Waiting for microphone setup...");
  const url = chrome.runtime.getURL(
    `mic-permission/index.html?tabId=${encodeURIComponent(String(currentTabId || ""))}`
  );
  await chrome.tabs.create({ url, active: true });
}

async function handleMicrophonePermissionResult(data = {}) {
  if (data.tabId && Number(data.tabId) !== Number(currentTabId)) {
    return;
  }

  isRequestingMicrophone = false;
  syncVoiceButtons();

  if (!data.granted) {
    microphonePermissionState = data.reason === "blocked" ? "denied" : "unknown";
    handleVoiceCaptureFailure(
      data.error || "Microphone access failed. Check Chrome permissions and try again.",
      { reason: data.reason }
    );
    return;
  }

  try {
    await syncMicrophonePermissionState();
    await startLocalMicrophoneCapture();
  } catch (error) {
    handleVoiceCaptureFailure(error?.message || "Microphone access failed.", {
      reason: error?.name === "NotAllowedError" ? "blocked" : "capture_failed",
    });
  }
}

function downsampleToPcm16(float32Samples, inputRate, outputRate) {
  if (inputRate === outputRate) {
    const pcm = new Int16Array(float32Samples.length);
    for (let i = 0; i < float32Samples.length; i += 1) {
      const sample = Math.max(-1, Math.min(1, float32Samples[i]));
      pcm[i] = sample < 0 ? sample * 0x8000 : sample * 0x7fff;
    }
    return pcm;
  }

  const ratio = inputRate / outputRate;
  const length = Math.round(float32Samples.length / ratio);
  const pcm = new Int16Array(length);

  for (let i = 0; i < length; i += 1) {
    const start = Math.floor(i * ratio);
    const end = Math.min(float32Samples.length, Math.floor((i + 1) * ratio));
    let sum = 0;
    let count = 0;

    for (let j = start; j < end; j += 1) {
      sum += float32Samples[j];
      count += 1;
    }

    const sample = count ? sum / count : 0;
    const clamped = Math.max(-1, Math.min(1, sample));
    pcm[i] = clamped < 0 ? clamped * 0x8000 : clamped * 0x7fff;
  }

  return pcm;
}

function arrayBufferToBase64(arrayBuffer) {
  const bytes = new Uint8Array(arrayBuffer);
  let binary = "";
  for (let i = 0; i < bytes.length; i += 1) {
    binary += String.fromCharCode(bytes[i]);
  }
  return btoa(binary);
}

function handleMicrophoneChunk(pcmBytes) {
  const rms = computeRmsLevel(pcmBytes);
  const now = performance.now();
  const base64 = arrayBufferToBase64(pcmBytes.buffer);
  updatePersonaWaveform("input", rms);

  const inputRing = document.getElementById("inputRing");
  if (inputRing && !voiceOrbShell?.classList.contains("has-persona")) {
    const scale = 1 + Math.min(rms * 40, 0.4); // max scale 1.4
    inputRing.style.transform = `scale(${scale})`;
    inputRing.style.opacity = Math.min(0.2 + rms * 10, 1).toFixed(2); // between 0.2 and 1
  }

  if (rms >= SPEECH_THRESHOLD) {
    speechLastDetectedAt = now;
    if (!speechActivityActive) {
      speechActivityActive = true;
      chrome.runtime.sendMessage({ type: "live_activity_start", tabId: currentTabId });
      for (const bufferedChunk of speechLeadInChunks) {
        sendLiveAudioChunk(bufferedChunk);
      }
      speechLeadInChunks = [];
    }

    sendLiveAudioChunk(base64);
    return;
  }

  if (speechActivityActive) {
    if (now - speechLastDetectedAt <= SPEECH_SILENCE_HANG_MS) {
      sendLiveAudioChunk(base64);
      return;
    }

    chrome.runtime.sendMessage({ type: "live_activity_end", tabId: currentTabId });
    resetSpeechActivityState();
    return;
  }

  speechLeadInChunks.push(base64);
  if (speechLeadInChunks.length > SPEECH_LEAD_IN_CHUNKS) {
    speechLeadInChunks.shift();
  }
}

function sendLiveAudioChunk(base64) {
  chrome.runtime.sendMessage({
    type: "live_audio_chunk",
    tabId: currentTabId,
    data: base64,
    mimeType: "audio/pcm;rate=16000",
  });
}

function resetSpeechActivityState() {
  speechActivityActive = false;
  speechLastDetectedAt = 0;
  speechLeadInChunks = [];
}

function computeRmsLevel(pcmBytes) {
  if (!pcmBytes?.length) {
    return 0;
  }

  let sumSquares = 0;
  for (let i = 0; i < pcmBytes.length; i += 1) {
    const sample = pcmBytes[i] / 32768;
    sumSquares += sample * sample;
  }
  return Math.sqrt(sumSquares / pcmBytes.length);
}

function setMessageBody(node, text, allowHtml) {
  const body = node.querySelector(".message-body") || node;
  if (allowHtml) {
    body.innerHTML = text;
  } else {
    body.textContent = text;
  }
}

function normalizeUiState(state) {
  if (state === "connected" || state === "page_received" || state === "idle") {
    return "ready";
  }
  if (state === "listening" || state === "thinking" || state === "acting" || state === "error") {
    return state;
  }
  return "ready";
}

function renderUiState() {
  const visibleState = isAssistantSpeaking ? "speaking" : baseUiState;
  appShell.dataset.uiState = visibleState;
  if (statusBar) {
    statusBar.dataset.state = visibleState;
  }
  voiceHero.dataset.state = visibleState;
  if (heroStateBadge) {
    heroStateBadge.textContent = getUiStateLabel(visibleState);
  }
  syncPersonaState();
}

function getUiStateLabel(state) {
  const labels = {
    ready: "Ready",
    listening: "Listening",
    thinking: "Thinking",
    acting: "Acting",
    speaking: "Speaking",
    error: "Attention",
  };
  return labels[state] || "Ready";
}

function markAssistantSpeaking(durationMs = 1000) {
  isAssistantSpeaking = true;
  renderUiState();
  clearTimeout(speakingStateTimer);
  speakingStateTimer = setTimeout(() => {
    isAssistantSpeaking = false;
    renderUiState();
  }, durationMs);
}

function clearAssistantSpeaking() {
  isAssistantSpeaking = false;
  clearTimeout(speakingStateTimer);
  speakingStateTimer = null;
  renderUiState();
}

init();
