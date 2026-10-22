"""Bounded recovery by the existing exit observer, without another startup entry."""
from __future__ import annotations

from contextlib import contextmanager
import ctypes
from ctypes import wintypes as w
import hashlib
import os
from pathlib import Path
import sys
import time

from codex_pet.process_evidence import launch_via_windows_broker

MAX_RESTARTS = 3
WINDOW_SECONDS = 600


def file_hash(file: Path) -> str:
    with file.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def shutting_down() -> bool:
    api = ctypes.WinDLL("user32", use_last_error=True)
    return bool(api.GetSystemMetrics(0x2000))  # SM_SHUTTINGDOWN


@contextmanager
def recovery_lock(data: Path):
    api = ctypes.WinDLL("kernel32", use_last_error=True)
    api.CreateMutexW.argtypes, api.CreateMutexW.restype = [w.LPVOID, w.BOOL, w.LPCWSTR], w.HANDLE
    api.ReleaseMutex.argtypes = api.CloseHandle.argtypes = [w.HANDLE]
    key = hashlib.sha256(str(data.resolve()).casefold().encode("utf-8")).hexdigest()
    ctypes.set_last_error(0)
    handle = api.CreateMutexW(None, True, "Local\\CodexToolsPet.Recovery." + key)
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    owned = ctypes.get_last_error() != 183
    try:
        yield owned
    finally:
        if owned:
            api.ReleaseMutex(handle)
        api.CloseHandle(handle)


def suppression(session: dict, app: dict, stop: dict) -> str | None:
    from codex_pet.diagnostics import classify_exit
    if not session.get("recovery_enabled"):
        return "disabled-for-development-or-fixture"
    if app.get("exit_intent"):
        return "intentional-exit"
    if app.get("session_end_requested") or app.get("qt_about_to_quit"):
        return "session-or-qt-shutdown"
    if app.get("clean_return"):
        return "completed-return"
    if app.get("stage") != "ready":
        return "startup-not-ready"
    if classify_exit(0, app, stop, session) == "updater-requested-stop":
        return "updater-requested-stop"
    return None


def recover_session(folder: Path, session: dict) -> None:
    """The original exit result stays intact; recovery has its own local record."""
    from codex_pet.diagnostics import read_json, write_json, now
    record = {"checked_at": now(), "source_session": session.get("session_id")}

    def finish(state: str, **fields):
        write_json(folder / "recovery.json", {**record, "state": state, **fields})

    reason = suppression(session, read_json(folder / "app.json"), read_json(folder / "stop-request.json"))
    if reason:
        finish("suppressed", reason=reason)
        return
    # Never trust an arbitrary command/path left in a diagnostic file.
    executable = Path(sys.executable).with_name("CodexToolsPet.exe")
    if not getattr(sys, "frozen", False) or not session.get("frozen"):
        finish("suppressed", reason="not-a-frozen-release")
        return
    if Path(session.get("executable", "")).resolve() != executable.resolve():
        finish("suppressed", reason="executable-identity-mismatch")
        return
    data = folder.parent.parent.parent
    with recovery_lock(data) as owned:
        if not owned:
            finish("suppressed", reason="another-recovery-in-progress")
            return
        if not executable.is_file() or file_hash(executable) != session.get("executable_sha256"):
            finish("suppressed", reason="installed-executable-changed")
            return
        budget_file = data / "logs/recovery-budget.json"
        budget = read_json(budget_file)
        current = time.time()
        attempts = [value for value in budget.get("attempts", [])
                    if isinstance(value, (int, float)) and current - WINDOW_SECONDS <= value <= current]
        if len(attempts) >= MAX_RESTARTS:
            finish("rate-limited", reason="three-restarts-in-ten-minutes")
            return
        if shutting_down():
            finish("suppressed", reason="windows-shutting-down")
            return
        time.sleep(2 * (len(attempts) + 1))
        reason = suppression(session, read_json(folder / "app.json"), read_json(folder / "stop-request.json"))
        if reason or shutting_down():
            finish("suppressed", reason=reason or "windows-shutting-down")
            return
        # An updater can replace the file during backoff. Check again before launch.
        if file_hash(executable) != session.get("executable_sha256"):
            finish("suppressed", reason="installed-executable-changed")
            return
        write_json(budget_file, {"schema": 1, "attempts": [*attempts, time.time()]})
        env = {**os.environ, "CODEXTOOLS_PET_DATA_DIR": str(data), "PYINSTALLER_RESET_ENVIRONMENT": "1"}
        for name in ("QT_QPA_PLATFORM", "QT_PLUGIN_PATH", "QML2_IMPORT_PATH", "PYTHONHOME", "PYTHONPATH"):
            env.pop(name, None)
        try:
            child = launch_via_windows_broker([str(executable)], env=env)
            finish("started", pid=child.pid, launch_mode="windows-wmi-unpackaged")
        except Exception as error:
            finish("failed", error_type=type(error).__name__)
