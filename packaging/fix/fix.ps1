# 长日bot 修复工具（Windows）：长日bot 打不开时双击 Fix-Changri.bat 运行它。
# 由 launcher.ps1 调用：先从官网拉最新的这份（带签名，验过才用），拉不到就用压缩包里自带的。
# 只修「打不开」这一类：已经在运行 → 打开设置平台；卡死 → 全部关掉重新打开；位置被别的软件占了 → 问过再关；没在运行 → 启动它。
# 要兼容 Windows 自带的 PowerShell 5.1：不用 ?? / 三元运算符这类新语法。
param([string]$ToolDir = $PSScriptRoot)

$FixVersion = '@FIX_VERSION@'
$TaskName = 'ChangriBot'
$DefaultPort = 5091
try { [Console]::OutputEncoding = [Text.Encoding]::UTF8 } catch {}

function Say([string]$t, [string]$color = 'Gray') { Write-Host $t -ForegroundColor $color }
function Ok([string]$t) { Say "  √ $t" 'Green' }
function Warn([string]$t) { Say "  ! $t" 'Yellow' }
function Bad([string]$t) { Say "  × $t" 'Red' }
function AskYes([string]$q) {
    $a = Read-Host $q
    return @('y', 'yes', '是', '好') -contains ("$a".Trim().ToLower())
}

# ---------- 找长日bot ----------

function Get-ChangriProcs {
    # 长日bot 的进程：安装目录 runtime 里的 python.exe，命令行是「-m changri …」（看门进程和干活的都算）
    Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
        Where-Object { "$($_.CommandLine)" -match '-m\s+changri' }
}

function Get-RootFromExe([string]$exe) {
    # ...\<安装目录>\runtime\<编号>\python.exe → <安装目录>
    if (-not $exe) { return $null }
    $rt = Split-Path (Split-Path $exe -Parent) -Parent
    if ((Split-Path $rt -Leaf) -ne 'runtime') { return $null }
    $root = Split-Path $rt -Parent
    if (Test-Path (Join-Path $root 'Start-Changri.bat')) { return $root }
    return $null
}

function Get-TaskRoot {
    # 开机自启（计划任务 ChangriBot）指向的 Start-Changri.bat
    $t = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if (-not $t) { return $null }
    foreach ($a in $t.Actions) {
        $exe = "$($a.Execute)".Trim('"', ' ')
        if ($exe -match 'Start-Changri\.bat$' -and (Test-Path $exe)) { return (Split-Path $exe -Parent) }
    }
    return $null
}

function Find-Roots {
    $roots = New-Object System.Collections.Generic.List[string]
    foreach ($p in @(Get-ChangriProcs)) {
        $r = Get-RootFromExe $p.ExecutablePath
        if ($r -and -not $roots.Contains($r)) { $roots.Add($r) }
    }
    $r = Get-TaskRoot
    if ($r -and -not $roots.Contains($r)) { $roots.Add($r) }
    foreach ($d in @($ToolDir, (Split-Path $ToolDir -Parent))) {    # 修复工具解压在安装目录里、或者旁边
        if ($d -and (Test-Path (Join-Path $d 'Start-Changri.bat')) -and -not $roots.Contains($d)) { $roots.Add($d) }
    }
    return $roots
}

function Ask-Root {
    Warn '没找到长日bot 装在哪里。'
    while ($true) {
        $p = "$(Read-Host '请把长日bot 文件夹里的 Start-Changri.bat 拖进这个窗口，然后按回车（直接按回车 = 不修了）')".Trim().Trim('"')
        if (-not $p) { return $null }
        if (Test-Path $p -PathType Leaf) { $p = Split-Path $p -Parent }
        if (Test-Path (Join-Path $p 'Start-Changri.bat')) { return $p }
        Bad '这个文件夹里没有 Start-Changri.bat，再试一次。'
    }
}

function Get-Port([string]$root) {
    $f = Join-Path $root 'data\config.toml'
    if (Test-Path $f) {
        $m = [regex]::Match((Get-Content $f -Raw -Encoding UTF8), '(?m)^\s*port\s*=\s*(\d+)')
        if ($m.Success) { return [int]$m.Groups[1].Value }
    }
    return $DefaultPort
}

# ---------- 看状态 ----------

function Test-Healthy([int]$port) {
    try {
        $r = Invoke-WebRequest "http://127.0.0.1:$port/healthz" -UseBasicParsing -TimeoutSec 3
        return ($r.StatusCode -eq 200 -and "$($r.Content)".Trim() -eq 'ok')
    } catch { return $false }
}

function Get-ListenerPid([int]$port) {
    $c = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($c) { return [int]$c.OwningProcess }
    foreach ($line in (netstat -ano -p TCP)) {             # 老系统没有 Get-NetTCPConnection
        $f = -split "$line"
        if ($f.Count -ge 5 -and $f[1] -match ":$port$" -and $f[3] -eq 'LISTENING') { return [int]$f[4] }
    }
    return $null
}

function Get-Proc([int]$id) { Get-CimInstance Win32_Process -Filter "ProcessId=$id" -ErrorAction SilentlyContinue }

function Test-IsChangri($p) { return ($p -and "$($p.Name)" -eq 'python.exe' -and "$($p.CommandLine)" -match '-m\s+changri') }

function Wait-Healthy([int]$port, [int]$secs) {
    for ($i = 0; $i -lt $secs; $i += 2) {
        if (Test-Healthy $port) { return $true }
        Write-Host -NoNewline '.'
        Start-Sleep -Seconds 2
    }
    Write-Host ''
    return (Test-Healthy $port)
}

