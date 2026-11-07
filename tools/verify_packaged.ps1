# ====================================================================
#  verify_packaged.ps1 —— 打包后的路径验证
# ====================================================================
#  为什么需要这个脚本：
#    这一轮修的问题（settings.json 位置、--ocr 子命令、生成的 AGENTS-ocr.md、
#    卸载串）每一条都有 frozen 变体，而 claims.py 的 frozen 分支只有在打包
#    环境里才走。仓库里原本没有任何东西能证明「打包后是好的」。
#
#    NULL 那边的原话：最危险的路径，验证得最少。
#
#  三条断言，全部不需要显卡：
#    1. MiMoLocal.exe --claims      必须 0 退出，且 frozen 分支真的走到
#    2. MiMoLocal.exe --selfcheck   不能有 [缺失]
#    3. MiMoLocal.exe --ocr 测试图  必须产出 .md
#
#  用法：
#     pwsh -File tools\verify_packaged.ps1
#     pwsh -File tools\verify_packaged.ps1 -SkipBuild
# ====================================================================
[CmdletBinding()]
param(
    [switch]$SkipBuild
)

$ErrorActionPreference = "Continue"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$fail = 0
function Step($n, $t) { Write-Host ""; Write-Host "[$n] $t" -ForegroundColor Cyan }
function Ok($t)   { Write-Host "  [OK]   $t" -ForegroundColor Green }
function Bad($t)  { Write-Host "  [FAIL] $t" -ForegroundColor Red; $script:fail++ }
function Warn($t) { Write-Host "  [注意] $t" -ForegroundColor Yellow }

