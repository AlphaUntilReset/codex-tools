"""Real Windows child-process evidence; never touches daily pet or settings."""
from __future__ import annotations

import ctypes
from ctypes import wintypes as w
import json
import logging
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from codex_pet import diagnostics as d
from codex_pet.process_evidence import ProcessHandle

pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows process evidence")


def wait_json(file, predicate=lambda value: True, timeout=12):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = d.read_json(file)
        if value and predicate(value):
            return value
        time.sleep(0.025)
    pytest.fail(f"Timed out reading {file}: {d.read_json(file)}")


FIXTURE = '''
import os, sys, time, logging
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from codex_pet import diagnostics as d
s=d.begin()
logging.warning("synthetic fixture log")
if not s or not s.state.get("observer_armed"): raise RuntimeError("observer not armed")
(Path(os.environ["CODEXTOOLS_PET_DATA_DIR"])/"ready.json").write_text(str(s.folder),encoding="utf-8")
while not (Path(os.environ["CODEXTOOLS_PET_DATA_DIR"])/"release").exists(): time.sleep(.025)
mode=sys.argv[2]
if mode=="exception":
    try: raise RuntimeError("synthetic unhandled exception")
    except BaseException: sys.excepthook(*sys.exc_info());s.finish(1,clean=False)
    raise SystemExit(1)
code=int(mode)
d.event(exit_intent="user-quit")
s.finish(code)
raise SystemExit(code)
'''


