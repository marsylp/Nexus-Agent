"""终端 UI 工具 — 统一的排版、颜色和格式化

所有命令模块和 Agent 引擎的终端输出都通过本模块格式化，
确保风格一致。支持 NO_COLOR 环境变量和非交互终端降级。

主要组件：
- ANSI 颜色函数: bold/dim/red/green/yellow/blue/cyan/gray
- 语义化输出: success/error/warn/info/hint/section/item
- 表格: kv_table（键值对对齐）/ cmd_table（命令帮助）
- 可视化: progress_bar / box / separator / banner
- 动画: Spinner（loading 旋转动画，支持 with 语法）
"""
from __future__ import annotations
import os, shutil

# ── ANSI 颜色 ───────────────────────────────────────────────
# 遵循 NO_COLOR 规范（https://no-color.org/）
# 非 TTY 终端（如管道、CI）自动禁用颜色

_NO_COLOR = os.environ.get("NO_COLOR", "") != "" or not os.isatty(1)


def _c(code: str, text: str) -> str:
    """应用 ANSI 颜色码，NO_COLOR 环境下返回原文"""
    if _NO_COLOR:
        return text
    return f"\033[{code}m{text}\033[0m"


# 基础色
def bold(t: str) -> str: return _c("1", t)
def dim(t: str) -> str: return _c("2", t)
def italic(t: str) -> str: return _c("3", t)
def red(t: str) -> str: return _c("31", t)
def green(t: str) -> str: return _c("32", t)
def yellow(t: str) -> str: return _c("33", t)
def blue(t: str) -> str: return _c("34", t)
def magenta(t: str) -> str: return _c("35", t)
def cyan(t: str) -> str: return _c("36", t)
def white(t: str) -> str: return _c("37", t)
def gray(t: str) -> str: return _c("90", t)

# 背景色
def bg_green(t: str) -> str: return _c("42;30", t)
def bg_red(t: str) -> str: return _c("41;37", t)
def bg_yellow(t: str) -> str: return _c("43;30", t)
def bg_blue(t: str) -> str: return _c("44;37", t)
def bg_cyan(t: str) -> str: return _c("46;30", t)


# ── 终端宽度 ────────────────────────────────────────────────

def term_width() -> int:
    try:
        return shutil.get_terminal_size().columns
    except Exception:
        return 80


# ── Box 绘制 ────────────────────────────────────────────────

# 圆角 Box 字符
_TL, _TR, _BL, _BR = "╭", "╮", "╰", "╯"
_H, _V = "─", "│"
_SEP_L, _SEP_R = "├", "┤"


def box(title: str, lines: list[str], width: int = 0, color_fn=cyan) -> str:
    """绘制带标题的圆角 Box"""
    w = width or min(term_width() - 2, 60)
    inner = w - 4  # 两侧 │ + 空格

    out = []
    # 顶边 + 标题
    title_display = f" {title} "
    pad = w - 2 - len(title_display)
    top = color_fn(f"{_TL}{_H}{title_display}{_H * max(pad, 0)}{_TR}")
    out.append(top)

    # 内容行
    for line in lines:
        # 截断过长行
        display = line[:inner]
        padding = inner - _visible_len(display)
        out.append(f"{color_fn(_V)} {display}{' ' * max(padding, 0)} {color_fn(_V)}")

    # 底边
    out.append(color_fn(f"{_BL}{_H * (w - 2)}{_BR}"))
    return "\n".join(out)


def separator(char: str = "─", width: int = 0, color_fn=gray) -> str:
    w = width or min(term_width() - 2, 60)
    return color_fn(char * w)


def _visible_len(s: str) -> int:
    """计算字符串的可见长度（排除 ANSI 转义码）"""
    import re
    return len(re.sub(r'\033\[[0-9;]*m', '', s))


# ── 格式化输出函数 ──────────────────────────────────────────

def banner(title: str, version: str = ""):
    """启动 Banner"""
    w = min(term_width() - 2, 60)
    border_h = "═" * (w - 4)
    empty_line = " " * (w - 4)
    lines = [
        "",
        bold(cyan("  ╔{}╗".format(border_h))),
        bold(cyan("  ║{}║".format(empty_line))),
    ]
    display = "◈ {}".format(title)
    if version:
        display += "  " + dim("v{}".format(version))
    pad = w - 4 - _visible_len(display)
    lines.append(bold(cyan("  ║ {}{} ║".format(display, " " * max(pad - 2, 0)))))
    lines.append(bold(cyan("  ║{}║".format(empty_line))))
    lines.append(bold(cyan("  ╚{}╝".format(border_h))))
    lines.append("")
    print("\n".join(lines))


