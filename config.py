"""
配置：路径、端口、模型位置。

设计原则：自用软件，路径写死。不搞"自动发现"那套——
少一层推断就少一处可能出错的地方。
换目录时只改这个文件。
"""
from pathlib import Path
import sys

# ---------- 项目根 ----------
# PyInstaller 打包后 __file__ 指向临时解压目录，必须用 exe 自己的位置。
if getattr(sys, "frozen", False):
    ROOT = Path(sys.executable).resolve().parent
else:
    ROOT = Path(__file__).resolve().parent

ASSETS = ROOT / "assets"
LOGS = ROOT / "logs"

# 打包成 exe 装到别处后，assets（mimo.exe + OCR 模型，约 880 MB）未必跟着走。
# 找不到就用开发目录里那份 —— 自用软件，没必要为这 880 MB 反复复制。
ASSETS_FALLBACK = Path(r"D:\mimo-desktop\assets")
if not ASSETS.is_dir() and ASSETS_FALLBACK.is_dir():
    ASSETS = ASSETS_FALLBACK

# AGENTS.md（OCR 工具说明）也一样，装到别处时 fallback
AGENTS_FILE = ROOT / "AGENTS.md"
_AGENTS_FALLBACK = Path(r"D:\mimo-desktop\AGENTS.md")
if not AGENTS_FILE.exists() and _AGENTS_FALLBACK.exists():
    AGENTS_FILE = _AGENTS_FALLBACK

# 日志目录必须在任何东西写日志之前存在，否则自检会报「写不进去」
try:
    LOGS.mkdir(parents=True, exist_ok=True)
except Exception:
    pass

# 模型和引擎本来就在 D 盘，不进安装包
MODEL_ROOT = Path(r"D:\models")
ENGINE_ROOT = Path(r"D:\llama.cpp")

# ---------- 推理引擎 ----------
# U 盘项目里锁定的那一份（b11270 / CUDA 13.4）。
# CUDA 运行库走系统 PATH，引擎目录里不需要额外 DLL。
ENGINE = Path(r"D:\llama.cpp\llama-b11270-bin-win-cuda-13.4-x64\llama-server.exe")

# ---------- 聊天模型（MiMo V2.6 Distill Qwen 9B）----------
MODEL_DIR = Path(r"D:\models\MiMo-9B-GGUF")
CHAT_MODEL = MODEL_DIR / "MiMo-V2.6-Distill-Qwen-9B-Q5_K_M.gguf"
CHAT_MMPROJ = MODEL_DIR / "mmproj-MiMo-V2.6-Distill-Qwen-9B-f16.gguf"

# ---------- 文档识别（Xiaomi-OCR-0，0.8B）----------
OCR_DIR = ASSETS / "ocr"
OCR_MODEL = OCR_DIR / "Xiaomi-OCR-0-Q5_K_M.gguf"
OCR_MMPROJ = OCR_DIR / "mmproj-Xiaomi-OCR-0-F16.gguf"

# ---------- AI 编程（MiMo Code）----------
MIMO_EXE = ASSETS / "mimo.exe"
MIMO_AGENT_DIR = ROOT / "agent"          # XDG 四路径的落点

# ---------- 版本 ----------
# 单一来源。About、诊断报告、安装器都从这里取，别在各处再写一份。
VERSION = "1.0.17"

# ---------- 运行期设置 ----------
# 当前模型、端口等可变状态存在这里，**不写回本文件**。
# 打包成 exe 后 config.py 还在，但改它是错的：中途被杀会留半截源码。
SETTINGS_FILE = ROOT / "settings.json"
# 每个会话发过哪些图片 —— 切换对话时要把缩略图恢复出来，
# 但 MiMo Code 的历史里只存 OCR 文本，不存图片路径。
SESSION_IMAGES_FILE = ROOT / "agent" / "session-images.json"

# OCR 引擎的 pid 文件。tools/ocr.py 起的引擎不属于 UI 管，靠这个收尸。
OCR_PID_FILE = LOGS / "ocr.pid"
# 三个子进程都要有 pid 文件 —— 程序异常退出（崩溃、被强杀、Blue Screen）时
# 它们不会跟着退出，下次启动就会撞端口。有 pid 才能在下一次启动时收尸。
CHAT_PID_FILE = LOGS / "chat.pid"
AGENT_PID_FILE = LOGS / "agent.pid"

