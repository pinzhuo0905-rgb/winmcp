"""网页抓取工具：把 URL 变成可读文本。

用标准库 urllib + html.parser——不引入 requests / BeautifulSoup。
对 agent 场景够用：它要的是「页面上说了什么」，不是完整的 DOM 保真。
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from html.parser import HTMLParser

from .base import Registry, Risk, ToolError, ToolResult, obj, prop

_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) winmcp/1.0"
_MAX_CHARS = 20000

_SKIP_TAGS = {"script", "style", "noscript", "svg", "head", "iframe"}
_BLOCK_TAGS = {
    "p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6",
    "section", "article", "header", "footer", "table", "ul", "ol", "pre",
}


class _TextExtractor(HTMLParser):
    """把 HTML 压成纯文本，保留块级元素换行。"""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.title = ""
        self._skip_depth = 0
        self._in_title = False

    def handle_starttag(self, tag: str, attrs: list) -> None:
        if tag in _SKIP_TAGS:
            self._skip_depth += 1
        elif tag == "title":
            self._in_title = True
        elif tag in _BLOCK_TAGS:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIP_TAGS and self._skip_depth > 0:
            self._skip_depth -= 1
        elif tag == "title":
            self._in_title = False
        elif tag in _BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        if self._in_title:
            self.title += data.strip()
            return
        self.parts.append(data)

    def text(self) -> str:
        raw = "".join(self.parts)
        raw = re.sub(r"[ \t\r\f\v]+", " ", raw)
        raw = re.sub(r"\n\s*\n\s*\n+", "\n\n", raw)
        return raw.strip()


def register(reg: Registry) -> None:
    @reg.tool(
        name="Scrape",
        description=(
            "Fetch a web page and return its readable text. Use `query` to only return "
            "lines mentioning a term. This does not run JavaScript, so client-rendered "
            "pages may come back mostly empty."
        ),
        schema=obj(
            {
                "url": prop("string", "Page URL (http/https)"),
                "query": prop("string", "Optional: only keep lines containing this text"),
                "limit": prop("integer", "Max characters to return, default 20000"),
            },
            required=["url"],
        ),
        risk=Risk.LOW,
        category="web",
    )
    def scrape(url: str, query: str = "", limit: int = _MAX_CHARS) -> ToolResult:
        target = str(url).strip()
        if not target.lower().startswith(("http://", "https://")):
            raise ToolError("url 必须以 http:// 或 https:// 开头")

        req = urllib.request.Request(target, headers={"User-Agent": _UA})
        try:
            with urllib.request.urlopen(req, timeout=25) as resp:
                charset = resp.headers.get_content_charset() or "utf-8"
                body = resp.read().decode(charset, errors="replace")
                status = resp.status
                final = resp.url
        except urllib.error.HTTPError as exc:
            raise ToolError(f"HTTP {exc.code}：{exc.reason}") from exc
        except urllib.error.URLError as exc:
            raise ToolError(f"连不上 {target}：{exc.reason}") from exc

        if body.lstrip().startswith(("{", "[")):
            # JSON 接口——直接给原文，不要走 HTML 解析
            text = body
            title = ""
        else:
            parser = _TextExtractor()
            parser.feed(body)
            text = parser.text()
            title = parser.title

        if query:
            q = query.lower()
            kept = [ln for ln in text.splitlines() if q in ln.lower()]
            text = "\n".join(kept)
            note = f"{len(kept)} line(s) matching {query!r}"
        else:
            note = f"{len(text)} chars"

        cap = max(500, int(limit))
        truncated = len(text) > cap
        shown = text[:cap]

        return ToolResult(
            text=(
                f"{final} — HTTP {status}, {note}"
                + (f" (title: {title!r})" if title else "")
                + (f"\n(truncated to {cap} chars)" if truncated else "")
                + f"\n\n{shown}"
            ),
            data={
                "url": final,
                "status": status,
                "title": title,
                "length": len(text),
                "truncated": truncated,
                "text": shown,
            },
        )


def _json_dumps(obj_: object) -> str:
    return json.dumps(obj_, ensure_ascii=False)


__all__ = ["register"]
