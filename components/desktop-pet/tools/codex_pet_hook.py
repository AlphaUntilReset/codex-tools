"""Optional official lifecycle observer. No decisions, chat storage, or networking.

Reads hook stdin, projects five metadata fields, atomically writes one short
request notice per session. It intentionally does NOT claim a live waiting state.
Always exits successfully without returning permission/continuation decisions.
"""
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import time
from uuid import UUID


def project_event(payload, now):
    if not isinstance(payload, dict):
        return None
    event = payload.get("hook_event_name")
    if event == "PermissionRequest":
        kind = "approval_request"
    elif (event == "PreToolUse"
          and payload.get("tool_name") in ("request_user_input", "functions.request_user_input")):
        kind = "input_request"
    else:
        return None
    try:
        session = str(UUID(payload["session_id"]))
    except (KeyError, TypeError, ValueError, AttributeError):
        return None
    turn = payload.get("turn_id")
    if not isinstance(turn, str) or not 0 < len(turn) <= 128:
        return None
    return {"schema": 1, "session_id": session, "turn_id": turn,
            "kind": kind, "observed_at": now}


def write_event(event, directory):
    directory.mkdir(parents=True, exist_ok=True)
    name = hashlib.sha256(event["session_id"].encode()).hexdigest() + ".json"
    fd, tmp = tempfile.mkstemp(prefix=".event-", suffix=".tmp", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(event, stream)
        os.replace(tmp, directory / name)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def main():
    try:
        # Never log malformed/oversized input or an exception containing payloads.
        raw = sys.stdin.buffer.read(1024 * 1024 + 1)
        if len(raw) > 1024 * 1024:
            return 0
        event = project_event(json.loads(raw), time.time())
        if event:
            base = Path(os.environ.get("CODEXTOOLS_PET_DATA_DIR") or
                        Path(os.environ.get("LOCALAPPDATA", Path.home())) / "CodexToolsPet")
            write_event(event, base / "hook-events")
    except Exception:
        pass  # An observational extension must not block the user's tool call.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
