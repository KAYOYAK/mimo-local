# 第三方组件与许可

本项目（MiMo Local）自己的代码以 **Apache-2.0** 发布，见 [LICENSE](LICENSE)。

但它**依赖或调用了别人的东西**。这份文档说清楚每一项是什么、什么许可、从哪来。

> ⚠️ **仓库里不包含任何模型文件和引擎二进制。** 模型有 6.4 GB，引擎有 700 MB，
> 都放不进 Git。你需要自己下载放到指定位置（见 README 的「准备文件」一节）。

---

## 一、运行必需的组件

### 1. llama.cpp

| | |
|---|---|
| 用途 | 推理引擎，加载 GGUF 模型并提供 HTTP API |
| 许可 | **MIT** |
| 来源 | https://github.com/ggml-org/llama.cpp |
| 本项目使用 | 编译好的 `llama-server.exe`（build b11270），放在 `D:\llama.cpp\llama-b11270-bin-win-cuda-13.4-x64\` |
| 是否修改 | 否，原样调用 |

用的是官方预编译包，没有改任何代码，只通过命令行参数和 HTTP 接口驱动。

### 2. MiMo Code

| | |
|---|---|
| 用途 | AI 编程 agent 引擎（本项目用它的 `mimo serve` 模式做后端） |
| 许可 | **MIT** |
| 来源 | https://github.com/XiaomiMiMo/MiMo-Code |
| 本项目使用 | `mimo.exe`（v0.1.16），放在 `assets/mimo.exe` |
| 是否修改 | 否，通过 HTTP API 驱动 |

官方发布的是 Bun 编译的独立二进制，不需要 Node.js 运行时。

### 3. PySide6 / Qt

| | |
|---|---|
| 用途 | 图形界面（Qt for Python） |
| 许可 | **LGPL-3.0**（也有商业许可） |
| 来源 | https://pypi.org/project/PySide6/ |
| 本项目使用 | 通过 pip 安装，动态链接 |
| 是否修改 | 否 |

本项目以动态链接方式使用 Qt，符合 LGPL-3.0 的要求。如果你要把它打包分发，
请注意 Qt 的 LGPL 义务（提供替换库的方式等）。

### 4. Python

| | |
|---|---|
| 用途 | 运行环境 |
| 许可 | **PSF-2.0** |
| 来源 | https://www.python.org/ |

本项目代码用 Python 3.10+ 编写，用到 `tkinter`（标准库）和 `PySide6`（第三方）。

---

## 二、模型文件（不在仓库里，需要自己下载）

### 5. MiMo-V2.6-Distill-Qwen-9B

| | |
|---|---|
| 用途 | 主模型（聊天 / 编程 agent） |
| 文件 | `MiMo-V2.6-Distill-Qwen-9B-Q5_K_M.gguf`（6.02 GB） |
| 投影 | `mmproj-MiMo-V2.6-Distill-Qwen-9B-f16.gguf`（0.86 GB） |
| 许可 | **以小米官方发布页为准** —— 本项目只调用，不重新分发 |
| 来源 | 小米 MiMo 官方模型发布渠道 |
| 是否需要署名 | 按模型发布页的要求 |

> 这里没写死一个许可证名称，是因为我查不到该权重的官方许可文本。
> **使用前请自己去发布页确认。** 本项目不附带模型文件，也不对模型本身的许可做判断。

### 6. Xiaomi-OCR-0

| | |
|---|---|
| 用途 | 文档识别（把图片转成 Markdown / 表格 / 公式） |
| 文件 | `Xiaomi-OCR-0-Q5_K_M.gguf`（0.54 GB） |
| 投影 | `mmproj-Xiaomi-OCR-0-F16.gguf`（0.19 GB） |
| 许可 | **Apache-2.0** |
| 来源 | 小米 Xiaomi-OCR-0 官方发布 |
| 基座 | Qwen/Qwen3.5-0.8B-Base |

### 7. 中转格式说明

两个模型都用 GGUF 格式，由 llama.cpp 加载。GGUF 是 llama.cpp 项目的格式，
不受上述模型许可单独约束。

---

## 三、开发与构建工具（不随程序分发）

| 工具 | 许可 | 用途 |
|---|---|---|
| PyInstaller | GPL-2.0 + 例外条款 | 打包成 exe |
| uv | Apache-2.0 / MIT | 安装 Python 依赖 |
| Pillow、numpy 等 | 各自的许可 | **未使用**（已从打包中排除） |

> **PyInstaller 的 GPL 例外**：它明确允许用 PyInstaller 打包的程序以任意许可发布
> （因为打包产物只是「运行时的聚合」，不构成衍生作品）。见其 LICENSE 的
> `Bootloader exception`。

---

## 四、本项目不包含的东西

| 没有 | 原因 |
|---|---|
| 模型权重 | 6.4 GB，Git 放不下 |
| llama.cpp 二进制 | 700 MB，而且应该让用户从官方下最新版 |
| MiMo Code 二进制 | 130 MB，同上 |
| 任何云服务 / API Key | 本项目**完全离线**，不调用任何网络服务 |

---

## 五、如果你要再分发

- 本项目代码：遵守 **Apache-2.0**（保留 LICENSE 和版权声明）
- Qt/PySide6：遵守 **LGPL-3.0**（动态链接、可替换）
- MiMo Code：遵守 **MIT**（保留版权声明）
- llama.cpp：遵守 **MIT**
- 模型：**自己去各自发布页确认** —— 这是最容易踩的一条，因为模型许可经常比代码严格

本项目是自用工具，不做商用。如果模型许可禁止某种用途，那不是本项目能替你决定的。
