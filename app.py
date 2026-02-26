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

import requests
import streamlit as st
from datetime import datetime as _dt

from shared_chat import get_shared_chat, get_display_name

# ── Paths ────────────────────────────────────────────────────────────────────

APP_DIR = Path(__file__).parent
CONTEXT_FILE = APP_DIR / "combined_context.md"          # local (unencrypted)
CONTEXT_FILE_ENC = APP_DIR / "combined_context.md.enc"  # repo (encrypted)
ENV_FILE = APP_DIR / ".env"
LOGO_FILE = APP_DIR / "logo.png"
TREE_FILE = APP_DIR / "tree.png"
SCHOOL_DOCS = Path.home() / "Desktop" / "school docs"
GDRIVE_LINKS_FILE = APP_DIR / "gdrive_links.json"
AUDIO_STATE_FILE = APP_DIR / "audio_state.json"
AUDIO_PID_FILE = APP_DIR / "audio_worker.pid"
AUDIO_LOG_FILE = APP_DIR / "audio_worker.log"
PYTHON_PATH = Path.home() / "anaconda3" / "bin" / "python"

# ── Environment detection ────────────────────────────────────────────────────

IS_CLOUD = not Path.home().joinpath("Desktop", "school docs").exists()


def _load_gdrive_links():
    """Load Google Drive link mapping (filename → drive_url)."""
    if GDRIVE_LINKS_FILE.exists():
        try:
            with open(GDRIVE_LINKS_FILE) as f:
                return json.load(f)
        except Exception:
            pass
    return {}


# ── User identity helpers ────────────────────────────────────────────────────

def get_user_email():
    if st.session_state.get("auth_email"):
        return st.session_state["auth_email"]
    # Native iOS app passes ?app_user=tim to identify the user
    app_user = st.query_params.get("app_user")
    if app_user:
        # Map known app users to their email addresses
        app_user_map = {"tim": "tim.trailor@castlefederation.org"}
        if app_user.lower() in app_user_map:
            email = app_user_map[app_user.lower()]
            st.session_state.auth_email = email
            st.session_state.authenticated = True
            return email
    # Local mode: each tab gets a unique session identity so active_users
    # can distinguish them (auth is skipped locally → no email set).
    if "local_session_id" not in st.session_state:
        import uuid
        st.session_state.local_session_id = uuid.uuid4().hex[:6]
    return f"local-{st.session_state.local_session_id}@localhost"


def get_user_name():
    return get_display_name(get_user_email())

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
    # Fall back to unified credentials.py (local dev)
    if not os.environ.get("ANTHROPIC_API_KEY"):
        try:
            import sys
            sys.path.insert(0, str(APP_DIR.parent))
            from credentials import ANTHROPIC_API_KEY, GEMINI_API_KEY
            os.environ["ANTHROPIC_API_KEY"] = ANTHROPIC_API_KEY
            os.environ["GEMINI_API_KEY"] = GEMINI_API_KEY
        except ImportError:
            pass
    # Final fallback: .env file
    if not os.environ.get("ANTHROPIC_API_KEY") and ENV_FILE.exists():
        for line in ENV_FILE.read_text().splitlines():
            line = line.strip()
            if "=" in line and not line.startswith("#"):
                key, val = line.split("=", 1)
                os.environ[key.strip()] = val.strip().strip('"').strip("'")

load_env()

# ── Google Sheets logging ────────────────────────────────────────────────────

_LOG_HEADERS = [
    "Timestamp", "Event", "Email", "School Focus", "Model",
    "Input Mode", "Question", "Answer", "Sources",
    "Tokens In", "Tokens Out", "Cache Hit",
]


def _get_gspread_client():
    """Return a cached gspread client, or None if unavailable."""
    if "gs_client" in st.session_state:
        return st.session_state.gs_client
    try:
        import gspread
        from google.oauth2.service_account import Credentials

        scopes = [
            "https://www.googleapis.com/auth/spreadsheets",
            "https://www.googleapis.com/auth/drive",
        ]
        # Cloud: credentials in Streamlit secrets
        try:
            info = dict(st.secrets["gcp_service_account"])
            creds = Credentials.from_service_account_info(info, scopes=scopes)
        except Exception:
            # Local: unified credentials.py
            try:
                import sys
                sys.path.insert(0, str(APP_DIR.parent))
                from credentials import GCP_SERVICE_ACCOUNT
                creds = Credentials.from_service_account_info(GCP_SERVICE_ACCOUNT, scopes=scopes)
            except (ImportError, Exception):
                st.session_state.gs_client = None
                return None

        client = gspread.authorize(creds)
        st.session_state.gs_client = client
        return client
    except Exception:
        st.session_state.gs_client = None
        return None


