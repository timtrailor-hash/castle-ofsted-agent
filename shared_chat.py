"""
Shared chat state for Castle Federation Ofsted Agent.

Uses st.cache_resource to hold a single Python object shared across all
Streamlit sessions.  Thread-safe via threading.Lock.

Ephemeral by design — chat resets on server restart, which is fine for
time-bounded inspections.
"""

import threading
import time
import uuid

import streamlit as st


class SharedChat:
    def __init__(self):
        self.messages: list[dict] = []
        self.evidence_history: list[dict] = []
        self.processing: dict | None = None  # {user_name, question, started_at, model}
        self.active_users: dict = {}  # {email: {name, last_seen}}
        self.lock = threading.Lock()
        self.last_cleanup = time.time()

    # ── Messages ─────────────────────────────────────────────────────────

    def add_user_message(self, content: str, user_name: str, user_email: str,
                         input_mode: str = "text") -> str:
        msg_id = str(uuid.uuid4())[:8]
        with self.lock:
            self.messages.append({
                "id": msg_id,
                "role": "user",
                "content": content,
                "user_name": user_name,
                "user_email": user_email,
                "input_mode": input_mode,
                "timestamp": time.time(),
            })
        return msg_id

    def add_assistant_message(self, content: str, parsed: dict,
                              model: str = "", school_focus: str = "",
                              usage: dict | None = None,
                              answering_user: str = "") -> str:
        msg_id = str(uuid.uuid4())[:8]
        ev_idx = len(self.evidence_history)
        with self.lock:
            self.evidence_history.append({
                "question": self.messages[-1]["content"] if self.messages else "",
                "evidence": parsed.get("evidence", ""),
                "source": parsed.get("source", ""),
                "raw": content,
            })
            self.messages.append({
                "id": msg_id,
                "role": "assistant",
                "content": content,
                "parsed": parsed,
                "evidence_idx": ev_idx,
                "model": model,
                "school_focus": school_focus,
                "answering_user": answering_user,
                "timestamp": time.time(),
            })
        return msg_id

    def add_error_message(self, error_text: str) -> str:
        msg_id = str(uuid.uuid4())[:8]
        with self.lock:
            self.messages.append({
                "id": msg_id,
                "role": "assistant",
                "content": f"Error: {error_text}",
                "parsed": {},
                "evidence_idx": None,
                "timestamp": time.time(),
            })
        return msg_id

    def get_message_count(self) -> int:
        self._maybe_daily_cleanup()
        return len(self.messages)

    def _maybe_daily_cleanup(self):
        """Auto-clear chat at 2:00 AM UK time daily."""
        from datetime import datetime, timedelta, timezone
        try:
            from zoneinfo import ZoneInfo
            uk = ZoneInfo("Europe/London")
        except ImportError:
            uk = timezone.utc
        now = datetime.now(uk)
        today_2am = now.replace(hour=2, minute=0, second=0, microsecond=0)
        if now.hour < 2:
            today_2am -= timedelta(days=1)
        cutoff = today_2am.timestamp()
        if self.last_cleanup < cutoff and time.time() >= cutoff:
            with self.lock:
                self.messages.clear()
                self.evidence_history.clear()
                self.processing = None
            self.last_cleanup = time.time()

    # ── Processing indicator ─────────────────────────────────────────────

    def set_processing(self, user_name: str, question: str, model: str):
        with self.lock:
            self.processing = {
                "user_name": user_name,
                "question": question,
                "model": model,
                "started_at": time.time(),
            }

    def clear_processing(self):
        with self.lock:
            self.processing = None

    def get_processing(self) -> dict | None:
        with self.lock:
            if self.processing is None:
                return None
            # Auto-clear stale processing after 120 seconds
            if time.time() - self.processing["started_at"] > 120:
                self.processing = None
                return None
            return dict(self.processing)

    # ── Active users ─────────────────────────────────────────────────────

    def heartbeat(self, user_email: str, user_name: str) -> dict:
        now = time.time()
        with self.lock:
            self.active_users[user_email] = {"name": user_name, "last_seen": now}
            # Prune users not seen in 30 seconds
            self.active_users = {
                email: info for email, info in self.active_users.items()
                if now - info["last_seen"] < 30
            }
            return dict(self.active_users)

    # ── Reset ────────────────────────────────────────────────────────────

    def reset_chat(self):
        with self.lock:
            self.messages.clear()
            self.evidence_history.clear()
            self.processing = None


@st.cache_resource
def get_shared_chat() -> SharedChat:
    """Singleton shared chat — one instance per Streamlit server process."""
    return SharedChat()


def get_display_name(email: str) -> str:
    """Extract first name from email: tim.trailor@castlefederation.org -> Tim
    Local sessions: local-ab12cd@localhost -> Local (ab12cd)"""
    if not email or "@" not in email:
        return "Governor"
    local = email.split("@")[0]
    # Local dev sessions get a short identifier
    if local.startswith("local-"):
        tag = local.replace("local-", "")
        return f"Local ({tag})"
    first = local.split(".")[0]
    return first.capitalize()
