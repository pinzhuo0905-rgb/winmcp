# winmcp

> **The full Windows-MCP capability surface, built in.**
> Native desktop control in-process — no separate server, no JSON-RPC on the hot path,
> no third-party runtime dependencies.

[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](pyproject.toml)
[![Platform](https://img.shields.io/badge/platform-Windows%2010%20%2F%2011-0078D4.svg)](#requirements)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Tests](https://img.shields.io/badge/tests-197%20passing-brightgreen.svg)](#testing)

---

## What it is

`winmcp` implements every capability of
[Windows-MCP](https://github.com/CursorTouch/Windows-MCP) — all 18 tools — **directly in
your process**, using `ctypes` against the Win32 API and the Python standard library.

It is not a wrapper. There is no server to install, no subprocess to manage, no protocol
handshake on the hot path. A tool call is a function call.

It also ships an **MCP server mode**, so existing MCP clients (Claude Desktop, Cursor,
VS Code) can use it as a drop-in replacement for a separate desktop-control server.

### Core purpose

Give an agent reliable, low-latency control of a Windows desktop — and stop it from
degrading into handing the user a list of steps instead of doing the work.

### Target platform

| | |
|---|---|
| **OS** | Windows 10 / Windows 11 (x64). Windows 7/8.1 should work — the API surface used is old and stable — but is untested. |
| **Python** | 3.10, 3.11, 3.12, 3.13 |
| **Architecture** | 64-bit only |

---

## Capability coverage

All 18 tools from Windows-MCP 4.0.11, implemented natively:

| Category | Tools |
|---|---|
| **Observe** | `Screenshot`, `Snapshot`, `DisplayInventory` |
| **Input** | `Click`, `Type`, `Scroll`, `Move`, `Shortcut`, `MultiSelect`, `MultiEdit` |
| **System** | `App`, `Process`, `Clipboard`, `Notification`, `Wait`, `WaitFor` |
| **Files** | `FileSystem` |
| **Web** | `Scrape` |

Beyond a straight port, three things were done differently:

- **Semantic targeting.** Every input tool accepts `label` as well as `loc`. `label`
  resolves against the Win32 control tree, so `Click(label="Save")` survives a window
  being moved or resized — `Click(loc=[420, 318])` does not.
- **PNG screenshots.** GDI returns raw pixels; `winmcp` encodes PNG in pure Python with
  `zlib`. The same 1366×768 frame is **146 KB instead of 4 MB**.
- **Concise tool descriptions.** The full schema set is **3,353 tokens** rather than
  6,333 — descriptions are part of the prompt on every turn, so their length is a
  permanent tax.

### Known limitation

The element tree comes from `EnumChildWindows`, not UIAutomation. Classic Win32 controls
(`Button`, `Edit`, `SysListView32`, and modern names like `NotepadTextBox`,
`RichEditD2DPT`) are enumerated correctly. Applications that render their entire UI onto
a single surface — some Electron and WinUI apps — expose little or nothing, and
`Snapshot` says so explicitly, telling the caller to fall back to coordinates.

UIAutomation would close this gap but requires either a third-party package or hand-written
COM vtable calls, both of which conflict with the zero-dependency goal. The trade-off is
deliberate and documented rather than hidden.

---

## Requirements

**Runtime: none.** `winmcp` imports only the standard library (`ctypes`, `zlib`, `json`,
`subprocess`, `urllib`, `html.parser`, `threading`, `struct`).

**Development:** `pytest` and `ruff` are optional. The test suite has its own runner and
works without pytest installed.

**Optional:** Pillow is *not* required — PNG encoding is implemented from scratch.

---

## Performance

Measured on this machine (Windows 11, Python 3.13, 1366×768 single display) with
`winmcp bench`:

### Startup

| | winmcp | A separate MCP server process |
|---|---:|---:|
| Ready to serve | **62 ms** | ~3,800 ms cold start |
| Per-call process boundary | none | stdio IPC + JSON-RPC |
| `tools/list` round trip | **in memory** | ~400 ms |

The 62 ms figure covers importing the package, building the registry, and constructing
the agent. There is no cold-start penalty because there is no second process.

### Operation latency

| Operation | Time |
|---|---:|
| Screen capture (GDI `BitBlt`, 1366×768) | 20.4 ms |
| PNG encode (pure Python, 146 KB output) | 27.3 ms |
| Full `Screenshot` tool (capture + encode) | 44 ms |
| Enumerate windows | 0.9 ms |
| Enumerate elements in a window | 0.1 ms |
| Enumerate processes (292 running) | 5.9 ms |
| Clipboard read | < 0.1 ms |
| `DisplayInventory` dispatch | 0.6 ms |
| `Snapshot` without image | 0.1 ms |

### Resource usage

| | |
|---|---|
| Resident memory (imported, idle) | ~18 MB |
| Background processes | 0 |
| Threads | 0 until a notification or async read is used |
| Disk footprint | ~180 KB of source |

### Context cost

Tool schemas are re-read by the model on **every turn**.

| | Tokens |
|---|---:|
| All 18 tools | 3,353 |
| Typical recall (2–6 tools) | 226 – 1,313 |
| Reduction | **61% – 93%** |

Eight-turn task, fixed overhead: **26,824 → 1,808–10,504 tokens.**

### Test suite

197 tests in **2.1 s**, zero third-party dependencies.

---

## Install

```bash
git clone https://github.com/pinzhuo0905-rgb/winmcp.git
cd winmcp
pip install -e .
```

No network access is required beyond fetching the source.

Verify:

```bash
winmcp info
winmcp bench
python scripts/run_tests.py
```

---

## Usage

### 1. Python API

```python
from winmcp import Agent, AgentConfig

agent = Agent(AgentConfig(workspace="."))

# Decide whether the user wants this done, or explained
decision = agent.plan("rename the files in this folder")
if decision.should_execute:
    result = agent.call("FileSystem", {"mode": "list", "path": "."})
    print(result.text)
```

Tool calls are plain function calls — no IPC, no serialization:

```python
agent.call("Screenshot")                              # -> PNG bytes in result.image_png
agent.call("Click", {"label": "Save"})                # semantic, not coordinates
agent.call("Type", {"text": "hello", "clear": True})
agent.call("WaitFor", {"condition": "window", "text": "Notepad"})
```

Only expose the tools a task needs:

```python
agent = Agent(AgentConfig(workspace=".", include=("Screenshot", "Click", "Type")))
print(agent.prompt_tokens())   # 667 instead of 3,353
```

Guard against typing into the wrong window:

```python
agent.type_safely("hello", expect_window="Notepad")   # raises if focus is elsewhere
```

### 2. MCP server

```bash
winmcp serve
winmcp serve --tools Screenshot Click Type      # narrow the exposed surface
winmcp serve --exclude-tools FileSystem Process
```

Client configuration:

```json
{
  "mcpServers": {
    "winmcp": { "command": "winmcp", "args": ["serve"] }
  }
}
```

### 3. CLI

```bash
winmcp tools                                    # list tools and their cost
winmcp call DisplayInventory
winmcp call Screenshot --save shot.png
winmcp call Click --arg 'label=Save'
winmcp call FileSystem --arg 'mode=list' --arg 'path=.'
winmcp plan "install this for me"               # run the intent check
winmcp plan "how do I install this" --audit "Step 1: open a terminal"
```

---

## Architecture

```
src/winmcp/
├── win32/            ctypes bindings — no business logic
│   ├── const.py      constants, structs, and function signatures
│   ├── input.py      SendInput: mouse, keyboard, Unicode text
│   ├── screen.py     GDI capture, pure-Python PNG encoder, displays
│   ├── windows.py    enumeration, focus, control tree
│   ├── clipboard.py  retrying clipboard access
│   └── process.py    Toolhelp32 process listing
├── tools/            the 18 tools
│   ├── base.py       Tool protocol, registry, fast dispatcher
│   ├── observe.py    Screenshot / Snapshot / DisplayInventory
│   ├── input.py      Click / Type / Scroll / Move / Shortcut / Multi*
│   ├── system.py     App / Process / Clipboard / Notification / Wait / WaitFor
│   ├── files.py      FileSystem
│   └── web.py        Scrape
├── prec.py           pre-response intent check and draft audit
├── keywords.py       Chinese/English bridge for tool recall
├── guards.py         focus confirmation, tiered verification
├── agent.py          high-level API
├── server.py         MCP stdio server
└── cli.py            command line
```

### The pre-response check

Most agents that *can* operate a computer still answer as if they cannot. Asked to install
something, they describe the installation. `prec.py` runs before a response is generated:

```
1. environment gate   workspace bound? provider available? tools present?
2. intent             does the user want it done, or explained?
3. capability match   can the available tools cover it?
4. execute            do it
```

Plus a **draft audit**: after writing a reply, check whether it hands back steps when
execution was required, and reject it if so.

Ambiguity defaults to **execute**, not explain. The user bound a workspace and enabled
desktop control — that is standing authorization. Acting on a wrong guess costs one
clarification; explaining when the user wanted action costs a wasted turn.

### Why the keyword bridge exists

Tool descriptions are English; many users write Chinese. A plain bag-of-words match has an
**empty intersection**, recall fails, and the model concludes it lacks the capability.
`keywords.py` maps Chinese terms onto the English vocabulary actually used in tool
descriptions. It is a small hand-maintained table — no model call, no embeddings, no network.

### Why tiered verification

```
task success = per-step success ^ steps
95% ^ 8 steps = 66%
95% ^ 3 steps = 86%
```

Verifying every step doubles the turn count; verifying none lets errors compound.
`guards.py` batches low-risk actions and verifies high-risk ones immediately.

---

## Testing

```bash
python scripts/run_tests.py          # 197 tests, ~2 s
python scripts/run_tests.py -k prec  # filter
python scripts/run_tests.py -s 10    # report the slowest tests
pytest -q                            # also works if pytest is installed
```

The runner installs a minimal `pytest` shim, so test files stay in standard pytest style
while requiring nothing to be installed.

Tests that move the mouse or send keystrokes are deliberately excluded — a test suite
must not hijack the machine it runs on.

---

## License

MIT — see [LICENSE](LICENSE).