function Wait-PortFree([int]$port, [int]$secs = 15) {
    for ($i = 0; $i -lt $secs; $i++) {
        if (-not (Get-ListenerPid $port)) { return $true }
        Start-Sleep -Seconds 1
    }
    return -not (Get-ListenerPid $port)
}

# ---------- 关掉、打开 ----------

function Stop-Tree([int]$id) {
    if ($id -eq $PID) { return $false }
    & taskkill.exe /PID $id /T /F *> $null
    return ($LASTEXITCODE -eq 0)
}

function Stop-AllChangri {
    # 长日bot 的所有进程，连同开它的 Start-Changri.bat 窗口（不然窗口里的循环会马上再开一个）
    $killed = 0
    $schtasksDone = $false
    foreach ($p in @(Get-ChangriProcs)) {
        $top = $p
        for ($i = 0; $i -lt 4; $i++) {
            $parent = Get-Proc $top.ParentProcessId
            if (-not $parent -or $parent.ProcessId -eq $PID) { break }
            if ((Test-IsChangri $parent) -or ("$($parent.Name)" -eq 'cmd.exe' -and "$($parent.CommandLine)" -match 'Start-Changri\.bat')) { $top = $parent } else { break }
        }
        if (-not $schtasksDone) { & schtasks.exe /End /TN $TaskName *> $null; $schtasksDone = $true }   # 开机自启那份先停掉（不然它会再拉起）
        if (Get-Proc $top.ProcessId) {
            if (Stop-Tree $top.ProcessId) { $killed++ } else { Bad "关不掉进程 $($top.ProcessId)" }
        }
    }
    return $killed
}

function Start-ChangriBot([string]$root) {
    $t = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if ($t -and $t.State -ne 'Disabled') {
        & schtasks.exe /Run /TN $TaskName *> $null                    # 装了开机自启：用它启动（后台跑，关掉窗口也不停）
        if ($LASTEXITCODE -eq 0) { Ok '已经用「开机自启」的方式在后台启动长日bot'; return }
    }
    Start-Process -FilePath (Join-Path $root 'Start-Changri.bat') -WorkingDirectory $root
    Ok '已经打开长日bot 的窗口（以后别关掉它，关掉长日bot 就停了）'
}

function Show-LastError([string]$root) {
    $f = Join-Path $root 'data\errors.jsonl'
    if (-not (Test-Path $f)) { return }
    $last = Get-Content $f -Encoding UTF8 -Tail 1
    try {
        $e = $last | ConvertFrom-Json
        Say "  最近一次报错：$($e.source)：$($e.message)" 'Yellow'
    } catch {}
}

function Open-Admin([int]$port) {
    Start-Process "http://127.0.0.1:$port/"
    Ok "设置平台已经在浏览器里打开：http://127.0.0.1:$port/"
}

# ---------- 主流程 ----------

Say ''
Say "  长日bot 修复工具（${FixVersion}）" 'Cyan'
Say '  ----------------------------------------'
$roots = @(Find-Roots)
if ($roots.Count -eq 0) {
    $one = Ask-Root
    if (-not $one) { return }
    $roots = @($one)
}
$root = $roots[0]
if ($roots.Count -gt 1) {
    Warn "这台电脑上找到 $($roots.Count) 份长日bot：$($roots -join '；')。先修第一份。"
}
$port = Get-Port $root
Say "  长日bot 装在：$root"
Say "  设置平台的端口：$port"
Say ''

if (Test-Healthy $port) {
    Ok '长日bot 正在正常运行，没有问题。'
    Open-Admin $port
    return
}

$holder = Get-ListenerPid $port
$hp = $null
if ($holder) { $hp = Get-Proc $holder }

if ($hp -and -not (Test-IsChangri $hp)) {
    $name = "$($hp.Name)"
    if (-not $name) { $name = "PID $holder" }
    Bad "长日bot 需要的位置（$port 端口）被电脑上的「${name}」占着，所以长日bot 打不开。"
    if ($hp.ExecutablePath) { Say "    它在：$($hp.ExecutablePath)" }
    if (-not (AskYes "  要关掉「${name}」、让长日bot 正常打开吗？输入 Y 再按回车 = 关掉；直接按回车 = 不关")) {
        Warn "没有关。也可以打开 $root\data\config.toml，把 port = $port 改成别的数字（比如 $($port + 10)），保存后重新打开长日bot，以后用新端口访问设置平台。"
        return
    }
    if (-not (Stop-Tree $holder) -or -not (Wait-PortFree $port)) {
        Bad "没能关掉「${name}」。重启电脑后再运行一次这个修复工具。"
        return
    }
    Ok "已经关掉「${name}」"
}
elseif ($hp -or @(Get-ChangriProcs).Count -gt 0) {
    Warn '长日bot 开着，但是没有反应。再等它一会儿'
    if (Wait-Healthy $port 30) {
        Ok '等到了，长日bot 正常了。'
        Open-Admin $port
        return
    }
    Warn '长日bot 卡住了，帮你把它全部关掉、重新打开……'
    $n = Stop-AllChangri
    if (-not (Wait-PortFree $port)) {
        Bad '没能关掉卡住的长日bot。重启电脑后再运行一次这个修复工具。'
        return
    }
    Ok "已经关掉卡住的长日bot（$n 个）"
}
else {
    Warn '长日bot 没有在运行。'
}

Start-ChangriBot $root
Say '  等长日bot 启动（最多 2 分钟）'
if (Wait-Healthy $port 120) {
    Ok '修好了，长日bot 正常运行。'
    Open-Admin $port
    return
}
Bad '长日bot 还是没能打开。'
Show-LastError $root
Say '  请把这个窗口截图发给维护的人。' 'Yellow'
return
