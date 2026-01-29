# System Workflow

Last updated: 2026-01-27

## High-level flow
1) User submits an issue or question in the chat UI.
2) Research agent checks for SOP matches in the KB and fetches Tavily web hints.
3) Orchestrator uses the SOP + Tavily hints to generate a diagnostic script and summary.
4) UI shows the diagnostic summary and asks the user to approve running the script.
5) If approved, the action agent executes the diagnostic commands; outputs are logged.
6) Diagnosis summary is generated from the command outputs.
7) Fix planner uses diagnostics + SOP + Tavily hints to generate a resolution script.
8) UI presents the fix summary and requests confirmation to run it.
9) Executor runs approved commands and logs results.

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
- Uses Tavily search.
- UI shows web sources and the web query used (for debugging).

## Fix stages
- Bluetooth and Wi‑Fi fixes run in stages. Each retry advances to a new stage with different commands.
- After the final stage, the UI stops and asks for escalation.

## Turn off Wifi and Bluetooth
-  Get-PnpDevice -FriendlyName "Intel(R) Wireless Bluetooth(R)" | Disable-PnpDevice -Confirm:$false
-  Disable-NetAdapter -Name "Wi-Fi" -Confirm:$false