def _get_sheet_id():
    """Return the Google Sheet ID from secrets or .env."""
    try:
        return st.secrets["GOOGLE_SHEETS_ID"]
    except Exception:
        return os.environ.get("GOOGLE_SHEETS_ID", "")


def log_event(event, email="", school="", model="", input_mode="",
              question="", answer="", sources="",
              tokens_in=0, tokens_out=0, cache_hit=False):
    """Append one row to the Activity Log sheet. Never raises."""
    try:
        client = _get_gspread_client()
        sheet_id = _get_sheet_id()
        if not client or not sheet_id:
            return

        spreadsheet = client.open_by_key(sheet_id)
        try:
            worksheet = spreadsheet.worksheet("Activity Log")
        except Exception:
            worksheet = spreadsheet.sheet1
            worksheet.update_title("Activity Log")

        # Auto-create headers if sheet is empty
        if not worksheet.row_values(1):
            worksheet.append_row(_LOG_HEADERS, value_input_option="RAW")

        row = [
            _dt.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC"),
            event,
            email,
            school,
            model,
            input_mode,
            question[:1000] if question else "",
            answer[:2000] if answer else "",
            sources[:500] if sources else "",
            tokens_in,
            tokens_out,
            "Yes" if cache_hit else "No",
        ]
        worksheet.append_row(row, value_input_option="RAW")
    except Exception:
        pass  # Silent failure — logging must never break the app


# ── Models ───────────────────────────────────────────────────────────────────

MODELS = {
    "Haiku (fastest)": {"provider": "anthropic", "model": "claude-haiku-4-5-20251001"},
    "Sonnet (balanced)": {"provider": "anthropic", "model": "claude-sonnet-4-5-20250929"},
    "Gemini Flash (free)": {"provider": "gemini", "model": "gemini-2.0-flash"},
}

NAVY = "#2a2e45"
GOLD = "#C1A559"
LIGHT_BG = "#f7f7f7"

# Token budget: Claude's 200K context limit minus headroom for system prompt,
# tool definitions, messages, and tokenizer differences (tiktoken undercounts vs Claude)
MAX_CONTEXT_TOKENS = 150_000

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
- NEVER reference, quote, or discuss Part 2 minutes — these are highly confidential and not available

--- SCHOOL DOCUMENTS ---

