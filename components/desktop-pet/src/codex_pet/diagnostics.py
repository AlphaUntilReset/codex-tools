"""Local session evidence, including a separate observer of the real Windows exit code.

Each writer owns a different JSON file. No settings, chat contents or network upload.
The observer records the real exit before bounded, intent-aware release recovery.
"""
from __future__ import annotations

from datetime import datetime, timezone
import faulthandler
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import shutil
import sys
import threading
import time
import uuid

from codex_pet import __version__, paths
from codex_pet.process_evidence import ProcessHandle, same_live_process, launch_independent

_session = None


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def read_json(file: Path) -> dict:
    try:
        return json.loads(file.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return {}


def write_json(file: Path, value: dict) -> None:
    temporary = file.with_name(file.name + "." + str(os.getpid()) + ".tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.flush()
        os.fsync(stream.fileno())
    # Windows readers/virus scanners may briefly deny delete sharing.
    for attempt in range(10):
        try:
            os.replace(temporary, file)
            return
        except PermissionError:
            if attempt == 9:
                raise
            time.sleep(.025)


def reconcile_and_prune(root: Path, keep: int = 30) -> None:
    """Retain all live sessions and the newest 30 finished/incomplete sessions."""
    inactive = []
    for folder in sorted(root.glob("*"), reverse=True):
        if not folder.is_dir() or folder.is_symlink():
            continue
        session = read_json(folder / "session.json")
        if not session:
            continue
        if same_live_process(session["pid"], session["created_ticks"]):
            continue
        monitor = read_json(folder / "monitor.json")
        if monitor.get("state") == "watching" and same_live_process(monitor["pid"], monitor["created_ticks"]):
            continue
        if monitor.get("state") != "exited" and monitor.get("exit_code") is None:
            write_json(folder / "recovery.json", {
                "checked_at": now(), "state": "exit-code-unavailable",
                "reason": "No final structured exit result. Inspect observer.log and Windows application events for additional evidence.",
                "exit_code": None,
            })
        inactive.append(folder)
    for folder in inactive[keep:]:
        shutil.rmtree(folder)


def classify_exit(code: int, app: dict, request: dict, session: dict) -> str:
    try:
        age = (datetime.now(timezone.utc) - datetime.fromisoformat(request["requested_at"])).total_seconds()
    except (ValueError, KeyError, TypeError):
        age = -1
    valid_request = (request.get("pid") == session["pid"]
                     and request.get("created_ticks") == session["created_ticks"]
                     and request.get("reason") == "plugin-updater-stop" and 0 <= age <= 30)
    if valid_request:
        return "updater-requested-stop"
    if code == 0 and app.get("clean_return"):
        return "normal-return"
    return "nonzero-exit" if code else "zero-exit-without-clean-return"


def observe(folder: Path) -> int:
    """Acquire the handle before acknowledging readiness, then wait for termination."""
    state = {"state": "starting", "pid": os.getpid(), "started_at": now()}
    try:
        session = read_json(folder / "session.json")
        with ProcessHandle(os.getpid()) as own:
            state["created_ticks"] = own.created
        with ProcessHandle(session["pid"]) as target:
            if target.created != session["created_ticks"]:
                raise RuntimeError("PID creation time mismatch; refusing to observe a reused PID")
            state.update(state="watching", target_pid=session["pid"], ready_at=now())
            write_json(folder / "monitor.json", state)
            while not target.wait(1000):
                pass
            code = target.exit_code()
        app = read_json(folder / "app.json")
        request = read_json(folder / "stop-request.json")
        state.update(state="exited", ended_at=now(), exit_code=code,
                     exit_code_hex=f"0x{code:08X}",
                     exit_code_signed=code if code < 0x80000000 else code - 0x100000000,
                     classification=classify_exit(code, app, request, session),
                     clean_return=bool(app.get("clean_return")),
                     exit_intent=app.get("exit_intent"),
                     app_return_code=app.get("return_code"),
                     requested_stop=request or None,
                     evidence=["session.json", "app.json", "application.log", "fatal.log", "observer.log"])
        write_json(folder / "monitor.json", state)
        from codex_pet.recovery import recover_session
        try:
            recover_session(folder, session)
        except Exception as error:
            write_json(folder / "recovery.json", {"state": "failed", "error_type": type(error).__name__, "checked_at": now()})
        return 0
    except BaseException as error:
        state.update(state="observer-error", failed_at=now(), error=repr(error))
        state.setdefault("exit_code", None)
        try:
            write_json(folder / "monitor.json", state)
        except OSError:
            try:
                with (folder / "observer.log").open("a", encoding="utf-8") as output:
                    output.write(json.dumps(state, ensure_ascii=False) + "\n")
            except OSError:
                pass
        return 1


class Session:
    def __init__(self, folder: Path, manifest: dict):
        self.folder, self.manifest = folder, manifest
        self.state = {"session_id": manifest["session_id"], "started_at": now()}
        self.lock = threading.RLock()
        self.handlers = []
        self.fatal = None
        self.original_exception = sys.excepthook
        self.original_thread_exception = threading.excepthook

    def update(self, **fields) -> None:
        try:
            with self.lock:
                self.state.update(fields, updated_at=now())
                write_json(self.folder / "app.json", self.state)
        except OSError:
            logging.getLogger("codex_pet.diagnostics").warning("诊断状态写入失败", exc_info=True)

    def install(self) -> None:
        handler = RotatingFileHandler(self.folder / "application.log", maxBytes=256 * 1024,
                                      backupCount=2, encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
        logging.getLogger().addHandler(handler)
        logging.getLogger().setLevel(logging.INFO)
        self.handlers.append(handler)
        self.fatal = (self.folder / "fatal.log").open("a", encoding="utf-8")
        faulthandler.enable(file=self.fatal, all_threads=True)

        def exception(kind, value, trace):
            logging.getLogger("codex_pet.exception").critical("Unhandled Python/Qt callback exception", exc_info=(kind, value, trace))
            self.update(last_exception_at=now(), last_exception_type=kind.__name__)
            self.original_exception(kind, value, trace)

        def thread_exception(args):
            logging.getLogger("codex_pet.exception").critical("Unhandled background thread exception", exc_info=(args.exc_type, args.exc_value, args.exc_traceback))
            self.update(last_thread_exception_at=now(), last_thread_exception_type=args.exc_type.__name__)
            self.original_thread_exception(args)

        sys.excepthook, threading.excepthook = exception, thread_exception
        self.update(stage="bootstrap")

    def arm_observer(self, command: list[str] | None = None, timeout: float = 3.0) -> None:
        deadline = time.monotonic() + timeout
        if command is None:
            if getattr(sys, "frozen", False):
                command = [str(Path(sys.executable).with_name("CodexToolsMonitor.exe"))]
            else:
                command = [sys.executable, str(paths.app_base_dir() / "run_pet.py")]
        try:
            with (self.folder / "observer.log").open("ab") as output:
                process, mode = launch_independent(command + ["--exit-monitor", str(self.folder)],
                                                  stdin=-3, stdout=output, stderr=output, close_fds=True,
                                                  env={**os.environ, "PYINSTALLER_RESET_ENVIRONMENT": "1"})
            self.update(observer_pid=process.pid, observer_launch_mode=mode)
            while time.monotonic() < deadline:
                state = read_json(self.folder / "monitor.json")
                if state.get("state") == "watching":
                    self.update(observer_armed=True)
                    return
                code = process.poll()
                if code is not None:
                    raise RuntimeError(f"Observer exited before readiness: {code}")
                time.sleep(0.025)
            raise TimeoutError("Observer readiness timed out; the pet will continue starting")
        except Exception:
            self.update(observer_armed=False)
            logging.getLogger("codex_pet.diagnostics").exception("独立退出记录器未就绪，桌宠继续启动")

    def finish(self, code: int, *, clean: bool = True) -> None:
        self.update(return_code=code, clean_return=clean, returned_at=now())
        logging.getLogger("codex_pet.diagnostics").info("Session %s returned: %s", self.manifest["session_id"], code)
        for handler in self.handlers:
            handler.flush()

    def close(self) -> None:
        sys.excepthook = self.original_exception
        threading.excepthook = self.original_thread_exception
        if self.fatal is not None:
            faulthandler.disable()
            self.fatal.close()
        for handler in self.handlers:
            logging.getLogger().removeHandler(handler)
            handler.close()


def begin(*, arm: bool = True) -> Session | None:
    global _session
    if os.name != "nt":
        return None
    try:
        root = paths.log_dir() / "sessions"
        root.mkdir(parents=True, exist_ok=True)
        # Housekeeping failure must not prevent new evidence or normal startup.
        try:
            reconcile_and_prune(root)
        except Exception:
            logging.getLogger("codex_pet.diagnostics").warning("旧诊断整理失败", exc_info=True)
        identifier = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex
        folder = root / identifier
        folder.mkdir()
        with ProcessHandle(os.getpid()) as process:
            created = process.created
            package_identity = process.package_identity()
        manifest = {"schema": 1, "session_id": identifier, "pid": os.getpid(),
                    "created_ticks": created, "started_at": now(), "version": __version__,
                    "executable": sys.executable, "frozen": bool(getattr(sys, "frozen", False)),
                    "python": sys.version.split()[0], "platform": sys.platform}
        manifest.update(package_identity=package_identity, parent_pid=os.getppid(),
                        recovery_enabled=bool(getattr(sys, "frozen", False)))
        if manifest["recovery_enabled"]:
            from codex_pet.recovery import file_hash
            manifest["executable_sha256"] = file_hash(Path(sys.executable))
        write_json(folder / "session.json", manifest)
        session = Session(folder, manifest)
        _session = session
        session.install()
        if arm:
            session.arm_observer()
        logging.getLogger("codex_pet.diagnostics").info("Session %s pid=%s observer=%s", identifier, os.getpid(), session.state.get("observer_armed"))
        return session
    except Exception:
        logging.getLogger("codex_pet.diagnostics").exception("诊断初始化失败，桌宠继续启动")
        return None


def event(**fields) -> None:
    if _session is not None:
        _session.update(**fields)


def install_qt_logging() -> None:
    from PySide6.QtCore import qInstallMessageHandler, QtMsgType

    def handler(kind, context, message):
        level = logging.ERROR if kind in (QtMsgType.QtCriticalMsg, QtMsgType.QtFatalMsg) else logging.WARNING
        if kind != QtMsgType.QtDebugMsg:
            logging.getLogger("codex_pet.qt").log(level, "%s: %s", context.category, message)

    qInstallMessageHandler(handler)
