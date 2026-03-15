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
"This is TechMart, a product shopping page. I found several accessibility issues: the page is missing a language attribute, there's no main landmark, heading levels skip from none to H3, the navigation uses non-semantic divs instead of nav elements, and there are multiple unlabeled buttons and form inputs. The page has 8 products displayed in a grid, with filter controls, a search bar, and a sort dropdown. The cart button in the header has no accessible label."

## 0:50–1:40 — Guide + Autopilot Mode

**Voice command:** "Filter to show only items under 50 dollars and sort by rating."

**Expected behavior:**
1. Northstar narrates: "I'll set the price filter first. I found two unlabeled price inputs — I'm using the 'Max' placeholder to identify the correct field."
2. Types "50" into the max price input
3. Narrates: "Now I'll click Apply. The Apply control is a div styled as a button — it's not a real button element."
4. Clicks the Apply filter div
5. Narrates: "Filter applied. The product grid now shows 4 items under $50. Now sorting by rating."
6. Selects "Top Rated" from the sort dropdown (which has no label)
7. Narrates: "Sorted by rating. The page now shows: Wireless Charging Pad at $24.99, USB-C Hub at $29.99, Premium Headphone Stand at $34.99, and Bluetooth Speaker at $39.99."

## 1:40–2:20 — Error Recovery

**Voice command:** "Add the top-rated item to cart."

**Expected behavior:**
1. Northstar clicks "Add" on the Bluetooth Speaker
2. Narrates: "Added Bluetooth Speaker to cart. The add button only says 'Add' with no context about which product — that's another accessibility gap."
3. Toast appears (no aria-live region — Northstar notes this)

**Voice command:** "Go to checkout."

1. Northstar clicks the cart icon (emoji with no label)
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
7. Northstar clicks "Place Order" and confirms success

## 2:50–3:20 — Accessibility Diagnosis

**Voice command:** "Why was that hard for a screen reader?"

**Expected Northstar response (diagnosis panel opens):**
"I found 18 accessibility barriers on this page:

**Critical:**
- Missing lang attribute on the HTML element
- No main landmark
- 5 form inputs with no labels (email, city, zip, MM/YY, CVV)
- Cart button has no accessible name
- Buttons say only 'Add' with no product context

**Serious:**
- Navigation uses clickable divs instead of nav and anchor elements
- Filter controls are spans with onclick handlers, not buttons
- H3 used as first heading — H1 is missing
- Checkout modal has no focus trap
- Toast notifications have no aria-live region
- Two identical 'Place Order' buttons with no distinguishing labels

**Moderate:**
- Promo banner uses positive tabindex (5)
- Sort dropdown has no associated label
- Footer text has very low contrast

A screen reader user would be unable to identify most interactive controls, couldn't navigate by landmarks, and would miss toast notifications entirely."

## 3:20–3:45 — Architecture

**Screen:** Architecture diagram

**Narration:**
"Northstar runs as a Chrome extension with a persistent side panel. Voice input flows through the Gemini 2.5 Flash Native Audio Live API for real-time conversation. Page understanding uses Gemini 2.5 Flash to interpret both the accessibility tree and screenshots. Complex recovery uses Gemini 2.5 Pro. Every action goes through a four-tier confidence ladder — semantic first, visual fallback only when needed — and every action is verified after execution. Sessions are stored in Firestore, the backend runs on Cloud Run, and the whole pipeline is deployed via Cloud Build."

## 3:45–4:00 — Closing

**Screen:** Hero shot — Northstar side panel with completed task

**Narration:**
"Northstar doesn't ask the broken web to become accessible first. It makes the web usable now. When the accessibility tree is correct, Northstar accelerates browsing. When it's broken, Northstar becomes the user's eyes, guide, and hands."