{context}"""


def trim_context(text, max_tokens=MAX_CONTEXT_TOKENS):
    """Trim context to fit within token budget using tiktoken as approximation."""
    try:
        import tiktoken
        enc = tiktoken.get_encoding("cl100k_base")
        tokens = enc.encode(text)
        if len(tokens) > max_tokens:
            text = enc.decode(tokens[:max_tokens])
            text += "\n\n[... context trimmed to fit token budget ...]\n"
        return text
    except ImportError:
        # Fallback: rough char-based trim (4 chars ≈ 1 token)
        max_chars = max_tokens * 4
        if len(text) > max_chars:
            text = text[:max_chars] + "\n\n[... context trimmed to fit token budget ...]\n"
        return text


def build_system_prompt(school_focus_key, context, policy_names=None):
    prompt = SYSTEM_PROMPT_TEMPLATE.format(
        school_focus=SCHOOL_FOCUS[school_focus_key],
        context=context,
    )
    if policy_names:
        policy_list = ", ".join(policy_names)
        prompt += (
            "\n\n--- REAL-TIME POLICY LOOKUP ---\n\n"
            "You have a tool called \"fetch_policy\" that can download policy documents "
            "from the Castle Federation website in real time. Use it ONLY when the question "
            "asks about a specific policy not already covered in the documents above.\n\n"
            f"Available policies: {policy_list}"
        )
    return prompt


# ── Policy fetching tool ────────────────────────────────────────────────────

POLICIES_PAGE_URL = "https://www.castlefederation.org/Policies/"

FETCH_POLICY_TOOL = {
    "name": "fetch_policy",
    "description": (
        "Fetch a specific policy document from the Castle Federation website. "
        "Use this when the question is about a school policy that isn't in the "
        "pre-loaded documents above. Returns the full text of the policy PDF."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "policy_name": {
                "type": "string",
                "description": "The name of the policy to fetch, from the available policies list.",
            }
        },
        "required": ["policy_name"],
    },
}


def get_policy_index():
    """Fetch the policies page and return a dict of {name: download_url}."""
    from urllib.parse import unquote

    try:
        resp = requests.get(
            f"https://r.jina.ai/{POLICIES_PAGE_URL}",
            timeout=30,
            headers={"Accept": "text/markdown"},
        )
        if resp.status_code != 200:
            return {}
        urls = re.findall(
            r'https://www\.castlefederation\.org/admin/inc/FrontEndFiles/AutoLists/download/\?url=[^\s\)]+\.pdf',
            resp.text, re.IGNORECASE,
        )
        urls += re.findall(
            r'https://www\.castlefederation\.org/admin/inc/FrontEndFiles/AutoLists/download/\?url=[^\s\)]+%2Epdf',
            resp.text, re.IGNORECASE,
        )
        urls = list(dict.fromkeys(urls))  # deduplicate
        if not urls:
            urls = re.findall(
                r'https://www\.castlefederation\.org[^\s\)]*\.pdf',
                resp.text, re.IGNORECASE,
            )
            urls = list(dict.fromkeys(urls))
        index = {}
        for url in urls:
            name_part = unquote(url.split("url=")[-1] if "url=" in url else url.split("/")[-1])
            name = name_part.replace("/docs/policies/", "").replace(".pdf", "").replace("_", " ").strip()
            if name:
                index[name] = url
        return index
    except Exception:
        return {}


def fetch_single_policy(policy_name, policy_index):
    """Download and extract text from a single policy PDF."""
    import io

    try:
        import pdfplumber
    except ImportError:
        return "PDF extraction not available (pdfplumber not installed)"

    url = policy_index.get(policy_name)
    if not url:
        # Fuzzy match
        policy_lower = policy_name.lower()
        for name, u in policy_index.items():
            if policy_lower in name.lower() or name.lower() in policy_lower:
                url = u
                policy_name = name
                break
    if not url:
        return f"Policy '{policy_name}' not found. Available: {', '.join(policy_index.keys())}"
    try:
        resp = requests.get(url, timeout=30)
        if resp.status_code != 200:
            return f"Failed to download policy (HTTP {resp.status_code})"
        text = ""
        with pdfplumber.open(io.BytesIO(resp.content)) as pdf:
            for page in pdf.pages:
                page_text = page.extract_text()
                if page_text:
                    text += page_text + "\n\n"
                for table in page.extract_tables():
                    for row in table:
                        cells = [str(c).strip() for c in row if c]
                        if cells:
                            text += " | ".join(cells) + "\n"
        if text.strip():
            return f"[POLICY: {policy_name}]\n\n{text.strip()}"
        return f"No text could be extracted from {policy_name}"
    except Exception as e:
        return f"Error fetching policy: {e}"


# ── API ──────────────────────────────────────────────────────────────────────

def query_model(model_info, system_prompt, messages, placeholder, policy_index=None):
    if model_info["provider"] == "anthropic":
        from anthropic import Anthropic
        client = Anthropic()
        system = [{"type": "text", "text": system_prompt, "cache_control": {"type": "ephemeral"}}]
        tools = [FETCH_POLICY_TOOL] if policy_index else None

        full_text = ""
        total_usage = {"input": 0, "output": 0, "cache_read": 0, "cache_created": 0}

        kwargs = dict(model=model_info["model"], max_tokens=1024, system=system, messages=messages)
        if tools:
            kwargs["tools"] = tools

        with client.messages.stream(**kwargs) as stream:
            for text in stream.text_stream:
                full_text += text
                placeholder.markdown(full_text + "▌")
            resp = stream.get_final_message()

        total_usage["input"] += resp.usage.input_tokens
        total_usage["output"] += resp.usage.output_tokens
        total_usage["cache_read"] += getattr(resp.usage, "cache_read_input_tokens", 0)
        total_usage["cache_created"] += getattr(resp.usage, "cache_creation_input_tokens", 0)

        # Handle tool use — fetch policy from website
        if resp.stop_reason == "tool_use" and policy_index:
            tool_block = next((b for b in resp.content if b.type == "tool_use"), None)

            if tool_block and tool_block.name == "fetch_policy":
                policy_name = tool_block.input.get("policy_name", "")
                placeholder.markdown(f"📄 *Fetching policy from school website: **{policy_name}**...*")

                policy_text = fetch_single_policy(policy_name, policy_index)

                # Build assistant content as dicts for the API
                assistant_content = []
                for block in resp.content:
                    if block.type == "text" and block.text:
                        assistant_content.append({"type": "text", "text": block.text})
                    elif block.type == "tool_use":
                        assistant_content.append({
                            "type": "tool_use", "id": block.id,
                            "name": block.name, "input": block.input,
                        })

                tool_messages = messages + [
                    {"role": "assistant", "content": assistant_content},
                    {"role": "user", "content": [
                        {"type": "tool_result", "tool_use_id": tool_block.id, "content": policy_text}
                    ]},
                ]

                full_text = ""
                with client.messages.stream(
                    model=model_info["model"], max_tokens=1024, system=system,
                    messages=tool_messages, tools=tools,
                ) as stream2:
                    for text in stream2.text_stream:
                        full_text += text
                        placeholder.markdown(full_text + "▌")
                    resp2 = stream2.get_final_message()

                total_usage["input"] += resp2.usage.input_tokens
                total_usage["output"] += resp2.usage.output_tokens
                total_usage["cache_read"] += getattr(resp2.usage, "cache_read_input_tokens", 0)
                total_usage["cache_created"] += getattr(resp2.usage, "cache_creation_input_tokens", 0)

        return full_text, total_usage
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


# ── Authentication — email magic code for @castlefederation.org ──────────────

import hmac
import hashlib
import smtplib
from datetime import datetime, timedelta
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from extra_streamlit_components import CookieManager


def generate_code(email, secret, window_minutes=10):
    """Generate a time-based 6-digit code. Valid for window_minutes."""
    time_window = int(time.time()) // (window_minutes * 60)
    msg = f"{email.lower().strip()}:{time_window}".encode()
    h = hmac.new(secret.encode(), msg, hashlib.sha256).hexdigest()
    return str(int(h[:8], 16) % 1000000).zfill(6)


def verify_code(email, code, secret, window_minutes=10):
    """Verify code — check current and previous time window for grace period."""
    current = generate_code(email, secret, window_minutes)
    time_window_prev = int(time.time()) // (window_minutes * 60) - 1
    msg_prev = f"{email.lower().strip()}:{time_window_prev}".encode()
    h_prev = hmac.new(secret.encode(), msg_prev, hashlib.sha256).hexdigest()
    previous = str(int(h_prev[:8], 16) % 1000000).zfill(6)
    return code == current or code == previous


def send_code_email(email, code):
    """Send the sign-in code via SMTP."""
    try:
        smtp_host = st.secrets.get("SMTP_HOST", "smtp.gmail.com")
        smtp_port = int(st.secrets.get("SMTP_PORT", 587))
        smtp_user = st.secrets["SMTP_USER"]
        smtp_pass = st.secrets["SMTP_PASS"]
    except Exception:
        return False, "Email not configured. Contact the Chair of Governors."

    msg = MIMEMultipart()
    msg["From"] = f"Castle CE Federation <{smtp_user}>"
    msg["To"] = email
    msg["Subject"] = "Your Ofsted Agent sign-in code"
    body = f"""Hello,

