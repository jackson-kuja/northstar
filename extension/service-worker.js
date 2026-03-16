/**
 * Northstar Service Worker
 * Manages the backend session, screenshot capture, and routing between the
 * side panel, content script, and backend.
 */

import {
  loadLiveSettings,
  normalizeLiveSettings,
  saveLiveSettings,
} from "./shared/live-settings.js";

const BACKEND_URL = "ws://localhost:8080/ws";
const activeConnections = {};

chrome.action.onClicked.addListener(async (tab) => {
  await chrome.sidePanel.open({ tabId: tab.id });
});

chrome.sidePanel.setPanelBehavior({ openPanelOnActionClick: true });

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  const handler = messageHandlers[message.type];
  if (!handler) {
    return false;
  }
  handler(message, sender, sendResponse);
  return true;
});

const messageHandlers = {
  page_state: async (message, sender) => {
    const tabId = sender.tab?.id;
    if (tabId && activeConnections[tabId]) {
      activeConnections[tabId].sendToBackend({
        type: "page_state",
        data: message.data,
      });
    }
  },

  action_result: async (message, sender) => {
    const tabId = sender.tab?.id;
    if (!tabId || !activeConnections[tabId]) return;

    const screenshot = await captureVisibleTabSnapshot(tabId);
    activeConnections[tabId].sendToBackend({
      type: "action_result",
      data: {
        ...message.data,
        screenshot,
      },
    });
  },

  connect_session: async (message) => {
    const tabId = message.tabId;
    if (!tabId) return;
    await ensureBackendConnection(tabId, { forceReconnect: true });
  },

  get_live_settings: async (_message, _sender, sendResponse) => {
    try {
      const settings = await loadLiveSettings();
      sendResponse?.({ ok: true, settings });
    } catch (error) {
      sendResponse?.({ ok: false, error: error?.message || "Failed to load Northstar settings." });
    }
  },

  update_live_settings: async (message, _sender, sendResponse) => {
    try {
      const settings = await saveLiveSettings(message.data || {});
      await broadcastLiveSettings(settings);
      chrome.runtime.sendMessage({ type: "live_settings_updated", settings }).catch(() => {});
      sendResponse?.({ ok: true, settings });
    } catch (error) {
      sendResponse?.({ ok: false, error: error?.message || "Failed to save Northstar settings." });
    }
  },

  user_message: async (message) => {
    const tabId = message.tabId;
    if (tabId && activeConnections[tabId]) {
      activeConnections[tabId].sendToBackend({
        type: "user_message",
        text: message.text,
      });
    }
  },

  live_audio_chunk: async (message) => {
    const tabId = message.tabId;
    if (tabId && activeConnections[tabId]) {
      activeConnections[tabId].sendToBackend({
        type: "live_audio_chunk",
        data: message.data,
        mimeType: message.mimeType,
      });
    }
  },

  live_activity_start: async (message) => {
    const tabId = message.tabId;
    if (tabId && activeConnections[tabId]) {
      activeConnections[tabId].sendToBackend({
        type: "live_activity_start",
      });
    }
  },

  live_activity_end: async (message) => {
    const tabId = message.tabId;
    if (tabId && activeConnections[tabId]) {
      activeConnections[tabId].sendToBackend({
        type: "live_activity_end",
      });
    }
  },

  live_end: async (message) => {
    const tabId = message.tabId;
    if (tabId && activeConnections[tabId]) {
      activeConnections[tabId].sendToBackend({
        type: "live_end",
      });
    }
  },

  live_stop: async (message) => {
    const tabId = message.tabId;
    if (tabId && activeConnections[tabId]) {
      activeConnections[tabId].sendToBackend({
        type: "live_stop",
      });
    }
  },

  request_screenshot: async (message) => {
    const tabId = message.tabId;
    if (!tabId || !activeConnections[tabId]) return;

    try {
      const screenshot = await captureVisibleTabSnapshot(tabId);
      activeConnections[tabId].sendToBackend({
        type: "screenshot",
        data: screenshot,
      });
    } catch (error) {
      console.error("Screenshot capture failed:", error);
    }
  },

  request_page_map: async (message) => {
    const tabId = message.tabId;
    try {
      await requestPageMap(tabId);
    } catch (error) {
      console.error("Page map extraction failed:", error);
    }
  },

  refresh_context: async (message, sender, sendResponse) => {
    const tabId = message.tabId;
    if (!tabId) {
      sendResponse?.({ ok: false, error: "Missing tabId" });
      return;
    }

    try {
      await ensureBackendConnection(tabId);
      await requestInitialContext(tabId);
      sendResponse?.({ ok: true });
    } catch (error) {
      console.error("[Northstar] Context refresh failed:", error);
      sendResponse?.({ ok: false, error: error.message });
    }
  },

  disconnect_session: async (message) => {
    const tabId = message.tabId;
    if (activeConnections[tabId]) {
      activeConnections[tabId].ws?.close();
      delete activeConnections[tabId];
    }
  },
};

