---
name: scenarios-cua
description: "Axis VALUES for an agent that drives a browser or desktop: it clicks, types and reads a rendered surface. Use when the agent's actions are navigation and interaction against a UI rather than API calls. Read _framework.md first for the invariant axes; this file supplies only what differs when the world is a screen. NOT for an agent that only calls HTTP tools."
---

# Computer and browser use: the axis values

> **Selection check.** You are in the right file if success depends on what is on a screen. If the
> agent only calls HTTP tools, you are in the wrong file.

**T — domain objects.** Form, record, cart, booking, file, report, setting, crossed with the 12
operations. **The Execute cell is "submit, pay, delete or send on a live UI"** — irreversible and
the highest stakes here, because there is no transaction to roll back.

**W — counterparty.** A human tasker *and the site itself*. Traits: role, authentication,
account state. Anonymous versus authenticated changes what is even reachable.

**D — disposition.** Task urgency, with light affect. The second dimension that matters more than
mood is **world volatility**: A/B variants and timing flux mean the same page is not the same page.

**X — the five questions, on a screen.**
- x1 fidelity: DPI, zoom, theme, how the page actually renders
- x2 medium: browser, OS, mobile viewport
- x3 stability: timing races, lazy-loaded content that arrives after the agent looked
- x4 interference: popups, overlays, cookie banners, CAPTCHA
- x5 presentation: dynamic DOM ids, iframes, shadow DOM, whether an accessibility tree exists

x5 is the one that breaks agents most often here, because a selector that worked once is not a
selector that works.

**I — interaction dynamics.** A step loop. Levers: action loops that repeat without progressing,
cross-tab flows, and resuming mid-flow after an interruption.

**O — overlay.** Attack surface is page content and hidden text. Dominant harm classes: a
destructive or irreversible click, data entered into the wrong form, dark patterns, financial and
quantity errors, phishing.
