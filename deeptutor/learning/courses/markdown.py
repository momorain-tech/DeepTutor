"""课程教材用的"受限 Markdown"：只许写文字排版，不许带任何能执行的东西。

为什么要限制：教材会原样展示给所有学员，也会喂给辅导模型。普通 Markdown 允许
内嵌 HTML（``<script>``、``<iframe onload=...>``），MDX 还允许 import/export 和
``{表达式}``——任何一种混进来，都等于让写课程的人（或改了课程文件的人）在每个
学员的浏览器里跑代码。所以这里白名单式地只放行：标题、段落、列表、引用、表格、
代码块、强调、链接（仅 http(s) 和本页锚点）。

允许的唯一扩展语法：标题末尾的 ``{#anchor}``，用来给知识点和引用定位。
"""

from __future__ import annotations

from dataclasses import dataclass, field
import re

# 标题锚点：## 标题 {#anchor-id}
_HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*(?:\{#([a-z0-9][a-z0-9_-]{0,63})\})?\s*$")
# 任何像 HTML 标签、注释、声明的东西：<a、</a、<!--、<!DOCTYPE、<?xml
_HTML = re.compile(r"<\s*(?:[A-Za-z]|/|!|\?)")
# 自动链接 <https://...> 也是尖括号语法，统一不许用，免得和 HTML 判断搅在一起。
_LINK = re.compile(r"(!?)\[([^\]]*)\]\(([^)\s]*)(?:\s+\"[^\"]*\")?\)")
_MDX_STATEMENT = re.compile(r"^\s*(?:import|export)\s")
_FENCE = re.compile(r"^\s*(```|~~~)")
_ANCHOR_SYNTAX = re.compile(r"\{#[^}]*\}")


@dataclass
class MarkdownCheck:
    anchors: list[str] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.problems


def check_markdown(text: str) -> MarkdownCheck:
    """逐行检查教材，返回其中的锚点和所有违规（带行号）。"""
    result = MarkdownCheck()
    seen: set[str] = set()
    in_fence = False
    for number, line in enumerate(text.splitlines(), start=1):
        if _FENCE.match(line):
            in_fence = not in_fence
            continue
        if in_fence:
            # 代码块里的内容渲染成纯文本，写 <div> 演示也安全。
            continue
        # 行内代码同理，先去掉再检查。
        visible = re.sub(r"`[^`]*`", "", line)
        if _HTML.search(visible):
            result.problems.append(f"第 {number} 行：不允许 HTML 标签或尖括号语法")
        if _MDX_STATEMENT.match(visible):
            result.problems.append(f"第 {number} 行：不允许 MDX 的 import/export")
        heading = _HEADING.match(visible)
        if heading and heading.group(3):
            anchor = heading.group(3)
            if anchor in seen:
                result.problems.append(f"第 {number} 行：锚点 {anchor!r} 重复")
            seen.add(anchor)
            result.anchors.append(anchor)
            # 去掉合法的 "{#anchor}"（start(3) 指向 # 后面，往前退两格是 "{#"）。
            rest = visible[: heading.start(3) - 2]
        else:
            rest = visible
        if _ANCHOR_SYNTAX.search(rest):
            result.problems.append(
                f"第 {number} 行：锚点只能写在标题末尾，且只能用小写字母、数字、- 和 _"
            )
        elif "{" in rest or "}" in rest:
            # MDX 会把 {…} 当成 JavaScript 表达式执行；正文里要写花括号请放进 `行内代码`。
            result.problems.append(f"第 {number} 行：正文不能出现花括号（需要时放进 `行内代码`）")
        for match in _LINK.finditer(visible):
            image, _label, target = match.groups()
            if image:
                result.problems.append(f"第 {number} 行：第一版教材暂不支持图片")
                continue
            if target.startswith("#"):
                continue
            if not target.startswith(("https://", "http://")):
                result.problems.append(f"第 {number} 行：链接只能指向 http(s) 网址或本页锚点（#xxx）")
    if in_fence:
        result.problems.append("代码块没有闭合")
    return result


__all__ = ["MarkdownCheck", "check_markdown"]
