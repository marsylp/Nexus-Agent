"""终端 Markdown 渲染器 — 将 Markdown 文本渲染为带 ANSI 颜色的终端输出

轻量实现，不依赖第三方库。支持：
- 标题（# ## ###）→ 加粗 + 颜色
- 代码块（```lang ... ```）→ 灰色背景 + 语言标签
- 行内代码（`code`）→ 青色高亮
- 列表（- / * / 1.）→ 缩进 + 符号
- 加粗（**text**）→ ANSI 加粗
- 分隔线（---）→ 灰色横线

设计原则：
- 流式友好：支持逐行渲染，也支持完整文本一次性渲染
- 零依赖：只用标准库 + ui.py 的颜色函数
- 降级安全：NO_COLOR 环境下返回原始文本
"""
from __future__ import annotations

import re
import os

_NO_COLOR = os.environ.get("NO_COLOR", "") != "" or not os.isatty(1)


def _c(code: str, text: str) -> str:
    if _NO_COLOR:
        return text
    return f"\033[{code}m{text}\033[0m"


def _bold(t: str) -> str: return _c("1", t)
def _dim(t: str) -> str: return _c("2", t)
def _cyan(t: str) -> str: return _c("36", t)
def _green(t: str) -> str: return _c("32", t)
def _yellow(t: str) -> str: return _c("33", t)
def _magenta(t: str) -> str: return _c("35", t)
def _gray(t: str) -> str: return _c("90", t)
def _white(t: str) -> str: return _c("37", t)
def _bg_gray(t: str) -> str: return _c("100", t) if not _NO_COLOR else t


# 代码块语言 → 颜色标签
_LANG_COLORS = {
    "java": _yellow,
    "kotlin": _magenta,
    "python": _green,
    "javascript": _yellow,
    "typescript": _cyan,
    "bash": _green,
    "shell": _green,
    "sh": _green,
    "sql": _cyan,
    "json": _gray,
    "yaml": _gray,
    "xml": _gray,
    "html": _yellow,
    "css": _cyan,
    "swift": _magenta,
    "go": _cyan,
    "rust": _yellow,
    "c": _gray,
    "cpp": _cyan,
}


class TerminalMarkdownRenderer:
    """终端 Markdown 渲染器

    支持两种模式：
    1. 完整渲染：render(full_text) → 渲染后的完整文本
    2. 流式渲染：feed(chunk) → 逐块输出渲染后的文本
    """

    def __init__(self, indent: int = 2):
        self._indent = " " * indent
        # 流式状态
        self._in_code_block = False
        self._code_lang = ""
        self._line_buffer = ""

    def render(self, text: str) -> str:
        """完整渲染 Markdown 文本"""
        lines = text.split("\n")
        output = []
        in_code = False
        code_lang = ""

        for line in lines:
            # 代码块开始/结束
            if line.strip().startswith("```"):
                if not in_code:
                    code_lang = line.strip()[3:].strip()
                    lang_label = code_lang if code_lang else "code"
                    color_fn = _LANG_COLORS.get(code_lang.lower(), _gray)
                    output.append(self._indent + _gray("┌─ ") + color_fn(lang_label) + _gray(" ─"))
                    in_code = True
                else:
                    output.append(self._indent + _gray("└─"))
                    in_code = False
                    code_lang = ""
                continue

            if in_code:
                # 代码行：灰色 + 缩进
                output.append(self._indent + _gray("│ ") + _dim(line))
                continue

            # 普通行渲染
            output.append(self._render_line(line))

        return "\n".join(output)

    def _render_line(self, line: str) -> str:
        """渲染单行 Markdown"""
        stripped = line.strip()

        # 空行
        if not stripped:
            return ""

        # 分隔线
        if re.match(r'^-{3,}$|^\*{3,}$|^_{3,}$', stripped):
            return self._indent + _gray("─" * 40)

        # 标题
        m = re.match(r'^(#{1,3})\s+(.+)', stripped)
        if m:
            level = len(m.group(1))
            title = m.group(2)
            if level == 1:
                return "\n" + self._indent + _bold(_cyan("█ " + title))
            elif level == 2:
                return "\n" + self._indent + _bold(_green("▎ " + title))
            else:
                return self._indent + _bold("  " + title)

        # 无序列表
        m = re.match(r'^(\s*)[-*]\s+(.+)', line)
        if m:
            indent = m.group(1)
            content = self._render_inline(m.group(2))
            return self._indent + indent + _cyan("•") + " " + content

        # 有序列表
        m = re.match(r'^(\s*)(\d+)\.\s+(.+)', line)
        if m:
            indent = m.group(1)
            num = m.group(2)
            content = self._render_inline(m.group(3))
            return self._indent + indent + _cyan(num + ".") + " " + content

        # 引用
        if stripped.startswith(">"):
            content = self._render_inline(stripped[1:].strip())
            return self._indent + _gray("▏ ") + _dim(content)

        # 表格行（简单处理）
        if "|" in stripped and stripped.startswith("|"):
            # 分隔行跳过
            if re.match(r'^\|[\s\-:|]+\|$', stripped):
                return self._indent + _gray("├" + "─" * 40 + "┤")
            cells = [c.strip() for c in stripped.split("|")[1:-1]]
            rendered = _gray(" │ ").join(self._render_inline(c) for c in cells)
            return self._indent + _gray("│ ") + rendered + _gray(" │")

        # 普通段落
        return self._indent + self._render_inline(stripped)

    def _render_inline(self, text: str) -> str:
        """渲染行内 Markdown 元素"""
        # 加粗
        text = re.sub(r'\*\*(.+?)\*\*', lambda m: _bold(m.group(1)), text)
        # 行内代码
        text = re.sub(r'`([^`]+)`', lambda m: _cyan(m.group(1)), text)
        # 斜体
        text = re.sub(r'(?<!\*)\*([^*]+)\*(?!\*)', lambda m: _dim(m.group(1)), text)
        return text

    # ── 流式渲染 ─────────────────────────────────────────────

    def feed(self, chunk: str) -> str:
        """流式输入一个 chunk，返回可输出的渲染文本

        逻辑：将 chunk 追加到行缓冲区，遇到换行符时渲染完整行。
        未完成的行暂存，等下一个 chunk 补全。
        """
        self._line_buffer += chunk
        output_parts = []

        while "\n" in self._line_buffer:
            line, self._line_buffer = self._line_buffer.split("\n", 1)
            rendered = self._render_stream_line(line)
            output_parts.append(rendered + "\n")

        return "".join(output_parts)

    def flush(self) -> str:
        """刷新缓冲区中剩余的内容"""
        if self._line_buffer:
            rendered = self._render_stream_line(self._line_buffer)
            self._line_buffer = ""
            return rendered
        return ""

    def _render_stream_line(self, line: str) -> str:
        """流式模式下渲染单行"""
        stripped = line.strip()

        # 代码块开始/结束
        if stripped.startswith("```"):
            if not self._in_code_block:
                self._code_lang = stripped[3:].strip()
                lang_label = self._code_lang if self._code_lang else "code"
                color_fn = _LANG_COLORS.get(self._code_lang.lower(), _gray)
                self._in_code_block = True
                return self._indent + _gray("┌─ ") + color_fn(lang_label) + _gray(" ─")
            else:
                self._in_code_block = False
                self._code_lang = ""
                return self._indent + _gray("└─")

        if self._in_code_block:
            return self._indent + _gray("│ ") + _dim(line)

        return self._render_line(line)

    def reset(self):
        """重置流式状态"""
        self._in_code_block = False
        self._code_lang = ""
        self._line_buffer = ""
