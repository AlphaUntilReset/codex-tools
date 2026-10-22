"""Intent, update-race and real Windows recovery, using isolated fixtures only."""
import ctypes
from ctypes import wintypes as w
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading
import time
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from codex_pet import diagnostics as d, recovery as r
from codex_pet.process_evidence import ProcessHandle, relaunch_outside_package, same_live_process

pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows lifecycle")


@pytest.fixture
def release(tmp_path, monkeypatch):
    install = tmp_path / "程序 中文"; install.mkdir()
    executable = install / "CodexToolsPet.exe"; executable.write_bytes(b"synthetic executable")
    folder = tmp_path / "用户数据/logs/sessions/synthetic"; folder.mkdir(parents=True)
    data = folder.parent.parent.parent
    session = {"session_id": "synthetic", "pid": 123, "created_ticks": "456", "frozen": True,
               "recovery_enabled": True, "executable": str(executable), "executable_sha256": r.file_hash(executable)}
    d.write_json(folder / "app.json", {"stage": "ready"})
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(install / "CodexToolsMonitor.exe"))
    monkeypatch.setattr(r, "shutting_down", lambda: False)
    monkeypatch.setattr(r.time, "sleep", lambda _: None)
    calls = []
    monkeypatch.setattr(r, "launch_via_windows_broker", lambda command, **kwargs: calls.append((command, kwargs)) or SimpleNamespace(pid=345))
    return folder, session, executable, data, calls


@pytest.mark.parametrize("app", [{"exit_intent": "user-quit"}, {"exit_intent": "duplicate-instance"},
                                 {"clean_return": True}, {"qt_about_to_quit": True},
                                 {"session_end_requested": True}, {"stage": "bootstrap"}])
def test_intentional_or_incomplete_exits_never_restart(release, app):
    folder, session, _, _, calls = release
    d.write_json(folder / "app.json", {"stage": "ready", **app})
    r.recover_session(folder, session)
    assert d.read_json(folder / "recovery.json")["state"] == "suppressed"
    assert not calls


def test_unexpected_exit_recovers_same_installation_and_settings(release):
    folder, session, executable, data, calls = release
    settings = data / "settings.json"; settings.write_bytes(b'{"size":238,"custom":true}')
    r.recover_session(folder, session)
    assert d.read_json(folder / "recovery.json")["state"] == "started"
    assert calls[0][0] == [str(executable)]
    assert calls[0][1]["env"]["CODEXTOOLS_PET_DATA_DIR"] == str(data)
    assert "QT_QPA_PLATFORM" not in calls[0][1]["env"]
    assert settings.read_bytes() == b'{"size":238,"custom":true}'


def test_restart_budget_survives_new_sessions_and_eventually_expires(release, monkeypatch):
    folder, session, _, data, calls = release
    for _ in range(4): r.recover_session(folder, session)
    assert len(calls) == 3
    assert d.read_json(folder / "recovery.json")["state"] == "rate-limited"
    d.write_json(data / "logs/recovery-budget.json", {"attempts": [time.time() - 601] * 3})
    r.recover_session(folder, session)
    assert len(calls) == 4


@pytest.mark.parametrize("during_backoff", [False, True])
def test_updater_stop_wins_before_and_during_backoff(release, monkeypatch, during_backoff):
    folder, session, _, _, calls = release
    def mark(_=None):
        d.write_json(folder / "stop-request.json", {**session, "reason": "plugin-updater-stop", "requested_at": d.now()})
    if during_backoff: monkeypatch.setattr(r.time, "sleep", mark)
    else: mark()
    r.recover_session(folder, session)
    assert d.read_json(folder / "recovery.json")["reason"] == "updater-requested-stop"
    assert not calls


@pytest.mark.parametrize("during_backoff", [False, True])
def test_replaced_executable_is_never_started(release, monkeypatch, during_backoff):
    folder, session, executable, _, calls = release
    def replace(_=None): executable.write_bytes(b"updated executable")
    if during_backoff: monkeypatch.setattr(r.time, "sleep", replace)
    else: replace()
    r.recover_session(folder, session)
    assert d.read_json(folder / "recovery.json")["reason"] == "installed-executable-changed"
    assert not calls


