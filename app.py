#!/usr/bin/env python3
"""
Ofsted Inspection Agent — Streamlit GUI
Castle CE Federation branding. Redesigned conversation UX.
"""

import os
import re
import json
import time
import base64
import subprocess
from pathlib import Path

import streamlit as st

# ── Paths ────────────────────────────────────────────────────────────────────

APP_DIR = Path(__file__).parent
CONTEXT_FILE = APP_DIR / "combined_context.md"
ENV_FILE = APP_DIR / ".env"
LOGO_FILE = APP_DIR / "logo.png"
TREE_FILE = APP_DIR / "tree.png"
SCHOOL_DOCS = Path.home() / "Desktop" / "school docs"
AUDIO_STATE_FILE = APP_DIR / "audio_state.json"
AUDIO_PID_FILE = APP_DIR / "audio_worker.pid"
AUDIO_LOG_FILE = APP_DIR / "audio_worker.log"
PYTHON_PATH = Path.home() / "anaconda3" / "bin" / "python"

# ── Environment detection ────────────────────────────────────────────────────

IS_CLOUD = not Path.home().joinpath("Desktop", "school docs").exists()

# ── Load API keys (Streamlit secrets first, then .env fallback) ──────────────

def load_env():
    # Try Streamlit secrets first (used in cloud deployment)
    try:
        if "ANTHROPIC_API_KEY" in st.secrets:
            os.environ["ANTHROPIC_API_KEY"] = st.secrets["ANTHROPIC_API_KEY"]
        if "GEMINI_API_KEY" in st.secrets:
            os.environ["GEMINI_API_KEY"] = st.secrets["GEMINI_API_KEY"]
    except Exception:
        pass
    # Fall back to .env file (local dev)
    if ENV_FILE.exists() and not os.environ.get("ANTHROPIC_API_KEY"):
        for line in ENV_FILE.read_text().splitlines():
            line = line.strip()
            if "=" in line and not line.startswith("#"):
                key, val = line.split("=", 1)
                os.environ[key.strip()] = val.strip().strip('"').strip("'")

load_env()

# ── Models ───────────────────────────────────────────────────────────────────

MODELS = {
    "Haiku (fastest)": {"provider": "anthropic", "model": "claude-haiku-4-5-20251001"},
    "Sonnet (balanced)": {"provider": "anthropic", "model": "claude-sonnet-4-5-20250929"},
    "Gemini Flash (free)": {"provider": "gemini", "model": "gemini-2.0-flash"},
}

NAVY = "#2a2e45"
GOLD = "#C1A559"
LIGHT_BG = "#f7f7f7"

# ── System prompt ────────────────────────────────────────────────────────────

SCHOOL_FOCUS = {
    "Victoria CE Infant & Nursery School": """SCHOOL FOCUS: Victoria CE Infant & Nursery School
- This inspection is specifically about VICTORIA — all answers must relate to Victoria's data, outcomes, finances, staffing, and context
- Victoria was rated Requires Improvement (Oct 2023) — emphasise the improvement journey since then
- Use Victoria-specific data: EYFS, KS1, phonics, GLD, attendance, PPG, SEND, Victoria's budget/accounts
- When discussing governance (link visits, committees, challenge), frame it through Victoria's priorities and Victoria's improvement plan
- Thomas Coram data/context should only be mentioned if directly relevant to a federation-wide point""",

    "Thomas Coram CE School": """SCHOOL FOCUS: Thomas Coram CE School
- All answers must relate to Thomas Coram's data, outcomes, finances, staffing, and context
- Thomas Coram was rated Good (June 2023)
- Use Thomas Coram-specific data: KS2 results, reading/writing/maths, attendance, PPG, SEND, Thomas Coram's budget/accounts
- When discussing governance, frame it through Thomas Coram's priorities
- Victoria data/context should only be mentioned if directly relevant to a federation-wide point""",

    "Both (Federation-wide)": """SCHOOL FOCUS: Castle CE Federation (both schools)
- Answer with data from BOTH Victoria CE Infant & Nursery School AND Thomas Coram CE School
- Compare and contrast where relevant
- Victoria was rated Requires Improvement (Oct 2023), Thomas Coram rated Good (June 2023)
- Cover federation-wide governance, shared leadership, shared Christian vision
- Include school-specific data where the question relates to outcomes, finance, or staffing""",
}

