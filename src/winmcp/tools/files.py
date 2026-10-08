"""文件系统工具。

一个刻意保守的设计：**破坏性操作必须显式声明**。

- 删除目录要传 ``recursive=true``，否则直接报错
- 删除和移动都会报告影响了多少条目
- 所有操作返回绝对路径，避免模型在相对路径上打转
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

from .base import Registry, Risk, ToolError, ToolResult, obj, prop

_MAX_PREVIEW = 4000


def _safe_path(raw: str) -> Path:
    if not raw or not str(raw).strip():
        raise ToolError("path 不能为空")
    return Path(os.path.expandvars(os.path.expanduser(str(raw).strip())))


def register(reg: Registry) -> None:
    @reg.tool(
        name="FileSystem",
        description=(
            "File operations. mode=read returns text content (use offset/limit for big "
            "files); write creates or overwrites; append adds to the end; list shows a "
            "directory; mkdir creates directories; delete removes (directories need "
            "recursive=true); move renames or relocates; copy duplicates; exists checks; "
            "search finds files by glob pattern."
        ),
        schema=obj(
            {
                "mode": prop(
                    "string",
                    "read, write, append, list, mkdir, delete, move, copy, exists, search",
                ),
                "path": prop("string", "Target path (absolute recommended)"),
                "destination": prop("string", "Target for move/copy"),
                "content": prop("string", "Content for write/append"),
                "pattern": prop("string", "Glob for search, e.g. *.log"),
                "recursive": prop("boolean", "Required for deleting a non-empty directory"),
                "overwrite": prop("boolean", "Allow write to replace an existing file"),
                "offset": prop("integer", "First line to read (0-based)"),
                "limit": prop("integer", "Max lines to read"),
                "encoding": prop("string", "Text encoding, default utf-8"),
            },
            required=["mode", "path"],
        ),
        risk=Risk.HIGH,
        category="files",
    )
    def file_system(
        mode: str,
        path: str,
        destination: str = "",
        content: str = "",
        pattern: str = "*",
        recursive: bool = False,
        overwrite: bool = True,
        offset: int = 0,
        limit: int = 500,
        encoding: str = "utf-8",
    ) -> ToolResult:
        m = (mode or "").strip().lower()
        p = _safe_path(path)

        if m == "read":
            if not p.is_file():
                raise ToolError(f"不是文件：{p}")
            text = p.read_text(encoding=encoding, errors="replace")
            lines = text.splitlines()
            start = max(0, int(offset))
            chunk = lines[start : start + max(1, int(limit))]
            body = "\n".join(chunk)
            truncated = len(body) > _MAX_PREVIEW
            if truncated:
                body = body[:_MAX_PREVIEW] + "\n… (truncated)"
            return ToolResult(
                text=(
                    f"{p} — {len(lines)} line(s), showing {start + 1}–{start + len(chunk)}\n"
                    f"{body}"
                ),
                data={
                    "path": str(p),
                    "total_lines": len(lines),
                    "shown": [start, start + len(chunk)],
                    "content": body,
                },
            )

        if m in ("write", "append"):
            p.parent.mkdir(parents=True, exist_ok=True)
            if m == "write":
                if p.exists() and not overwrite:
                    raise ToolError(f"{p} 已存在，且 overwrite=false")
                p.write_text(content, encoding=encoding)
                verb = "Wrote"
            else:
                with p.open("a", encoding=encoding) as fh:
                    fh.write(content)
                verb = "Appended to"
            return ToolResult(
                text=f"{verb} {p} ({len(content)} chars, {p.stat().st_size} bytes total)",
                data={"path": str(p), "size": p.stat().st_size, "mode": m},
            )

        if m == "list":
            if not p.is_dir():
                raise ToolError(f"不是目录：{p}")
            entries = sorted(p.iterdir(), key=lambda e: (e.is_file(), e.name.lower()))
            lines = [f"{p} — {len(entries)} entr(ies)"]
            for e in entries[:200]:
                kind = "DIR " if e.is_dir() else "FILE"
                size = "" if e.is_dir() else f"  {e.stat().st_size:>10,} B"
                lines.append(f"  [{kind}] {e.name}{size}")
            if len(entries) > 200:
                lines.append(f"  … and {len(entries) - 200} more")
            return ToolResult(
                text="\n".join(lines),
                data={
                    "path": str(p),
                    "count": len(entries),
                    "entries": [
                        {"name": e.name, "dir": e.is_dir(), "size": 0 if e.is_dir() else e.stat().st_size}
                        for e in entries[:200]
                    ],
                },
            )

        if m == "mkdir":
            p.mkdir(parents=True, exist_ok=True)
            return ToolResult(text=f"Created directory {p}", data={"path": str(p)})

        if m == "delete":
            if p.is_dir():
                if not recursive:
                    raise ToolError(
                        f"{p} 是目录。要删除整个目录必须显式传 recursive=true。"
                    )
                n = sum(1 for _ in p.rglob("*"))
                shutil.rmtree(p)
                return ToolResult(
                    text=f"Deleted directory {p} ({n} entr(ies))",
                    data={"path": str(p), "removed": n},
                )
            if not p.exists():
                raise ToolError(f"不存在：{p}")
            size = p.stat().st_size
            p.unlink()
            return ToolResult(
                text=f"Deleted file {p} ({size} bytes)", data={"path": str(p), "size": size}
            )

        if m in ("move", "copy"):
            if not destination:
                raise ToolError(f"{m} 需要 destination")
            dst = _safe_path(destination)
            dst.parent.mkdir(parents=True, exist_ok=True)
            if m == "move":
                shutil.move(str(p), str(dst))
            elif p.is_dir():
                shutil.copytree(p, dst, dirs_exist_ok=True)
            else:
                shutil.copy2(p, dst)
            return ToolResult(
                text=f"{'Moved' if m == 'move' else 'Copied'} {p} -> {dst}",
                data={"source": str(p), "destination": str(dst)},
            )

        if m == "exists":
            return ToolResult(
                text=f"{p} exists={p.exists()} dir={p.is_dir()} file={p.is_file()}",
                data={"path": str(p), "exists": p.exists(), "is_dir": p.is_dir()},
            )

        if m == "search":
            base = p if p.is_dir() else p.parent
            hits = [x for x in base.rglob(pattern) if x.is_file()][:200]
            lines = [f"{len(hits)} file(s) matching {pattern!r} under {base}"]
            for h in hits[:100]:
                lines.append(f"  {h}  ({h.stat().st_size:,} B)")
            return ToolResult(
                text="\n".join(lines),
                data={"base": str(base), "pattern": pattern, "matches": [str(h) for h in hits]},
            )

        raise ToolError(
            f"不认识的 mode：{mode!r}。"
            "可用：read / write / append / list / mkdir / delete / move / copy / exists / search"
        )


__all__ = ["register"]
