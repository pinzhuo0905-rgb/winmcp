"""Install winmcp without setuptools.

``pip install -e .`` needs a build backend, and pip downloads ``setuptools`` from PyPI
on demand. On a machine without network access — or without setuptools already
installed — that path is closed.

This installer does the same job directly: copy the package into site-packages, write a
dist-info so ``pip list`` sees it, and drop a console script on PATH. It is deliberately
small and readable, and it can undo itself.

    python scripts/install_local.py              # install into the current interpreter
    python scripts/install_local.py --uninstall
    python scripts/install_local.py --target <python.exe>
    python scripts/install_local.py --dry-run
"""

from __future__ import annotations

import argparse
import contextlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PACKAGE = "winmcp"
VERSION = "1.0.0"

CONSOLE_SCRIPT = """\
@echo off
"{python}" -m winmcp.cli %*
"""

#: Git Bash / MSYS do not resolve ``.cmd`` shims, so an extension-less shell script is
#: written alongside. Without it ``winmcp`` works in cmd and PowerShell but not in bash.
SH_CONSOLE_SCRIPT = """\
#!/bin/sh
exec "{python}" -m winmcp.cli "$@"
"""


def _metadata() -> str:
    return (
        "Metadata-Version: 2.1\n"
        f"Name: {PACKAGE}\n"
        f"Version: {VERSION}\n"
        "Summary: Native Windows desktop control: the full Windows-MCP capability "
        "surface, built in.\n"
        "Requires-Python: >=3.10\n"
        "License: MIT\n"
        "Description-Content-Type: text/markdown\n"
    )


def _wheel() -> str:
    return (
        "Wheel-Version: 1.0\n"
        "Generator: winmcp install_local\n"
        "Root-Is-Purelib: true\n"
        "Tag: py3-none-any\n"
    )


def paths(target_python: str) -> dict[str, Path]:
    """Ask the target interpreter where things go."""
    code = (
        "import json, site, sysconfig;"
        "print(json.dumps({"
        "'purelib': sysconfig.get_path('purelib'),"
        "'scripts': sysconfig.get_path('scripts'),"
        "'prefix': sysconfig.get_path('data'),"
        "}))"
    )
    out = subprocess.run(
        [target_python, "-c", code], capture_output=True, text=True, check=True
    ).stdout
    raw = json.loads(out.strip().splitlines()[-1])
    return {k: Path(v) for k, v in raw.items()}


def _write_console_scripts(scripts_dir: Path, python: str) -> list[Path]:
    """Windows needs a .cmd shim; Git Bash additionally needs an extension-less one."""
    written: list[Path] = []
    for suffix in (".cmd", ".bat"):
        path = scripts_dir / f"{PACKAGE}{suffix}"
        path.write_text(CONSOLE_SCRIPT.format(python=python), encoding="utf-8")
        written.append(path)

    sh = scripts_dir / PACKAGE
    sh.write_text(SH_CONSOLE_SCRIPT.format(python=python), encoding="utf-8")
    with contextlib.suppress(OSError):
        sh.chmod(0o755)
    written.append(sh)
    return written


def add_to_user_path(directory: Path, dry_run: bool = False) -> str:
    """Append ``directory`` to the per-user PATH.

    Only the *user* PATH is touched, never the system one. The change is additive —
    nothing existing is removed or reordered — and ``--uninstall`` reverses it.

    Returns a human-readable status.
    """
    import winreg

    key_path = r"Environment"
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_READ) as key:
        try:
            current, kind = winreg.QueryValueEx(key, "Path")
        except FileNotFoundError:
            current, kind = "", winreg.REG_EXPAND_SZ

    entries = [e for e in current.split(";") if e.strip()]
    target = str(directory)
    if any(Path(e).resolve() == directory.resolve() for e in entries if e.strip()):
        return "already on the user PATH"

    if dry_run:
        return f"would append to the user PATH: {target}"

    updated = ";".join([*entries, target])
    with winreg.OpenKey(key_path and winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_WRITE) as key:
        winreg.SetValueEx(key, "Path", 0, kind, updated)

    _broadcast_environment_change()
    return f"appended to the user PATH: {target} (takes effect in new terminals)"