SYSTEM_PROMPT_TEMPLATE = """You are an Ofsted inspection assistant for a school governor at the Castle CE Federation.

{school_focus}

YOUR ROLE:
- Help the governor answer questions from Ofsted inspectors during a LIVE inspection call
- Provide specific, evidence-based answers with data and quotes from school documents
- Reference exact source documents so the governor can open them if needed

CRITICAL — THIS IS A LIVE INSPECTION:
- The input comes from real-time speech transcription of an Ofsted inspection call
- Questions may be slightly garbled, incomplete, or contain transcription errors
- NEVER ask for clarification — the governor cannot type back during a live call
- NEVER say "could you complete the question" or "what do you mean by"
- ALWAYS give the best possible answer based on what you understood
- If the question seems incomplete, answer the most likely interpretation
- If the question contains multiple topics, answer ALL of them
- If you're unsure what was asked, answer the closest matching topic from the school documents

ANSWERING STYLE — THE GOVERNOR IS READING THIS WHILE ON A LIVE VIDEO CALL:
- Each bullet must be ONE short sentence the governor can say out loud
- Maximum 8-12 words per bullet point
- Lead each bullet with the key fact or number FIRST
- No paragraphs, no dense text, no waffle
- Use "→" before each bullet
- Frame as "critical friend" providing strategic oversight
- For SIAMS: reference the Christian vision

RESPONSE FORMAT (strict — always use this format, no exceptions):
ANSWER:
→ [short talking point 1 — one sentence, key fact first]
→ [short talking point 2 — one sentence, key fact first]
→ [short talking point 3 — one sentence, key fact first]

EVIDENCE:
→ [single data point or quote with number]
→ [single data point or quote with number]
→ [single data point or quote with number]

SOURCE:
[EXACT filename(s) copied from the [SOURCE: ...] tags — one per line, including extension]

CRITICAL SOURCE RULES:
- Every section in the documents is tagged with [SOURCE: filename | folder | date]
- You MUST copy the EXACT filename from these tags, including the file extension (.docx, .pdf, etc.)
- Do NOT abbreviate, rename, or reformat filenames
- If multiple sources, list each on its own line
- Example: if the tag says [SOURCE: Victoria SEF Jan 26 V5 (1).docx | ...], write exactly: Victoria SEF Jan 26 V5 (1).docx

EXAMPLE OF GOOD FORMAT:
ANSWER:
→ GLD rose from 62% to 71% this year
→ Phonics pass rate now 83%, up 8pp from 2023
→ We monitor through termly data reviews at P&C committee

EVIDENCE:
→ GLD 71% vs national 67% (Assessment Details HFL 2025)
→ Phonics 83% vs 79% national (Victoria KS1 Data)
→ P&C minutes show data challenge each term since Jan 2024

SOURCE:
Victoria SEF Jan 26 V5 (1).docx
VIC_SAFEGUARDING Report Autumn 2025.docx
FGB Minutes 2026-11-26.docx

IMPORTANT:
- Don't make anything up — say if you don't have specific data
- Prioritise most recent data (2025-26 over older)
- ALWAYS respond in the ANSWER/EVIDENCE/SOURCE format above, even if the input is unclear
- Keep it SHORT — the governor needs to glance and speak, not read an essay
- SOURCE filenames must be EXACT copies from the [SOURCE: ...] tags — never abbreviate

--- SCHOOL DOCUMENTS ---

{context}"""


def build_system_prompt(school_focus_key, context):
    return SYSTEM_PROMPT_TEMPLATE.format(
        school_focus=SCHOOL_FOCUS[school_focus_key],
        context=context,
    )


# ── API ──────────────────────────────────────────────────────────────────────

def query_model(model_info, system_prompt, messages, placeholder):
    if model_info["provider"] == "anthropic":
        from anthropic import Anthropic
        client = Anthropic()
        system = [{"type": "text", "text": system_prompt, "cache_control": {"type": "ephemeral"}}]
        full_text = ""
        with client.messages.stream(
            model=model_info["model"], max_tokens=1024, system=system, messages=messages,
        ) as stream:
            for text in stream.text_stream:
                full_text += text
                placeholder.markdown(full_text + "▌")
            resp = stream.get_final_message()
        usage = {
            "input": resp.usage.input_tokens, "output": resp.usage.output_tokens,
            "cache_read": getattr(resp.usage, "cache_read_input_tokens", 0),
            "cache_created": getattr(resp.usage, "cache_creation_input_tokens", 0),
        }
        return full_text, usage
    else:
        import google.generativeai as genai
        genai.configure(api_key=os.environ.get("GEMINI_API_KEY", ""))
        model = genai.GenerativeModel(model_info["model"], system_instruction=system_prompt)
        history = [{"role": "user" if m["role"] == "user" else "model", "parts": [m["content"]]} for m in messages[:-1]]
        chat = model.start_chat(history=history)
        full_text = ""
        for chunk in chat.send_message(messages[-1]["content"], stream=True):
            if chunk.text:
                full_text += chunk.text
                placeholder.markdown(full_text + "▌")
        return full_text, {"input": 0, "output": 0, "cache_read": 0, "cache_created": 0}


