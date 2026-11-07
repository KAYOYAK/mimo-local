"""
和 llama-server 说话：流式聊天 + 文档识别。

只用标准库 urllib，不引入 requests —— 少一个依赖就少一处装不上的可能。
SSE 流式用 urlopen 返回对象逐行读，它是实时的，不会攒完再给。
"""
from __future__ import annotations

import base64
import json
import mimetypes
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable, Iterator

import config


class ApiError(Exception):
    pass


# ---------- 聊天（流式） ----------

def chat_stream(
    port: int,
    messages: list[dict],
    on_delta: Callable[[str], None],
    on_reasoning: Callable[[str], None] | None = None,
    max_tokens: int = -1,
    temperature: float = 0.6,
    timeout: float = 300,
) -> str:
    """发一条聊天请求，边收边回调。返回完整回复文本。

    on_delta(chunk)      —— 正文增量
    on_reasoning(chunk)  —— 思考过程增量（模型有 reasoning 时才来）
    """
    payload = {
        "messages": messages,
        "stream": True,
        "max_tokens": max_tokens,
        "temperature": temperature,
    }
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/v1/chat/completions",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    full = []
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            for raw in resp:
                line = raw.decode("utf-8", "replace").strip()
                if not line or not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                try:
                    obj = json.loads(data)
                except json.JSONDecodeError:
                    continue
                choices = obj.get("choices") or []
                if not choices:
                    continue
                delta = choices[0].get("delta") or {}
                # 思考过程在 reasoning_content 里；正文在 content 里
                rc = delta.get("reasoning_content")
                if rc and on_reasoning:
                    on_reasoning(rc)
                c = delta.get("content")
                if c:
                    full.append(c)
                    on_delta(c)
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode("utf-8", "replace")
        except Exception:
            pass
        # 上下文超限是最常见的失败，给一句人话
        if "exceed" in body.lower() or e.code == 500:
            raise ApiError(
                f"引擎报错（HTTP {e.code}）。多半是上下文超了 —— "
                f"开个新对话，或者把问题/图片弄短一点。\n{body[:300]}"
            ) from e
        raise ApiError(f"引擎报错（HTTP {e.code}）：{body[:300]}") from e
    except urllib.error.URLError as e:
        raise ApiError(f"连不上引擎：{e.reason}") from e

    return "".join(full)


# ---------- 文档识别（非流式，一次性出结果） ----------

_MIME = {
    ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".png": "image/png", ".webp": "image/webp",
    ".bmp": "image/bmp", ".gif": "image/gif",
    ".tif": "image/tiff", ".tiff": "image/tiff",
}


def _mime_of(path: Path) -> str:
    return _MIME.get(path.suffix.lower()) or "image/jpeg"


def ocr_image(
    port: int,
    image_path: Path,
    task: str = "doc",
    timeout: float = 300,
) -> str:
    """把一张图转成 Markdown / OTSL / LaTeX / JSON。返回文本。"""
    prompt = config.OCR_PROMPTS.get(task, config.OCR_PROMPTS["doc"])
    b64 = base64.b64encode(image_path.read_bytes()).decode("ascii")
    payload = {
        "messages": [{
            "role": "user",
            "content": [
                {"type": "image_url",
                 "image_url": {"url": f"data:{_mime_of(image_path)};base64,{b64}"}},
                {"type": "text", "text": prompt},
            ],
        }],
        "max_tokens": 4096,
        "stream": False,
    }
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/v1/chat/completions",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            obj = json.loads(resp.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode("utf-8", "replace")
        except Exception:
            pass
        if "exceed" in body.lower():
            raise ApiError("图太大或内容太长，超出上下文。把图片压小一点再试。") from e
        raise ApiError(f"识别失败（HTTP {e.code}）：{body[:300]}") from e
    except urllib.error.URLError as e:
        raise ApiError(f"连不上 OCR 引擎：{e.reason}") from e

    choices = obj.get("choices") or []
    if not choices:
        raise ApiError("引擎没返回内容")
    text = choices[0].get("message", {}).get("content", "") or ""
    return _strip_fence(text)


def _strip_fence(text: str) -> str:
    """模型有时把整段包在 ```markdown 里，剥掉外层，否则导出会多一层。"""
    t = text.strip()
    if t.startswith("```"):
        lines = t.splitlines()
        if len(lines) >= 2 and lines[0].startswith("```"):
            # 去掉第一行和最后一个 ```
            body = lines[1:]
            if body and body[-1].strip().startswith("```"):
                body = body[:-1]
            return "\n".join(body).strip()
    return t


# ---------- 引擎信息 ----------

def get_props(port: int, timeout: float = 3) -> dict:
    """拿引擎的 /props（模型路径、上下文大小等）。"""
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/props", timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8", "replace"))
    except Exception as e:
        raise ApiError(f"读不到引擎信息：{e}") from e


def get_context_size(port: int) -> int:
    """引擎实际的上下文长度。拿不到就给个默认值。"""
    try:
        props = get_props(port)
        n = props.get("default_generation_settings", {}).get("n_ctx")
        return int(n) if n else 32768
    except Exception:
        return 32768