# ---------- 端口 ----------
CHAT_PORT = 8080        # 聊天引擎
OCR_PORT = 8090         # 文档识别引擎（和聊天引擎一起常驻）
AGENT_PORT = 4096       # mimo serve（agent 后端）

# ---------- 聊天引擎参数 ----------
# 上下文：AI 编程需要 64K。32K 下 MiMo Code 的系统提示 + 工具定义吃掉 27K，
# 跑两三轮就超限（实测 277 秒后失败）。
#
# 设 0 = **按显存自动推导**（core/hardware.py），上限 CHAT_CTX_MAX。
# 别在这里写死 65536 —— 那样自动降级分支永远不会执行，6 GB 的卡也会照样要 64K，
# 然后启动即 OOM。
CHAT_CTX = 0
CHAT_CTX_MAX = 65536

# KV 缓存精度。空 = 按显存自动（>= 9 GB 用 q8_0，否则 q4_0）。
# 只压缓存精度，不动模型权重 —— 这是 U 盘版实测省下 716 MiB 的做法。
CHAT_KV = ""

# GPU 卸载层数。None = 按显存自动（装得下就 99 层，装不下交给 --fit on）。
CHAT_NGL = None

# ---------- 采样参数（移植自 U 盘版，年初调了一轮才定的值）----------
# 缺 top-p/top-k/repeat-penalty 时，长代码输出明显更容易复读。
CHAT_TEMP = 0.6
CHAT_TOP_P = 0.95
CHAT_TOP_K = 20
CHAT_MIN_P = 0.0
CHAT_REPEAT_PENALTY = 1.1
CHAT_N_PREDICT = -1

# 0 = 按物理核心数自动
CHAT_THREADS = 0

# ---------- 文档识别参数 ----------
# 官方参考实现是 do_sample=False（贪心解码），所以温度 0、top_k 1。
# prompt 也必须是作者给的那五条 —— 换词输出格式就会飘。
OCR_CTX = 8192
OCR_PROMPTS = {
    "doc": (
        "Extract all information from the main body of the document image and "
        "represent it in markdown format, ignoring headers and footers. Tables "
        "should be expressed in OTSL format, formulas in the document should be "
        "represented using LATEX format, and the parsing should be organized "
        "according to the reading order."
    ),
    "text": "Extract the text in the image.",
    "table": "Parse the table in the image into OTSL.",
    "formula": "Identify the formula in the image and represent it using LATEX format.",
    "kie": "Extract key information in the image",
}

OCR_TASK_LABELS = {
    "doc": "整页文档 → Markdown",
    "text": "只取文字",
    "table": "表格 → OTSL",
    "formula": "公式 → LaTeX",
    "kie": "关键信息 → JSON",
}


def check_paths() -> list[str]:
    """启动前自查。返回缺失项的描述，空列表表示都齐了。"""
    missing = []
    if not ENGINE.exists():
        missing.append(f"推理引擎：{ENGINE}")
    if not CHAT_MODEL.exists():
        missing.append(f"聊天模型：{CHAT_MODEL}")
    if not OCR_MODEL.exists():
        missing.append(f"OCR 模型：{OCR_MODEL}")
    if not MIMO_EXE.exists():
        missing.append(f"AI 编程：{MIMO_EXE}")
    return missing


# ---------- 运行期可变状态（从 settings.json 读）----------
#
# 为什么不在切换模型时改写本文件：
#   1. 打包成 exe 后 config.py 在压缩包里，磁盘上没有这个文件 → 必然失败
#   2. 即使源码运行，读到一半被杀会留下半截 Python 文件 → 程序再也起不来
#      （报错还是 ModuleNotFoundError 这种看不出根因的）
# 所以运行期状态一律写 settings.json，本文件只当默认值。

def _settings() -> dict:
    try:
        import json
        if SETTINGS_FILE.is_file():
            obj = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
            return obj if isinstance(obj, dict) else {}
    except Exception:
        pass
    return {}


def current_model() -> Path:
    """当前模型。settings.json 里选过的优先，否则用 CHAT_MODEL 默认值。"""
    p = _settings().get("chat_model")
    if p:
        try:
            cand = Path(p)
            if cand.is_file():
                return cand
        except Exception:
            pass
    return CHAT_MODEL


def current_mmproj() -> Path:
    """当前模型配套的投影文件。"""
    p = _settings().get("chat_mmproj")
    if p:
        try:
            cand = Path(p)
            if cand.is_file():
                return cand
        except Exception:
            pass
    return CHAT_MMPROJ