def warmup_cache(model_id, system_prompt):
    from anthropic import Anthropic
    client = Anthropic()
    system = [{"type": "text", "text": system_prompt, "cache_control": {"type": "ephemeral"}}]
    resp = client.messages.create(model=model_id, max_tokens=16, system=system,
                                   messages=[{"role": "user", "content": "Ready."}])
    return getattr(resp.usage, "cache_creation_input_tokens", 0), getattr(resp.usage, "cache_read_input_tokens", 0)


# ── Helpers ──────────────────────────────────────────────────────────────────

def parse_response(text):
    answer, evidence, source = "", "", ""
    parts = re.split(r'\n(?=ANSWER:|EVIDENCE:|SOURCE:)', text)
    for part in parts:
        if part.startswith("ANSWER:"): answer = part[7:].strip()
        elif part.startswith("EVIDENCE:"): evidence = part[9:].strip()
        elif part.startswith("SOURCE:"): source = part[7:].strip()
    return {"answer": answer or text, "evidence": evidence, "source": source, "raw": text}


def format_bullets(text):
    """Convert →/•/- bullet lines into styled HTML list items."""
    lines = text.strip().split("\n")
    html_parts = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        # Strip leading bullet characters
        cleaned = re.sub(r'^[→•\-\*]\s*', '', line)
        if cleaned:
            html_parts.append(f'<li>{cleaned}</li>')
    if html_parts:
        return '<ul class="answer-bullets">' + ''.join(html_parts) + '</ul>'
    # Fallback: if no bullets detected, just return the text with line breaks
    return text.replace('\n', '<br>')


def build_file_index():
    """Build a filename→path index once at startup. Avoids repeated Desktop access prompts."""
    index = {}
    if SCHOOL_DOCS.exists():
        for f in SCHOOL_DOCS.rglob("*"):
            if f.is_file():
                index[f.name] = str(f)
    return index


def match_citation(cite, idx):
    """Try to find a file matching a single citation string against the file index."""
    doc_exts = {".docx", ".pdf", ".pptx", ".xlsx", ".odt", ".doc", ".html", ".xls"}
    cite_lower = cite.lower().strip()
    if len(cite_lower) < 5:
        return None
    # 1. Exact filename match (case-insensitive)
    for name, path in idx.items():
        if name.lower().strip() == cite_lower:
            return (name, path)
    # 2. Stem match (citation without extension vs file stem)
    cite_no_ext = re.sub(r'\.\w{2,5}$', '', cite_lower).strip()
    for name, path in idx.items():
        if Path(name).suffix.lower() not in doc_exts:
            continue
        if Path(name).stem.lower().strip() == cite_no_ext:
            return (name, path)
    # 3. Clean stem (strip trailing (1)/(2) from file stems)
    cite_clean = re.sub(r'\s*\(\d+\)\s*$', '', cite_no_ext).strip()
    if len(cite_clean) >= 8:
        for name, path in idx.items():
            if Path(name).suffix.lower() not in doc_exts:
                continue
            file_clean = re.sub(r'\s*\(\d+\)\s*$', '', Path(name).stem.lower()).strip()
            if file_clean == cite_clean:
                return (name, path)
    # 4. Containment (citation in file stem or vice versa)
    if len(cite_clean) >= 12:
        for name, path in idx.items():
            if Path(name).suffix.lower() not in doc_exts:
                continue
            file_clean = re.sub(r'\s*\(\d+\)\s*$', '', Path(name).stem.lower()).strip()
            if len(file_clean) >= 12 and (cite_clean in file_clean or file_clean in cite_clean):
                return (name, path)
    return None


