"""
渲染决策：一个 part 要不要显示、显示成什么。

为什么单独抽出来：
    「模型正文不显示」这个 bug 曾经真实发生过——`_on_part` 里多了一个
    无条件的 return，所有 text part 在创建文本块之前就被丢掉了。
    而当时的自检和声明断言全在查「某个字符串在不在源码里」，
    没有一条和「用户能不能看见回答」有交集。

    把决策逻辑抽成不依赖 Qt 的纯函数之后，自检可以直接拿假 part 喂它：
    不需要 GPU、不需要起引擎、不需要窗口，就能验证「正文会被渲染」。

约定：返回 (Step | None, 原因)。None 表示不显示，原因用于自检报错。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class Step:
    kind: str          # "text" | "reasoning" | "tool"
    text: str = ""
    tool: str = ""
    status: str = ""
    args: str = ""


# 这些 part 类型不显示（只是分隔/元数据）
INVISIBLE = {"step-start", "step-finish", "snapshot", "patch"}

MAX_TEXT = 4000        # 超长注入文本的截断阈值

# 我们发给模型的 payload 的特征标记。
# 9B 有时会把收到的 user message 原样复述一遍当成自己的回答，
# 那些文本会带着这些结构标记 —— 它们不是回答，不能显示。
PAYLOAD_MARKS = ("【用户的问题】", "【用户发的图片：", "【用户的情况】",
                 "你无法直接查看图片本身", "这是**图片里的内容**")


def looks_like_payload(text: str) -> bool:
    """这段文字是不是「模型把我们的 payload 复述了一遍」。

    判据：同时出现 **两处以上** payload 结构标记。
    完整 payload 一定带「【用户的问题】」+「【用户发的图片：」+
    「你无法直接查看图片本身」，命中 3 个。
    而真实回答即使提到「图片里的内容」，也只命中 1 个 —— 不能误杀。
    """
    if not text:
        return False
    hits = sum(1 for mk in PAYLOAD_MARKS if mk in text)
    return hits >= 2


# ---------- Markdown → HTML ----------
# 模型是编程助手，回答里一定有 **粗体**、`行内代码`、```代码块```、
# 列表。QTextBrowser 只认 HTML，所以要把这些转过去。
# 只做最常用的几种，不追求完整 Markdown 规范。

def md_to_html(text: str) -> str:
    """把模型回答里的常见 Markdown 转成 QTextBrowser 能认的 HTML。

    调用前调用方应该已经做过 html.escape —— 所以这里处理的是
    转义后的文本（&lt; &gt; &amp; 已就位），只认 Markdown 标记。
    """
    import re

    if not text:
        return ""

    # 1. 先把代码块（```...```）摘出来占位，免得里面的 ** ` # 被误处理
    fences: list[str] = []

    def _stash(m: re.Match) -> str:
        code = m.group(1).rstrip("\n")
        escaped = code.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        block = (f'<table width="100%" cellpadding="8" cellspacing="0" border="0"'
                 f' style="margin:8px 0;"><tr><td bgcolor="#141414">'
                 f'<pre style="color:#d4d4d8;font-family:Consolas,monospace;'
                 f'font-size:12px;margin:0;line-height:1.5;">{escaped}</pre>'
                 f'</td></tr></table>')
        fences.append(block)
        return f"\x00FENCE{len(fences) - 1}\x00"

    text = re.sub(r"```[^\n]*\n(.*?)```", _stash, text, flags=re.S)

    # 2. 行内代码 `xxx`
    def _icode(m: re.Match) -> str:
        inner = m.group(1)
        return (f'<span style="background:#2b2b2b;color:#ff9b7f;'
                f'font-family:Consolas,monospace;font-size:12.5px;'
                f'padding:1px 4px;">{inner}</span>')

    text = re.sub(r"`([^`\n]+)`", _icode, text)

    # 3. 粗体 **xxx**（先做，避免和斜体抢）
    text = re.sub(r"\*\*([^*\n]+)\*\*", r"<b>\1</b>", text)
    text = re.sub(r"__([^_\n]+)__", r"<b>\1</b>", text)

    # 4. 标题 # / ## / ### → 粗体大字
    def _head(m: re.Match) -> str:
        level = len(m.group(1))
        size = {1: 17, 2: 15, 3: 14}.get(level, 13)
        return (f'<div style="font-size:{size}px;font-weight:600;'
                f'margin:10px 0 4px 0;">{m.group(2)}</div>')

    text = re.sub(r"^(#{1,4})\s+(.+)$", _head, text, flags=re.M)

    # 5. 无序列表 - / * / •
    def _ul(m: re.Match) -> str:
        return f'<div style="margin:2px 0 2px 14px;">• {m.group(1)}</div>'

    text = re.sub(r"^\s*[-*•]\s+(.+)$", _ul, text, flags=re.M)

    # 6. 有序列表 1. 2.
    def _ol(m: re.Match) -> str:
        return f'<div style="margin:2px 0 2px 14px;">{m.group(1)}. {m.group(2)}</div>'

    text = re.sub(r"^\s*(\d+)[.、]\s+(.+)$", _ol, text, flags=re.M)

    # 7. 引用 > xxx（注意：调用方已 escape，行首是 &gt; 不是 >）
    def _quote(m: re.Match) -> str:
        return (f'<div style="margin:4px 0;padding-left:10px;'
                f'border-left:2px solid #3a3a3a;color:#a1a1aa;">{m.group(1)}</div>')

    text = re.sub(r"^\s*(?:&gt;|>)\s?(.*)$", _quote, text, flags=re.M)

    # 8. 换行 → <br>
    text = text.replace("\n", "<br>")

    # 9. 代码块占位换回来
    for i, block in enumerate(fences):
        text = text.replace(f"\x00FENCE{i}\x00", block)

    return text


def decide(part: dict[str, Any], echoes: dict[str, int]) -> tuple[Step | None, str]:
    """决定一个 part 要不要渲染。

    echoes：用户发过的原文 → 剩余可吞次数（Counter 语义，命中一次减一次）。
    这个函数**会修改 echoes**（命中 echo 时减一），调用方传同一个 dict 即可。
    """
    if not isinstance(part, dict):
        return None, "不是 dict"

    ptype = part.get("type") or ""
    if ptype in INVISIBLE:
        return None, f"类型 {ptype} 不显示"

    # 系统注入的隐藏文本（memory 路径、instructions 副本等）
    if part.get("synthetic"):
        return None, "synthetic（系统注入）"

    if ptype == "text":
        txt = (part.get("text") or "").strip()
        if not txt:
            return None, "空文本"
        # 用户消息的 echo —— 我们已经自己显示过一遍了。
        #
        # ⚠ 这里的匹配范围必须严格：
        #   精确相等                      → 是用户消息的 echo，吞掉
        #   key 是「完整 payload」时      → 允许包含匹配（带图片时 payload
        #     比界面显示的「（看图）」长，事件里回显的是完整 payload）
        #
        # 绝不能无条件用 txt.startswith(key)：
        #   用户说「你好」、模型回答「你好！有什么可以帮你」→ 回答会被吞掉。
        #   这是真实事故：新对话第一句的回答完全不显示。
        for key, n in list(echoes.items()):
            if n <= 0 or not key:
                continue
            if txt == key:
                echoes[key] -= 1
                return None, "echo（用户已显示）"
            if ("【用户的问题】" in key) or ("【用户发的图片：" in key):
                if txt.startswith(key) or key.startswith(txt):
                    echoes[key] -= 1
                    return None, "echo（用户已显示）"
        # 模型把 payload 复述了一遍 —— 不是回答
        if looks_like_payload(txt):
            return None, "复述了发给模型的 payload（不是回答）"
        if len(txt) > MAX_TEXT:
            txt = txt[:MAX_TEXT] + "\n…（内容过长，已截断）"
        return Step("text", txt), "正文"

    if ptype == "reasoning":
        txt = (part.get("text") or "").strip()
        if not txt:
            return None, "空 reasoning"
        return Step("reasoning", txt), "思考过程"

    if ptype == "tool":
        st = part.get("state") or {}
        inp = st.get("input")
        args = ""
        if inp:
            try:
                import json
                args = json.dumps(inp, ensure_ascii=False)
            except Exception:
                args = str(inp)
            if len(args) > 220:
                args = args[:220] + "…"
        return Step("tool", tool=part.get("tool", "?"),
                    status=st.get("status", ""), args=args), "工具调用"

    return None, f"未知类型 {ptype}"
