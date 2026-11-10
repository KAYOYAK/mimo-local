"""生成 README 用的多张功能展示图。

每个场景都是一次「假对话」渲染 —— 内容用真实跑出来的结果，
通过 Qt 渲染而不是真启动引擎，这样可重复、可批量。

    python tools/make_gallery.py

产物（docs/）：
    screenshot.png      主图：发图 → OCR → 回答
    shot-code.png       写代码：Markdown 代码块渲染
    shot-tools.png      工具调用：折叠的 agent 步骤
    shot-history.png    多对话与历史
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PySide6.QtWidgets import QApplication

app = QApplication(sys.argv)
import ui_qt

W, H = 1440, 920

SESSIONS = [
    {"id": "ses_1", "title": "收据解析：MR. D.I.Y.", "titleRevision": 1,
     "time": {"updated": 1794060000000}},
    {"id": "ses_2", "title": "写一个快速排序", "titleRevision": 1,
     "time": {"updated": 1794050000000}},
    {"id": "ses_3", "title": "项目里有哪些文件", "titleRevision": 1,
     "time": {"updated": 1794040000000}},
    {"id": "ses_4", "title": "修复端口占用问题", "titleRevision": 1,
     "time": {"updated": 1794030000000}},
]


def new_window(active_sid: str = "ses_1"):
    """建一个已启动状态的窗口。"""
    w = ui_qt.MainWindow()
    w.resize(W, H)
    w.proj_label.setText(r"D:\projects\demo")
    w.lbl_dir.setText("demo")
    w.workdir = Path(r"D:\projects\demo")
    w.btn_engine.setText("停止引擎")
    w._fill_sessions(SESSIONS)
    w.ctx_label.setText("上下文 64K\n9B + OCR 在线\n全本地推理")
    w._set_status("就绪", "ok")
    # 清空对话区
    w.items.clear()
    w._by_id.clear()
    w._echoes.clear()
    w._seen.clear()
    w._expanded.clear()
    return w


def add_user(w, text):
    it = ui_qt.Item("", "text", role="user")
    it.text = text
    w.items.append(it)


def add_ai(w, text, idx=0):
    it = ui_qt.Item(f"ai{idx}", "text")
    it.text = text
    w.items.append(it)


def add_reasoning(w, text, idx=0):
    it = ui_qt.Item(f"rs{idx}", "reasoning")
    it.text = text
    w.items.append(it)


def add_tool(w, tool, args, status="completed", idx=0, expanded=False):
    it = ui_qt.Item(f"tl{idx}", "tool")
    it.tool = tool
    it.status = status
    it.args = args
    w.items.append(it)
    if expanded:
        w._expanded.add(w._fold_key(it))


def add_image(w, img: Path, ocr_text: str):
    it = ui_qt.Item("", "ocr")
    it.tool = img.name
    it.text = ocr_text
    it._pending = img
    w.items.append(it)


def shot(w, name: str):
    w._dirty = True
    w._flush()
    w.chat.verticalScrollBar().setValue(0)
    QApplication.processEvents()
    pm = w.grab()
    out = ROOT / "docs" / name
    pm.save(str(out))
    print(f"  ✓ {name:22} {pm.width()}x{pm.height()}  "
          f"{out.stat().st_size / 1024:.0f} KB")
    w.close()


# ==================== 场景 1：看图问答 ====================

def scene_image_qa():
    w = new_window()
    add_user(w, "这张收据一共多少钱？买了什么？")

    img = ROOT / "docs" / "测试图.jpg"
    ocr_text = """MR. D.I.Y. SDN BHD
(CO.REG :704427-T )
LOT 1851-A & 1851-B, JALAN KPB 6,
KAWASAN PERINDUSTRIAN BALAKONG,
43300 SERI KEMBANGAN, SELANGOR
(GST ID No :001092886528)
(CHERAS LEISURE MALL)
-TAX INVOICE-

CLOTH PEG E-140                     *S
WA36 - 6/60
9555112901406            1 X  5.90   5.90

Item(s) : 1                  Qty(s) : 1
------------------------------------------
Total Incl. GST@6%                RM 5.90
CASH                              RM 5.90