def parse_source_citations(source_text):
    """Parse the SOURCE section text into individual citation strings."""
    if not source_text:
        return []
    citations = []
    for line in re.split(r'[\n;]', source_text):
        cite = re.sub(r'^[→•\-\*\d.)\s]+', '', line).strip().rstrip('.')
        if cite and len(cite) > 3:
            citations.append(cite)
    return citations


def read_audio_state():
    try: return json.loads(AUDIO_STATE_FILE.read_text()) if AUDIO_STATE_FILE.exists() else {}
    except: return {}


def audio_worker_running():
    if AUDIO_PID_FILE.exists():
        try:
            os.kill(int(AUDIO_PID_FILE.read_text().strip()), 0)
            return True
        except: AUDIO_PID_FILE.unlink(missing_ok=True)
    return False


def start_audio_worker():
    # Use explicit Python path to ensure correct environment
    python = str(PYTHON_PATH) if PYTHON_PATH.exists() else "python3"
    # Ensure ffmpeg is on PATH for the child process
    env = os.environ.copy()
    for p in ("/opt/homebrew/bin", "/usr/local/bin"):
        if p not in env.get("PATH", ""):
            env["PATH"] = p + ":" + env.get("PATH", "")
    subprocess.Popen(
        [python, str(APP_DIR / "audio_worker.py")],
        cwd=str(APP_DIR),
        env=env,
    )


def stop_audio_worker():
    if AUDIO_PID_FILE.exists():
        try: os.kill(int(AUDIO_PID_FILE.read_text().strip()), 15)
        except: pass
        AUDIO_PID_FILE.unlink(missing_ok=True)
    # Clear state
    if AUDIO_STATE_FILE.exists():
        AUDIO_STATE_FILE.write_text(json.dumps({"status": "stopped", "transcript": "", "questions": [], "last_update": time.time()}))


def clear_audio_questions():
    state = read_audio_state()
    state["questions"] = []
    AUDIO_STATE_FILE.write_text(json.dumps(state))


# ── Page config ──────────────────────────────────────────────────────────────

st.set_page_config(page_title="Ofsted Agent — Castle CE Federation", page_icon="🏫", layout="wide")


# ── Authentication ───────────────────────────────────────────────────────────

def check_password():
    """Password gate for shared governor access. Skipped if no password configured."""
    try:
        password = st.secrets["password"]
    except Exception:
        return True  # No password configured = local dev, no auth needed

    if st.session_state.get("authenticated"):
        return True

    # Login screen
    col1, col2, col3 = st.columns([1, 2, 1])
    with col2:
        if LOGO_FILE.exists():
            logo_b64 = base64.b64encode(LOGO_FILE.read_bytes()).decode()
            st.markdown(f'<div style="text-align:center;padding:2em 0"><img src="data:image/png;base64,{logo_b64}" width="250"></div>', unsafe_allow_html=True)
        st.markdown(f'<h2 style="text-align:center;color:{NAVY}">Ofsted Inspection Agent</h2>', unsafe_allow_html=True)
        st.markdown(f'<p style="text-align:center;color:{GOLD};font-style:italic">"Do everything in love" — 1 Corinthians 16:14</p>', unsafe_allow_html=True)
        st.markdown("---")
        pwd = st.text_input("Governor password", type="password", placeholder="Enter shared password")
        if st.button("Sign in", use_container_width=True, type="primary"):
            if pwd == password:
                st.session_state.authenticated = True
                st.rerun()
            else:
                st.error("Incorrect password. Please try again.")
        st.markdown(f'<p style="text-align:center;color:#999;font-size:0.8em;margin-top:2em">Castle CE Federation — Governor Access Only</p>', unsafe_allow_html=True)
    return False


if not check_password():
    st.stop()

# ── CSS — complete redesign ──────────────────────────────────────────────────

