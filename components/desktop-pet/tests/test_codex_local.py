"""Codex 只读尽力同步桥测试（C01/C04/C05/C09/C10 逻辑层）。"""

from __future__ import annotations

import sqlite3
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from codex_pet.bridge import BridgeState, Connection, TaskStatus  # noqa: E402
from codex_pet.bridge.codex_local import CodexLocalBridge  # noqa: E402


class FakeClock:
    def __init__(self, t: float):
        self.t = t

    def __call__(self) -> float:
        return self.t


def make_home(tmp_path: Path) -> Path:
    home = tmp_path / "codex-home"
    home.mkdir()
    return home


def seed_turns_db(home: Path, turns: list[tuple]) -> Path:
    """turns: (thread_id, turn_id, status, started_at, completed_at|None, error_json|None)"""
    db = home / "thread_history_1.sqlite"
    con = sqlite3.connect(db)
    con.execute(
        "CREATE TABLE thread_turns (thread_id TEXT, turn_id TEXT, status TEXT, "
        "error_json TEXT, started_at REAL, completed_at REAL)"
    )
    con.executemany(
        "INSERT INTO thread_turns VALUES (?,?,?,?,?,?)",
        [(t[0], t[1], t[2], t[5], t[3], t[4]) for t in turns],
    )
    con.commit()
    con.close()
    return db


def seed_state_db(home: Path, sources: dict[str, str]) -> None:
    db = home / "state_5.sqlite"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE threads (id TEXT, thread_source TEXT)")
    con.executemany("INSERT INTO threads VALUES (?,?)", list(sources.items()))
    con.commit()
    con.close()


NOW = 1_790_000_000.0


def test_running_and_completed_mapped(tmp_path):
    home = make_home(tmp_path)
    seed_turns_db(
        home,
        [
            ("t-run", "a", "inProgress", NOW - 60, None, None),
            ("t-done", "b", "completed", NOW - 120, NOW - 30, None),
            ("t-fail", "c", "failed", NOW - 90, NOW - 10, '{"msg":"x"}'),
            ("t-stop", "d", "interrupted", NOW - 70, NOW - 5, None),
        ],
    )
    bridge = CodexLocalBridge(codex_home=home, clock=FakeClock(NOW))
    received = []
    bridge.set_callback(lambda s: received.append(s))
    bridge.start()
    bridge.stop()
    assert len(received) == 1
    state = received[0]
    assert state.connection is Connection.CONNECTED
    assert state.source == "codex-desktop" and state.mode == "local-ro"
    assert not state.is_demo
    assert state.tasks["t-run"].status is TaskStatus.RUNNING
    assert state.tasks["t-done"].status is TaskStatus.COMPLETED
    assert state.tasks["t-fail"].status is TaskStatus.BLOCKED
    assert state.tasks["t-stop"].status is TaskStatus.IDLE
    assert state.aggregate_status() is TaskStatus.BLOCKED
    # 聚合优先级 needs_input > blocked > running（本例含 blocked）


def test_stale_states_are_not_live_facts(tmp_path):
    """过期 running 和陈旧完成不作为实时事实（C06 相关）。"""
    home = make_home(tmp_path)
    seed_turns_db(
        home,
        [
            ("t-stale-run", "a", "inProgress", NOW - 3600, None, None),  # 1 小时前
            ("t-old-done", "b", "completed", NOW - 7200, NOW - 7100, None),
        ],
    )
    bridge = CodexLocalBridge(codex_home=home, clock=FakeClock(NOW))
    received = []
    bridge.set_callback(lambda s: received.append(s))
    bridge.start()
    bridge.stop()
    state = received[0]
    assert state.tasks == {}
    assert state.aggregate_status() is TaskStatus.UNKNOWN


def test_subagent_threads_excluded(tmp_path):
    home = make_home(tmp_path)
    seed_turns_db(
        home,
        [
            ("t-user", "a", "inProgress", NOW - 30, None, None),
            ("t-sub", "b", "inProgress", NOW - 20, None, None),
        ],
    )
    seed_state_db(home, {"t-user": "user", "t-sub": "subagent"})
    bridge = CodexLocalBridge(codex_home=home, clock=FakeClock(NOW))
    received = []
    bridge.set_callback(lambda s: received.append(s))
    bridge.start()
    bridge.stop()
    tasks = received[0].tasks
    assert set(tasks) == {"t-user"}


def test_latest_turn_per_thread_wins(tmp_path):
    """同一线程以最新一轮为准，旧轮不覆盖新轮（C05 去重/乱序）。"""
    home = make_home(tmp_path)
    seed_turns_db(
        home,
        [
            ("t1", "old", "completed", NOW - 300, NOW - 200, None),
            ("t1", "new", "inProgress", NOW - 10, None, None),
        ],
    )
    bridge = CodexLocalBridge(codex_home=home, clock=FakeClock(NOW))
    received = []
    bridge.set_callback(lambda s: received.append(s))
    bridge.start()
    bridge.stop()
    assert received[0].tasks["t1"].status is TaskStatus.RUNNING


