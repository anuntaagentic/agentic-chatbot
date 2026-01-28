# System Workflow

Last updated: 2026-01-27

## High-level flow
1) User submits an issue or question in the chat UI.
2) Diagnosis agent builds a step-by-step diagnostic plan.
3) Plan steps are preflighted (availability + allow/deny lists).
4) Commands execute in sequence; outputs are logged.
5) RAG agent retrieves top matches from `tech_support_dataset.vectors.pkl`.
6) Web scraper agent retrieves web hints (DuckDuckGo).
7) Fix planner composes:
   - direct answer (for info questions), or
   - a fix plan with commands (for repair/install).
8) Review/Reflection:
   - If any commands are blocked by allow-list, the Fix Planner receives the block list.
   - The Fix Planner proposes a safe alternative command or escalates to a human.
9) Gatekeeper finalizes:
   - Combines KB + web hints into a single response.
   - Removes promotional or unsafe output and enforces source attribution.
10) Execution + verification:
   - Executor runs fix commands and verifies outcome.
   - If verification fails, UI allows staged retries with different commands.
11) UI displays:
   - diagnostics output,
   - sources (KB + web),
   - web query + result count (when web sources are empty),
   - concise answer or fix plan.
9) If fixes are proposed, the user must confirm.
10) Executor runs approved commands and logs results.

## RAG workflow
- Vector generation (one-time or on CSV changes):
  - `python -m app.build_vectors`
  - Output: `tech_support_dataset.vectors.pkl`
- App startup:
  - Loads vectors from the cached file only (no rebuild at runtime).

## Agents and roles
| Agent | Purpose |
| --- | --- |
| Orchestrator (The Architect) | Analyzes the user query and builds the master plan. |
| Research Agent (The Librarian) | Retrieves KB + web sources and produces a fact sheet. |
| Action Agent (The Engineer) | Runs diagnostics, preflight checks, and executes commands. |
| Gatekeeper (The Output Controller) | Synthesizes and sanitizes final answers with sources. |

## Source attribution rules
- KB sources display Conversation_ID and issue text.
- Web sources display title + URL.
- Answers cite KB ID and web link when used.

## Web search behavior
- Uses DuckDuckGo instant answers first, then falls back to HTML parsing.
- UI shows the final web result and the web query used (for debugging).
- Web search is skipped for system-info questions (to avoid irrelevant ads/results).

## Fix stages
- Bluetooth and Wi‑Fi fixes run in stages. Each retry advances to a new stage with different commands.
- After the final stage, the UI stops and asks for escalation.

## Turn off Wifi and Bluetooth
-  Get-PnpDevice -FriendlyName "Intel(R) Wireless Bluetooth(R)" | Disable-PnpDevice -Confirm:$false
-  Disable-NetAdapter -Name "Wi-Fi" -Confirm:$false
