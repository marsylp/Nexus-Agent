"""终端 Markdown 渲染器测试"""
import os
import pytest

# 测试时禁用颜色，方便断言
os.environ["NO_COLOR"] = "1"

from agent_core.md_render import TerminalMarkdownRenderer


class TestRenderHeadings:
    def test_h1(self):
        md = TerminalMarkdownRenderer()
        result = md.render("# 标题一")
        assert "█ 标题一" in result

    def test_h2(self):
        md = TerminalMarkdownRenderer()
        result = md.render("## 标题二")
        assert "▎ 标题二" in result

    def test_h3(self):
        md = TerminalMarkdownRenderer()
        result = md.render("### 标题三")
        assert "标题三" in result


class TestRenderCodeBlocks:
    def test_code_block_with_lang(self):
        md = TerminalMarkdownRenderer()
        text = "```java\nHandler handler = new Handler();\n```"
        result = md.render(text)
        assert "java" in result
        assert "Handler handler" in result
        assert "┌─" in result
        assert "└─" in result

    def test_code_block_without_lang(self):
        md = TerminalMarkdownRenderer()
        text = "```\nsome code\n```"
        result = md.render(text)
        assert "code" in result
        assert "some code" in result

    def test_inline_code(self):
        md = TerminalMarkdownRenderer()
        result = md.render("使用 `Handler` 类")
        assert "Handler" in result


class TestRenderLists:
    def test_unordered_list(self):
        md = TerminalMarkdownRenderer()
        result = md.render("- 第一项\n- 第二项")
        assert "•" in result
        assert "第一项" in result
        assert "第二项" in result

    def test_ordered_list(self):
        md = TerminalMarkdownRenderer()
        result = md.render("1. 第一步\n2. 第二步")
        assert "1." in result
        assert "2." in result

    def test_nested_list(self):
        md = TerminalMarkdownRenderer()
        result = md.render("- 外层\n  - 内层")
        assert "外层" in result
        assert "内层" in result


class TestRenderInline:
    def test_bold(self):
        md = TerminalMarkdownRenderer()
        result = md.render("这是 **加粗** 文本")
        assert "加粗" in result

    def test_separator(self):
        md = TerminalMarkdownRenderer()
        result = md.render("---")
        assert "─" in result

    def test_blockquote(self):
        md = TerminalMarkdownRenderer()
        result = md.render("> 引用内容")
        assert "引用内容" in result
        assert "▏" in result


class TestRenderTable:
    def test_simple_table(self):
        md = TerminalMarkdownRenderer()
        text = "| 名称 | 说明 |\n|------|------|\n| A | 描述A |"
        result = md.render(text)
        assert "名称" in result
        assert "描述A" in result


class TestStreamRendering:
    def test_stream_basic(self):
        md = TerminalMarkdownRenderer()
        # 模拟流式输入
        out = ""
        out += md.feed("# 标题\n")
        out += md.feed("正文内容\n")
        out += md.flush()
        assert "█ 标题" in out
        assert "正文内容" in out

    def test_stream_code_block(self):
        md = TerminalMarkdownRenderer()
        out = ""
        out += md.feed("```java\n")
        out += md.feed("int x = 1;\n")
        out += md.feed("```\n")
        out += md.flush()
        assert "java" in out
        assert "int x = 1;" in out
        assert "┌─" in out
        assert "└─" in out

    def test_stream_partial_line(self):
        """chunk 不以换行结尾时，应缓冲等待"""
        md = TerminalMarkdownRenderer()
        out1 = md.feed("# 标")  # 不完整的行
        assert out1 == ""  # 应该缓冲
        out2 = md.feed("题\n")  # 补全换行
        assert "█ 标题" in out2

    def test_stream_reset(self):
        md = TerminalMarkdownRenderer()
        md.feed("```java\n")
        assert md._in_code_block
        md.reset()
        assert not md._in_code_block
        assert md._line_buffer == ""


class TestRenderFullExample:
    """测试完整的 Handler 机制回答渲染"""

    def test_handler_answer(self):
        md = TerminalMarkdownRenderer()
        text = """## Handler 机制

Android 的 Handler 机制用于线程间通信。

### 核心组件

- **Handler** — 发送和处理消息
- **Looper** — 循环读取 MessageQueue
- **MessageQueue** — 消息队列

### 示例代码

```java
Handler handler = new Handler(Looper.getMainLooper());
handler.post(() -> {
    textView.setText("更新 UI");
});
```

> 注意：不要在子线程直接更新 UI。"""

        result = md.render(text)
        # 标题渲染
        assert "▎ Handler 机制" in result
        assert "核心组件" in result
        # 列表渲染
        assert "•" in result
        assert "Handler" in result
        # 代码块渲染
        assert "java" in result
        assert "┌─" in result
        assert "└─" in result
        assert "Looper.getMainLooper()" in result
        # 引用渲染
        assert "▏" in result