Your sign-in code for the Castle CE Federation Ofsted Agent is:

    {code}

This code is valid for 10 minutes.

If you did not request this, please ignore this email.

Castle CE Federation
"Do everything in love" — 1 Corinthians 16:14
"""
    msg.attach(MIMEText(body, "plain"))
    try:
        with smtplib.SMTP(smtp_host, smtp_port) as server:
            server.starttls()
            server.login(smtp_user, smtp_pass)
            server.send_message(msg)
        return True, "Code sent"
    except Exception as e:
        return False, f"Failed to send email: {e}"


def create_auth_token(email, secret, days=7):
    """Create a signed token that expires in N days."""
    expiry = int(time.time()) + (days * 86400)
    payload = f"{email.lower().strip()}:{expiry}"
    signature = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()[:16]
    return base64.b64encode(f"{payload}:{signature}".encode()).decode()


def verify_auth_token(token, secret):
    """Verify a signed auth token. Returns email if valid, None if expired/invalid."""
    try:
        decoded = base64.b64decode(token).decode()
        payload, signature = decoded.rsplit(":", 1)
        expected = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()[:16]
        if signature != expected:
            return None
        email, expiry = payload.rsplit(":", 1)
        if int(expiry) < int(time.time()):
            return None
        return email
    except Exception:
        return None


def check_auth():
    """Email-based auth for @castlefederation.org. Active in cloud, skipped locally."""
    # Skip auth locally (not cloud)
    if not IS_CLOUD:
        return True
    # Skip if SMTP not configured yet
    try:
        _ = st.secrets["SMTP_USER"]
    except Exception:
        return True

    if st.session_state.get("authenticated"):
        return True

    # Check for remember-me cookie
    cookie_manager = CookieManager(key="auth_cookies")
    token = cookie_manager.get("ofsted_auth")
    if token:
        secret = st.secrets.get("AUTH_SECRET", "castle-fed-2026")
        email = verify_auth_token(token, secret)
        if email:
            st.session_state.authenticated = True
            st.session_state.auth_email = email
            log_event("cookie_login", email=email)
            return True

    # Initialize auth state
    if "auth_step" not in st.session_state:
        st.session_state.auth_step = "email"
    if "auth_email" not in st.session_state:
        st.session_state.auth_email = ""

    col1, col2, col3 = st.columns([1, 2, 1])
    with col2:
        if LOGO_FILE.exists():
            logo_b64 = base64.b64encode(LOGO_FILE.read_bytes()).decode()
            st.markdown(f'<div style="text-align:center;padding:2em 0"><img src="data:image/png;base64,{logo_b64}" width="250"></div>', unsafe_allow_html=True)
        st.markdown(f'<h2 style="text-align:center;color:{NAVY}">Ofsted Inspection Agent</h2>', unsafe_allow_html=True)
        st.markdown(f'<p style="text-align:center;color:{GOLD};font-style:italic">"Do everything in love" — 1 Corinthians 16:14</p>', unsafe_allow_html=True)
        st.markdown("---")
        st.markdown('<style>input[type="text"], input[type="password"] { color: #1a1a2e !important; background: #ffffff !important; -webkit-text-fill-color: #1a1a2e !important; }</style>', unsafe_allow_html=True)

        if st.session_state.auth_step == "email":
            email = st.text_input("Governor email address", placeholder="name@castlefederation.org")
            if st.button("Send sign-in code", use_container_width=True, type="primary"):
                email = email.strip().lower()
                if not email.endswith("@castlefederation.org"):
                    st.error("Please use your @castlefederation.org email address.")
                else:
                    secret = st.secrets.get("AUTH_SECRET", "castle-fed-2026")
                    code = generate_code(email, secret)
                    ok, msg = send_code_email(email, code)
                    if ok:
                        st.session_state.auth_email = email
                        st.session_state.auth_step = "code"
                        st.rerun()
                    else:
                        st.error(msg)

        elif st.session_state.auth_step == "code":
            st.success(f"Code sent to **{st.session_state.auth_email}**")
            st.caption("Check your inbox (and spam folder). The code is valid for 10 minutes.")
            code = st.text_input("Enter 6-digit code", max_chars=6, placeholder="000000")
            c1, c2 = st.columns(2)
            with c1:
                if st.button("Verify", use_container_width=True, type="primary"):
                    secret = st.secrets.get("AUTH_SECRET", "castle-fed-2026")
                    if verify_code(st.session_state.auth_email, code.strip(), secret):
                        st.session_state.authenticated = True
                        log_event("login", email=st.session_state.auth_email)
                        # Set 7-day remember-me cookie
                        token = create_auth_token(st.session_state.auth_email, secret, days=7)
                        cookie_manager = CookieManager(key="auth_cookies_set")
                        cookie_manager.set("ofsted_auth", token,
                                           expires_at=datetime.now() + timedelta(days=7))
                        st.rerun()
                    else:
                        st.error("Invalid or expired code. Please try again.")
            with c2:
                if st.button("Back", use_container_width=True):
                    st.session_state.auth_step = "email"
                    st.rerun()

        st.markdown(f'<p style="text-align:center;color:#999;font-size:0.8em;margin-top:2em">Castle CE Federation — Governor Access Only</p>', unsafe_allow_html=True)
    return False


if not check_auth():
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

    /* Processing banner */
    .processing-banner {{
        background: linear-gradient(135deg, {NAVY} 0%, #3d4260 100%);
        color: white;
        padding: 14px 20px;
        border-radius: 10px;
        margin: 8px 0 16px 0;
        border-left: 4px solid {GOLD};
        animation: pulse-bg 2s ease-in-out infinite;
    }}
    .processing-banner .proc-label {{
        color: {GOLD};
        font-size: 0.75em;
        font-weight: 600;
        text-transform: uppercase;
        letter-spacing: 1px;
    }}
    @keyframes pulse-bg {{
        0%, 100% {{ opacity: 1; }}
        50% {{ opacity: 0.85; }}
    }}

    /* Active users pill */
    .active-users {{
        color: #95a5a5;
        font-size: 0.85em;
        line-height: 1.4;
    }}
    .active-users .online-dot {{
        display: inline-block;
        width: 8px; height: 8px;
        background: #2dcc70;
        border-radius: 50%;
        margin-right: 4px;
    }}
</style>
""", unsafe_allow_html=True)