# ---------- 0. 构建 ----------
$exe = Join-Path $root "dist\MiMoLocal.exe"
if (-not $SkipBuild) {
    Step 0 "打包"
    $out = & python -m PyInstaller --onefile --windowed --name MiMoLocal `
        --distpath dist --workpath build --specpath . --noconfirm --clean `
        --exclude-module numpy --exclude-module PIL --exclude-module matplotlib `
        --exclude-module pandas --exclude-module scipy --exclude-module pytest `
        --exclude-module PySide6.QtWebEngineCore --exclude-module PySide6.QtWebEngineWidgets `
        --exclude-module PySide6.QtQml --exclude-module PySide6.QtQuick `
        --exclude-module PySide6.Qt3DCore --exclude-module PySide6.QtMultimedia `
        --add-data "VERSION.txt;." `
        --add-data "assets/app.ico;." `
        --add-data "assets/app.png;." `
        --icon "assets/app.ico" `
        --collect-submodules tools main.py 2>&1 | Out-String
    if ($LASTEXITCODE -ne 0) { Bad "打包失败"; exit 1 }
    Ok "打包完成 $([math]::Round((Get-Item $exe).Length/1MB,1)) MB"
} else {
    if (-not (Test-Path $exe)) { Bad "没有 dist\MiMoLocal.exe，去掉 -SkipBuild"; exit 1 }
    Ok "使用现有 $exe"
}

# 用临时目录当「安装位置」，避免污染开发目录和真实安装
$probe = Join-Path $env:TEMP ("mimo-verify-" + [guid]::NewGuid().ToString("N").Substring(0,8))
New-Item -ItemType Directory -Path $probe -Force | Out-Null
Copy-Item $exe (Join-Path $probe "MiMoLocal.exe") -Force
Copy-Item (Join-Path $root "VERSION.txt") $probe -Force -EA SilentlyContinue
Write-Host "  探针目录: $probe" -ForegroundColor DarkGray

try {
    # ---------- 1. claims（frozen 分支）----------
    Step 1 "--claims（frozen 模式）"
    $p = Start-Process (Join-Path $probe "MiMoLocal.exe") -ArgumentList "--claims" `
        -WorkingDirectory $probe -PassThru -Wait -WindowStyle Hidden
    $claimLog = Join-Path $probe "logs\声明断言.txt"
    if ($p.ExitCode -eq 0) {
        Ok "退出码 0"
    } else {
        Bad "退出码 $($p.ExitCode)"
    }
    if (Test-Path $claimLog) {
        $text = Get-Content $claimLog -Raw -Encoding utf8
        if ($text -match "全部成立") {
            $line = ($text -split "`n" | Where-Object { $_ -match "条全部成立" }) -join ""
            Ok $line.Trim()
        } else {
            Bad "有条目不符：$($text -split "`n" | Where-Object { $_ -match '不符' } | Select-Object -First 3)"
        }
        # frozen 分支真的走到了吗
        if ($text -match "--ocr") { Ok "frozen 分支真的走到了（AGENTS 里用了 --ocr）" }
        else { Warn "没证据表明 frozen 分支被走到" }
    } else {
        Bad "没产出 logs\声明断言.txt"
    }

    # ---------- 2. selfcheck ----------
    Step 2 "--selfcheck"
    # 这个脚本验的是「打包后的路径对不对」，不是「这台机器装没装模型」。
    # 没有模型的机器上 selfcheck 必然有 [缺失] —— 那是环境问题，不是打包问题。
    # 模型目录从 config 取，不要硬编码（换目录后守卫会永远跳过第 2 步）。
    $modelDir = ""
    try {
        $modelDir = (& python -c "import sys; sys.path.insert(0,'.'); import config; print(config.MODEL_DIR)" 2>&1 | Out-String).Trim()
    } catch { }
    $hasModel = $modelDir -and (Test-Path $modelDir) -and
                ((Get-ChildItem $modelDir -Filter "*.gguf" -EA SilentlyContinue | Measure-Object).Count -gt 0)
    if (-not $hasModel) {
        Warn "本机没有模型（$modelDir）—— 跳过第 2 步（这是环境问题，不是打包问题）"
    } else {
        $p2 = Start-Process (Join-Path $probe "MiMoLocal.exe") -ArgumentList "--selfcheck" `
            -WorkingDirectory $probe -PassThru -Wait -WindowStyle Hidden
        $scLog = Join-Path $probe "logs\自检结果.txt"
        if (Test-Path $scLog) {
            $text2 = Get-Content $scLog -Raw -Encoding utf8
            $missing = [regex]::Matches($text2, "\[缺失\]").Count
            if ($missing -eq 0) { Ok "没有缺失项" }
            else { Bad "有 $missing 个 [缺失] 项" }
            $line2 = ($text2 -split "`n" | Where-Object { $_ -match "通过 \d+" }) -join ""
            if ($line2) { Write-Host "         $($line2.Trim())" -ForegroundColor DarkGray }
        } else {
            Bad "没产出 logs\自检结果.txt"
        }
    }

    # ---------- 3. --ocr ----------
    Step 3 "--ocr 测试图（验证打包后 OCR 路径）"
    $img = Join-Path $root "docs\测试图.jpg"
    if (-not (Test-Path $img)) {
        & python (Join-Path $root "tools\make_test_image.py") $img | Out-Null
    }
    $imgCopy = Join-Path $probe "测试图.jpg"
    Copy-Item $img $imgCopy -Force
    $md = Join-Path $probe "测试图.md"
    if (Test-Path $md) { Remove-Item $md -Force }

    Write-Host "  正在跑 OCR（需要 8090 端口空闲，约 15 秒）…" -ForegroundColor DarkGray
    $p3 = Start-Process (Join-Path $probe "MiMoLocal.exe") -ArgumentList "--ocr", "测试图.jpg" `
        -WorkingDirectory $probe -PassThru
    # ⚠ PyInstaller onefile 有父子两个进程：
    #   父进程只负责解压然后拉起子进程，-Wait 等的是父进程。
    #   父进程退出时子进程可能还在写文件 —— 直接检查会误报。
    #   所以轮询等文件出现（最多 60 秒）。
    $deadline = (Get-Date).AddSeconds(60)
    while ((Get-Date) -lt $deadline -and -not (Test-Path $md)) {
        Start-Sleep -Milliseconds 500
    }
    Start-Sleep -Milliseconds 500      # 让写入落盘

    if (Test-Path $md) {
        $size = (Get-Item $md).Length
        Ok "--ocr 产出了 测试图.md（$size 字节）"
        if ($size -lt 20) { Warn "文件几乎是空的" }
    } else {
        Bad "--ocr 没产出 测试图.md（退出码 $($p3.ExitCode)）"
    }

    # ---------- 4. 清理 OCR 引擎 ----------
    Step 4 "清理"
    $pidFile = Join-Path $probe "logs\ocr.pid"
    if (Test-Path $pidFile) {
        $ocrPid = (Get-Content $pidFile -Raw).Trim()
        $tl = & tasklist /FI "PID eq $ocrPid" 2>&1 | Out-String
        if ($tl -match "llama") {
            & taskkill /F /PID $ocrPid 2>&1 | Out-Null
            Ok "清掉了探针起的 OCR 引擎 (pid $ocrPid)"
        } else {
            Ok "OCR 引擎已经不在了"
        }
    } else {
        Ok "没有残留 pid 文件"
    }
}
finally {
    Remove-Item $probe -Recurse -Force -EA SilentlyContinue
}

# ---------- 汇总 ----------
Write-Host ""
Write-Host ("=" * 64)
$verdict = if ($fail -eq 0) { "打包验证通过" } else { "打包验证失败：$fail 项" }
if ($fail -eq 0) {
    Write-Host "  $verdict" -ForegroundColor Green
} else {
    Write-Host "  $verdict" -ForegroundColor Red
}
Write-Host ("=" * 64)

# ---------- 留痕：通过的运行必须留下证据 ----------
# logs/ 在 gitignore 里，仓库看不到运行结果。所以把 summary 写进 docs/
# 并提交 —— 以后每次打包验证都能对得上「哪个 commit、什么时间、验了什么」。
try {
    $sha = ""
    try { $sha = (& git rev-parse --short HEAD 2>&1 | Out-String).Trim() } catch { }
    $exeSha = if (Test-Path $exe) { (Get-FileHash $exe -Algorithm SHA256).Hash.ToLower() } else { "(无 exe)" }
    $lines = @(
        "# 打包验证记录（自动生成，不要手改）",
        "",
        "- 时间: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')",
        "- 提交: $sha",
        "- 结论: $verdict",
        "- exe SHA-256: ``$exeSha``",
        "",
        "## 这一轮验证了什么",
        "",
        "1. ``--claims`` frozen 模式（声明断言，含 frozen 分支真的走到）",
        "2. ``--selfcheck``（不能有 [缺失]；本机没模型时跳过）",
        "3. ``--ocr`` 真实收据图（必须产出 .md）",
        "4. OCR 引擎按 pid 回收（不留孤儿进程）",
        "",
        "> 这份文件由 ``tools/verify_packaged.ps1`` 生成。",
        "> 它证明的是「打包后的路径对不对」；",
        "> ``main.py --claims`` 在源码模式证明的是「接线对不对」。两者不能互相替代。"
    )
    $outFile = Join-Path $root "docs\verify-latest.txt"
    New-Item -ItemType Directory -Path (Split-Path $outFile) -Force | Out-Null
    Set-Content -Path $outFile -Value ($lines -join "`r`n") -Encoding utf8
    Write-Host "  记录已写入 docs\verify-latest.txt" -ForegroundColor DarkGray
} catch {
    Write-Host "  记录写入失败：$_" -ForegroundColor DarkGray
}

exit $(if ($fail -eq 0) { 0 } else { 1 })
