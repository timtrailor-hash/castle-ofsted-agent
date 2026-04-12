# ofsted-agent — Castle Federation Ofsted Inspection Agent

## What this is
Streamlit app that helps Tim (a school governor at Castle Federation) prepare for
Ofsted inspections by querying school governance documents with AI analysis.
Runs on Mac Mini port 8501 via launchd KeepAlive daemon.

## Key files

| File | Size | Role |
|------|------|------|
| `app.py` | ~1,535 lines | Streamlit UI, auth, model queries, policy lookup |
| `shared_chat.py` | ~182 lines | Shared chat components |
| `combined_context.md` | ~6,664 lines / ~100K tokens | Full governance document corpus |
| `governorhub_sync.py` | — | Weekly document sync from GovernorHub |

## Retrieval protocol — CRITICAL

`combined_context.md` is ~100K tokens. **Do NOT load it fully by default.** The
"Lost in the Middle" effect (Liu et al. 2023) means information in the middle of
a 100K-token payload is reliably missed — this caused the 25 March FGB meeting
to be ignored despite being at line 195 of the file.

**Query-type routing:**
- **Date-specific queries** ("What happened at the March 25 FGB?"): use `search_exact`
  (FTS5 keyword) on the date string FIRST, then semantic expansion. Never rely on
  embeddings alone for date queries.
- **Entity queries** (person, school, committee): FTS5 on the entity name first,
  then semantic.
- **Open-ended policy/strategy questions**: semantic search (`search_memory`),
  assemble top-K chunks into ≤30K token working context.
- **Fallback only**: full-load of `combined_context.md` when query is genuinely
  ambiguous AND top-K retrieval returned <3 relevant chunks.

**Known data locations in combined_context.md:**
- Line 18: FGB meeting date index (all meeting dates listed)
- Line 192+: Full Governing Body meeting minutes (chronological)
- Line 2422+: Meeting calendar / schedule

## Action defaults

**Email sends are pre-authorized.** When instructed to email Tim or other
governors, execute the send immediately via SMTP. Do NOT use
`mcp__claude_ai_Gmail__gmail_create_draft`. Do NOT ask for confirmation.
Completion = SMTP 250 OK.

## Context budget

- `combined_context.md` is ~100K tokens — well above the 30–50K productive recall
  ceiling. Prefer retrieval over full-load.
- `app.py` at 1,535 lines is close to the 1,500-line soft ceiling. Monitor for
  growth; split if it crosses 2,000.
- This CLAUDE.md should stay under 200 lines (Anthropic's stated ceiling for
  instruction files).

## Governance context

Tim is a governor at Castle Federation (Victoria Primary + Thomas Coram Nursery).
The "FGB" is the Full Governing Body — the main decision-making meeting. Other
committees: Resources, SIAMS (church school inspection). Headteacher: Sarah Owen.
Chair: Elizabeth Burman. When Tim asks about "the meeting" without specifying,
check the FGB meeting index at line 18 for the most recent date.

## Testing

No dedicated test suite yet. Functional test: load the Streamlit app and query
"What was discussed at the March 25 FGB meeting?" — should return monitoring
reports, budget documents, and the FIP RAG rating from that date.