function createBackendConnection(tabId, sessionId) {
  let ws = null;
  let reconnectAttempts = 0;
  const maxReconnectAttempts = 5;
  let openResolve = null;
  let openReject = null;

  const resetOpenPromise = () =>
    new Promise((resolve, reject) => {
      openResolve = resolve;
      openReject = reject;
    });

  let openPromise = resetOpenPromise();

  const connection = {
    ws: null,
    openPromise,

    connect() {
      const backendHost =
        typeof globalThis.__NORTHSTAR_BACKEND__ !== "undefined"
          ? globalThis.__NORTHSTAR_BACKEND__
          : BACKEND_URL;

      ws = new WebSocket(`${backendHost}/${sessionId}`);
      connection.ws = ws;

      ws.onopen = async () => {
        reconnectAttempts = 0;
        openResolve?.();
        connection.openPromise = Promise.resolve();
        broadcastToSidePanel(tabId, {
          type: "connection_status",
          status: "connected",
          sessionId,
        });

        const liveSettings = await loadLiveSettings();
        connection.sendToBackend({ type: "live_settings", data: liveSettings });
        connection.sendToBackend({ type: "live_start" });
        await requestInitialContext(tabId);
      };

      ws.onmessage = (event) => {
        try {
          handleBackendMessage(tabId, JSON.parse(event.data));
        } catch (error) {
          console.error("[Northstar] Failed to parse backend message:", error);
        }
      };

      ws.onclose = () => {
        openReject?.(new Error("WebSocket closed"));
        connection.openPromise = resetOpenPromise();
        broadcastToSidePanel(tabId, {
          type: "connection_status",
          status: "disconnected",
        });

        if (reconnectAttempts < maxReconnectAttempts) {
          reconnectAttempts += 1;
          setTimeout(() => connection.connect(), 1000 * reconnectAttempts);
        }
      };

      ws.onerror = (error) => {
        openReject?.(error instanceof Error ? error : new Error("WebSocket error"));
        console.error("[Northstar] WebSocket error:", error);
      };
    },

    sendToBackend(data) {
      if (ws && ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify(data));
      }
    },
  };

  return connection;
}

async function broadcastLiveSettings(settings) {
  const normalized = normalizeLiveSettings(settings);
  for (const connection of Object.values(activeConnections)) {
    connection.sendToBackend({
      type: "live_settings",
      data: normalized,
    });
  }
}

function handleBackendMessage(tabId, message) {
  switch (message.type) {
    case "assistant_message":
    case "status":
    case "diagnosis":
    case "live_audio_output":
    case "live_transcript":
    case "browser_task_status":
      broadcastToSidePanel(tabId, message);
      break;

    case "action":
      executeAction(tabId, message.data);
      break;
  }
}

async function executeAction(tabId, action) {
  broadcastToSidePanel(tabId, {
    type: "status",
    data: { state: "acting", action: action.name },
  });

  try {
    await sendMessageToContentScript(tabId, {
      type: "execute_action",
      action,
    });
  } catch (error) {
    console.error("[Northstar] Action execution failed:", error);
    if (isNavigationChannelClosure(error, action)) {
      try {
        const result = await waitForNavigationActionResult(tabId, action);
        sendActionResultToBackend(tabId, result);
        return;
      } catch (navigationError) {
        console.error("[Northstar] Navigation follow-up failed:", navigationError);
        sendActionResultToBackend(tabId, {
          action: action.name,
          success: false,
          error: navigationError.message,
          page_state: {},
        });
        return;
      }
    }

    sendActionResultToBackend(tabId, {
      action: action.name,
      success: false,
      error: error.message,
      page_state: {},
    });
  }
}

function sendActionResultToBackend(tabId, data) {
  if (activeConnections[tabId]) {
    activeConnections[tabId].sendToBackend({
      type: "action_result",
      data,
    });
  }
}

function isNavigationChannelClosure(error, action) {
  const message = String(error?.message || "");
  if (!message.includes("message channel is closed")) {
    return false;
  }

  if (message.includes("back/forward cache")) {
    return true;
  }

  return ["navigate", "click", "click_at"].includes(action?.name);
}

