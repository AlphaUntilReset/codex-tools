"""Windows process handles and independent launches; no Qt or development runtime."""
from __future__ import annotations

import ctypes
import base64
from ctypes import wintypes as w
import json
import os
import subprocess
import sys
from pathlib import Path


class ProcessHandle:
    def __init__(self, pid: int):
        self.api = ctypes.WinDLL("kernel32", use_last_error=True)
        signatures = {
            "OpenProcess": ([w.DWORD, w.BOOL, w.DWORD], w.HANDLE),
            "CloseHandle": ([w.HANDLE], w.BOOL),
            "GetProcessTimes": ([w.HANDLE] + [ctypes.POINTER(w.FILETIME)] * 4, w.BOOL),
            "WaitForSingleObject": ([w.HANDLE, w.DWORD], w.DWORD),
            "GetExitCodeProcess": ([w.HANDLE, ctypes.POINTER(w.DWORD)], w.BOOL),
        }
        for name, (args, result) in signatures.items():
            method = getattr(self.api, name)
            method.argtypes, method.restype = args, result
        self.handle = self.api.OpenProcess(0x100000 | 0x1000, False, pid)
        if not self.handle:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            times = [w.FILETIME() for _ in range(4)]
            if not self.api.GetProcessTimes(self.handle, *(ctypes.byref(t) for t in times)):
                raise ctypes.WinError(ctypes.get_last_error())
            self.created = str((times[0].dwHighDateTime << 32) | times[0].dwLowDateTime)
        except BaseException:
            self.close()
            raise

    def wait(self, milliseconds: int = 0) -> bool:
        result = self.api.WaitForSingleObject(self.handle, milliseconds)
        if result == 0xFFFFFFFF:
            raise ctypes.WinError(ctypes.get_last_error())
        return result == 0

    def exit_code(self) -> int:
        # Query only after the handle is signaled: even exit code 259 is valid then.
        if not self.wait():
            raise RuntimeError("Process has not exited")
        code = w.DWORD()
        if not self.api.GetExitCodeProcess(self.handle, ctypes.byref(code)):
            raise ctypes.WinError(ctypes.get_last_error())
        return code.value

    def package_identity(self) -> str | None:
        query = self.api.GetPackageFullName
        query.argtypes = [w.HANDLE, ctypes.POINTER(w.UINT), w.LPWSTR]
        query.restype = w.LONG
        size = w.UINT()
        code = query(self.handle, ctypes.byref(size), None)
        if code == 15700:  # APPMODEL_ERROR_NO_PACKAGE
            return None
        if code != 122:
            raise ctypes.WinError(code)
        text = ctypes.create_unicode_buffer(size.value)
        code = query(self.handle, ctypes.byref(size), text)
        if code:
            raise ctypes.WinError(code)
        return text.value

    def close(self) -> None:
        if self.handle:
            self.api.CloseHandle(self.handle)
            self.handle = None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


def same_live_process(pid: int, created: str) -> bool:
    try:
        with ProcessHandle(pid) as process:
            return process.created == created and not process.wait()
    except OSError:
        return False


class BrokeredProcess:
    def __init__(self, pid: int):
        self.pid = pid
        self.process = ProcessHandle(pid)

    def poll(self):
        return self.process.exit_code() if self.process.wait() else None

    def __del__(self):
        if getattr(self, "process", None):
            self.process.close()


