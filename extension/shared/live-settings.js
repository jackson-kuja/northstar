export const LIVE_SETTINGS_STORAGE_KEY = "northstarLiveSettings";

export const LIVE_VOICE_OPTIONS = [
  "Aoede",
  "Achernar",
  "Achird",
  "Algenib",
  "Algieba",
  "Alnilam",
  "Autonoe",
  "Callirrhoe",
  "Charon",
  "Despina",
  "Enceladus",
  "Erinome",
  "Fenrir",
  "Gacrux",
  "Iapetus",
  "Kore",
  "Laomedeia",
  "Leda",
  "Orus",
  "Pulcherrima",
  "Puck",
  "Rasalgethi",
  "Sadachbia",
  "Sadaltager",
  "Schedar",
  "Sulafat",
  "Umbriel",
  "Vindemiatrix",
  "Zephyr",
  "Zubenelgenubi",
];

export const LIVE_SETTINGS_DEFAULTS = Object.freeze({
  voiceName: "",
  thinkingBudget: -1,
  allowInterruptions: true,
  enableInputTranscription: true,
  enableOutputTranscription: true,
});

const LIVE_THINKING_BUDGET_VALUES = Object.freeze([-1, 0, 1024]);

function normalizeString(value, { maxLength = 80 } = {}) {
  return String(value ?? "").trim().slice(0, maxLength);
}

function normalizeBoolean(value, fallback) {
  return typeof value === "boolean" ? value : fallback;
}

function normalizeIntegerChoice(value, fallback, allowedValues) {
  if (typeof value === "boolean") {
    return fallback;
  }

  const parsedValue =
    typeof value === "number"
      ? value
      : Number.parseInt(String(value ?? "").trim(), 10);

  if (!Number.isFinite(parsedValue) || !allowedValues.includes(parsedValue)) {
    return fallback;
  }

  return parsedValue;
}

function getThinkingBudgetLabel(thinkingBudget) {
  switch (thinkingBudget) {
    case 0:
      return "Fast replies";
    case 1024:
      return "More reasoning";
    default:
      return "Auto thinking";
  }
}

export function normalizeLiveSettings(input = {}) {
  return {
    voiceName: normalizeString(input.voiceName, { maxLength: 40 }),
    thinkingBudget: normalizeIntegerChoice(
      input.thinkingBudget,
      LIVE_SETTINGS_DEFAULTS.thinkingBudget,
      LIVE_THINKING_BUDGET_VALUES
    ),
    allowInterruptions: normalizeBoolean(
      input.allowInterruptions,
      LIVE_SETTINGS_DEFAULTS.allowInterruptions
    ),
    enableInputTranscription: normalizeBoolean(
      input.enableInputTranscription,
      LIVE_SETTINGS_DEFAULTS.enableInputTranscription
    ),
    enableOutputTranscription: normalizeBoolean(
      input.enableOutputTranscription,
      LIVE_SETTINGS_DEFAULTS.enableOutputTranscription
    ),
  };
}

export async function loadLiveSettings(storageArea = chrome.storage.local) {
  const stored = await storageArea.get(LIVE_SETTINGS_STORAGE_KEY);
  return normalizeLiveSettings(stored?.[LIVE_SETTINGS_STORAGE_KEY] || {});
}

export async function saveLiveSettings(settings, storageArea = chrome.storage.local) {
  const normalized = normalizeLiveSettings(settings);
  await storageArea.set({ [LIVE_SETTINGS_STORAGE_KEY]: normalized });
  return normalized;
}

export function getLiveSettingsSummary(settings) {
  const normalized = normalizeLiveSettings(settings);

  const transcriptLabel =
    normalized.enableInputTranscription && normalized.enableOutputTranscription
      ? "Both transcripts on"
      : normalized.enableInputTranscription
        ? "User transcript on"
        : normalized.enableOutputTranscription
          ? "Assistant transcript on"
          : "Transcripts off";

  const summaryParts = [
    normalized.voiceName || "Model default voice",
    getThinkingBudgetLabel(normalized.thinkingBudget),
  ];

  if (!normalized.allowInterruptions) {
    summaryParts.push("Interruptions off");
  }

  summaryParts.push(transcriptLabel);
  return summaryParts.join(" · ");
}
