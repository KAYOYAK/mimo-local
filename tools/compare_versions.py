"""对比 v1.0.23 和 HEAD，找出被删掉的函数/方法。

README 承诺「功能只多不少」，但函数改名、重构都可能悄悄丢掉能力。
这个脚本按文件比 def 名单，列出 v1.0.23 有、HEAD 没有的。
"""
import subprocess
import sys
from pathlib import Path

ROOT = Path(r"D:\mimo-desktop")
OLD = sys.argv[1] if len(sys.argv) > 1 else "v1.0.23"
NEW = sys.argv[2] if len(sys.argv) > 2 else "HEAD"


def run(*args: str) -> str:
    r = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    return r.stdout


def py_files(ref: str) -> list[str]:
    out = run("ls-tree", "-r", "--name-only", ref)
    return [p for p in out.splitlines() if p.endswith(".py")]


def defs_of(ref: str) -> dict[str, set[str]]:
    """返回 {文件: {def 名}}。"""
    result: dict[str, set[str]] = {}
    for f in py_files(ref):
        content = run("show", f"{ref}:{f}")
        names: set[str] = set()
        for line in content.splitlines():
            s = line.strip()
            if s.startswith("def "):
                name = s[4:].split("(")[0].strip()
                if name:
                    names.add(name)
            elif s.startswith("class "):
                name = s[6:].split("(")[0].split(":")[0].strip()
                if name:
                    names.add(f"class {name}")
        if names:
            result[f] = names
    return result


def main() -> int:
    old = defs_of(OLD)
    new = defs_of(NEW)

    # 只看两边都存在的文件 —— 整文件删掉的情况另报
    removed_files = sorted(set(old) - set(new))
    if removed_files:
        print("整个文件被删（需要确认是否合并进别处）：")
        for f in removed_files:
            print(f"  - {f}  ({len(old[f])} 个定义)")
    else:
        print("没有被整体删除的 Python 文件。")

    print()
    lost: list[tuple[str, str]] = []
    for f in sorted(set(old) & set(new)):
        gone = old[f] - new[f]
        # 过滤掉临时脚本里的私有辅助（_xxx）和已经确认被替代的
        for name in sorted(gone):
            if name.startswith("_") and not name.startswith("_" + "class"):
                continue
            lost.append((f, name))

    if not lost:
        print("没有被删掉的公开函数/方法/类。")
        return 0

    print(f"v1.0.23 有、现在没有的公开定义（{len(lost)} 个）：")
    by_file: dict[str, list[str]] = {}
    for f, name in lost:
        by_file.setdefault(f, []).append(name)
    for f, names in by_file.items():
        print(f"  {f}:")
        for n in names:
            print(f"    - {n}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