def remove_from_user_path(directory: Path) -> str:
    """Undo :func:`add_to_user_path`."""
    import winreg

    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Environment", 0, winreg.KEY_READ) as key:
        try:
            current, kind = winreg.QueryValueEx(key, "Path")
        except FileNotFoundError:
            return "user PATH unchanged"
        entries = [e for e in current.split(";") if e.strip()]
        kept = [e for e in entries if Path(e).resolve() != directory.resolve()]
        if len(kept) == len(entries):
            return "not present on the user PATH"

    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Environment", 0, winreg.KEY_WRITE) as key:
        winreg.SetValueEx(key, "Path", 0, kind, ";".join(kept))
    _broadcast_environment_change()
    return f"removed from the user PATH: {directory}"


def _broadcast_environment_change() -> None:
    """Tell running processes the environment changed, so new shells pick it up."""
    import ctypes

    HWND_BROADCAST = 0xFFFF
    WM_SETTINGCHANGE = 0x001A
    SMTO_ABORTIFHUNG = 0x0002
    result = ctypes.c_ulong()
    ctypes.windll.user32.SendMessageTimeoutW(  # type: ignore[attr-defined]
        HWND_BROADCAST, WM_SETTINGCHANGE, 0, ctypes.c_wchar_p("Environment"),
        SMTO_ABORTIFHUNG, 3000, ctypes.byref(result),
    )


def install(target_python: str, dry_run: bool = False, add_to_path: bool = True) -> int:
    where = paths(target_python)
    site = where["purelib"]
    scripts = where["scripts"]
    dist_info = site / f"{PACKAGE}-{VERSION}.dist-info"

    print(f"target interpreter : {target_python}")
    print(f"site-packages      : {site}")
    print(f"scripts            : {scripts}")
    print()

    if not site.is_dir():
        print(f"error: {site} does not exist")
        return 1

    src = ROOT / "src" / PACKAGE
    if not src.is_dir():
        print(f"error: package source not found at {src}")
        return 1

    if dry_run:
        print(f"would copy   {src} -> {site / PACKAGE}")
        print(f"would write  {dist_info}")
        print(f"would write  {scripts / 'winmcp.cmd'}")
        if add_to_path:
            print(f"would append to the user PATH: {scripts}")
        return 0

    # 1. package
    dest = site / PACKAGE
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(src, dest, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    py_files = sorted(p.relative_to(site) for p in dest.rglob("*.py"))
    print(f"installed package   {dest}  ({len(py_files)} modules)")

    # 2. dist-info so pip list recognises it
    dist_info.mkdir(parents=True, exist_ok=True)
    (dist_info / "METADATA").write_text(_metadata(), encoding="utf-8")
    (dist_info / "WHEEL").write_text(_wheel(), encoding="utf-8")
    (dist_info / "INSTALLER").write_text("winmcp-install-local\n", encoding="utf-8")
    record_lines = [
        f"{p.relative_to(site).as_posix()},," for p in sorted(dest.rglob("*")) if p.is_file()
    ]
    record_lines += [
        f"{n.relative_to(site).as_posix()},," for n in sorted(dist_info.glob("*"))
    ]
    (dist_info / "RECORD").write_text("\n".join(record_lines) + "\n", encoding="utf-8")
    print(f"installed metadata  {dist_info}")

    # 3. console script
    for path in _write_console_scripts(scripts, target_python):
        print(f"installed launcher  {path}")

    # 4. make the launcher reachable
    if add_to_path:
        print(f"PATH                {add_to_user_path(scripts)}")

    print()
    print("verify:")
    print(f'  "{target_python}" -m winmcp.cli info')
    print("  winmcp info            (in a new terminal)")
    return 0


def uninstall(target_python: str) -> int:
    where = paths(target_python)
    site = where["purelib"]
    removed = 0
    for path in (site / PACKAGE, site / f"{PACKAGE}-{VERSION}.dist-info"):
        if path.exists():
            shutil.rmtree(path)
            print(f"removed {path}")
            removed += 1
    for name in (f"{PACKAGE}.cmd", f"{PACKAGE}.bat", PACKAGE):
        p = where["scripts"] / name
        if p.exists():
            p.unlink()
            print(f"removed {p}")
            removed += 1
    print(f"PATH                {remove_from_user_path(where['scripts'])}")
    if not removed:
        print("nothing to remove")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="install winmcp without setuptools")
    ap.add_argument("--target", default=sys.executable, help="target python.exe")
    ap.add_argument("--uninstall", action="store_true", help="remove a previous install")
    ap.add_argument("--dry-run", action="store_true", help="show what would happen")
    ap.add_argument(
        "--no-path", dest="add_to_path", action="store_false",
        help="skip adding the Scripts directory to the user PATH",
    )
    args = ap.parse_args()

    if args.uninstall:
        return uninstall(args.target)
    return install(args.target, dry_run=args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