st.markdown(f"""
<style>
    .stApp {{ background-color: {LIGHT_BG}; }}

    /* Hide Streamlit chrome */
    #MainMenu, footer, header {{ visibility: hidden; }}

    /* Sidebar */
    section[data-testid="stSidebar"] {{ background-color: {NAVY}; }}
    section[data-testid="stSidebar"] p,
    section[data-testid="stSidebar"] li,
    section[data-testid="stSidebar"] h1,
    section[data-testid="stSidebar"] h2,
    section[data-testid="stSidebar"] h3,
    section[data-testid="stSidebar"] label {{ color: white !important; }}

    /* Chat input — FORCE visible text */
    [data-testid="stChatInput"] textarea,
    [data-testid="stChatInput"] input,
    .stChatInput textarea,
    .stChatInputContainer textarea,
    div[data-testid="stChatInput"] > div > div > textarea {{
        color: #1a1a2e !important;
        background-color: #ffffff !important;
        caret-color: #1a1a2e !important;
        -webkit-text-fill-color: #1a1a2e !important;
    }}

    /* Live transcript banner */
    .transcript-banner {{
        background: linear-gradient(135deg, {NAVY} 0%, #3d4260 100%);
        color: #8890b0;
        padding: 12px 20px;
        border-radius: 10px;
        font-family: 'SF Mono', 'Menlo', monospace;
        font-size: 0.9em;
        line-height: 1.6;
        margin-bottom: 16px;
        max-height: 100px;
        overflow-y: auto;
        border: 1px solid rgba(193, 165, 89, 0.3);
    }}
    .transcript-banner .live {{ color: #ffffff; font-weight: 500; }}
    .transcript-banner .label {{
        color: {GOLD};
        font-weight: 600;
        font-size: 0.8em;
        text-transform: uppercase;
        letter-spacing: 1px;
        display: block;
        margin-bottom: 4px;
    }}

    /* Mic status pill */
    .mic-pill {{
        display: inline-block;
        padding: 4px 12px;
        border-radius: 20px;
        font-size: 0.8em;
        font-weight: 600;
        margin-left: 8px;
    }}
    .mic-live {{ background: #e54e39; color: white; animation: blink 1.5s infinite; }}
    .mic-loading {{ background: {GOLD}; color: {NAVY}; }}
    .mic-off {{ background: #95a5a5; color: white; }}
    @keyframes blink {{ 0%,100% {{ opacity: 1; }} 50% {{ opacity: 0.5; }} }}

    /* Question bubble */
    .q-bubble {{
        background: {NAVY};
        color: white;
        padding: 14px 20px;
        border-radius: 16px 16px 4px 16px;
        margin: 8px 0;
        font-size: 1.05em;
        line-height: 1.5;
        border-left: 4px solid {GOLD};
        max-width: 85%;
        margin-left: auto;
    }}
    .q-label {{
        color: {GOLD};
        font-size: 0.75em;
        font-weight: 600;
        text-transform: uppercase;
        letter-spacing: 1px;
        margin-bottom: 4px;
    }}

    /* Answer bubble */
    .a-bubble {{
        background: white;
        color: {NAVY};
        padding: 18px 22px;
        border-radius: 16px 16px 16px 4px;
        margin: 8px 0;
        font-size: 1.05em;
        line-height: 1.7;
        border-left: 4px solid #2dcc70;
        box-shadow: 0 2px 8px rgba(0,0,0,0.06);
        max-width: 90%;
    }}
    .a-label {{
        color: #2dcc70;
        font-size: 0.75em;
        font-weight: 600;
        text-transform: uppercase;
        letter-spacing: 1px;
        margin-bottom: 6px;
    }}

    /* Answer bullet list — large, spaced, scannable */
    .answer-bullets {{
        list-style: none;
        padding: 0;
        margin: 4px 0 0 0;
    }}
    .answer-bullets li {{
        padding: 6px 0 6px 20px;
        position: relative;
        font-size: 1.05em;
        line-height: 1.4;
        border-bottom: 1px solid #eef0f4;
    }}
    .answer-bullets li:last-child {{ border-bottom: none; }}
    .answer-bullets li::before {{
        content: "→";
        position: absolute;
        left: 0;
        color: #2dcc70;
        font-weight: 700;
    }}

    /* Evidence bullet list — slightly smaller */
    .evidence-inline .answer-bullets li {{
        font-size: 0.9em;
        padding: 4px 0 4px 18px;
        color: #555;
    }}
    .evidence-inline .answer-bullets li::before {{
        color: {GOLD};
    }}

    /* Evidence inline card */
    .evidence-inline {{
        background: #faf8f2;
        border: 1px solid {GOLD};
        border-radius: 8px;
        padding: 12px 16px;
        margin-top: 10px;
        font-size: 0.9em;
        color: #555;
    }}
    .evidence-inline strong {{ color: {NAVY}; }}

    /* Source label (non-clickable fallback) */
    .source-label {{
        color: #888;
        font-size: 0.8em;
        margin-top: 8px;
    }}

    /* Warmup overlay */
    .warmup-overlay {{
        background: linear-gradient(135deg, {NAVY}, #3d4260);
        color: {GOLD};
        padding: 40px;
        border-radius: 16px;
        text-align: center;
        margin: 40px 0;
    }}
    .warmup-overlay h2 {{ color: white; margin-bottom: 8px; }}
    .warmup-overlay .spinner {{
        display: inline-block;
        width: 40px; height: 40px;
        border: 4px solid rgba(193,165,89,0.3);
        border-top: 4px solid {GOLD};
        border-radius: 50%;
        animation: spin 1s linear infinite;
        margin-bottom: 16px;
    }}
    @keyframes spin {{ to {{ transform: rotate(360deg); }} }}

    /* Ready banner */
    .ready-banner {{
        background: #2dcc70;
        color: white;
        padding: 14px 20px;
        border-radius: 10px;
        text-align: center;
        font-size: 1.05em;
        margin-bottom: 16px;
    }}



    /* Motto */
    .motto {{ color: {GOLD}; font-style: italic; font-size: 0.85em; text-align: center; padding: 0.5em; }}
</style>
""", unsafe_allow_html=True)

