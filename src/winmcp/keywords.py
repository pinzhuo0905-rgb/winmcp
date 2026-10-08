"""Chinese/English keyword bridge.

Windows desktop tooling describes itself in English ("Take a screenshot and inspect
the screen") while many users write requests in Chinese ("帮我截个图"). A plain
bag-of-words match between the two has an **empty intersection** — recall fails, the
model concludes it lacks the capability, and it degrades into writing instructions.

This module maps Chinese terms onto the English vocabulary that actually appears in
tool descriptions. It is deliberately a small hand-maintained table: no model call,
no network, no embeddings.

Note on tokenization: the Chinese pattern is greedy, so a whole Chinese phrase becomes
a *single* token. The bridge therefore matches by **substring**, not equality —
otherwise single-character shorthands like 装 (install) would never fire.
"""

from __future__ import annotations

import re

#: Chinese term -> English words that appear in tool descriptions.
CN_EN_BRIDGE: dict[str, tuple[str, ...]] = {
    # observation
    "截图": ("screenshot", "screen", "capture"),
    "屏幕": ("screen", "display", "desktop"),
    "桌面": ("desktop", "screen"),
    "界面": ("ui", "screen", "window"),
    "看看": ("look", "inspect", "see"),
    "观察": ("observe", "inspect"),
    "显示器": ("display", "monitor"),
    "分辨率": ("display", "dpi", "resolution"),
    # mouse
    "点击": ("click", "mouse"),
    "单击": ("click", "mouse"),
    "双击": ("click", "double"),
    "右键": ("right", "click", "context"),
    "鼠标": ("mouse", "cursor"),
    "移动": ("move", "cursor"),
    "拖拽": ("drag", "move"),
    "滚动": ("scroll", "wheel"),
    "选中": ("select", "click"),
    # keyboard
    "输入": ("type", "text", "input"),
    "打字": ("type", "text"),
    "文字": ("text", "type"),
    "文本": ("text", "type"),
    "键盘": ("keyboard", "key", "shortcut"),
    "快捷键": ("shortcut", "key", "combination"),
    # apps and windows
    "打开": ("open", "launch", "start", "app"),
    "启动": ("launch", "start", "open"),
    "运行": ("run", "launch", "start"),
    "关闭": ("close", "kill", "terminate"),
    "程序": ("app", "application", "program", "process"),
    "应用": ("app", "application"),
    "软件": ("app", "application", "program"),
    "窗口": ("window", "app"),
    "焦点": ("focus", "foreground"),
    "进程": ("process", "task", "pid"),
    "结束": ("kill", "terminate", "stop"),
    # files
    "文件": ("file", "filesystem", "path"),
    "文件夹": ("folder", "directory", "filesystem"),
    "目录": ("directory", "folder", "filesystem"),
    "读取": ("read", "file"),
    "写入": ("write", "file"),
    "重命名": ("rename", "file"),
    "复制": ("copy", "file"),
    "删除": ("delete", "remove", "file"),
    "创建": ("create", "new", "file"),
    "新建": ("create", "new"),
    "搜索": ("search", "find"),
    # clipboard
    "剪贴板": ("clipboard", "copy", "paste"),
    "粘贴": ("paste", "clipboard"),
    "剪切": ("cut", "clipboard"),
    # web
    "网页": ("web", "page", "url", "scrape"),
    "抓取": ("scrape", "fetch", "extract"),
    "链接": ("url", "link", "web"),
    # waiting
    "等待": ("wait", "pause", "delay"),
    "直到": ("wait", "until", "condition"),
    # notification
    "通知": ("notification", "toast", "notify"),
    "提示": ("notification", "notify", "toast"),
    # generic tasks
    "整理": ("organize", "move", "file", "sort"),
    "清理": ("clean", "delete", "organize"),
    "安装": ("install", "setup"),
    "配置": ("configure", "setup", "set"),
    "检查": ("check", "inspect", "test"),
    "修复": ("fix", "repair"),
    "重启": ("restart", "start"),
    "保存": ("save", "write"),
}

#: Single-character shorthands. Spoken Chinese uses them constantly
#: ("装一下" = "安装一下"). Omitting them makes such requests look impossible.
#: Over-matching here is the safer failure: a few extra tokens versus the model
#: believing it has no capability and writing a tutorial instead.
CN_EN_SHORTHAND: dict[str, tuple[str, ...]] = {
    "装": ("install", "setup", "app", "launch"),
    "删": ("delete", "remove", "file"),
    "建": ("create", "new", "file"),
    "查": ("search", "find", "check", "inspect"),
    "搜": ("search", "find"),
    "开": ("open", "launch", "start"),
    "关": ("close", "kill", "terminate"),
    "存": ("save", "write", "file"),
    "改": ("rename", "modify", "edit", "file"),
    "移": ("move", "file"),
    "拷": ("copy", "file"),
    "贴": ("paste", "clipboard"),
    "剪": ("cut", "clipboard"),
    "点": ("click", "mouse"),
    "打": ("type", "keyboard", "open"),
    "滚": ("scroll", "wheel"),
    "等": ("wait", "until", "condition"),
    "看": ("look", "inspect", "screenshot", "screen"),
    "读": ("read", "file"),
    "写": ("write", "type", "file"),
    "修": ("fix", "repair"),
}

_EN_NORMALIZE: dict[str, str] = {
    "screenshots": "screenshot", "clicks": "click", "clicking": "click",
    "typing": "type", "types": "type", "scrolling": "scroll",
    "applications": "application", "apps": "app", "files": "file",
    "folders": "folder", "windows": "window", "processes": "process",
    "opens": "open", "launches": "launch", "starts": "start", "waits": "wait",
    "moves": "move", "captures": "capture",
}

_TOKEN_RE = re.compile(r"[a-z]{2,}|[\u4e00-\u9fff]{1,}")


def tokenize(text: str) -> set[str]:
    """Split text into tokens. Chinese runs stay whole; English splits on words."""
    return set(_TOKEN_RE.findall((text or "").lower()))


def expand(tokens: set[str]) -> set[str]:
    """Expand Chinese tokens into their English equivalents and normalise variants."""
    out: set[str] = set()
    for t in tokens:
        out.add(t)
        if t in _EN_NORMALIZE:
            out.add(_EN_NORMALIZE[t])
        for cn, ens in CN_EN_BRIDGE.items():
            if cn in t:
                out.update(ens)
        for cn, ens in CN_EN_SHORTHAND.items():
            if cn in t:
                out.update(ens)
    return out


def expanded_tokens(text: str) -> set[str]:
    """``tokenize`` followed by ``expand``."""
    return expand(tokenize(text))


__all__ = [
    "CN_EN_BRIDGE",
    "CN_EN_SHORTHAND",
    "expand",
    "expanded_tokens",
    "tokenize",
]
