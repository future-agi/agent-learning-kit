---
name: browser-and-computer-use
description: "The agent acts on a screen. Evidence: playwright, puppeteer, selenium, a CDP client or a computer-use loop in the dependencies; code that navigates URLs, clicks selectors or reads a DOM; success judged by what is visible rather than by an API response. The world is the real site or application. NOT this file for an agent that only calls HTTP tools."
---

# Agents that drive a browser or a desktop

> **Selection check.** You are in the right file if the agent's actions are clicks, keystrokes or navigation against a rendered surface. If it only calls HTTP tools, you are in the wrong file.

The world is a real site or application, not a table-shaped imitation of one. Build the submitted
application and its dependencies, then make its visible state repeatable between scenarios.

`fi/alk/harness/world/kinds.py` maps `browser`, `computer_use` and `cua` to a browser world. Start
there rather than inventing a different kind.

## Read the journey before you build it

Find the application entrypoint, development command, authentication path, backend services, seed
process and browser automation hooks. Identify the stable URL, viewport requirements, initial user
state and the action that marks a journey complete. Use the repository's own startup configuration
and change only documented dependency or base-URL seams.

Do not replace an unavailable application with static HTML, a fake API or a page that jumps directly
to the target state. That tests the agent's ability to recognize your mock, not its ability to use

## Make reset honest

Every scenario needs the same starting application state. Record how reset restores accounts,
database rows, files, browser storage, queues and background jobs. A page reload is not a reset if
the previous scenario changed server-side state. If an effect cannot be reset without rebuilding
the application, make that limitation explicit instead of allowing scenario order to decide results.

## Check journeys, not only outcomes

State lives in the page and its backend. Verify both where useful. A useful check can establish
that the expected control is present and enabled, a confirmation appears after the action, the
server-side record changed once, and a forbidden route or invalid form is refused. A final database
row alone can pass when the agent reached it through the wrong screen, bypassed confirmation, or
left the UI in a broken state.

Capture durable evidence from the application's existing browser, network and server logs. Do not
invent a parallel interaction protocol for the harness.