"""Minimal request notices, not an authoritative approval/waiting state."""
import hashlib
import json
from pathlib import Path

NOTICE_TTL_S = 120
LABELS = {
    "approval_request": "检测到审批请求 · 请到 Codex 确认",
    "input_request": "检测到输入请求 · 请到 Codex 确认",
}


def request_notice(directory: Path, task_id: str, turn_id: str, now: float) -> str:
    path = directory / (hashlib.sha256(task_id.encode()).hexdigest() + ".json")
    try:
        if path.stat().st_size > 2048:
            return ""
        event = json.loads(path.read_text(encoding="utf-8"))
        if (event.get("schema") != 1 or event.get("session_id") != task_id
                or event.get("turn_id") != turn_id):
            return ""
        age = now - event["observed_at"]
        if not 0 <= age < NOTICE_TTL_S:
            return ""
        return LABELS.get(event.get("kind"), "")
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return ""