# ── Session state ────────────────────────────────────────────────────────────

for key, default in [
    ("cache_warmed", False), ("warming_up", False), ("processed_questions", set()),
    ("token_count", {"input": 0, "output": 0, "cache_read": 0}),
    ("last_msg_count", 0), ("pending_answer", False),
]:
    if key not in st.session_state:
        st.session_state[key] = default

if "context" not in st.session_state:
    if CONTEXT_FILE.exists():
        # Local: read plaintext directly
        st.session_state.context = trim_context(CONTEXT_FILE.read_text())
    elif CONTEXT_FILE_ENC.exists():
        # Cloud: decrypt from encrypted file using key in secrets
        try:
            from cryptography.fernet import Fernet
            key = st.secrets["CONTEXT_KEY"].encode()
            st.session_state.context = trim_context(
                Fernet(key).decrypt(CONTEXT_FILE_ENC.read_bytes()).decode()
            )
        except Exception as e:
            st.error(f"Failed to decrypt context: {e}")
            st.session_state.context = ""
    else:
        st.session_state.context = ""

if "policy_index" not in st.session_state:
    try:
        st.session_state.policy_index = get_policy_index()
    except Exception:
        st.session_state.policy_index = {}


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

    # Active users
    shared_chat = get_shared_chat()
    active = shared_chat.heartbeat(get_user_email(), get_user_name())
    if active:
        names = sorted(set(info["name"] for info in active.values()))
        names_str = ", ".join(names)
        st.markdown(f"Online: **{names_str}**")

    st.markdown("### Session")
    n_questions = len([m for m in shared_chat.messages if m["role"] == "user"])
    total_tok = st.session_state.token_count["input"] + st.session_state.token_count["output"]
    st.markdown(f"Questions: **{n_questions}** · Tokens: **{total_tok:,}**")
    st.markdown(f"Cache: **{'Warmed' if st.session_state.cache_warmed else 'Cold'}**")

    if st.button("New Chat", use_container_width=True):
        shared_chat.reset_chat()
        st.session_state.last_msg_count = 0
        st.session_state.pending_answer = False
        st.session_state.processed_questions = set()
        st.rerun()

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

