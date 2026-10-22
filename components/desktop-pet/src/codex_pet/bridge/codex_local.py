"""Codex 桌面版只读"尽力同步"桥（非官方接口，见 docs/integration-investigation.md）。

数据源：~/.codex/thread_history_1.sqlite 的 thread_turns（只读连接、固定字段白名单）。
限制：needs_input 无数据源不提供；轮询非推送；官方库结构变化时降级 disconnected。
"""

from __future__ import annotations

import logging
import json
import os
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QTimer
from codex_pet import paths
from codex_pet.bridge.hook_events import request_notice

from codex_pet.bridge.interface import (
    Bridge,
    BridgeState,
    Connection,
    TaskSnapshot,
    TaskStatus,
)

log = logging.getLogger("codex_pet.bridge.codex_local")

POLL_MS = 2000
ACTIVITY_WINDOW_S = 20 * 60  # 超过该窗口的轮次状态不作为实时事实

TURN_STATUS_MAP = {
    "inProgress": TaskStatus.RUNNING,
    "completed": TaskStatus.COMPLETED,
    "failed": TaskStatus.BLOCKED,
    "interrupted": TaskStatus.IDLE,
}


def _iso(epoch_s: float) -> str:
    return datetime.fromtimestamp(epoch_s, tz=timezone.utc).isoformat()


def codex_desktop_running() -> bool:
    """Check the official package's process, not a similarly named unrelated app."""
    import ctypes
    from ctypes import wintypes

    class Entry(ctypes.Structure):
        _fields_ = [("size", wintypes.DWORD), ("usage", wintypes.DWORD),
                    ("pid", wintypes.DWORD), ("heap", ctypes.c_size_t),
                    ("module", wintypes.DWORD), ("threads", wintypes.DWORD),
                    ("parent", wintypes.DWORD), ("priority", wintypes.LONG),
                    ("flags", wintypes.DWORD), ("name", wintypes.WCHAR * 260)]

    if os.name != "nt":
        return False
    k = ctypes.WinDLL("kernel32", use_last_error=True)
    k.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    k.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(Entry)]
    k.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(Entry)]
    k.OpenProcess.restype = wintypes.HANDLE
    k.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD,
                                          wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
    k.CloseHandle.argtypes = [wintypes.HANDLE]
    snap = k.CreateToolhelp32Snapshot(2, 0)
    if snap == ctypes.c_void_p(-1).value:
        return False
    try:
        entry = Entry()
        entry.size = ctypes.sizeof(entry)
        more = k.Process32FirstW(snap, ctypes.byref(entry))
        while more:
            if entry.name.lower() in ("chatgpt.exe", "codex.exe"):
                handle = k.OpenProcess(0x1000, False, entry.pid)
                if handle:
                    try:
                        path = ctypes.create_unicode_buffer(32768)
                        size = wintypes.DWORD(len(path))
                        if k.QueryFullProcessImageNameW(handle, 0, path, ctypes.byref(size)):
                            if "\\windowsapps\\openai.codex_" in path.value.lower():
                                return True
                    finally:
                        k.CloseHandle(handle)
            more = k.Process32NextW(snap, ctypes.byref(entry))
    finally:
        k.CloseHandle(snap)
    return False


