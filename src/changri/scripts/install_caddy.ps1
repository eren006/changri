# 给长日bot 装 Caddy（一键 HTTPS 用，Windows 版）。由 install_caddy.bat 以管理员身份调用，不要单独运行。
# 做的事：下载 caddy.exe（2.10 以上）→ 注册成开机自启的 Windows 服务「ChangriCaddy」（接口模式，只在本机 127.0.0.1:2019 收配置）→ Windows 防火墙放行端口。
# 之后在设置平台的「HTTPS」页点「开通」，配置由程序推给 Caddy。重复运行是安全的（会升级到最新版）。
$ErrorActionPreference = "Continue"     # 不用 Stop：PowerShell 5 下外部程序（netsh、caddy）写一行错误输出就会被当成致命错误；要捕获的命令单独加 -ErrorAction Stop
[Console]::OutputEncoding = [Text.Encoding]::UTF8
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
$ProgressPreference = "SilentlyContinue"

$Dir = Join-Path $env:ProgramData "ChangriCaddy"
$Exe = Join-Path $Dir "caddy.exe"
$Svc = "ChangriCaddy"
$Min = [version]"2.10.0"

function Fail($msg) { Write-Host $msg; exit 1 }

function Get-CaddyVersion($path) {
  if (-not (Test-Path $path)) { return $null }
  $out = & $path version 2>$null
  if ($out -match "v(\d+\.\d+\.\d+)") { return [version]$Matches[1] }
  return $null
}

function Test-Api {
  try { Invoke-WebRequest -UseBasicParsing -TimeoutSec 5 -ErrorAction Stop "http://127.0.0.1:2019/config/" | Out-Null; return $true } catch { return $false }
}

$existing = Get-Service -Name $Svc -ErrorAction SilentlyContinue
# 2019 端口上已经有一个不是我们装的 Caddy：可能在跑别人的网站，不接管
if (-not $existing -and (Test-Api)) {
  Fail "这台服务器上已经有一个 Caddy 在运行（本机 2019 端口），为了不弄坏它，脚本不接管。先停掉它再重新运行。"
}

switch ($env:PROCESSOR_ARCHITECTURE) {
  "AMD64" { $Arch = "amd64" }
  "ARM64" { $Arch = "arm64" }
  default { Fail "不认识的 CPU 架构 $($env:PROCESSOR_ARCHITECTURE)" }
}

New-Item -ItemType Directory -Force -Path $Dir | Out-Null
$tmp = Join-Path $Dir "caddy.download.exe"
Remove-Item $tmp -ErrorAction SilentlyContinue
Write-Host "下载 Caddy 程序……"
try {
  Invoke-WebRequest -UseBasicParsing -TimeoutSec 180 -ErrorAction Stop -OutFile $tmp "https://caddyserver.com/api/download?os=windows&arch=$Arch"
} catch {
  Write-Host "官网下载不通，改从 GitHub 下载……"
  try {
    $tag = (Invoke-RestMethod -TimeoutSec 30 -ErrorAction Stop "https://api.github.com/repos/caddyserver/caddy/releases/latest").tag_name
    $zip = Join-Path $Dir "caddy.zip"
    Invoke-WebRequest -UseBasicParsing -TimeoutSec 300 -ErrorAction Stop -OutFile $zip "https://github.com/caddyserver/caddy/releases/download/$tag/caddy_$($tag.TrimStart('v'))_windows_$Arch.zip"
    $unz = Join-Path $Dir "unzip"
    Remove-Item $unz -Recurse -ErrorAction SilentlyContinue
    Expand-Archive -Path $zip -DestinationPath $unz -Force -ErrorAction Stop
    Move-Item -Force -ErrorAction Stop (Join-Path $unz "caddy.exe") $tmp
    Remove-Item $zip, $unz -Recurse -ErrorAction SilentlyContinue
  } catch {
    Fail "没能下载 Caddy，请检查这台服务器能不能上网。（$($_.Exception.Message)）"
  }
}
$v = Get-CaddyVersion $tmp
if (-not $v) { Remove-Item $tmp -ErrorAction SilentlyContinue; Fail "下载的文件不能运行。" }
if ($v -lt $Min) { Remove-Item $tmp -ErrorAction SilentlyContinue; Fail "下载到的 Caddy 是 $v，需要 2.10 以上。" }

if ($existing -and $existing.Status -ne "Stopped") { Stop-Service -Name $Svc -Force; Start-Sleep -Seconds 2 }
try { Move-Item -Force -ErrorAction Stop $tmp $Exe } catch { Fail "替换 caddy.exe 失败（可能还在运行）：$($_.Exception.Message)" }
Write-Host "已装好 Caddy $v（$Exe）。"

if (-not $existing) {
  # Caddy 自己支持作为 Windows 服务运行；--resume 记住设置平台推过来的配置，重启后照旧
  # 用 New-Service 而不是 sc.exe：Windows PowerShell 5 给外部程序传带引号的参数会传坏
  try {
    New-Service -ErrorAction Stop -Name $Svc -BinaryPathName "`"$Exe`" run --resume" -StartupType Automatic `
      -DisplayName "Caddy（长日bot 的 HTTPS）" -Description "长日bot 的 HTTPS。配置由设置平台通过本机 127.0.0.1:2019 下发，不要手动改。" | Out-Null
  } catch { Fail "注册 Windows 服务失败：$($_.Exception.Message)" }
  & sc.exe failure $Svc reset= 86400 actions= restart/5000/restart/5000/restart/30000 | Out-Null
}
try { Start-Service -Name $Svc -ErrorAction Stop } catch { Fail "Caddy 服务没能启动：$($_.Exception.Message)" }
Start-Sleep -Seconds 3
if (-not (Test-Api)) { Fail "Caddy 没有正常启动。打开「事件查看器 → Windows 日志 → 应用程序」看看 Caddy 的报错。" }

# Windows 防火墙：80 和 443（证书验证，有一个能通就行）、5443（设置平台 https）、6092-6199（各机器人小手机的 https）
& netsh advfirewall firewall delete rule name="ChangriHTTPS" 2>$null | Out-Null
& netsh advfirewall firewall add rule name="ChangriHTTPS" dir=in action=allow protocol=TCP localport="80,443,5443,6092-6199" | Out-Null
if ($LASTEXITCODE -eq 0) { Write-Host "已在 Windows 防火墙放行 80、443、5443、6092-6199。" }

Write-Host ""
Write-Host "Caddy 已就绪。回到设置平台的「HTTPS」页刷新，点「开通 HTTPS」。"
Write-Host "别忘了到云服务商的安全组放行 80、443 和 5443（以及小手机的 https 端口，页面上会列出来）。"
Write-Host "停用：sc.exe stop $Svc　　卸载：sc.exe delete $Svc，再删掉 $Dir"
