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
        # 用户消息的 echo —— 我们已经自己显示过一遍了
        if echoes.get(txt, 0) > 0:
            echoes[txt] -= 1
            return None, "echo（用户已显示）"
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