if not st.session_state.context:
    st.error("No documents loaded. Run `crawl_and_prepare.py` first.")
    st.stop()

# ── Build file index on first run (local only — no school docs in cloud) ──
if "file_index" not in st.session_state:
    st.session_state.file_index = build_file_index() if not IS_CLOUD else {}

# ── Cache warmup ──
shared_chat = get_shared_chat()
shared_chat.check_reset_flag()
# Skip warmup overlay if shared chat already has messages (cache warm from another session)
if shared_chat.get_message_count() > 0 and not st.session_state.cache_warmed:
    st.session_state.cache_warmed = True

if not st.session_state.cache_warmed and model_info["provider"] == "anthropic" and not st.session_state.warming_up and not st.session_state.get("warmup_failed"):
    if IS_CLOUD:
        # Cloud: skip blocking warmup — cache warms on the first real question.
        st.session_state.cache_warmed = True
    else:
        st.session_state.warming_up = True
        st.markdown(
            '<div class="warmup-overlay">'
            '<div class="spinner"></div>'
            '<h2>Loading School Documents</h2>'
            f'<p>Caching ~{len(st.session_state.context) // 4:,} tokens of school documents...<br>This takes ~15 seconds on first load, then answers are fast.</p>'
            '</div>', unsafe_allow_html=True,
        )
        try:
            policy_names = list(st.session_state.get("policy_index", {}).keys())
            sys_prompt = build_system_prompt(school_focus, st.session_state.context, policy_names)
            warmup_cache(model_info["model"], sys_prompt)
            st.session_state.cache_warmed = True
            st.session_state.warming_up = False
            st.rerun()
        except Exception as e:
            st.error(f"Cache warmup failed: {e}. The cache will warm on your first question instead.")
            st.session_state.warming_up = False
            st.session_state.warmup_failed = True