def launch_via_windows_broker(command: list[str], **kwargs) -> BrokeredProcess:
    """WMI creates the observer outside the caller's non-breakaway job.

    Only Windows' installed PowerShell is used briefly; the observer itself is
    bundled. No scheduled task, service registration or elevation is involved.
    """
    ps = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32/WindowsPowerShell/v1.0/powershell.exe"
    script = '''
$ErrorActionPreference='Stop'
$childEnvironment=[Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($env:CODEXTOOLS_CHILD_ENVIRONMENT))|ConvertFrom-Json
$variables=@($childEnvironment.PSObject.Properties|ForEach-Object {$_.Name+'='+[string]$_.Value})
$startup=New-CimInstance -ClassName Win32_ProcessStartup -ClientOnly -Property @{CreateFlags=[uint32]0x09000000;ShowWindow=[uint16]0;EnvironmentVariables=[string[]]$variables}
$result=Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{CommandLine=$env:CODEXTOOLS_OBSERVER_COMMAND;CurrentDirectory=$env:CODEXTOOLS_OBSERVER_DIRECTORY;ProcessStartupInformation=$startup}
if($result.ReturnValue -ne 0){throw ('Observer broker error: '+$result.ReturnValue)}
[Console]::Out.WriteLine($result.ProcessId)
'''
    env = dict(kwargs.get("env") or os.environ)
    child_env = dict(env)
    env.update(CODEXTOOLS_OBSERVER_COMMAND=subprocess.list2cmdline(command),
               CODEXTOOLS_OBSERVER_DIRECTORY=str(Path(command[0]).resolve().parent),
               CODEXTOOLS_CHILD_ENVIRONMENT=base64.b64encode(json.dumps(child_env).encode("utf-8")).decode("ascii"),
               PSModulePath=str(ps.parent / "Modules"))
    result = subprocess.run([str(ps), "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", script],
                            env=env, stdin=subprocess.DEVNULL, capture_output=True,
                            creationflags=subprocess.CREATE_NO_WINDOW, timeout=12, check=True)
    process = BrokeredProcess(int(result.stdout.strip()))
    if process.process.package_identity() is not None:
        raise RuntimeError("Windows broker child still has package identity")
    return process


def relaunch_outside_package() -> bool:
    """Frozen pets launched by a packaged client must leave its servicing lifetime."""
    if os.name != "nt" or not getattr(sys, "frozen", False):
        return False
    with ProcessHandle(os.getpid()) as own:
        if own.package_identity() is None:
            return False
    if os.environ.get("CODEXTOOLS_PACKAGE_RELAUNCH"):
        raise RuntimeError("Package identity remained after independent relaunch")
    env = {**os.environ, "PYINSTALLER_RESET_ENVIRONMENT": "1", "CODEXTOOLS_PACKAGE_RELAUNCH": "1"}
    launch_via_windows_broker([sys.executable, *sys.argv[1:]], env=env)
    return True


def launch_independent(command: list[str], **kwargs) -> tuple[subprocess.Popen | BrokeredProcess, str]:
    """Break away from killing jobs, never silently inherit one on access denied."""
    if os.name != "nt":
        return subprocess.Popen(command, start_new_session=True, **kwargs), "new-session"
    with ProcessHandle(os.getpid()) as own:
        if own.package_identity() is not None:
            return launch_via_windows_broker(command, **kwargs), "windows-wmi-broker"
    api = ctypes.WinDLL("kernel32", use_last_error=True)
    api.GetCurrentProcess.restype = w.HANDLE
    api.IsProcessInJob.argtypes = [w.HANDLE, w.HANDLE, ctypes.POINTER(w.BOOL)]
    api.QueryInformationJobObject.argtypes = [w.HANDLE, ctypes.c_int, w.LPVOID, w.DWORD, w.LPVOID]
    inside = w.BOOL()
    if not api.IsProcessInJob(api.GetCurrentProcess(), None, ctypes.byref(inside)):
        raise ctypes.WinError(ctypes.get_last_error())
    flags = subprocess.CREATE_NO_WINDOW
    if inside.value:
        flags |= subprocess.CREATE_BREAKAWAY_FROM_JOB
    try:
        return subprocess.Popen(command, creationflags=flags, **kwargs), "job-breakaway" if inside.value else "no-job"
    except OSError as error:
        if error.winerror != 5 or not inside.value:
            raise
        # JOBOBJECT_BASIC_LIMIT_INFORMATION.LimitFlags is at offset 16 on x86/x64.
        basic = ctypes.create_string_buffer(64)
        if not api.QueryInformationJobObject(None, 2, basic, len(basic), None):
            raise ctypes.WinError(ctypes.get_last_error())
        if int.from_bytes(basic.raw[16:20], "little") & 0x2000:
            return launch_via_windows_broker(command, **kwargs), "windows-wmi-broker"
        return subprocess.Popen(command, creationflags=subprocess.CREATE_NO_WINDOW, **kwargs), "non-killing-system-job"