def start_fixture(tmp_path, mode="0", flags=0):
    env = os.environ.copy()
    env["CODEXTOOLS_PET_DATA_DIR"] = str(tmp_path)
    env["QT_QPA_PLATFORM"] = "offscreen"
    # The Windows venv redirector is a different PID; target the actual interpreter.
    return subprocess.Popen([sys._base_executable, "-c", FIXTURE, str(ROOT / "src"), mode],
                            env=env, creationflags=flags, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def ready(tmp_path):
    deadline = time.monotonic() + 12
    file = tmp_path / "ready.json"
    while time.monotonic() < deadline:
        if file.exists():
            folder = Path(file.read_text(encoding="utf-8"))
            wait_json(folder / "monitor.json", lambda v: v.get("state") == "watching")
            return folder
        time.sleep(.025)
    pytest.fail("Synthetic parent never became ready")


@pytest.mark.parametrize("code", [0, 7, 259])
def test_normal_and_nonzero_real_exit_codes(tmp_path, code):
    p = start_fixture(tmp_path, str(code))
    try:
        folder = ready(tmp_path)
        (tmp_path / "release").touch()
        p.wait(timeout=8)
        result = wait_json(folder / "monitor.json", lambda v: v.get("state") == "exited")
        assert result["exit_code"] == code
        assert result["exit_code_hex"] == f"0x{code:08X}"
        assert result["clean_return"]
        assert result["classification"] == ("normal-return" if code == 0 else "nonzero-exit")
        assert "synthetic fixture log" in (folder / "application.log").read_text(encoding="utf-8")
    finally:
        if p.poll() is None: p.kill()
        p.wait()


@pytest.mark.parametrize("code", [37, 0xC0000005])
def test_external_termination_preserves_unsigned_and_signed_code(tmp_path, code):
    p = start_fixture(tmp_path)
    try:
        folder = ready(tmp_path)
        api = ctypes.WinDLL("kernel32", use_last_error=True)
        api.TerminateProcess.argtypes = [w.HANDLE, w.UINT]
        assert api.TerminateProcess(int(p._handle), code)
        p.wait(timeout=8)
        result = wait_json(folder / "monitor.json", lambda v: v.get("state") == "exited")
        assert result["exit_code"] == code  # Synthetic code, not an actual access violation.
        assert result["exit_code_signed"] == (code if code < 0x80000000 else code - 0x100000000)
        assert not result["clean_return"]
        assert result["classification"] == "nonzero-exit"
    finally:
        if p.poll() is None: p.kill()
        p.wait()


def test_exception_trace_is_linked_to_exit_session(tmp_path):
    p = start_fixture(tmp_path, "exception")
    try:
        folder = ready(tmp_path)
        (tmp_path / "release").touch()
        p.wait(timeout=8)
        result = wait_json(folder / "monitor.json", lambda v: v.get("state") == "exited")
        assert result["exit_code"] == 1 and not result["clean_return"]
        text = (folder / "application.log").read_text(encoding="utf-8")
        assert "Traceback" in text and "synthetic unhandled exception" in text
        assert "fatal.log" in result["evidence"]
    finally:
        if p.poll() is None: p.kill()
        p.wait()


def test_lost_observer_records_unknown_and_retains_live_sessions(tmp_path):
    p = start_fixture(tmp_path)
    try:
        folder = ready(tmp_path)
        monitor = d.read_json(folder / "monitor.json")
        with ProcessHandle(monitor["pid"]) as handle:
            api = ctypes.WinDLL("kernel32")
            api.OpenProcess.argtypes, api.OpenProcess.restype = [w.DWORD, w.BOOL, w.DWORD], w.HANDLE
            api.TerminateProcess.argtypes = [w.HANDLE, w.UINT]
            api.CloseHandle.argtypes = [w.HANDLE]
            owned = api.OpenProcess(1, False, monitor["pid"])
            try: assert api.TerminateProcess(owned, 23)
            finally: api.CloseHandle(owned)
            assert handle.wait(5000)
        d.reconcile_and_prune(folder.parent, keep=0)
        assert folder.exists()  # Original parent still alive.
        p.kill();p.wait()
        d.reconcile_and_prune(folder.parent)
        result = d.read_json(folder / "recovery.json")
        assert result["state"] == "exit-code-unavailable" and result["exit_code"] is None
        d.reconcile_and_prune(folder.parent, keep=0)
        assert not folder.exists()
    finally:
        if p.poll() is None: p.kill()
        p.wait()


def test_pid_reuse_and_missing_target_are_not_reported_as_exits(tmp_path):
    with ProcessHandle(os.getpid()) as current:
        manifest = {"pid": os.getpid(), "created_ticks": str(int(current.created) + 1)}
    d.write_json(tmp_path / "session.json", manifest)
    assert d.observe(tmp_path) == 1
    result = d.read_json(tmp_path / "monitor.json")
    assert result["exit_code"] is None and "mismatch" in result["error"]


def test_final_write_error_does_not_erase_an_already_observed_exit_code(tmp_path):
    folder = tmp_path / "completed";folder.mkdir()
    d.write_json(folder / "session.json", {"pid": 0, "created_ticks": "0"})
    d.write_json(folder / "monitor.json", {"state": "observer-error", "exit_code": 37})
    d.reconcile_and_prune(tmp_path)
    assert not (folder / "recovery.json").exists()
    assert d.read_json(folder / "monitor.json")["exit_code"] == 37


def test_unavailable_monitor_does_not_block_application(tmp_path, monkeypatch):
    monkeypatch.setenv("CODEXTOOLS_PET_DATA_DIR", str(tmp_path))
    s = d.begin(arm=False)
    try:
        s.arm_observer([str(tmp_path / "missing.exe")], timeout=.05)
        assert s.state["observer_armed"] is False
        s.finish(0)
        assert d.read_json(s.folder / "app.json")["clean_return"]
    finally:
        s.close();d._session = None


def test_recent_update_intent_requires_pid_and_creation_time():
    session = {"pid": 10, "created_ticks": "123"}
    request = {**session, "reason": "plugin-updater-stop", "requested_at": d.now()}
    assert d.classify_exit(1, {}, request, session) == "updater-requested-stop"
    assert d.classify_exit(1, {}, {**request, "created_ticks": "124"}, session) == "nonzero-exit"
    assert d.classify_exit(1, {}, {**request, "requested_at": "2020-01-01T00:00:00+00:00"}, session) == "nonzero-exit"


@pytest.mark.filterwarnings("ignore::pytest.PytestUnhandledThreadExceptionWarning")
def test_thread_qt_and_native_trace_logs_are_session_local(tmp_path, monkeypatch):
    import faulthandler
    import threading
    from PySide6.QtCore import qWarning, qInstallMessageHandler
    monkeypatch.setenv("CODEXTOOLS_PET_DATA_DIR", str(tmp_path))
    s = d.begin(arm=False)
    old_qt = qInstallMessageHandler(None)
    try:
        d.install_qt_logging()
        qWarning("synthetic Qt warning")
        def fail():
            raise ValueError("synthetic thread failure")
        thread = threading.Thread(target=fail)
        thread.start();thread.join()
        faulthandler.dump_traceback(file=s.fatal, all_threads=True)
        text = (s.folder / "application.log").read_text(encoding="utf-8")
        assert "synthetic Qt warning" in text and "synthetic thread failure" in text
        assert s.state["last_thread_exception_type"] == "ValueError"
        assert "test_thread_qt" in (s.folder / "fatal.log").read_text(encoding="utf-8")
    finally:
        qInstallMessageHandler(old_qt)
        s.close();d._session = None


def test_real_updater_stop_writes_matching_intent_and_waits_for_evidence(tmp_path):
    install = tmp_path / "install";install.mkdir()
    data = tmp_path / "data";data.mkdir()
    exe = install / "CodexToolsPet.exe"
    env = {**os.environ, "CODEXTOOLS_DIAGNOSTIC_FIXTURE_EXE": str(exe)}
    ps = str(Path(os.environ["SystemRoot"]) / "System32/WindowsPowerShell/v1.0/powershell.exe")
    subprocess.run([ps, "-NoProfile", "-NonInteractive", "-Command",
                    'Add-Type -TypeDefinition \'public static class ExitFixture {public static void Main(){System.Threading.Thread.Sleep(60000);}}\' -OutputAssembly $env:CODEXTOOLS_DIAGNOSTIC_FIXTURE_EXE -OutputType ConsoleApplication'],
                   env=env, check=True, capture_output=True)
    p = subprocess.Popen([str(exe)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    observer = None
    try:
        folder = data / "logs/sessions/synthetic-update";folder.mkdir(parents=True)
        with ProcessHandle(p.pid) as target:
            d.write_json(folder / "session.json", {"pid": p.pid, "created_ticks": target.created})
        observer = subprocess.Popen([sys._base_executable, str(ROOT / "run_pet.py"), "--exit-monitor", str(folder)],
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        wait_json(folder / "monitor.json", lambda v: v.get("state") == "watching")
        request = tmp_path / "request.json"
        d.write_json(request, {"id": "desktop-pet", "install": str(install), "dataDir": str(data)})
        adapter = ROOT.parents[1] / "tools/plugin-update-windows.ps1"
        result = subprocess.run([ps, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(adapter),
                                 "-Action", "stop", "-InputFile", str(request)],
                                check=True, capture_output=True, text=True)
        assert json.loads(result.stdout)["ok"]
        p.wait(timeout=5);observer.wait(timeout=5)
        record = d.read_json(folder / "monitor.json")
        assert record["state"] == "exited"
        assert record["classification"] == "updater-requested-stop"
        assert record["exit_code"] == p.returncode & 0xFFFFFFFF
    finally:
        if p.poll() is None: p.kill()
        p.wait()
        if observer:
            if observer.poll() is None: observer.kill()
            observer.wait()


@pytest.mark.parametrize("job_flags", [0x2800, 0x2000])
def test_observer_survives_synthetic_parent_job_close(tmp_path, job_flags):
    class Basic(ctypes.Structure):
        _fields_ = [("processTime", ctypes.c_int64), ("jobTime", ctypes.c_int64), ("flags", w.DWORD),
                    ("min", ctypes.c_size_t), ("max", ctypes.c_size_t), ("active", w.DWORD),
                    ("affinity", ctypes.c_size_t), ("priority", w.DWORD), ("scheduling", w.DWORD)]
    class Extended(ctypes.Structure):
        _fields_ = [("basic", Basic), ("io", ctypes.c_uint64 * 6), ("memory", ctypes.c_size_t * 4)]
    api = ctypes.WinDLL("kernel32", use_last_error=True)
    api.CreateJobObjectW.argtypes, api.CreateJobObjectW.restype = [w.LPVOID, w.LPCWSTR], w.HANDLE
    api.SetInformationJobObject.argtypes = [w.HANDLE, ctypes.c_int, w.LPVOID, w.DWORD]
    api.AssignProcessToJobObject.argtypes = [w.HANDLE, w.HANDLE]
    api.IsProcessInJob.argtypes = [w.HANDLE, w.HANDLE, ctypes.POINTER(w.BOOL)]
    api.CloseHandle.argtypes = [w.HANDLE]
    job = api.CreateJobObjectW(None, None)
    assert job
    limits = Extended();limits.basic.flags = job_flags
    assert api.SetInformationJobObject(job, 9, ctypes.byref(limits), ctypes.sizeof(limits))
    # Start the fixture suspended, assign only it to our job, then resume it.
    p = start_fixture(tmp_path, flags=0x4 | subprocess.CREATE_BREAKAWAY_FROM_JOB)
    try:
        assert api.AssignProcessToJobObject(job, int(p._handle))
        native = ctypes.WinDLL("ntdll")
        native.NtResumeProcess.argtypes = [w.HANDLE]
        assert native.NtResumeProcess(int(p._handle)) == 0
        folder = ready(tmp_path)
        if job_flags == 0x2000:
            assert d.read_json(folder / "app.json")["observer_launch_mode"] == "windows-wmi-broker"
        monitor = d.read_json(folder / "monitor.json")
        with ProcessHandle(monitor["pid"]) as observer:
            assert observer.package_identity() is None
            inside = w.BOOL()
            assert api.IsProcessInJob(observer.handle, job, ctypes.byref(inside))
            assert not inside.value
            api.CloseHandle(job);job = None
            p.wait(timeout=8)
            result = wait_json(folder / "monitor.json", lambda v: v.get("state") == "exited")
            assert result["exit_code"] == p.returncode & 0xFFFFFFFF
            # Job closure can report zero. A zero code alone is not a clean exit.
            assert not result["clean_return"]
            assert result["classification"] in ("nonzero-exit", "zero-exit-without-clean-return")
            assert observer.wait(5000)
    finally:
        if job: api.CloseHandle(job)
        if p.poll() is None: p.kill()
        p.wait()
