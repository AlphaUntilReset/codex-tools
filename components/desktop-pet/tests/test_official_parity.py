"""State provenance, handoff, GUI persistence and observational-hook regression tests."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
from urllib.parse import parse_qs, urlparse

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QWidget
from codex_pet.bridge import BridgeState, Connection, TaskSnapshot, TaskStatus
from codex_pet.bridge.codex_local import CodexLocalBridge
from codex_pet.bridge.hook_events import request_notice
from codex_pet.quick_task import QuickTaskDialog, new_task_url
from codex_pet.settings import Settings, load_settings, save_settings
from codex_pet.task_panel import TaskPanel
from test_codex_local import NOW, make_home, seed_turns_db

app = QApplication.instance() or QApplication([])
TID = "01a0d7bd-79b1-7053-aa0d-a70deec3fe70"


def tool_module(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "tools" / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def write_read_state(home, identities):
    data = {"electron-thread-read-state-v1": {"version": 1, "unreadByIdentity": identities},
            "unrelated_prompt_history": "PRIVATE_TEST_SENTINEL"}
    path = home / ".codex-global-state.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def test_official_unread_changes_without_database_turn_change(tmp_path):
    home = make_home(tmp_path)
    seed_turns_db(home, [(TID, "turn", "completed", NOW - 180, NOW - 60, None)])
    path = write_read_state(home, {"identity": {"local:host": [TID]}})
    before = path.read_bytes()
    bridge = CodexLocalBridge(home, clock=lambda: NOW)
    task = bridge._snapshot_tasks()[TID]
    assert task.status is TaskStatus.READY and task.unread is True
    assert path.read_bytes() == before
    write_read_state(home, {"identity": {"local:host": []}})
    task = bridge._snapshot_tasks()[TID]
    assert task.status is TaskStatus.COMPLETED and task.unread is False


@pytest.mark.parametrize("identities", [
    {}, {"a": {"local:x": [TID]}, "b": {"local:y": []}},
    {"a": {"local:x": [TID], "local:y": []}},
    {"a": {"remote:x": [TID]}}, {"a": {"local:x": [12]}},
])
def test_ambiguous_unread_is_unavailable_not_guessed(tmp_path, identities):
    home = make_home(tmp_path)
    write_read_state(home, identities)
    assert CodexLocalBridge(home)._unread_thread_ids() is None


def test_old_unread_survives_but_stale_running_does_not(tmp_path):
    home = make_home(tmp_path)
    seed_turns_db(home, [(TID, "turn", "completed", NOW - 9000, NOW - 8000, None),
                          ("stale", "turn", "inProgress", NOW - 9000, None, None)])
    write_read_state(home, {"a": {"local:x": [TID, "stale"]}})
    tasks = CodexLocalBridge(home, clock=lambda: NOW)._snapshot_tasks()
    assert set(tasks) == {TID} and tasks[TID].status is TaskStatus.READY


def test_unread_source_corruption_does_not_disconnect_tasks(tmp_path):
    home = make_home(tmp_path)
    seed_turns_db(home, [(TID, "turn", "completed", NOW - 180, NOW - 60, None)])
    (home / ".codex-global-state.json").write_text("partial", encoding="utf-8")
    states = []
    bridge = CodexLocalBridge(home, clock=lambda: NOW)
    bridge.set_callback(states.append)
    bridge._poll()
    assert states[0].connection is Connection.CONNECTED
    assert "task_unread" not in states[0].capabilities
    assert states[0].tasks[TID].unread is None


def test_official_priority_and_compact_preference_persist(tmp_path):
    pet = QWidget()
    pet.show()
    panel = TaskPanel(pet)
    state = BridgeState("demo", "demo", Connection.CONNECTED, tasks={
        "run": TaskSnapshot("run", TaskStatus.RUNNING, "now", title="演示工作"),
        "done": TaskSnapshot("done", TaskStatus.COMPLETED, "now", title="演示已读"),
        "ready": TaskSnapshot("ready", TaskStatus.READY, "now", title="演示未读", unread=True),
    })
    assert state.aggregate_status() is TaskStatus.READY
    panel.set_state(state)
    assert panel.rows_layout.count() == 3
    assert "未读" in panel.rows_layout.itemAt(0).widget().text()
    settings = Settings(attention_only=True, task_panel_collapsed=True)
    save_settings(settings, tmp_path)
    restored, _ = load_settings(tmp_path)
    panel.apply_settings(restored)
    assert panel.collapse.isChecked()
    assert not panel.scroll.isVisible()
    assert "待处理 1" in panel.collapse.text()
    pet.hide()
    panel.close()
    pet.close()


def test_composer_encodes_untrusted_text_as_one_query_value():
    prompt = '检查中文 & #test?\n`$(not-a-command)` @context $skill'
    url = new_task_url(prompt)
    assert url.startswith("codex://threads/new?")
    assert parse_qs(urlparse(url).query) == {"prompt": [prompt]}
    assert new_task_url(" ") == "codex://threads/new"
    with pytest.raises(ValueError):
        new_task_url("中" * 1000)


def test_composer_enter_handoff_and_failed_draft_retention():
    calls = []
    dialog = QuickTaskDialog(opener=lambda s: calls.append(s) or False)
    dialog.present()
    dialog.editor.setPlainText("演示草稿")
    QTest.keyClick(dialog.editor, Qt.Key_Return)
    assert calls == ["演示草稿"] and dialog.isVisible()
    assert dialog.editor.toPlainText() == "演示草稿"
    dialog._opener = lambda s: calls.append(s) or True
    QTest.keyClick(dialog.editor, Qt.Key_Return)
    assert len(calls) == 2 and not dialog.isVisible()
    assert dialog.editor.toPlainText() == "演示草稿"
    dialog.close()


def test_observer_projects_only_metadata_and_does_not_fake_waiting(tmp_path):
    hook = tool_module("codex_pet_hook")
    payload = {"session_id": TID, "turn_id": "turn", "hook_event_name": "PermissionRequest",
               "tool_input": {"command": "PRIVATE_TEST_SENTINEL"}, "transcript_path": "secret"}
    event = hook.project_event(payload, NOW)
    hook.write_event(event, tmp_path)
    assert set(event) == {"schema", "session_id", "turn_id", "kind", "observed_at"}
    assert "PRIVATE" not in next(tmp_path.glob("*.json")).read_text()
    assert "审批请求" in request_notice(tmp_path, TID, "turn", NOW + 1)
    assert request_notice(tmp_path, TID, "new-turn", NOW + 1) == ""
    assert request_notice(tmp_path, TID, "turn", NOW + 121) == ""
    assert request_notice(tmp_path, TID, "turn", NOW - 1) == ""
    home = make_home(tmp_path)
    seed_turns_db(home, [(TID, "turn", "inProgress", NOW - 30, None, None)])
    task = CodexLocalBridge(home, clock=lambda: NOW + 1, event_dir=tmp_path)._snapshot_tasks()[TID]
    assert task.status is TaskStatus.RUNNING  # A request is NOT evidence of current waiting.
    assert "审批请求" in task.attention_reason
    payload.update(hook_event_name="PreToolUse", tool_name="request_user_input_async")
    assert hook.project_event(payload, NOW) is None


def test_observer_no_stdout_decision_or_stderr_on_failure(tmp_path):
    env = {**os.environ, "CODEXTOOLS_PET_DATA_DIR": str(tmp_path)}
    result = subprocess.run([sys.executable, str(ROOT / "tools/codex_pet_hook.py")],
                            input=b"malformed", capture_output=True, env=env, timeout=5)
    assert result.returncode == 0 and not result.stdout and not result.stderr


def test_hook_install_merge_idempotent_and_remove_preserves_others(tmp_path):
    setup = tool_module("setup_request_hooks")
    other = {"hooks": {"PermissionRequest": [{"hooks": [{"command": "other"}]}]}, "keep": 123}
    proposed = setup.definition(tmp_path / "CodexToolsHook.exe")
    merged = setup.merge_hooks(other, proposed)
    assert setup.merge_hooks(merged, proposed) == merged
    removed = setup.merge_hooks(merged, proposed, remove=True)
    assert removed["hooks"]["PermissionRequest"] == other["hooks"]["PermissionRequest"]
    assert removed["keep"] == 123 and other["hooks"].keys() == {"PermissionRequest"}
