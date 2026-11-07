"""
运行期设置：存在外面，不写回源码。

为什么要有这个模块（两条来自实际踩坑）：

1. **不能把运行期状态写回源码。** 之前「切换模型」的实现是读 `config.py`
   → 正则替换 → 写回。这在打包成 exe 之后必然失败（`config.py` 进了 exe，
   磁盘上根本没有），而且源码模式下中途被杀会留下半截文件，程序再也起不来。

2. **写文件要原子。** 先写临时文件再 `os.replace()`。Windows 上 `os.replace`
   对同分区的文件是原子的；直接 `write_text` 被打断就是半个 JSON。

存放位置：
    源码运行  → <项目根>/settings.json
    打包运行  → <exe 目录>/settings.json（通常就是安装目录）
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any


class Settings:
    """一个很小的 JSON 键值存储。值都是可 JSON 序列化的基本类型。"""

    def __init__(self, path: Path):
        self.path = path
        self._data: dict[str, Any] = {}
        self.load()

    # ---------- 读写 ----------

    def load(self) -> None:
        self._data = {}
        if not self.path.is_file():
            return
        try:
            raw = self.path.read_text(encoding="utf-8")
            obj = json.loads(raw)
            if isinstance(obj, dict):
                self._data = obj
        except (json.JSONDecodeError, OSError):
            # 文件坏了就当空的 —— 这比让程序起不来好
            self._data = {}

    def save(self) -> None:
        """原子写入：临时文件 → fsync → os.replace。"""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = None
        try:
            fd, tmp_name = tempfile.mkstemp(
                prefix=".settings-", suffix=".json", dir=str(self.path.parent)
            )
            tmp = Path(tmp_name)
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(self._data, f, ensure_ascii=False, indent=2)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, self.path)
            tmp = None
        finally:
            if tmp is not None:
                try:
                    tmp.unlink()
                except OSError:
                    pass

    # ---------- 取值 ----------

    def get(self, key: str, default: Any = None) -> Any:
        return self._data.get(key, default)

    def set(self, key: str, value: Any) -> None:
        self._data[key] = value
        self.save()

    def delete(self, key: str) -> None:
        if key in self._data:
            del self._data[key]
            self.save()

    def as_dict(self) -> dict:
        return dict(self._data)