def section(title: str):
    """段落标题"""
    print(f"\n  {bold(title)}")


def item(label: str, value: str = "", icon: str = "•"):
    """列表项"""
    if value:
        print(f"    {icon} {label}: {value}")
    else:
        print(f"    {icon} {label}")


def success(msg: str):
    print(f"    {green('✓')} {msg}")


def error(msg: str):
    print(f"    {red('✗')} {msg}")


def warn(msg: str):
    print(f"    {yellow('!')} {msg}")


def info(msg: str):
    print(f"    {blue('·')} {msg}")


def hint(msg: str):
    """提示信息（灰色）"""
    print(f"\n    {gray(msg)}")


def kv_table(rows: list[tuple[str, str]], indent: int = 4):
    """键值对表格，自动对齐"""
    if not rows:
        return
    max_key = max((_visible_len(k) for k, _ in rows), default=0)
    pad = " " * indent
    for key, val in rows:
        k_pad = max_key - _visible_len(key)
        print(f"{pad}{dim(key)}{' ' * k_pad}  {val}")


def cmd_table(rows: list[tuple[str, str]], indent: int = 4):
    """命令帮助表格"""
    if not rows:
        return
    max_cmd = max((_visible_len(c) for c, _ in rows), default=0)
    pad = " " * indent
    for cmd, desc in rows:
        c_pad = max_cmd - _visible_len(cmd)
        print(f"{pad}{cyan(cmd)}{' ' * c_pad}  {gray(desc)}")


def progress_bar(current: int, total: int, width: int = 20) -> str:
    """进度条"""
    if total == 0:
        return gray("░" * width)
    filled = int(width * current / total)
    pct = current / total * 100
    bar = green("█" * filled) + gray("░" * (width - filled))
    return f"{bar} {pct:.0f}%"


# ── Spinner 动画 ────────────────────────────────────────────

import sys, threading, time as _time


class Spinner:
    """终端 loading 动画

    用法:
        with Spinner("思考中"):
            do_something_slow()

        # 或手动控制
        sp = Spinner("连接中")
        sp.start()
        ...
        sp.stop("完成")
    """

    _FRAMES = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]
    _INTERVAL = 0.08

    def __init__(self, message: str = "处理中", indent: int = 4):
        self._message = message
        self._indent = indent
        self._running = False
        self._thread: threading.Thread | None = None
        self._frame_idx = 0
        self._start_time = 0.0

    def start(self):
        if _NO_COLOR or not sys.stderr.isatty():
            # 非交互终端，只打印静态文本
            sys.stderr.write("{}{} {} ...\n".format(
                " " * self._indent, gray("⏳"), self._message))
            sys.stderr.flush()
            return
        self._running = True
        self._start_time = _time.time()
        self._thread = threading.Thread(target=self._spin, daemon=True)
        self._thread.start()

    def stop(self, final_message: str = ""):
        self._running = False
        if self._thread:
            self._thread.join(timeout=1)
            self._thread = None
        if _NO_COLOR or not sys.stderr.isatty():
            if final_message:
                sys.stderr.write("{}  {}\n".format(" " * self._indent, final_message))
                sys.stderr.flush()
            return
        # 清除 spinner 行
        sys.stderr.write("\r\033[K")
        if final_message:
            elapsed = _time.time() - self._start_time
            sys.stderr.write("{}{} {}\n".format(
                " " * self._indent, final_message, gray("({:.1f}s)".format(elapsed))))
        sys.stderr.flush()

    def update(self, message: str):
        """更新 spinner 消息（不停止动画）"""
        self._message = message

    def _spin(self):
        pad = " " * self._indent
        while self._running:
            frame = cyan(self._FRAMES[self._frame_idx % len(self._FRAMES)])
            elapsed = _time.time() - self._start_time
            time_str = gray("({:.0f}s)".format(elapsed)) if elapsed > 2 else ""
            line = "{}{} {} {}".format(pad, frame, self._message, time_str)
            sys.stderr.write("\r\033[K" + line)
            sys.stderr.flush()
            self._frame_idx += 1
            _time.sleep(self._INTERVAL)

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, *args):
        self.stop()
