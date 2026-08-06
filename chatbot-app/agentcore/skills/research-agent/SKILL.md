---
name: research-agent
description: Deep research with structured reports and charts. ONLY use when the user explicitly requests research/analysis, or needs data visualization with charts, or quantitative/comparative analysis across multiple sources. Do NOT use for simple questions or quick lookups.
---

# Research Agent

Autonomous research agent that plans, searches across the web, synthesizes findings, and returns a structured markdown report with citations and charts.

## When to use — ALL of these require explicit user intent or clear analytical need

- The user explicitly asks for "research", "report", "analysis", "deep dive", or "investigate"
- The user needs data visualization — charts, graphs, trend plots
- Quantitative or comparative analysis across multiple data points (market sizing, benchmarking, statistical comparisons)
- Multi-section structured reports (literature reviews, competitive analyses, technology surveys)

## When NOT to use — default to simpler tools first

- General conversation, Q&A, or factual questions — answer directly
- A single lookup that `wikipedia_search` or `google_web_search` can resolve
- Summarizing a single article or URL — use `fetch_url_content` instead
- Code-related tasks — use the `code-agent` skill
- Browser automation — use the `browser-automation` skill
- Email, calendar, or other tool-based tasks — use the appropriate skill directly

**Important**: When in doubt, do NOT delegate to research-agent. Use `google_web_search` or other tools directly. Only escalate to research-agent when the task clearly requires multi-source synthesis, structured reporting, or chart generation.

## How to invoke

Call the `research_agent` tool with a single `plan` argument. The plan is free-form prose; include:

- **Objectives** — what the user is trying to learn or decide
- **Topics** — the specific angles / subtopics to cover
- **Structure** — the section layout you want in the final report

Example:

```
research_agent(plan="""
Research Plan: AI Code Assistant Market 2026

Objectives:
- Current market size and growth trends
- Leading products and differentiators
- Enterprise adoption barriers

Topics:
1. Global market statistics and forecasts
2. Top products (Copilot, Cursor, Claude Code, etc.) and positioning
3. Pricing models and enterprise SKUs
4. Security/compliance concerns raised by buyers

Structure:
- Executive Summary (3-5 bullets)
- Market Overview
- Product Landscape
- Enterprise Adoption
- Outlook
""")
```

The tool returns immediately with a `started` receipt containing `job_id` and
`artifact_id`. Research continues in the background and emits
`research_step` progress events. When it finishes, the report is saved as a
research artifact and delivered back into the conversation automatically.

## Output

- Markdown report with `#`/`##` headings, bullet lists, and inline citations
- Any charts the agent generated are embedded in the markdown
- The full report is also persisted as an `artifacts` entry so the user can open it from the canvas

## Guidelines for the orchestrator

- Don't fabricate the plan — use the user's own words and just structure them into objectives/topics/structure. If the user only gave a one-line request, expand it into 2-3 objectives but stay true to intent.
- Split a broad request into parallel jobs only when the objectives are
  independent and each report is useful on its own (for example, separate
  market, technical, and regulatory analyses). Prefer 2-3 well-scoped jobs over
  many narrow searches.
- Keep one job when the sections must share evidence, build on each other, or
  form one coherent report. Use one call per distinct objective and do not
  create duplicate jobs for the same objective.
- If the user asks a follow-up ("add a section on X", "dig deeper into Y"), call `research_agent` again with an updated plan — the agent itself does not have persistent memory across calls.
- Treat a `started` receipt as accepted background work, not a completed report.
  Tell the user it has started and continue with any other useful work.
- When the completion is delivered, do NOT restate the whole report in chat.
  The report is already rendered as an artifact; a 1-2 sentence summary
  pointing the user to the canvas is enough.