async function waitForNavigationActionResult(tabId, action, timeoutMs = 15000) {
  return await new Promise((resolve, reject) => {
    let settled = false;

    const cleanup = () => {
      chrome.tabs.onUpdated.removeListener(handleUpdated);
      clearTimeout(timeoutId);
    };

    const finish = (fn, value) => {
      if (settled) return;
      settled = true;
      cleanup();
      fn(value);
    };

    const timeoutId = setTimeout(() => {
      finish(reject, new Error("Timed out waiting for the new page to finish loading."));
    }, timeoutMs);

    const handleUpdated = async (updatedTabId, changeInfo) => {
      if (updatedTabId !== tabId || changeInfo.status !== "complete") {
        return;
      }

      try {
        const tab = await chrome.tabs.get(tabId);
        const pageMap = await requestPageMap(tabId).catch(() => null);
        const screenshot = await captureVisibleTabSnapshot(tabId).catch(() => null);

        finish(resolve, {
          action: action.name,
          success: true,
          page_state: pageMap || {
            url: tab.url || "",
            title: tab.title || "",
          },
          screenshot,
        });
      } catch (error) {
        finish(reject, error);
      }
    };

    chrome.tabs.onUpdated.addListener(handleUpdated);
  });
}

function broadcastToSidePanel(tabId, message) {
  chrome.runtime.sendMessage({ ...message, tabId }).catch(() => {});
}

async function requestInitialContext(tabId) {
  await ensureBackendConnection(tabId);

  try {
    const pageMap = await requestPageMap(tabId);
    if (pageMap && activeConnections[tabId]) {
      activeConnections[tabId].sendToBackend({
        type: "page_state",
        data: pageMap,
      });
    }
  } catch (error) {
    console.error("Initial page map extraction failed:", error);
  }

  try {
    const screenshot = await captureVisibleTabSnapshot(tabId);
    if (activeConnections[tabId]) {
      activeConnections[tabId].sendToBackend({
        type: "screenshot",
        data: screenshot,
      });
    }
  } catch (error) {
    console.error("Initial screenshot capture failed:", error);
  }
}

async function requestPageMap(tabId) {
  const response = await sendMessageToContentScript(tabId, { type: "extract_page_map" });
  return response?.data || null;
}

async function captureVisibleTabSnapshot(tabId) {
  const tab = await chrome.tabs.get(tabId);
  const dataUrl = await chrome.tabs.captureVisibleTab(tab.windowId, {
    format: "png",
  });
  const base64 = dataUrl.replace(/^data:image\/png;base64,/, "");
  const dimensions = await getImageDimensions(dataUrl);

  return {
    data: base64,
    mimeType: "image/png",
    width: dimensions.width,
    height: dimensions.height,
  };
}

async function getImageDimensions(dataUrl) {
  try {
    const response = await fetch(dataUrl);
    const blob = await response.blob();
    const bitmap = await createImageBitmap(blob);
    return { width: bitmap.width, height: bitmap.height };
  } catch {
    return { width: 0, height: 0 };
  }
}

chrome.tabs.onRemoved.addListener((tabId) => {
  if (activeConnections[tabId]) {
    activeConnections[tabId].ws?.close();
    delete activeConnections[tabId];
  }
});

chrome.tabs.onUpdated.addListener((tabId, changeInfo) => {
  if (changeInfo.status === "complete" && activeConnections[tabId]) {
    requestInitialContext(tabId).catch(() => {});
  }
});

async function sendMessageToContentScript(tabId, message) {
  try {
    return await chrome.tabs.sendMessage(tabId, message);
  } catch (error) {
    if (!isMissingReceiverError(error)) {
      throw error;
    }

    await ensureContentScript(tabId);
    return await chrome.tabs.sendMessage(tabId, message);
  }
}

async function ensureContentScript(tabId) {
  const tab = await chrome.tabs.get(tabId);
  if (!tab.url || !/^https?:/i.test(tab.url)) {
    throw new Error(`Cannot inject content script into tab URL: ${tab.url || "unknown"}`);
  }

  await chrome.scripting.executeScript({
    target: { tabId },
    files: ["content/content.js"],
  });
}

function isMissingReceiverError(error) {
  return String(error?.message || "").includes("Receiving end does not exist");
}

async function ensureBackendConnection(tabId, { forceReconnect = false } = {}) {
  let connection = activeConnections[tabId];

  if (forceReconnect && connection) {
    connection.ws?.close();
    connection = null;
    delete activeConnections[tabId];
  }

  if (!connection) {
    const sessionId = `session_${tabId}_${Date.now()}`;
    connection = createBackendConnection(tabId, sessionId);
    activeConnections[tabId] = connection;
    connection.connect();
  }

  if (connection.ws?.readyState === WebSocket.OPEN) {
    return connection;
  }

  await connection.openPromise;
  return connection;
}
