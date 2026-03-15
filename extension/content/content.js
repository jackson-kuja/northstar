/**
 * Northstar Content Script
 * Extracts page accessibility data, executes actions, and manages overlays.
 */

(() => {
  "use strict";

  // Overlay management
  let highlightOverlay = null;

  // Listen for messages from service worker
  chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
    switch (message.type) {
      case "extract_page_map":
        const pageMap = extractPageMap();
        chrome.runtime.sendMessage({
          type: "page_state",
          data: pageMap,
        });
        sendResponse({ success: true, data: pageMap });
        break;

      case "execute_action":
        executeAction(message.action).then((result) => {
          chrome.runtime.sendMessage({
            type: "action_result",
            data: result,
          });
          sendResponse({ success: true });
        });
        break;
    }
    return true;
  });

  /**
   * Extract a comprehensive accessibility map of the current page.
   */
  function extractPageMap() {
    const pageMap = {
      url: window.location.href,
      title: document.title,
      viewport: {
        width: window.innerWidth,
        height: window.innerHeight,
        devicePixelRatio: window.devicePixelRatio || 1,
      },
      landmarks: extractLandmarks(),
      headings: extractHeadings(),
      forms: extractForms(),
      interactives: extractInteractiveElements(),
      images: extractImages(),
      liveRegions: extractLiveRegions(),
      focusedElement: getFocusedElement(),
      accessibilityIssues: detectAccessibilityIssues(),
      scrollPosition: {
        x: window.scrollX,
        y: window.scrollY,
        maxX: document.documentElement.scrollWidth - window.innerWidth,
        maxY: document.documentElement.scrollHeight - window.innerHeight,
      },
    };
    return pageMap;
  }

  function extractLandmarks() {
    const landmarks = [];
    const landmarkRoles = [
      "banner", "navigation", "main", "complementary",
      "contentinfo", "search", "form", "region",
    ];
    const landmarkTags = {
      header: "banner",
      nav: "navigation",
      main: "main",
      aside: "complementary",
      footer: "contentinfo",
    };

    // ARIA role landmarks
    landmarkRoles.forEach((role) => {
      document.querySelectorAll(`[role="${role}"]`).forEach((el) => {
        landmarks.push({
          role,
          label: el.getAttribute("aria-label") || el.getAttribute("aria-labelledby") || "",
          tag: el.tagName.toLowerCase(),
        });
      });
    });

    // Semantic HTML landmarks
    Object.entries(landmarkTags).forEach(([tag, role]) => {
      document.querySelectorAll(tag).forEach((el) => {
        if (!el.getAttribute("role")) {
          landmarks.push({
            role,
            label: el.getAttribute("aria-label") || "",
            tag,
          });
        }
      });
    });

    return landmarks;
  }

  function extractHeadings() {
    const headings = [];
    document.querySelectorAll("h1, h2, h3, h4, h5, h6").forEach((el) => {
      if (isVisible(el)) {
        headings.push({
          level: parseInt(el.tagName[1]),
          text: el.textContent.trim().substring(0, 200),
          selector: getUniqueSelector(el),
        });
      }
    });
    return headings;
  }

  function extractForms() {
    const forms = [];
    document.querySelectorAll("form").forEach((form, idx) => {
      const fields = [];
      form.querySelectorAll("input, select, textarea, [role='textbox'], [role='combobox'], [role='listbox'], [role='checkbox'], [role='radio'], [role='switch'], [role='slider'], [role='spinbutton']").forEach((field) => {
        if (!isVisible(field)) return;
        const label = getFieldLabel(field);
        const rect = field.getBoundingClientRect();
        fields.push({
          type: field.type || field.getAttribute("role") || "text",
          label,
          value: field.value || "",
          required: field.required || field.getAttribute("aria-required") === "true",
          selector: getUniqueSelector(field),
          disabled: field.disabled || field.getAttribute("aria-disabled") === "true",
          bounds: {
            x: Math.round(rect.x),
            y: Math.round(rect.y),
            width: Math.round(rect.width),
            height: Math.round(rect.height),
          },
        });
      });
      forms.push({
        name: form.getAttribute("aria-label") || form.name || `Form ${idx + 1}`,
        action: form.action || "",
        fields,
      });
    });

    // Also find form fields outside of <form> elements
    const orphanFields = [];
    document.querySelectorAll("input, select, textarea").forEach((field) => {
      if (!isVisible(field) || field.closest("form")) return;
      const rect = field.getBoundingClientRect();
      orphanFields.push({
        type: field.type || "text",
        label: getFieldLabel(field),
        value: field.value || "",
        required: field.required || field.getAttribute("aria-required") === "true",
        selector: getUniqueSelector(field),
        disabled: field.disabled,
        bounds: {
          x: Math.round(rect.x),
          y: Math.round(rect.y),
          width: Math.round(rect.width),
          height: Math.round(rect.height),
        },
      });
    });
    if (orphanFields.length > 0) {
      forms.push({ name: "Standalone fields", fields: orphanFields });
    }

    return forms;
  }

  function extractInteractiveElements() {
    const interactives = [];
    const selector = 'a[href], button, [role="button"], [role="link"], [role="tab"], [role="menuitem"], [role="option"], [tabindex]:not([tabindex="-1"]), summary, details';

    document.querySelectorAll(selector).forEach((el) => {
      if (!isVisible(el)) return;
      // Skip form fields (handled separately)
      if (["INPUT", "SELECT", "TEXTAREA"].includes(el.tagName)) return;

      const issues = [];
      const text = getElementText(el);
      const ariaLabel = el.getAttribute("aria-label") || "";

      // Detect accessibility issues
      if (!text && !ariaLabel && el.tagName !== "DETAILS") {
        issues.push("missing-label");
      }
      if (el.tagName === "A" && !el.getAttribute("href")) {
        issues.push("link-no-href");
      }
      if (el.tagName === "DIV" || el.tagName === "SPAN") {
        if (el.getAttribute("role") === "button" || el.onclick || el.getAttribute("onclick")) {
          issues.push("non-semantic-interactive");
        }
      }
      if (el.getAttribute("tabindex") && parseInt(el.getAttribute("tabindex")) > 0) {
        issues.push("positive-tabindex");
      }

      const rect = el.getBoundingClientRect();

      interactives.push({
        tag: el.tagName.toLowerCase(),
        role: el.getAttribute("role") || getImplicitRole(el),
        text: text.substring(0, 150),
        ariaLabel,
        selector: getUniqueSelector(el),
        issues,
        bounds: {
          x: Math.round(rect.x),
          y: Math.round(rect.y),
          width: Math.round(rect.width),
          height: Math.round(rect.height),
        },
      });
    });

    return interactives;
  }

  function extractImages() {
    const images = [];
    document.querySelectorAll("img, [role='img'], svg[aria-label]").forEach((el) => {
      if (!isVisible(el)) return;
      images.push({
        alt: el.getAttribute("alt") || el.getAttribute("aria-label") || "",
        src: el.src || "",
        selector: getUniqueSelector(el),
      });
    });
    return images;
  }

  function extractLiveRegions() {
    const regions = [];
    document.querySelectorAll("[aria-live], [role='alert'], [role='status'], [role='log'], [role='timer']").forEach((el) => {
      regions.push({
        text: el.textContent.trim().substring(0, 300),
        politeness: el.getAttribute("aria-live") || "assertive",
        role: el.getAttribute("role") || "",
      });
    });
    return regions;
  }

  function getFocusedElement() {
    const el = document.activeElement;
    if (!el || el === document.body) return null;
    return {
      tag: el.tagName.toLowerCase(),
      text: getElementText(el).substring(0, 150),
      selector: getUniqueSelector(el),
      role: el.getAttribute("role") || getImplicitRole(el),
    };
  }

  function detectAccessibilityIssues() {
    const issues = [];

    // Missing language
    if (!document.documentElement.lang) {
      issues.push({
        severity: "error",
        description: "Page is missing lang attribute on <html>",
        element: "html",
      });
    }

    // Missing page title
    if (!document.title.trim()) {
      issues.push({
        severity: "error",
        description: "Page has no title",
        element: "head > title",
      });
    }

    // Missing main landmark
    if (!document.querySelector("main, [role='main']")) {
      issues.push({
        severity: "warning",
        description: "Page has no main landmark",
        element: "body",
      });
    }

    // Heading hierarchy
    const headings = document.querySelectorAll("h1, h2, h3, h4, h5, h6");
    let lastLevel = 0;
    let h1Count = 0;
    headings.forEach((h) => {
      const level = parseInt(h.tagName[1]);
      if (level === 1) h1Count++;
      if (lastLevel > 0 && level > lastLevel + 1) {
        issues.push({
          severity: "warning",
          description: `Heading level skipped from H${lastLevel} to H${level}`,
          element: getUniqueSelector(h),
        });
      }
      lastLevel = level;
    });
    if (h1Count === 0) {
      issues.push({ severity: "warning", description: "No H1 heading found", element: "body" });
    } else if (h1Count > 1) {
      issues.push({ severity: "warning", description: `Multiple H1 headings found (${h1Count})`, element: "body" });
    }

    // Images without alt
    document.querySelectorAll("img").forEach((img) => {
      if (!img.hasAttribute("alt") && isVisible(img)) {
        issues.push({
          severity: "error",
          description: "Image missing alt attribute",
          element: getUniqueSelector(img),
        });
      }
    });

    // Buttons/links without accessible names
    document.querySelectorAll("button, [role='button']").forEach((btn) => {
      if (isVisible(btn) && !getElementText(btn) && !btn.getAttribute("aria-label") && !btn.getAttribute("aria-labelledby")) {
        issues.push({
          severity: "error",
          description: "Button has no accessible name",
          element: getUniqueSelector(btn),
        });
      }
    });

    // Form inputs without labels
    document.querySelectorAll("input:not([type='hidden']), select, textarea").forEach((input) => {
      if (!isVisible(input)) return;
      const label = getFieldLabel(input);
      if (!label) {
        issues.push({
          severity: "error",
          description: `Form input (${input.type || "text"}) has no label`,
          element: getUniqueSelector(input),
        });
      }
    });

    // Focus traps — check for elements that capture tab with no escape
    document.querySelectorAll("[tabindex='-1']").forEach((el) => {
      if (el.closest("[role='dialog'], [role='alertdialog']")) {
        // Expected in modals
        return;
      }
    });

    // Auto-playing media
    document.querySelectorAll("video[autoplay], audio[autoplay]").forEach((media) => {
      if (!media.muted) {
        issues.push({
          severity: "error",
          description: "Auto-playing media without mute",
          element: getUniqueSelector(media),
        });
      }
    });

    return issues;
  }

  /**
   * Execute an action on the page.
   */
  async function executeAction(action) {
    const { mode = "dom", name, args = {} } = action;

    try {
      if (mode === "computer_use") {
        return await executeComputerUseAction(name, args);
      }

      switch (name) {
        case "click":
          return await performClick(args.target);
        case "type_text":
          return await performType(args.target, args.text);
        case "scroll":
          return await performScroll(args.direction, args.amount);
        case "navigate":
          return await performNavigate(args.url);
        case "read_element":
          return await performRead(args.target);
        case "get_page_map":
          return { action: name, success: true, page_state: extractPageMap() };
        case "highlight":
          return await performHighlight(args.target);
        case "diagnose_accessibility":
          return {
            action: name,
            success: true,
            page_state: extractPageMap(),
          };
        default:
          return { action: name, success: false, error: `Unknown action: ${name}`, page_state: extractPageMap() };
      }
    } catch (e) {
      return {
        action: name,
        success: false,
        error: e.message,
        page_state: extractPageMap(),
      };
    }
  }

  async function executeComputerUseAction(name, args) {
    switch (name) {
      case "click_at":
        return await performComputerClick(args.x, args.y);
      case "type_text":
        return await performComputerType(args.text, args.x, args.y, args.press_enter);
      case "keypress":
        return await performKeypress(args.key);
      case "scroll_by":
        return await performComputerScroll(args.dx, args.dy);
      case "wait":
        return await performWait(args.ms);
      default:
        return {
          action: name,
          success: false,
          error: `Unknown computer_use action: ${name}`,
          page_state: extractPageMap(),
        };
    }
  }

  async function performClick(target) {
    const el = findElement(target);
    if (!el) {
      return { action: "click", success: false, error: `Element not found: ${target}`, page_state: extractPageMap() };
    }

    // Show highlight briefly
    showHighlight(el);

    // Scroll into view
    el.scrollIntoView({ behavior: "smooth", block: "center" });
    await sleep(200);

    // Focus and click
    el.focus();
    el.click();
    await sleep(300);

    removeHighlight();
    return { action: "click", success: true, page_state: extractPageMap() };
  }

  async function performType(target, text) {
    const el = findElement(target);
    if (!el) {
      return { action: "type_text", success: false, error: `Element not found: ${target}`, page_state: extractPageMap() };
    }

    showHighlight(el);
    el.scrollIntoView({ behavior: "smooth", block: "center" });
    el.focus();
    await sleep(100);

    // Clear existing value
    el.value = "";
    el.dispatchEvent(new Event("input", { bubbles: true }));

    // Type character by character for realistic input
    for (const char of text) {
      el.value += char;
      el.dispatchEvent(new Event("input", { bubbles: true }));
      el.dispatchEvent(new KeyboardEvent("keydown", { key: char, bubbles: true }));
      el.dispatchEvent(new KeyboardEvent("keyup", { key: char, bubbles: true }));
      await sleep(30);
    }

    el.dispatchEvent(new Event("change", { bubbles: true }));
    await sleep(100);
    removeHighlight();
    return { action: "type_text", success: true, page_state: extractPageMap() };
  }

  async function performScroll(direction, amount) {
    const pixels = parseScrollAmount(amount);
    const scrollMap = {
      up: { top: -pixels, left: 0 },
      down: { top: pixels, left: 0 },
      left: { top: 0, left: -pixels },
      right: { top: 0, left: pixels },
    };
    const scroll = scrollMap[direction] || scrollMap.down;
    window.scrollBy({ ...scroll, behavior: "smooth" });
    await sleep(400);
    return { action: "scroll", success: true, page_state: extractPageMap() };
  }

  async function performNavigate(url) {
    window.location.href = url;
    return { action: "navigate", success: true, page_state: { url, title: "" } };
  }

  async function performRead(target) {
    const el = findElement(target);
    if (!el) {
      return { action: "read_element", success: false, error: `Element not found: ${target}`, page_state: extractPageMap() };
    }
    showHighlight(el);
    const text = el.textContent.trim();
    await sleep(200);
    removeHighlight();
    return {
      action: "read_element",
      success: true,
      data: { text },
      page_state: extractPageMap(),
    };
  }

  async function performHighlight(target) {
    const el = findElement(target);
    if (!el) {
      return { action: "highlight", success: false, error: `Element not found: ${target}`, page_state: extractPageMap() };
    }
    showHighlight(el);
    return { action: "highlight", success: true, page_state: extractPageMap() };
  }

  async function performComputerClick(rawX, rawY) {
    const { x, y } = resolveComputerCoordinates(rawX, rawY);
    const el = document.elementFromPoint(x, y);
    if (!el) {
      return { action: "click_at", success: false, error: `Element not found at ${x}, ${y}`, page_state: extractPageMap() };
    }

    showHighlight(el);
    el.scrollIntoView({ behavior: "smooth", block: "center" });
    await sleep(150);
    dispatchMouseSequence(el, x, y);
    await sleep(250);
    removeHighlight();

    return { action: "click_at", success: true, page_state: extractPageMap() };
  }

  async function performComputerType(text, rawX, rawY, pressEnter = false) {
    if (typeof rawX === "number" && typeof rawY === "number") {
      const clickResult = await performComputerClick(rawX, rawY);
      if (!clickResult.success) {
        return { action: "type_text", success: false, error: clickResult.error, page_state: extractPageMap() };
      }
    }

    const target = document.activeElement;
    if (!target) {
      return { action: "type_text", success: false, error: "No active element to type into", page_state: extractPageMap() };
    }

    if (target.isContentEditable) {
      target.textContent = text;
      target.dispatchEvent(new InputEvent("input", { bubbles: true, data: text, inputType: "insertText" }));
    } else if ("value" in target) {
      target.focus();
      target.value = "";
      target.dispatchEvent(new Event("input", { bubbles: true }));
      target.value = text;
      target.dispatchEvent(new Event("input", { bubbles: true }));
      target.dispatchEvent(new Event("change", { bubbles: true }));
    } else {
      return { action: "type_text", success: false, error: "Focused element is not editable", page_state: extractPageMap() };
    }

    if (pressEnter) {
      dispatchKey(target, "Enter");
    }

    await sleep(100);
    return { action: "type_text", success: true, page_state: extractPageMap() };
  }

  async function performKeypress(key) {
    const target = document.activeElement || document.body;
    const keys = String(key || "").split("+").map((value) => value.trim()).filter(Boolean);
    const mainKey = keys[keys.length - 1] || "Enter";

    const modifiers = {
      ctrlKey: keys.some((value) => /^(CTRL|CONTROL)$/i.test(value)),
      shiftKey: keys.some((value) => /^SHIFT$/i.test(value)),
      altKey: keys.some((value) => /^ALT$/i.test(value)),
      metaKey: keys.some((value) => /^(META|CMD|COMMAND)$/i.test(value)),
    };

    const eventInit = { key: mainKey, bubbles: true, cancelable: true, ...modifiers };
    target.dispatchEvent(new KeyboardEvent("keydown", eventInit));
    target.dispatchEvent(new KeyboardEvent("keyup", eventInit));
    await sleep(60);

    return { action: "keypress", success: true, page_state: extractPageMap() };
  }

  async function performComputerScroll(dx = 0, dy = 0) {
    window.scrollBy({
      left: normalizeComputerDelta(dx, window.innerWidth),
      top: normalizeComputerDelta(dy, window.innerHeight),
      behavior: "smooth",
    });
    await sleep(400);
    return { action: "scroll_by", success: true, page_state: extractPageMap() };
  }

  async function performWait(ms = 5000) {
    await sleep(Math.max(0, Number(ms) || 5000));
    return { action: "wait", success: true, page_state: extractPageMap() };
  }

  // ═══════════════════════════════════════════════
  // Helper functions
  // ═══════════════════════════════════════════════

  function findElement(target) {
    if (typeof target !== "string" || !target.trim()) {
      return null;
    }

    const normalizedTarget = normalizeTarget(target);
    if (!normalizedTarget) {
      return null;
    }

    // Try as CSS selector first
    try {
      const el = document.querySelector(normalizedTarget);
      if (el) return el;
    } catch {}

    if (looksLikeCssSelector(normalizedTarget)) {
      return null;
    }

    // Try by text content
    const allInteractive = document.querySelectorAll(
      'a, button, [role="button"], input, select, textarea, [role="link"], [role="tab"], [role="menuitem"], [tabindex]'
    );

    const targetLower = normalizedTarget.toLowerCase();

    // Exact text match
    for (const el of allInteractive) {
      if (!isVisible(el)) continue;
      const text = getElementText(el).toLowerCase().trim();
      const ariaLabel = (el.getAttribute("aria-label") || "").toLowerCase().trim();
      if (text === targetLower || ariaLabel === targetLower) return el;
    }

    // Partial match
    for (const el of allInteractive) {
      if (!isVisible(el)) continue;
      const text = getElementText(el).toLowerCase();
      const ariaLabel = (el.getAttribute("aria-label") || "").toLowerCase();
      if (text.includes(targetLower) || ariaLabel.includes(targetLower)) return el;
    }

    // Try by aria-label on any element
    const byAria = document.querySelector(
      `[aria-label="${escapeSelectorString(normalizedTarget)}" i]`
    );
    if (byAria) return byAria;

    // Try by placeholder
    const byPlaceholder = document.querySelector(
      `[placeholder="${escapeSelectorString(normalizedTarget)}" i]`
    );
    if (byPlaceholder) return byPlaceholder;

    return null;
  }

  function looksLikeCssSelector(target) {
    return /[#.:[\]>~=+]/.test(target);
  }

  function normalizeTarget(target) {
    const trimmed = String(target || "").trim();
    if (!trimmed) {
      return "";
    }

    const selectorMatch = trimmed.match(/selector="([^"]+)"/i);
    if (selectorMatch && selectorMatch[1]) {
      return selectorMatch[1].trim();
    }

    if (trimmed.includes(" | ")) {
      const maybeSelector = trimmed.split(" | ").pop().trim();
      if (looksLikeCssSelector(maybeSelector)) {
        return maybeSelector;
      }
    }

    return trimmed;
  }

  function escapeSelectorString(value) {
    return String(value).replace(/\\/g, "\\\\").replace(/"/g, '\\"');
  }

  function getElementText(el) {
    return (
      el.getAttribute("aria-label") ||
      el.textContent?.trim() ||
      el.getAttribute("title") ||
      el.getAttribute("alt") ||
      ""
    );
  }

  function getFieldLabel(field) {
    // Explicit label
    if (field.id) {
      const label = document.querySelector(`label[for="${field.id}"]`);
      if (label) return label.textContent.trim();
    }
    // Wrapping label
    const parent = field.closest("label");
    if (parent) return parent.textContent.trim();
    // ARIA
    return field.getAttribute("aria-label") || field.getAttribute("placeholder") || field.getAttribute("title") || "";
  }

  function getImplicitRole(el) {
    const roleMap = {
      A: "link",
      BUTTON: "button",
      INPUT: "textbox",
      SELECT: "combobox",
      TEXTAREA: "textbox",
      IMG: "img",
      NAV: "navigation",
      MAIN: "main",
      HEADER: "banner",
      FOOTER: "contentinfo",
      ASIDE: "complementary",
      ARTICLE: "article",
      SECTION: "region",
      UL: "list",
      OL: "list",
      LI: "listitem",
      TABLE: "table",
      FORM: "form",
    };
    return roleMap[el.tagName] || "";
  }

  function getUniqueSelector(el) {
    if (el.id) return `#${CSS.escape(el.id)}`;

    const parts = [];
    let current = el;
    while (current && current !== document.body && parts.length < 5) {
      let selector = current.tagName.toLowerCase();
      if (current.id) {
        selector = `#${CSS.escape(current.id)}`;
        parts.unshift(selector);
        break;
      }
      if (current.className && typeof current.className === "string") {
        const classes = current.className
          .trim()
          .split(/\s+/)
          .filter((c) => c && !c.includes(":"))
          .slice(0, 2)
          .map((c) => `.${CSS.escape(c)}`)
          .join("");
        selector += classes;
      }
      // Add nth-child for disambiguation
      const parent = current.parentElement;
      if (parent) {
        const siblings = Array.from(parent.children).filter(
          (s) => s.tagName === current.tagName
        );
        if (siblings.length > 1) {
          const index = siblings.indexOf(current) + 1;
          selector += `:nth-child(${index})`;
        }
      }
      parts.unshift(selector);
      current = current.parentElement;
    }
    return parts.join(" > ");
  }

  function isVisible(el) {
    if (!el) return false;
    const style = window.getComputedStyle(el);
    if (style.display === "none" || style.visibility === "hidden" || style.opacity === "0") {
      return false;
    }
    const rect = el.getBoundingClientRect();
    return rect.width > 0 && rect.height > 0;
  }

  function parseScrollAmount(amount) {
    if (!amount) return 400;
    if (amount === "small") return 200;
    if (amount === "medium") return 400;
    if (amount === "large") return 800;
    const num = parseInt(amount);
    return isNaN(num) ? 400 : num;
  }

  function resolveComputerCoordinates(rawX, rawY) {
    return {
      x: normalizeComputerCoordinate(rawX, window.innerWidth),
      y: normalizeComputerCoordinate(rawY, window.innerHeight),
    };
  }

  function normalizeComputerCoordinate(value, size) {
    const numeric = Number(value);
    if (!Number.isFinite(numeric)) return 0;
    if (numeric <= 1000) {
      return Math.round((numeric / 1000) * size);
    }
    return Math.round(numeric / (window.devicePixelRatio || 1));
  }

  function normalizeComputerDelta(value, viewportSize) {
    const numeric = Number(value);
    if (!Number.isFinite(numeric)) return 0;
    if (Math.abs(numeric) <= 1000) {
      return Math.round((numeric / 1000) * viewportSize);
    }
    return Math.round(numeric / (window.devicePixelRatio || 1));
  }

  function dispatchMouseSequence(el, x, y) {
    const eventInit = {
      bubbles: true,
      cancelable: true,
      composed: true,
      clientX: x,
      clientY: y,
      view: window,
    };

    el.dispatchEvent(new MouseEvent("mousemove", eventInit));
    el.dispatchEvent(new MouseEvent("mousedown", eventInit));
    el.dispatchEvent(new MouseEvent("mouseup", eventInit));
    el.dispatchEvent(new MouseEvent("click", eventInit));
    if (typeof el.click === "function") {
      el.click();
    }
  }

  function dispatchKey(target, key) {
    const eventInit = { key, bubbles: true, cancelable: true };
    target.dispatchEvent(new KeyboardEvent("keydown", eventInit));
    target.dispatchEvent(new KeyboardEvent("keyup", eventInit));
  }

  function showHighlight(el) {
    removeHighlight();
    const rect = el.getBoundingClientRect();
    highlightOverlay = document.createElement("div");
    highlightOverlay.id = "northstar-highlight";
    Object.assign(highlightOverlay.style, {
      position: "fixed",
      top: `${rect.top - 3}px`,
      left: `${rect.left - 3}px`,
      width: `${rect.width + 6}px`,
      height: `${rect.height + 6}px`,
      border: "3px solid #2563eb",
      borderRadius: "4px",
      backgroundColor: "rgba(37, 99, 235, 0.1)",
      zIndex: "2147483647",
      pointerEvents: "none",
      transition: "all 0.2s ease",
      boxShadow: "0 0 0 2px rgba(37, 99, 235, 0.3), 0 0 20px rgba(37, 99, 235, 0.15)",
    });

    // Add label
    const label = document.createElement("div");
    Object.assign(label.style, {
      position: "absolute",
      top: "-24px",
      left: "0",
      background: "#2563eb",
      color: "white",
      fontSize: "11px",
      fontFamily: "system-ui, sans-serif",
      padding: "2px 8px",
      borderRadius: "3px",
      whiteSpace: "nowrap",
      fontWeight: "500",
    });
    label.textContent = "Northstar";
    highlightOverlay.appendChild(label);

    document.body.appendChild(highlightOverlay);
  }

  function removeHighlight() {
    if (highlightOverlay) {
      highlightOverlay.remove();
      highlightOverlay = null;
    }
  }

  function sleep(ms) {
    return new Promise((resolve) => setTimeout(resolve, ms));
  }

  // Auto-extract page map on load
  setTimeout(() => {
    const pageMap = extractPageMap();
    chrome.runtime.sendMessage({ type: "page_state", data: pageMap }).catch(() => {});
  }, 1000);
})();