GST Summary          Amt(RM)    Tax(RM)
GST @6%                 5.57        0.33"""
    add_image(w, img, ocr_text)

    add_reasoning(w, "这是一张收据，OCR 已经转成文字了。用户问总价和买了什么。\n"
                     "从文字里找 Total 行和商品行……", 1)

    add_ai(w, ("这是一张 MR. D.I.Y. 的税务发票（Cheras Leisure Mall）：\n\n"
               "**一共 RM 5.90**\n"
               "• 商品：CLOTH PEG E-140 *S，数量 1，单价 RM 5.90\n"
               "• 含税前净额 RM 5.57，GST 6% 是 RM 0.33\n"
               "• 支付方式：现金"), 1)
    shot(w, "screenshot.png")


# ==================== 场景 2：写代码 ====================

def scene_code():
    w = new_window("ses_2")
    add_user(w, "用 Python 写一个快速排序，要能处理空列表和重复元素")

    add_reasoning(w, "用户要一个快速排序实现，强调两个边界：空列表、重复元素。\n"
                     "空列表直接返回；重复元素用双指针分区可以避免无限递归。", 1)

    add_ai(w, """用双指针分区，重复元素全部归到中间，避免退化：

```python
def quicksort(items: list) -> list:
    if len(items) <= 1:          # 空列表和单元素直接返回
        return list(items)

    pivot = items[len(items) // 2]
    left, mid, right = [], [], []
    for x in items:
        if x < pivot:
            left.append(x)
        elif x > pivot:
            right.append(x)
        else:
            mid.append(x)        # 重复元素集中到中间
    return quicksort(left) + mid + quicksort(right)
```

时间 O(n log n)（平均），空间 O(n) —— 每次递归都要新建三个列表。
要原地版本的话可以把 `mid` 那段换成 Lomuto 分区，但重复元素多时会退化。""", 1)
    shot(w, "shot-code.png")


# ==================== 场景 3：工具调用 ====================

def scene_tools():
    w = new_window("ses_3")
    add_user(w, "看看这个项目里有哪些文件，然后告诉我入口在哪")

    add_reasoning(w, "先列目录看结构，再找入口文件。", 1)
    add_tool(w, "list", '{"path": "D:\\\\projects\\\\demo"}', "completed", 1)
    add_tool(w, "read", '{"filePath": "D:\\\\projects\\\\demo\\\\README.md"}',
             "completed", 2)
    add_tool(w, "glob", '{"pattern": "**/*.py"}', "completed", 3)
    add_tool(w, "bash", '{"command": "wc -l src/*.py"}', "completed", 4)

    add_ai(w, ("项目结构是标准的 Python 包布局：\n\n"
               "```\n"
               "demo/\n"
               "├── src/\n"
               "│   ├── __init__.py\n"
               "│   ├── cli.py        ← 入口\n"
               "│   └── core.py\n"
               "├── tests/\n"
               "└── README.md\n"
               "```\n\n"
               "**入口在 `src/cli.py`** —— `setup.py` 里的 `console_scripts` "
               "指向它的 `main()`，一共 42 行。"), 1)
    shot(w, "shot-tools.png")


# ==================== 场景 4：历史对话 ====================

def scene_history():
    w = new_window("ses_3")
    add_user(w, "修一下启动慢的问题")
    add_ai(w, ("找到了：`Engine.stop()` 里同步等三个引擎退出，最坏 30 秒卡在 "
               "GUI 线程。\n\n"
               "**改法**：`closeEvent` 先 `hide()` 让窗口立刻消失，清理丢后台线程，"
               "完成后再 `QApplication.quit()`。\n\n"
               "实测从几十秒降到 **3ms**。"), 1)
    add_user(w, "那切换对话时上下文会串吗？")
    add_ai(w, ("不会。每个对话在 MiMo Code 里是独立的 session，"
               "消息历史完全分开，每次只发当前对话的。\n\n"
               "实测两个 session 交叉问：A 记住暗号「紫色大象」，B 问它 —— "
               "B 答「不知道，我没有跨会话的记忆」，回到 A 还记得。"), 2)
    shot(w, "shot-history.png")


def main() -> int:
    img = ROOT / "docs" / "测试图.jpg"
    if not img.is_file():
        print(f"缺测试图：{img}", file=sys.stderr)
        return 1

    print("生成展示图…")
    scene_image_qa()
    scene_code()
    scene_tools()
    scene_history()
    print("完成。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
