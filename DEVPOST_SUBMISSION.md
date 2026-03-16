# Northstar Devpost Submission

This file is the repo copy of the Devpost submission text, links, and assets for Northstar.

## General Info

### Project name

`Northstar`

### Elevator pitch

`Accessibility autopilot for the broken web.`

## Project Details

### About the project

I built Northstar because this is still a massive, current problem. [WebAIM's 2025 Million report](https://webaim.org/projects/million/) found **50,960,288** detectable accessibility errors across the top one million homepages, an average of **51 errors per page**, with detectable WCAG failures on **94.8%** of homepages. At the same time, the DOJ's April 24, 2024 [Title II web accessibility rule](https://www.ada.gov/resources/2024-03-08-web-rule/) applies to **109,893** state and local government websites and **8,805** mobile apps across **91,489** public entities, with compliance dates on **April 24, 2026** and **April 26, 2027** depending on entity size. DOJ's own [final rule analysis](https://public-inspection.federalregister.gov/2024-07758.pdf?1713876314=) estimates **$16.9B** in implementation costs in the first three years, including examples like **$149,643** for a small municipality and **$250,822** for a small school district.

That was the problem I wanted to focus on: not just how to make an agent browse the web, but how to help someone finish a task when the website itself is the obstacle.

Northstar is a Chrome extension that can look at the current page, explain what is there, point out the barriers it sees, and help the user complete the task anyway. It is voice-first, but it also works with typed commands.

It starts with semantic page understanding, and when the page markup is broken or misleading, it falls back to screenshot-based multimodal reasoning. That is what lets it keep working on sites where normal automation gets lost.

I built Northstar as a Chrome Extension (Manifest V3) backed by a FastAPI + WebSocket service on Google Cloud. The extension extracts a live page map from the current tab. The backend uses Gemini Live for real-time voice interaction and Gemini multimodal reasoning for planning and fallback.

I designed it around a simple confidence ladder:

- semantic action when the page structure is trustworthy
- semantic plus visual disambiguation when the target is ambiguous
- full visual fallback when the page is hostile to normal automation
- user handoff when confidence is too low

After every action, Northstar checks whether the page actually changed in the expected way. If not, it tries to recover instead of pretending it worked.

The hardest part was not getting a model to click. The hard part was deciding what the system should trust when the DOM, the visible UI, and the user's goal do not line up. That pushed me toward verification, fallback behavior, and asking the user when confidence is low instead of bluffing.

I learned that accessibility problems and browser-agent problems are often the same problem. Missing labels, broken semantics, and weak feedback make sites hard for people, and they also make them hard for agents.

I am proud that Northstar is not just a demo. It is meant to help someone get through a broken page right now, while also making the underlying problem visible.

Next, I want to expand it to more real workflows and test it more deeply with assistive-technology users.

### Built with

`Python, JavaScript, HTML, CSS, Chrome Extension (Manifest V3), FastAPI, WebSockets, Gemini API, Gemini Live API, Google Gen AI SDK, Google Cloud Run, Google Cloud Build, Firestore, Google Cloud Logging, Rive`

## Try It Out Links

- Public code repo: [https://github.com/jackson-kuja/northstar](https://github.com/jackson-kuja/northstar)
- Local setup and reproducible testing: [README.md](https://github.com/jackson-kuja/northstar/blob/main/README.md)
- Architecture diagram: [docs/architecture.png](https://github.com/jackson-kuja/northstar/blob/main/docs/architecture.png)
- Cloud deployment proof: [infra/cloudbuild.yaml](https://github.com/jackson-kuja/northstar/blob/main/infra/cloudbuild.yaml)

## Project Media

- Image gallery asset: [docs/architecture.png](/Users/jacksonkuja/Northstar/docs/architecture.png)
- Video demo link: `TODO - add final public video URL before submitting`