# ── Session state ────────────────────────────────────────────────────────────

for key, default in [
    ("messages", []), ("evidence_history", []),
    ("cache_warmed", False), ("warming_up", False), ("processed_questions", set()),
    ("token_count", {"input": 0, "output": 0, "cache_read": 0}),
]:
    if key not in st.session_state:
        st.session_state[key] = default

if "context" not in st.session_state:
    st.session_state.context = CONTEXT_FILE.read_text() if CONTEXT_FILE.exists() else ""


# ── Sidebar ──────────────────────────────────────────────────────────────────

with st.sidebar:
    if LOGO_FILE.exists():
        logo_b64 = base64.b64encode(LOGO_FILE.read_bytes()).decode()
        st.markdown(f'<div style="text-align:center;padding:1em 0"><img src="data:image/png;base64,{logo_b64}" width="200"></div>', unsafe_allow_html=True)

    st.markdown('<p class="motto">"Do everything in love" — 1 Corinthians 16:14</p>', unsafe_allow_html=True)
    st.markdown("---")

    st.markdown("### School")
    school_focus = st.selectbox("Select school", list(SCHOOL_FOCUS.keys()), index=0, label_visibility="collapsed")

    st.markdown("### Model")
    model_choice = st.selectbox("Select model", list(MODELS.keys()), index=0, label_visibility="collapsed")
    model_info = MODELS[model_choice]

    st.markdown("### Input")
    if IS_CLOUD:
        input_mode = "Text"
        st.selectbox("Select input", ["Text", "Audio (Mic) — local only"], index=0, disabled=True, label_visibility="collapsed")
        st.caption("🔇 Mic input requires the desktop app")
    else:
        input_mode = st.selectbox("Select input", ["Text", "Audio (Mic)"], index=0, label_visibility="collapsed")

    if input_mode == "Audio (Mic)":
        is_running = audio_worker_running()
        audio_state = read_audio_state()
        status = audio_state.get("status", "stopped")

        if is_running:
            if status == "loading_whisper":
                st.markdown('<span class="mic-pill mic-loading">Loading Whisper...</span>', unsafe_allow_html=True)
            elif status.startswith("error"):
                st.error(f"Mic error: {status}")
                if st.button("Retry", use_container_width=True):
                    stop_audio_worker()
                    time.sleep(0.5)
                    start_audio_worker()
                    st.rerun()
            else:
                st.markdown('<span class="mic-pill mic-live">🔴 LIVE</span>', unsafe_allow_html=True)
            if st.button("Stop Mic", use_container_width=True):
                stop_audio_worker()
                st.rerun()
        else:
            st.markdown('<span class="mic-pill mic-off">Mic off</span>', unsafe_allow_html=True)
            if st.button("Start Mic", use_container_width=True, type="primary"):
                start_audio_worker()
                time.sleep(2)  # Give Whisper time to start loading
                st.rerun()

    st.markdown("---")
    st.markdown("### Session")
    n_questions = len([m for m in st.session_state.messages if m["role"] == "user"])
    total_tok = st.session_state.token_count["input"] + st.session_state.token_count["output"]
    st.markdown(f"Questions: **{n_questions}** · Tokens: **{total_tok:,}**")
    st.markdown(f"Cache: **{'Warmed' if st.session_state.cache_warmed else 'Cold'}**")

    st.markdown("---")
    st.markdown("### Budget ($50)")
    cached = st.session_state.token_count.get("cache_read", 0)
    cost = ((st.session_state.token_count["input"] - cached) / 1e6) * 1.0 + (cached / 1e6) * 0.10 + (st.session_state.token_count["output"] / 1e6) * 5.0
    remaining = 50.0 - cost
    st.progress(max(0.0, min(1.0, remaining / 50.0)))
    st.markdown(f"**${cost:.3f}** used · **${remaining:.2f}** left")
    if remaining < 5: st.warning("Low! Switch to Gemini Flash.")

    st.markdown("---")
    st.markdown('<p style="color:#95a5a5;font-size:0.7em;text-align:center">Kindness · Resilience · Respect · Thankfulness<br>Flourishing in Learning and Love</p>', unsafe_allow_html=True)
    if TREE_FILE.exists():
        tree_b64 = base64.b64encode(TREE_FILE.read_bytes()).decode()
        st.markdown(f'<div style="text-align:center;opacity:0.4"><img src="data:image/png;base64,{tree_b64}" width="70"></div>', unsafe_allow_html=True)


