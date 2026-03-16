const params = new URLSearchParams(window.location.search);
const sourceTabId = Number(params.get("tabId") || "0") || null;

const permissionCard = document.getElementById("permissionCard");
const enableButton = document.getElementById("enableButton");
const returnButton = document.getElementById("returnButton");
const status = document.getElementById("status");

enableButton.addEventListener("click", async () => {
  enableButton.disabled = true;
  setStatus("Chrome is opening the microphone prompt for this setup tab...", "working");

  try {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    stream.getTracks().forEach((track) => track.stop());

    await chrome.runtime.sendMessage({
      type: "microphone_permission_result",
      data: {
        tabId: sourceTabId,
        granted: true,
      },
      tabId: sourceTabId,
    });

    setStatus("Microphone enabled. Returning to Northstar...", "success");
    await restoreSourceTab();
    await closeCurrentTab();
  } catch (error) {
    const reason = classifyFailure(error);
    await chrome.runtime.sendMessage({
      type: "microphone_permission_result",
      data: {
        tabId: sourceTabId,
        granted: false,
        reason,
        error: describeFailure(error),
      },
      tabId: sourceTabId,
    });

    setStatus(describeFailure(error), "error");
    enableButton.disabled = false;
  }
});

returnButton?.addEventListener("click", async () => {
  await restoreSourceTab();
  await closeCurrentTab();
});

function classifyFailure(error) {
  if (["NotAllowedError", "SecurityError", "PermissionDeniedError"].includes(error?.name || "")) {
    return "blocked";
  }
  if (error?.name === "NotFoundError" || error?.name === "DevicesNotFoundError") {
    return "no_device";
  }
  return "capture_failed";
}

function describeFailure(error) {
  const reason = classifyFailure(error);
  if (reason === "blocked") {
    return "Chrome still does not have microphone permission. Check browser mic settings and try again.";
  }
  if (reason === "no_device") {
    return "No microphone was found. Connect a microphone and try again.";
  }
  return "Microphone access failed. Try again.";
}

function setStatus(text, kind) {
  status.textContent = text;
  status.dataset.kind = kind || "";
  permissionCard.dataset.state = ["working", "success", "error"].includes(kind) ? kind : "idle";
}

async function restoreSourceTab() {
  if (!sourceTabId) {
    return;
  }

  try {
    await chrome.tabs.update(sourceTabId, { active: true });
  } catch {}
}

async function closeCurrentTab() {
  try {
    const current = await chrome.tabs.getCurrent();
    if (current?.id) {
      await chrome.tabs.remove(current.id);
    }
  } catch {
    window.close();
  }
}