if st.session_state.cache_warmed and not shared_chat.messages:
    if IS_CLOUD:
        st.markdown('<div class="ready-banner">Ready — ask a question below. The first answer may take ~20 seconds while documents load.</div>', unsafe_allow_html=True)
    else:
        n_files = len(st.session_state.get("file_index", {}))
        st.markdown(f'<div class="ready-banner">Ready — documents cached, {n_files:,} files indexed. Ask a question below.</div>', unsafe_allow_html=True)

# ── Chat history (from shared state) ──
for i, msg in enumerate(shared_chat.messages):
    if msg["role"] == "user":
        user_label = f"{msg.get('user_name', 'Governor')}'s Question"
        st.markdown(
            f'<div class="q-bubble">'
            f'<div class="q-label">{user_label}</div>'
            f'{msg["content"]}'
            f'</div>', unsafe_allow_html=True,
        )
    else:
        parsed = msg.get("parsed", {})
        answer = parsed.get("answer", msg["content"]) if parsed else msg["content"]
        evidence = parsed.get("evidence", "") if parsed else ""
        source = parsed.get("source", "") if parsed else ""

        # Show who triggered the answer and which model
        answering = msg.get("answering_user", "")
        answer_model = msg.get("model", "")
        label_parts = ["Answer"]
        if answering:
            label_parts.append(f"for {answering}")
        if answer_model:
            label_parts.append(f"&middot; {answer_model}")
        answer_label = " ".join(label_parts)

        answer_html = format_bullets(answer)
        html = f'<div class="a-bubble"><div class="a-label">{answer_label}</div>{answer_html}'
        if evidence:
            evidence_html = format_bullets(evidence)
            html += f'<div class="evidence-inline"><strong>📊 Evidence:</strong>{evidence_html}</div>'
        html += '</div>'
        st.markdown(html, unsafe_allow_html=True)

        # Source document references
        if source:
            citations = parse_source_citations(source)
            if IS_CLOUD:
                if citations:
                    refs = " · ".join(f"📄 {c}" for c in citations[:5])
                    st.markdown(f'<div class="source-label">{refs}</div>', unsafe_allow_html=True)
            else:
                idx = st.session_state.get("file_index", {})
                gdrive = _load_gdrive_links()
                for fi, cite in enumerate(citations[:5]):
                    file_match = match_citation(cite, idx) if idx else None
                    if file_match:
                        fname = file_match[0]
                        # Prefer Google Drive link (opens natively on iOS)
                        gdrive_entry = gdrive.get(fname)
                        if gdrive_entry and gdrive_entry.get("drive_url"):
                            doc_url = gdrive_entry["drive_url"]
                        else:
                            # Fallback to local file serving
                            rel_path = str(Path(file_match[1]).relative_to(SCHOOL_DOCS))
                            import urllib.parse
                            doc_url = f"/governors/doc/{urllib.parse.quote(rel_path)}"
                        st.markdown(
                            f'<a href="{doc_url}" target="_blank" style="display:block;padding:8px 12px;'
                            f'margin:4px 0;background:#1e3a5f;border-radius:6px;color:#C9A96E;'
                            f'text-decoration:none;font-size:14px;">📂 Open: {fname}</a>',
                            unsafe_allow_html=True
                        )
                    else:
                        st.caption(f"📄 {cite}")

