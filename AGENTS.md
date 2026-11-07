# 本地工具

这个环境跑在纯本地的模型上，除了读写文件、执行命令，还多一个**本地图片识别**工具。

## ocr —— 把图片转成 Markdown

```bash
python D:\mimo-desktop\tools\ocr.py <图片路径>
```

可选 `--task`：

| 参数 | 用途 |
|---|---|
| （默认 `doc`） | 整页文档 → Markdown，表格转 OTSL、公式转 LaTeX |
| `--task text` | 只提取文字 |
| `--task table` | 表格 → OTSL |
| `--task formula` | 公式 → LaTeX |

图片路径可以是相对的（相对当前工作目录）。

**输出的就是 Markdown 正文**，直接读它，不需要再解析。

### 什么时候用

用户提到以下内容时，先确认文件存在，然后调用这个工具：

- 「这张图里写了什么」「看看这个截图」
- 发票、收据、扫描件、书页
- 「把这张表格转成 Markdown」
- 「这个公式是什么」
- 任何**以图片形式存在的文档**

### 注意

- 第一次调用会启动 OCR 引擎（约 4 秒），之后就快了
- 一次只处理一张图，批量就循环调用
- 识别结果是**原文转录**，不要自己编造内容
- 这个工具**只能看图片**；PDF 需要先转成图片（可以用 `pdftoppm` 或让用户提供图片）

### 例子

```bash
# 整页文档
python D:\mimo-desktop\tools\ocr.py 发票.jpg

# 单独的表格
python D:\mimo-desktop\tools\ocr.py 表格.png --task table

# 相对路径
python D:\mimo-desktop\tools\ocr.py ./screenshots/error.png --task text
```
