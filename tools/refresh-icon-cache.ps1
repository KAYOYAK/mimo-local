# ====================================================================
#  refresh-icon-cache.ps1 —— 强制 Windows 重新读取图标
# ====================================================================
#  什么时候需要：
#    程序图标已经换好了（exe 和快捷方式都指向新的 .ico），
#    但桌面/开始菜单还显示旧图标。
#
#  原理：
#    Windows 把图标缓存成 iconcache_*.db，ie4uinit -show 只做温和刷新，
#    有时候不够。这里删掉缓存文件 + 重启 explorer.exe 强制重建。
#
#  代价：桌面会闪一下（explorer 重启，约 2 秒恢复）。
#  正在运行的程序不受影响，但打开的文件管理器窗口会关掉。
#
#  用法：
#    pwsh -File tools\refresh-icon-cache.ps1
#    pwsh -File tools\refresh-icon-cache.ps1 -Gentle   # 只温和刷新，不重启
# ====================================================================
[CmdletBinding()]
param(
    [switch]$Gentle
)

$ErrorActionPreference = "Continue"

function Ok($t)   { Write-Host "  [OK]   $t" -ForegroundColor Green }
function Info($t) { Write-Host "  $t" -ForegroundColor DarkGray }

if ($Gentle) {
    Info "温和刷新（ie4uinit -show）…"
    Start-Process ie4uinit.exe -ArgumentList "-show" -Wait -NoNewWindow -EA SilentlyContinue
    Ok "已刷新。如果桌面还是旧图标，去掉 -Gentle 再跑一次（会重启 explorer）"
    exit 0
}

Write-Host ""
Info "1. 删除图标缓存数据库…"
$cachePaths = @(
    "$env:LOCALAPPDATA\IconCache.db",
    "$env:LOCALAPPDATA\Microsoft\Windows\Explorer\iconcache_*.db",
    "$env:LOCALAPPDATA\Microsoft\Windows\Explorer\thumbcache_*.db"
)
$removed = 0
foreach ($pat in $cachePaths) {
    Get-ChildItem $pat -Force -EA SilentlyContinue | ForEach-Object {
        try {
            Remove-Item $_.FullName -Force -EA Stop
            $removed++
        } catch {
            # 被占用的删不掉，正常 —— explorer 重启后自己会重建
        }
    }
}
Ok "删了 $removed 个缓存文件"

Info "2. 重启 explorer.exe…"
$before = (Get-Process explorer -EA SilentlyContinue | Measure-Object).Count
Stop-Process -Name explorer -Force -EA SilentlyContinue
Start-Sleep -Seconds 2
if (-not (Get-Process explorer -EA SilentlyContinue)) {
    Start-Process explorer.exe
}
Start-Sleep -Seconds 3
$after = (Get-Process explorer -EA SilentlyContinue | Measure-Object).Count
Ok "explorer 已重启（$before → $after 个进程）"

Info "3. 收尾刷新…"
Start-Process ie4uinit.exe -ArgumentList "-show" -Wait -NoNewWindow -EA SilentlyContinue
Start-Sleep -Seconds 1

Write-Host ""
Write-Host ("=" * 56)
Write-Host "  完成。看桌面快捷方式，应该已经是橙色图标了。" -ForegroundColor Green
Write-Host ("=" * 56)
