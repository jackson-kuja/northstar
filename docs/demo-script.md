# Northstar Demo Script (4 minutes)

## Setup (before recording)
- Open Chrome with the Northstar extension loaded
- Open the TechMart demo site (demo-site/index.html)
- Backend running on Cloud Run
- Side panel closed initially

## 0:00–0:20 — The Problem

**Screen:** TechMart demo site (intentionally broken)

**Narration:**
"Over 1 billion people live with some form of disability. Screen readers are their bridge to the web — but that bridge only works when developers build it right. When buttons have no labels, when forms have no descriptions, when custom components ignore accessibility standards, screen reader users are locked out. Northstar changes that."

## 0:20–0:50 — Explore Mode

**Action:** Click Northstar icon to open side panel

**Voice command:** "Describe this page."

**Expected Northstar response:**
"This is TechMart, a product shopping page with eight products, a search bar, filter controls, and a checkout flow. I found several accessibility issues: the page is missing a language attribute and a main landmark, the first heading starts at H3 with no H1, the navigation and category filters use non-semantic elements, and several search, budget, and checkout fields rely on placeholders instead of labels. The cart button in the header is icon-only."

## 0:50–1:40 — Guide + Autopilot Mode

**Voice command:** "Filter to show only items under 50 dollars and sort by rating."

**Expected behavior:**
1. Northstar narrates: "I'll set the price filter first. I found two unlabeled price inputs — I'm using the 'Max' placeholder to identify the correct field."
2. Types "50" into the max price input
3. Narrates: "Now I'll click Apply. The Apply control is a div styled as a button — it's not a real button element."
4. Clicks the Apply filter div
5. Narrates: "Filter applied. The page now shows four items under 50 dollars. Now sorting by rating."
6. Selects "Top Rated" from the sort dropdown (which has no label)
7. Narrates: "Sorted by rating. The updated results summary says Beacon Bluetooth Speaker is now the top result at $39.99."

## 1:40–2:20 — Error Recovery

**Voice command:** "Add the top-rated item to cart."

**Expected behavior:**
1. Northstar identifies the first `Top result` card after sorting and clicks its `Add` button
2. Narrates: "Added Beacon Bluetooth Speaker to cart. The button only says 'Add', so the product card context is doing the work."
3. The cart badge increments and the visible event feed updates

**Voice command:** "Go to checkout."

1. Northstar clicks the icon-only cart button in the header
2. Checkout modal opens (no focus trap — noted)
3. Narrates: "The checkout modal opened but doesn't trap focus, which means keyboard users could accidentally tab out of it."

## 2:20–2:50 — Checkout Completion

**Voice command:** "Fill out the form with test data and complete the order."

**Expected behavior:**
1. Fills in Full Name: "Jane Doe"
2. Fills email (no label, uses placeholder)
3. Fills address, city, zip
4. Fills card number
5. Fills expiry and CVV (both unlabeled)
6. Narrates: "Form filled. I notice there are two identical 'Place Order' buttons, which is confusing, but I can still continue with the first one."
7. Northstar clicks `Place Order`, the modal closes, and the receipt card updates with a visible order ID

## 2:50–3:20 — Accessibility Diagnosis

**Voice command:** "Why was that hard for a screen reader?"

**Expected Northstar response (diagnosis panel opens, approximate):**
"This flow is difficult because the page depends on visual context that isn't expressed semantically.

Critical issues:
- Missing lang attribute on the HTML element
- No main landmark
- Unlabeled search, budget, and checkout fields
- Cart button has no accessible name
- Buttons say only 'Add' with no product context

Serious issues:
- Navigation uses clickable divs instead of nav and anchor elements
- Filter controls are spans with onclick handlers, not buttons
- The first heading starts at H3 and the page has no H1
- Checkout modal has no focus trap
- Two identical 'Place Order' buttons with no distinguishing labels

Moderate issues:
- Promo banner uses positive tabindex (5)
- Sort dropdown has no associated label
- Product images have no alt text
- Footer text has very low contrast

A screen reader user would have to guess which controls matter, wouldn't get a reliable structural outline, and would struggle to confirm what changed after each action." 

## 3:20–3:45 — Architecture

**Screen:** Architecture diagram

**Narration:**
"Northstar runs as a Chrome extension with a persistent side panel. Voice input flows through the Gemini 2.5 Flash Native Audio Live API for real-time conversation. The browser task loop uses Gemini 3 Flash with DOM-first actions, visual fallback when the page is broken, and verification after each step. Page context comes from the accessibility tree, the page map, and screenshots. Sessions can persist to Firestore, the backend runs on Cloud Run, and the deployment pipeline runs through Cloud Build."

## 3:45–4:00 — Closing

**Screen:** Hero shot — Northstar side panel with completed task

**Narration:**
"Northstar doesn't ask the broken web to become accessible first. It makes the web usable now. When the accessibility tree is correct, Northstar accelerates browsing. When it's broken, Northstar becomes the user's eyes, guide, and hands."
