"""进程层：枚举与终止。

用 Toolhelp32 快照——不需要 WMI，也不需要 psutil，标准库 + ctypes 就够。
"""

from __future__ import annotations

import ctypes
from dataclasses import dataclass

from .const import (
    INVALID_HANDLE_VALUE,
    MAX_PATH,
    PROCESS_TERMINATE,
    PROCESSENTRY32,
    TH32CS_SNAPPROCESS,
    kernel32,
)

#: 常见进程名 → 人类可读说明，方便模型理解列表内容
_KNOWN: dict[str, str] = {
    "explorer.exe": "Windows 资源管理器",
    "chrome.exe": "Google Chrome",
    "msedge.exe": "Microsoft Edge",
    "firefox.exe": "Firefox",
    "notepad.exe": "记事本",
    "cmd.exe": "命令提示符",
    "powershell.exe": "PowerShell",
    "python.exe": "Python",
    "pythonw.exe": "Python (无窗口)",
    "code.exe": "VS Code",
    "winword.exe": "Word",
    "excel.exe": "Excel",
    "powerpnt.exe": "PowerPoint",
    "wechat.exe": "微信",
    "qq.exe": "QQ",
    "dwm.exe": "桌面窗口管理器",
    "svchost.exe": "系统服务宿主",
}


@dataclass
class Process:
    pid: int
    name: str
    parent_pid: int
    threads: int

    @property
    def description(self) -> str:
        return _KNOWN.get(self.name.lower(), "")

    def as_dict(self) -> dict:
        out = {
            "pid": self.pid,
            "name": self.name,
            "parent_pid": self.parent_pid,
            "threads": self.threads,
        }
        if self.description:
            out["description"] = self.description
        return out


def list_processes(name_filter: str = "", limit: int = 0) -> list[Process]:
    """枚举进程。``name_filter`` 按子串匹配进程名。"""
    snapshot = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if snapshot == INVALID_HANDLE_VALUE or not snapshot:
        raise OSError("CreateToolhelp32Snapshot 失败")

    out: list[Process] = []
    try:
        entry = PROCESSENTRY32()
        entry.dwSize = ctypes.sizeof(PROCESSENTRY32)
        if not kernel32.Process32First(snapshot, ctypes.byref(entry)):
            return []

        needle = (name_filter or "").strip().lower()
        while True:
            name = entry.szExeFile.decode("utf-8", "replace")
            if not needle or needle in name.lower():
                out.append(
                    Process(
                        pid=int(entry.th32ProcessID),
                        name=name,
                        parent_pid=int(entry.th32ParentProcessID),
                        threads=int(entry.cntThreads),
                    )
                )
            if not kernel32.Process32Next(snapshot, ctypes.byref(entry)):
                break
    finally:
        kernel32.CloseHandle(snapshot)

    out.sort(key=lambda p: p.name.lower())
    return out[:limit] if limit > 0 else out


def find_processes(name: str) -> list[Process]:
    return list_processes(name_filter=name)


def kill(pid: int, force: bool = False) -> bool:
    """终止进程。``force`` 目前与普通终止等价（OpenProcess 已带 TERMINATE 权限）。"""
    _ = force
    handle = kernel32.OpenProcess(PROCESS_TERMINATE, False, pid)
    if not handle:
        return False
    try:
        return bool(kernel32.TerminateProcess(handle, 1))
    finally:
        kernel32.CloseHandle(handle)


def process_name(pid: int) -> str:
    """按 PID 反查进程名——窗口列表里显示归属进程时用得上。"""
    if pid <= 0:
        return ""
    for p in list_processes():
        if p.pid == pid:
            return p.name
    return ""


MAX_NAME = MAX_PATH  # 保持常量导出，便于测试断言

__all__ = ["MAX_NAME", "Process", "find_processes", "kill", "list_processes", "process_name"]