# ── Processing indicator (visible to all sessions) ──
proc = shared_chat.get_processing()
if proc:
    elapsed = int(time.time() - proc["started_at"])
    st.markdown(
        f'<div class="processing-banner">'
        f'<div class="proc-label">Processing</div>'
        f'<strong>{proc["user_name"]}</strong> is asking: '
        f'<em>"{proc["question"][:100]}"</em> &middot; {proc["model"]} &middot; {elapsed}s'
        f'</div>', unsafe_allow_html=True,
    )

# ── Process answer (only the session that submitted the question) ──
if st.session_state.pending_answer and shared_chat.messages and shared_chat.messages[-1]["role"] == "user":
    question = shared_chat.messages[-1]["content"]
    policy_names = list(st.session_state.get("policy_index", {}).keys())
    sys_prompt = build_system_prompt(school_focus, st.session_state.context, policy_names)
    api_msgs = [{"role": m["role"], "content": m["content"]} for m in shared_chat.messages]
    if len(api_msgs) > 12: api_msgs = api_msgs[-12:]

    placeholder = st.empty()
    try:
        full_text, usage = query_model(model_info, sys_prompt, api_msgs, placeholder, st.session_state.get("policy_index"))
        st.session_state.token_count["input"] += usage.get("input", 0)
        st.session_state.token_count["output"] += usage.get("output", 0)
        st.session_state.token_count["cache_read"] += usage.get("cache_read", 0)
        if usage.get("cache_read", 0) > 0 or usage.get("cache_created", 0) > 0:
            st.session_state.cache_warmed = True

        parsed = parse_response(full_text)
        shared_chat.add_assistant_message(
            content=full_text,
            parsed=parsed,
            model=model_choice,
            school_focus=school_focus,
            usage=usage,
            answering_user=get_user_name(),
        )
        shared_chat.clear_processing()
        st.session_state.pending_answer = False
        log_event("answer",
                  email=get_user_email(),
                  school=school_focus, model=model_choice,
                  input_mode=input_mode.lower(),
                  question=question,
                  answer=parsed.get("answer", ""),
                  sources=parsed.get("source", ""),
                  tokens_in=usage.get("input", 0),
                  tokens_out=usage.get("output", 0),
                  cache_hit=usage.get("cache_read", 0) > 0)
        placeholder.empty()
        st.rerun()
    except Exception as e:
        placeholder.error(f"Error: {e}")
        shared_chat.add_error_message(str(e))
        shared_chat.clear_processing()
        st.session_state.pending_answer = False

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
        recent = transcript[-200:] if len(transcript) > 200 else transcript
        st.markdown(
            f'<div class="transcript-banner">'
            f'<span class="label">🎙️ Live Transcript</span>'
            f'<span class="live">{recent}</span>'
            f'</div>', unsafe_allow_html=True,
        )
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
            shared_chat.add_user_message(q, get_user_name(), get_user_email(), "audio")
            shared_chat.set_processing(get_user_name(), q, model_choice)
            st.session_state.pending_answer = True
            log_event("question", email=get_user_email(),
                      school=school_focus, model=model_choice,
                      input_mode="audio", question=q)
            st.rerun()

# ── Chat input (always at bottom) ──
question = st.chat_input("Ask an Ofsted inspection question...")
if question:
    shared_chat.add_user_message(question, get_user_name(), get_user_email(), "text")
    shared_chat.set_processing(get_user_name(), question, model_choice)
    st.session_state.pending_answer = True
    log_event("question", email=get_user_email(),
              school=school_focus, model=model_choice,
              input_mode="text", question=question)
    st.rerun()


# ── Auto-refresh polling (all sessions) ──────────────────────────────────────
# Uses st.fragment to poll without full-page reruns. When new messages are
# detected, triggers a full app rerun to update the chat display.

@st.fragment(run_every=3)
def _poll_shared_chat():
    sc = get_shared_chat()
    count = sc.get_message_count()
    if count != st.session_state.get("last_msg_count", 0):
        st.session_state.last_msg_count = count
        st.rerun(scope="app")
    # Also rerun while processing is active (to update the elapsed timer)
    if sc.get_processing() is not None:
        st.rerun(scope="app")
    # Heartbeat to keep active users alive
    sc.heartbeat(get_user_email(), get_user_name())

if st.session_state.get("cache_warmed") or st.session_state.get("warmup_failed"):
    _poll_shared_chat()