def test_windows_shutdown_and_concurrent_recovery_suppress_launch(release, monkeypatch):
    folder, session, _, data, calls = release
    monkeypatch.setattr(r, "shutting_down", lambda: True)
    r.recover_session(folder, session)
    assert d.read_json(folder / "recovery.json")["reason"] == "windows-shutting-down"
    monkeypatch.setattr(r, "shutting_down", lambda: False)
    with r.recovery_lock(data) as owned:
        assert owned
        r.recover_session(folder, session)
    assert d.read_json(folder / "recovery.json")["reason"] == "another-recovery-in-progress"
    assert not calls


def test_packaged_bootstrap_relaunches_only_once(monkeypatch):
    import codex_pet.process_evidence as p
    class Packaged:
        def __init__(self, _): pass
        def __enter__(self): return self
        def __exit__(self, *_): pass
        def package_identity(self): return "synthetic-client-package"
    monkeypatch.setattr(p, "ProcessHandle", Packaged)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.delenv("CODEXTOOLS_PACKAGE_RELAUNCH", raising=False)
    calls = []
    monkeypatch.setattr(p, "launch_via_windows_broker", lambda command, **kwargs: calls.append((command, kwargs)))
    assert relaunch_outside_package() and len(calls) == 1
    monkeypatch.setenv("CODEXTOOLS_PACKAGE_RELAUNCH", "1")
    with pytest.raises(RuntimeError, match="remained"):
        relaunch_outside_package()


def test_real_zero_code_external_termination_is_recorded_and_recovered(tmp_path, monkeypatch):
    install = tmp_path / "安装 中文"; install.mkdir()
    executable = install / "CodexToolsPet.exe"
    ps = str(Path(os.environ["SystemRoot"]) / "System32/WindowsPowerShell/v1.0/powershell.exe")
    source = 'using System;using System.IO;using System.Diagnostics;public static class ToyRecovery {public static void Main(){var data=Environment.GetEnvironmentVariable("CODEXTOOLS_PET_DATA_DIR");File.WriteAllText(Path.Combine(data,"ready"),Process.GetCurrentProcess().Id.ToString());System.Threading.Thread.Sleep(60000);}}'
    subprocess.run([ps, "-NoProfile", "-NonInteractive", "-Command", 'Add-Type -TypeDefinition $env:TOY_RECOVERY_SOURCE -OutputAssembly $env:TOY_RECOVERY_EXE -OutputType ConsoleApplication'],
                   env={**os.environ, "TOY_RECOVERY_SOURCE": source, "TOY_RECOVERY_EXE": str(executable)}, check=True, capture_output=True)
    folder = tmp_path / "数据/logs/sessions/zero-exit"; folder.mkdir(parents=True)
    data = folder.parent.parent.parent
    p = subprocess.Popen([str(executable)], env={**os.environ, "CODEXTOOLS_PET_DATA_DIR": str(data)}, creationflags=subprocess.CREATE_NO_WINDOW)
    child_pid = child_created = None
    try:
        with ProcessHandle(p.pid) as target:
            session = {"session_id": "zero-exit", "pid": p.pid, "created_ticks": target.created,
                       "frozen": True, "recovery_enabled": True, "executable": str(executable), "executable_sha256": r.file_hash(executable)}
        d.write_json(folder / "session.json", session)
        d.write_json(folder / "app.json", {"stage": "ready"})
        monkeypatch.setattr(sys, "frozen", True, raising=False)
        monkeypatch.setattr(sys, "executable", str(install / "CodexToolsMonitor.exe"))
        def terminate():
            time.sleep(.2)
            api = ctypes.WinDLL("kernel32"); api.TerminateProcess.argtypes = [w.HANDLE, w.UINT]
            assert api.TerminateProcess(int(p._handle), 0)
        killer = threading.Thread(target=terminate); killer.start()
        assert d.observe(folder) == 0
        killer.join(); p.wait(timeout=5)
        result = d.read_json(folder / "monitor.json")
        assert result["exit_code"] == 0 and result["classification"] == "zero-exit-without-clean-return"
        recovered = d.read_json(folder / "recovery.json")
        assert recovered["state"] == "started"
        child_pid = recovered["pid"]
        with ProcessHandle(child_pid) as child:
            child_created = child.created
            assert child.package_identity() is None and not child.wait()
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if (data / "ready").exists() and (data / "ready").read_text() == str(child_pid): break
            time.sleep(.025)
        assert (data / "ready").read_text() == str(child_pid)
    finally:
        if p.poll() is None: p.kill()
        p.wait()
        if child_pid and child_created and same_live_process(child_pid, child_created):
            os.kill(child_pid, signal.SIGTERM)