# ── Main area ────────────────────────────────────────────────────────────────

with st.container():
    if not st.session_state.context:
        st.error("No documents loaded. Run `crawl_and_prepare.py` first.")
        st.stop()

    # ── Build file index on first run (local only — no school docs in cloud) ──
    if "file_index" not in st.session_state:
        st.session_state.file_index = build_file_index() if not IS_CLOUD else {}

    # ── Cache warmup ──
    if not st.session_state.cache_warmed and model_info["provider"] == "anthropic" and not st.session_state.warming_up:
        st.session_state.warming_up = True
        st.markdown(
            '<div class="warmup-overlay">'
            '<div class="spinner"></div>'
            '<h2>Loading School Documents</h2>'
            '<p>Caching 177,000 tokens of school documents...<br>This takes ~15 seconds on first load, then answers are fast.</p>'
            '</div>', unsafe_allow_html=True,
        )
        try:
            sys_prompt = build_system_prompt(school_focus, st.session_state.context)
            warmup_cache(model_info["model"], sys_prompt)
            st.session_state.cache_warmed = True
            st.session_state.warming_up = False
            st.rerun()
        except Exception as e:
            st.error(f"Cache warmup failed: {e}")
            st.session_state.warming_up = False

    elif st.session_state.cache_warmed and not st.session_state.messages:
        n_files = len(st.session_state.get("file_index", {}))
        st.markdown(f'<div class="ready-banner">✅ Ready — documents cached, {n_files:,} files indexed. Ask a question below.</div>', unsafe_allow_html=True)

    # ── Chat history ──
    for i, msg in enumerate(st.session_state.messages):
        if msg["role"] == "user":
            st.markdown(
                f'<div class="q-bubble">'
                f'<div class="q-label">Inspector Question</div>'
                f'{msg["content"]}'
                f'</div>', unsafe_allow_html=True,
            )
        else:
            parsed = msg.get("parsed", {})
            answer = parsed.get("answer", msg["content"]) if parsed else msg["content"]
            evidence = parsed.get("evidence", "") if parsed else ""
            source = parsed.get("source", "") if parsed else ""

            answer_html = format_bullets(answer)
            html = f'<div class="a-bubble"><div class="a-label">Governor Response</div>{answer_html}'
            if evidence:
                evidence_html = format_bullets(evidence)
                html += f'<div class="evidence-inline"><strong>📊 Evidence:</strong>{evidence_html}</div>'
            html += '</div>'
            st.markdown(html, unsafe_allow_html=True)

            # Source document references
            if source:
                citations = parse_source_citations(source)
                if IS_CLOUD:
                    # Cloud mode: show source names as styled references
                    if citations:
                        refs = " · ".join(f"📄 {c}" for c in citations[:5])
                        st.markdown(f'<div class="source-label">{refs}</div>', unsafe_allow_html=True)
                else:
                    # Local mode: clickable buttons that open files
                    idx = st.session_state.get("file_index", {})
                    for fi, cite in enumerate(citations[:5]):
                        file_match = match_citation(cite, idx) if idx else None
                        if file_match:
                            if st.button(f"📂  Open: {file_match[0]}", key=f"doc_{i}_{fi}", use_container_width=True):
                                subprocess.Popen(["open", file_match[1]])
                                st.toast(f"Opened: {file_match[0]}")
                        else:
                            st.caption(f"📄 {cite}")

    # ── Process last unanswered message ──
    if st.session_state.messages and st.session_state.messages[-1]["role"] == "user":
        question = st.session_state.messages[-1]["content"]
        sys_prompt = build_system_prompt(school_focus, st.session_state.context)
        api_msgs = [{"role": m["role"], "content": m["content"]} for m in st.session_state.messages]
        if len(api_msgs) > 12: api_msgs = api_msgs[-12:]

        placeholder = st.empty()
        try:
            full_text, usage = query_model(model_info, sys_prompt, api_msgs, placeholder)
            st.session_state.token_count["input"] += usage.get("input", 0)
            st.session_state.token_count["output"] += usage.get("output", 0)
            st.session_state.token_count["cache_read"] += usage.get("cache_read", 0)
            if usage.get("cache_read", 0) > 0 or usage.get("cache_created", 0) > 0:
                st.session_state.cache_warmed = True

            parsed = parse_response(full_text)
            ev_idx = len(st.session_state.evidence_history)
            st.session_state.evidence_history.append({
                "question": question, "evidence": parsed["evidence"],
                "source": parsed["source"],
                "raw": full_text,
            })
            st.session_state.messages.append({
                "role": "assistant", "content": full_text,
                "parsed": parsed, "evidence_idx": ev_idx,
            })
            placeholder.empty()
            st.rerun()
        except Exception as e:
            placeholder.error(f"Error: {e}")
            st.session_state.messages.append({"role": "assistant", "content": f"Error: {e}", "parsed": {}, "evidence_idx": None})

    # ── Live transcript (at bottom, just above input) ──
    if input_mode == "Audio (Mic)" and audio_worker_running():
        audio_state = read_audio_state()
        status = audio_state.get("status", "")
        transcript = audio_state.get("transcript", "")
        current_utterance = audio_state.get("current_utterance", "")

        if status == "loading_whisper":
            st.markdown(
                '<div class="transcript-banner">'
                '<span class="label">🎙️ Microphone</span>'
                'Loading speech recognition model... (first time takes ~10s)'
                '</div>', unsafe_allow_html=True,
            )
        elif transcript or current_utterance:
            # Show recent transcript
            recent = transcript[-200:] if len(transcript) > 200 else transcript
            st.markdown(
                f'<div class="transcript-banner">'
                f'<span class="label">🎙️ Live Transcript</span>'
                f'<span class="live">{recent}</span>'
                f'</div>', unsafe_allow_html=True,
            )
            # Show current utterance being built + submit button
            if current_utterance:
                tc1, tc2 = st.columns([4, 1])
                with tc1:
                    st.markdown(
                        f'<div style="background:#1e2238;color:{GOLD};padding:8px 16px;border-radius:8px;'
                        f'font-size:0.85em;border:1px dashed {GOLD};">'
                        f'<strong>Capturing:</strong> {current_utterance}'
                        f'<span style="color:#8890b0;font-size:0.85em"> — auto-submits after 3.5s pause</span>'
                        f'</div>', unsafe_allow_html=True,
                    )
                with tc2:
                    if st.button("⏎ Submit", key="force_submit", use_container_width=True, type="primary"):
                        try:
                            state = json.loads(AUDIO_STATE_FILE.read_text()) if AUDIO_STATE_FILE.exists() else {}
                            state["force_submit"] = True
                            AUDIO_STATE_FILE.write_text(json.dumps(state))
                        except:
                            pass
                        time.sleep(0.5)
                        st.rerun()
        else:
            st.markdown(
                '<div class="transcript-banner">'
                '<span class="label">🎙️ Listening</span>'
                'Waiting for speech...'
                '</div>', unsafe_allow_html=True,
            )

        # Auto-process detected questions
        for q in audio_state.get("questions", []):
            if q not in st.session_state.processed_questions:
                st.session_state.processed_questions.add(q)
                clear_audio_questions()
                st.session_state.messages.append({"role": "user", "content": q})
                st.rerun()

    # ── Chat input (always at bottom) ──
    question = st.chat_input("Ask an Ofsted inspection question...")
    if question:
        st.session_state.messages.append({"role": "user", "content": question})
        st.rerun()


# ── Audio auto-refresh ───────────────────────────────────────────────────────

if input_mode == "Audio (Mic)" and audio_worker_running():
    time.sleep(2)
    st.rerun()