def test_missing_db_reports_unsupported(tmp_path):
    home = make_home(tmp_path)
    bridge = CodexLocalBridge(codex_home=home, clock=FakeClock(NOW))
    received = []
    bridge.set_callback(lambda s: received.append(s))
    bridge.start()
    assert received[0].connection is Connection.UNSUPPORTED


def test_corrupt_db_degrades_to_disconnected(tmp_path):
    home = make_home(tmp_path)
    (home / "thread_history_1.sqlite").write_bytes(b"not a sqlite file")
    bridge = CodexLocalBridge(codex_home=home, clock=FakeClock(NOW))
    received = []
    bridge.set_callback(lambda s: received.append(s))
    bridge.start()
    assert received[0].connection in (Connection.DISCONNECTED, Connection.UNSUPPORTED)


def test_read_only_never_modifies_source(tmp_path):
    """C10 数据最小化/无写入：读取前后源文件字节一致。"""
    import hashlib

    home = make_home(tmp_path)
    db = seed_turns_db(home, [("t1", "a", "inProgress", NOW - 10, None, None)])
    before = hashlib.sha256(db.read_bytes()).hexdigest()
    bridge = CodexLocalBridge(codex_home=home, clock=FakeClock(NOW))
    bridge.start()
    bridge._poll()
    bridge.stop()
    after = hashlib.sha256(db.read_bytes()).hexdigest()
    assert before == after


def test_baseline_does_not_replay_old_tasks(tmp_path):
    """启动基线不把历史任务当事件回放；窗口内状态作为当前事实呈现。"""
    home = make_home(tmp_path)
    seed_turns_db(home, [("t1", "a", "completed", NOW - 100, NOW - 50, None)])
    bridge = CodexLocalBridge(codex_home=home, clock=FakeClock(NOW))
    received: list[BridgeState] = []
    bridge.set_callback(received.append)
    bridge.start()
    bridge._poll()
    bridge.stop()
    # 窗口内的 completed 属于当前可观测事实（ready），但未发生 running→ready 转换
    assert received[-1].tasks["t1"].status is TaskStatus.COMPLETED


def test_long_running_uses_latest_item_activity(tmp_path):
    home = make_home(tmp_path)
    db = seed_turns_db(home, [('t1','turn','inProgress',NOW-3600,None,None)])
    with sqlite3.connect(db) as c:
        c.execute('CREATE TABLE thread_items (thread_id TEXT, turn_id TEXT, created_at_ms INTEGER)')
        c.execute('INSERT INTO thread_items VALUES (?,?,?)', ('t1','turn',(NOW-5)*1000))
    bridge = CodexLocalBridge(codex_home=home, clock=FakeClock(NOW))
    assert bridge._snapshot_tasks()['t1'].status is TaskStatus.RUNNING


def test_titles_and_desktop_archived_filters(tmp_path):
    home = make_home(tmp_path)
    seed_turns_db(home, [(t,'turn','inProgress',NOW-10,None,None) for t in ('user','sub','archive','cli')])
    with sqlite3.connect(home/'state_5.sqlite') as c:
        c.execute('CREATE TABLE threads (id TEXT, thread_source TEXT, archived INTEGER, originator TEXT, title TEXT, name TEXT)')
        c.executemany('INSERT INTO threads VALUES (?,?,?,?,?,?)', [
            ('user','user',0,'Codex Desktop','原标题','新名称'),
            ('sub','subagent',0,'Codex Desktop','子代理',None),
            ('archive','user',1,'Codex Desktop','归档',None),
            ('cli','user',0,'codex_cli_rs','CLI',None)])
    bridge = CodexLocalBridge(codex_home=home, clock=FakeClock(NOW))
    tasks = bridge._snapshot_tasks()
    assert set(tasks) == {'user'}
    assert tasks['user'].title == '新名称'
    assert tasks['user'].turn_id == 'turn'


def test_missing_database_recovers_and_process_exit_clears_tasks(tmp_path):
    home = make_home(tmp_path)
    running = [True]
    bridge = CodexLocalBridge(codex_home=home, clock=FakeClock(NOW), process_probe=lambda: running[0])
    states = []
    bridge.set_callback(states.append)
    bridge.start()
    assert states[-1].connection is Connection.UNSUPPORTED
    seed_turns_db(home, [('t1','turn','inProgress',NOW-10,None,None)])
    bridge._poll()
    assert states[-1].connection is Connection.CONNECTED and states[-1].tasks
    running[0] = False
    bridge._poll()
    assert states[-1].connection is Connection.DISCONNECTED and not states[-1].tasks
    bridge.stop()


def test_task_link_rejects_arbitrary_url():
    from codex_pet.bridge.codex_local import codex_task_url
    tid = '01a0d7bd-79b1-7053-aa0d-a70deec3fe70'
    assert codex_task_url(tid) == 'codex://threads/' + tid
    with pytest.raises(ValueError):
        codex_task_url('https://example.com')