class CodexLocalBridge(Bridge):
    name = "codex-local"

    def __init__(
        self,
        codex_home: Optional[Path] = None,
        poll_ms: int = POLL_MS,
        clock=time.time,
        process_probe=None,
        event_dir: Path | None = None,
    ):
        super().__init__()
        env_home = os.environ.get("CODEX_HOME")
        self._home = Path(codex_home or env_home or Path.home() / ".codex")
        self._poll_ms = poll_ms
        self._clock = clock
        self._process_probe = process_probe or (codex_desktop_running if codex_home is None else lambda: True)
        self._timer: Optional[QTimer] = None
        self._tasks: dict[str, TaskSnapshot] = {}
        self._baseline_emitted = False
        self._unread_available = False
        self._event_dir = event_dir or paths.user_data_dir() / "hook-events"

    # ---- 数据访问（只读） ----

    def _connect_ro(self, db: Path) -> sqlite3.Connection:
        return sqlite3.connect(db.resolve().as_uri() + "?mode=ro", uri=True, timeout=0.05)

    def _thread_metadata(self) -> tuple[set[str], dict[str, str]]:
        """Read only identity/title/filter fields; never read chat/preview/auth content."""
        db = self._home / "state_5.sqlite"
        if not db.exists():
            return set(), {}
        con = self._connect_ro(db)
        try:
            columns = {r[1] for r in con.execute("PRAGMA table_info(threads)")}
            if not {"id", "thread_source"} <= columns:
                raise sqlite3.DatabaseError("Unsupported thread metadata schema")
            fields = [f for f in ("id", "thread_source", "archived", "originator", "title", "name") if f in columns]
            excluded, titles = set(), {}
            for row in con.execute("SELECT " + ",".join(fields) + " FROM threads"):
                data = dict(zip(fields, row))
                tid = data["id"]
                if (data.get("thread_source") == "subagent" or data.get("archived")
                        or data.get("originator") not in (None, "", "Codex Desktop")):
                    excluded.add(tid)
                titles[tid] = " ".join(str(data.get("name") or data.get("title") or "").split())[:160]
            return excluded, titles
        finally:
            con.close()

    def _latest_turns(self) -> list[tuple]:
        """Latest turn plus activity timestamp; no error payload or item_json reads."""
        db = self._home / "thread_history_1.sqlite"
        con = self._connect_ro(db)
        try:
            item_columns = {r[1] for r in con.execute("PRAGMA table_info(thread_items)")}
            activity = "NULL"
            if {"thread_id", "turn_id", "created_at_ms"} <= item_columns:
                activity = ("(SELECT MAX(i.created_at_ms)/1000.0 FROM thread_items i "
                            "WHERE i.thread_id=t.thread_id AND i.turn_id=t.turn_id)")
            rows = con.execute(
                "WITH latest AS (SELECT *, ROW_NUMBER() OVER (PARTITION BY thread_id "
                "ORDER BY started_at DESC, rowid DESC) rn FROM thread_turns) "
                "SELECT t.thread_id, t.turn_id, t.status, t.started_at, t.completed_at, "
                + activity + " FROM latest t WHERE t.rn=1"
            ).fetchall()
            return rows
        finally:
            con.close()

    def _snapshot_tasks(self) -> dict[str, TaskSnapshot]:
        now = self._clock()
        excluded, titles = self._thread_metadata()
        unread_ids = self._unread_thread_ids()
        self._unread_available = unread_ids is not None
        tasks: dict[str, TaskSnapshot] = {}
        for thread_id, turn_id, status, started_at, completed_at, activity in self._latest_turns():
            if thread_id in excluded:
                continue
            mapped = TURN_STATUS_MAP.get(status)
            if mapped is None:
                continue
            last_activity = completed_at or max(started_at, activity or started_at)
            notice = request_notice(self._event_dir, thread_id, turn_id, now) if mapped is TaskStatus.RUNNING else ""
            unread = None if unread_ids is None else thread_id in unread_ids
            if mapped is TaskStatus.COMPLETED and unread:
                mapped = TaskStatus.READY
            if now - last_activity > ACTIVITY_WINDOW_S and not notice and not (
                unread and mapped in (TaskStatus.READY, TaskStatus.BLOCKED)
            ):
                continue  # 陈旧状态不当实时事实
            tasks[thread_id] = TaskSnapshot(
                task_id=thread_id,
                status=mapped,
                observed_at=_iso(last_activity),
                seq=int(last_activity * 1000),
                title=titles.get(thread_id, ""),
                turn_id=turn_id,
                unread=unread,
                attention_reason=notice,
            )
        return tasks

    def _unread_thread_ids(self) -> set[str] | None:
        """Project the official read-state field only; never persist the source JSON.

        Multiple identities/hosts are ambiguous without authentication details.
        Fail closed instead of combining accounts or guessing the active host.
        """
        try:
            path = self._home / ".codex-global-state.json"
            if path.stat().st_size > 8 * 1024 * 1024:
                return None
            root = json.loads(path.read_text(encoding="utf-8"))
            state = root.get("electron-thread-read-state-v1", {})
            if state.get("version") != 1:
                return None
            identities = state.get("unreadByIdentity")
            if not isinstance(identities, dict) or len(identities) != 1:
                return None
            hosts = next(iter(identities.values()))
            if not isinstance(hosts, dict):
                return None
            local = [ids for key, ids in hosts.items() if key.startswith("local:")]
            if len(local) != 1 or not isinstance(local[0], list):
                return None
            if not all(isinstance(tid, str) for tid in local[0]):
                return None
            return set(local[0])
        except (OSError, ValueError, AttributeError, TypeError):
            return None

    # ---- 桥接生命周期 ----

    def start(self) -> None:
        self.stop()
        self._timer = QTimer()
        self._timer.setInterval(self._poll_ms)
        self._timer.timeout.connect(self._poll)
        self._timer.start()
        self._poll()  # Retry even when the database is absent at startup.

    def stop(self) -> None:
        if self._timer is not None:
            self._timer.stop()
            self._timer.deleteLater()
            self._timer = None

    def _poll(self) -> None:
        if not self._process_probe():
            self._tasks = {}
            self._emit(Connection.DISCONNECTED)
            return
        if not (self._home / "thread_history_1.sqlite").exists():
            self._tasks = {}
            self._emit(Connection.UNSUPPORTED)
            return
        try:
            self._tasks = self._snapshot_tasks()
            self._emit(Connection.CONNECTED)
        except (sqlite3.Error, OSError, ValueError, TypeError, KeyError, OverflowError) as exc:
            log.warning("codex-local 轮询失败，降级断开: %s", exc)
            self._tasks = {}
            self._emit(Connection.DISCONNECTED)

    def _emit(self, connection: Connection) -> None:
        self.emit(
            BridgeState(
                source="codex-desktop",
                mode="local-ro",
                connection=connection,
                capabilities=("task_status", "task_title") + (("task_unread",) if self._unread_available else ()),
                tasks=dict(self._tasks),
                last_observed_at=(
                    max((t.observed_at for t in self._tasks.values()), default=None)
                ),
            )
        )


def open_codex_desktop() -> bool:
    """打开官方 Codex 桌面版主程序（只打开主程序，不跳转具体任务）。"""
    import subprocess

    try:
        find = subprocess.run(
            ["powershell", "-NoProfile", "-c",
             "Get-StartApps | Where-Object AppID -like 'OpenAI.Codex_*!App' "
             "| Select-Object -First 1 -ExpandProperty AppID"],
            capture_output=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            text=True,
            timeout=10,
        )
        app_id = find.stdout.strip()
        if not app_id:
            return False
        subprocess.run(
            ["explorer.exe", f"shell:AppsFolder\\{app_id}"], check=False
        )
        return True
    except (OSError, subprocess.SubprocessError):
        return False


def codex_task_url(task_id: str) -> str:
    from uuid import UUID
    return f"codex://threads/{UUID(task_id)}"


def open_codex_task(task_id: str) -> bool:
    """Registered official Windows protocol; UUID-only, no shell interpolation."""
    from PySide6.QtCore import QUrl
    from PySide6.QtGui import QDesktopServices
    try:
        return QDesktopServices.openUrl(QUrl(codex_task_url(task_id)))
    except (ValueError, TypeError):
        return False
