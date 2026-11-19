# 长日bot「连不上更新服务器（URLError）」一键诊断 + 修复（Windows）
# 用法：在服务器上打开 PowerShell，把整段粘贴进去回车。长日bot 开着最好；没开就先在长日bot 安装目录里打开 PowerShell。
# 用长日bot 自带的 Python（和在线更新同一个）先连更新清单、再试下载更新包，按报错分类：
#   证书缺根 → 自动修；时间不对 / 服务器出错 / 连接被干扰 / 网络不通 → 打印检查结果和结论，让用户截图发给作者。
# 一行用法（不需要管理员）：
#   [Net.ServicePointManager]::SecurityProtocol=3072; irm https://system.changri.work/fix/update.txt | iex
# 注意：用 irm 下载本脚本这一下，Windows 就会顺手把缺的 Let's Encrypt 根证书自动装上（事件 4097），
# 所以先查系统日志里最近 10 分钟有没有这条，有就说明原因是「缺根证书」、而且已经修好了。
try { [Console]::OutputEncoding = [Text.Encoding]::UTF8 } catch {}
[Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
$autoRoot = @(Get-WinEvent -FilterHashtable @{LogName='Application'; Id=4097; StartTime=(Get-Date).AddMinutes(-10)} -ErrorAction SilentlyContinue | Where-Object { $_.Message -match 'ISRG' }).Count -gt 0
$url = 'https://system.changri.work/latest.json'
$root = $null
$p = Get-Process python, pythonw -ErrorAction SilentlyContinue | Where-Object { $_.Path -match '\\runtime\\r[^\\]+\\pythonw?\.exe$' } | Select-Object -First 1
if ($p) { $root = Split-Path (Split-Path (Split-Path $p.Path)) } elseif (Test-Path '.\current') { $root = (Get-Location).Path }
if (-not $root) {
  # 没有管理员权限时看不到开机自启（系统身份）跑的长日bot 的路径：查开机自启任务登记的目录，再在桌面 / 下载 / 文档 / 各个盘往下两层找
  Write-Host '正在找长日bot 装在哪里……'
  $cands = @()
  try { foreach ($a in (Get-ScheduledTask -TaskName 'ChangriBot' -ErrorAction Stop).Actions) { $cands += $a.WorkingDirectory; if ($a.Execute) { $cands += (Split-Path $a.Execute.Trim('"')) } } } catch {}
  $bases = @("$HOME\Desktop", "$HOME\Downloads", "$HOME\Documents", $HOME) + @(Get-PSDrive -PSProvider FileSystem -ErrorAction SilentlyContinue | Where-Object { $_.Free -ne $null } | ForEach-Object { $_.Root })
  foreach ($b in $bases) {
    if ($b -and (Test-Path $b)) {
      foreach ($d in @(Get-ChildItem $b -Directory -ErrorAction SilentlyContinue)) {
        $cands += $d.FullName
        $cands += @(Get-ChildItem $d.FullName -Directory -ErrorAction SilentlyContinue | ForEach-Object { $_.FullName })
      }
    }
  }
  $root = $cands | Where-Object { $_ -and (Test-Path (Join-Path $_ 'current')) -and (Test-Path (Join-Path $_ 'versions')) } | Select-Object -First 1
}
$py = $null
if ($root) {
  $ver = (Get-Content "$root\current" -TotalCount 1 -ErrorAction SilentlyContinue)
  if ($ver) { $ver = $ver.Trim(); $rt = (Get-Content "$root\versions\$ver\RUNTIME" -TotalCount 1 -ErrorAction SilentlyContinue) }
  if ($rt) { $py = "$root\runtime\$($rt.Trim())\python.exe" }
}
if (-not $root) {
  Write-Host '找不到长日bot。请先把长日bot开着，或者在长日bot安装目录（有 Start-Changri.bat 的那个文件夹）里打开 PowerShell 再运行。' -ForegroundColor Red
} elseif (-not $py -or -not (Test-Path $py)) {
  Write-Host "长日bot 目录：$root"
  Write-Host '长日bot 的程序文件不完整（找不到自带的 Python）。请把这个窗口截图发给作者。' -ForegroundColor Red
} else {
  function Test-Update {
    $code = "import urllib.request as u, urllib.parse as P, json; H={'User-Agent':'changri-bot-updater'}; " +
            "m=json.loads(u.urlopen(u.Request('$url',headers=H),timeout=15).read()); print('OK manifest', m['version']); " +
            "r=u.urlopen(u.Request(P.urljoin('$url',m['app']['url']),headers=H),timeout=30); r.read(65536); print('OK download')"
    $out = & $py -I -c $code 2>&1 | ForEach-Object { "$_" }
    return @($out | Where-Object { $_ -match '^OK|Error|error' })
  }
  function Show-Result($lines) {
    $err = $lines | Where-Object { $_ -notmatch '^OK' } | Select-Object -Last 1
    if ($lines -contains 'OK download') { return '能连上更新服务器，也能下载更新包' }
    if ($lines -match '^OK manifest') { return "能连上更新服务器，但下载更新包失败：$err" }
    return $err
  }
  function Test-Tcp($h, $port) {
    $c = New-Object System.Net.Sockets.TcpClient
    try { return ($c.ConnectAsync($h, $port).Wait(8000) -and $c.Connected) } catch { return $false } finally { $c.Close() }
  }
  Write-Host "长日bot 目录：$root（版本 $ver）"
  $res = Test-Update
  $r1 = Show-Result $res
  Write-Host "检查结果：$r1"
  if ($res -contains 'OK download' -and $autoRoot) {
    Write-Host '原因：这台服务器缺少根证书（证书问题）。刚才运行这行命令时，Windows 已经自动把证书装好了。' -ForegroundColor Yellow
    Write-Host '已修好！去后台「更新」页点更新就行。' -ForegroundColor Green
  } elseif ($res -contains 'OK download') {
    Write-Host '一切正常，直接去后台「更新」页点更新就行。' -ForegroundColor Green
  } elseif ($r1 -match 'CERTIFICATE_VERIFY_FAILED' -and $r1 -match 'expired|not yet valid') {
    Write-Host ("原因：这台服务器的时间不对（现在显示 {0}）。把系统时间改对（设置 → 时间和语言 → 立即同步）再试。" -f (Get-Date -Format 'yyyy-MM-dd HH:mm')) -ForegroundColor Red
  } elseif ($r1 -match 'CERTIFICATE_VERIFY_FAILED' -and $r1 -match 'self.signed|self signed') {
    Write-Host '原因：连接被安全软件或代理「拦截检查」了（证书被换成了它自己的）。关掉安全软件的 HTTPS 扫描 / 代理再试，或者把这个窗口截图发给作者。' -ForegroundColor Red
  } elseif ($r1 -match 'CERTIFICATE_VERIFY_FAILED') {
    Write-Host '原因：这台服务器缺少根证书（证书问题）。正在修复……' -ForegroundColor Yellow
    try { Invoke-WebRequest $url -UseBasicParsing -TimeoutSec 20 | Out-Null } catch { Write-Host "修复时出错：$($_.Exception.Message)" -ForegroundColor Red }
    $res2 = Test-Update
    Write-Host "修复后再检查：$(Show-Result $res2)"
    if ($res2 -contains 'OK download') { Write-Host '已修好！去后台「更新」页点更新就行。' -ForegroundColor Green }
    else { Write-Host '还是不行：这台服务器可能关掉了 Windows 自动更新根证书。请把这个窗口截图发给作者。' -ForegroundColor Red }
  } elseif ($r1 -match 'HTTPError|HTTP Error') {
    Write-Host '原因：网络是通的，是更新服务器那边返回了错误，不是你这边的问题。请把这个窗口截图发给作者。' -ForegroundColor Red
  } else {
    Write-Host '是网络问题。正在检查网络……' -ForegroundColor Yellow
    $u = [uri]$url
    $ips = @(); try { $ips = @([System.Net.Dns]::GetHostAddresses($u.Host) | ForEach-Object { $_.IPAddressToString }) } catch {}
    $dnsOk = $ips.Count -gt 0
    Write-Host ("[1] 域名解析 {0}：{1}" -f $u.Host, $(if ($dnsOk) { $ips -join ', ' } else { '失败' }))
    $portOk = $dnsOk -and (Test-Tcp $u.Host $u.Port)
    Write-Host ("[2] 连更新服务器 {0} 端口：{1}" -f $u.Port, $(if ($portOk) { '通' } else { '不通' }))
    $netOk = (Test-Tcp 'www.baidu.com' 443) -or (Test-Tcp 'www.qq.com' 443)
    Write-Host ("[3] 连外网（百度 / QQ 443 端口）：{0}" -f $(if ($netOk) { '通' } else { '不通' }))
    $ie = Get-ItemProperty 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Internet Settings' -ErrorAction SilentlyContinue
    $pyProxy = (& $py -I -c "import urllib.request as u; print(u.getproxies() or '')" 2>&1 | Select-Object -Last 1)
    $proxyOn = ($ie -and $ie.ProxyEnable -eq 1) -or [string]$pyProxy -ne ''
    Write-Host ("[4] 系统代理：{0}" -f $(if ($proxyOn) { "开着（$($ie.ProxyServer) $pyProxy）" } else { '没开' }))
    $myip = $null
    foreach ($s in 'http://4.ipw.cn', 'http://ip.3322.net', 'http://ifconfig.me/ip') {
      try { $myip = (Invoke-RestMethod $s -TimeoutSec 6).ToString().Trim(); if ($myip) { break } } catch {}
    }
    Write-Host ("[5] 这台服务器的公网 IP：{0}" -f $(if ($myip) { $myip } else { '查不到' }))
    Write-Host ("[6] 这台服务器的时间：{0}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm'))
    Write-Host ''
    if (-not $dnsOk) {
      Write-Host '结论：域名解析失败（DNS 问题）。把这台服务器的 DNS 改成 223.5.5.5 和 119.29.29.29 再试。' -ForegroundColor Red
    } elseif ($proxyOn) {
      Write-Host '结论：开着系统代理。代理软件没开的话，到「设置 → 网络和 Internet → 代理」把「使用代理服务器」关掉再试。' -ForegroundColor Red
    } elseif (-not $netOk) {
      Write-Host '结论：这台服务器连外网都不通。检查阿里云安全组的「出方向」规则、服务器上的防火墙 / 安全软件。' -ForegroundColor Red
    } elseif (-not $portOk) {
      Write-Host '结论：外网是通的，只有更新服务器连不上，可能是更新服务器那边拦了这个 IP。请把这个窗口截图发给作者。' -ForegroundColor Red
    } elseif ($r1 -match 'SSL|EOF|reset|10054') {
      Write-Host '结论：网络是通的，但加密连接被中途打断了，多半是安全软件或代理在干扰。关掉安全软件的 HTTPS 扫描再试，或者把这个窗口截图发给作者。' -ForegroundColor Red
    } else {
      Write-Host '结论：网络都是通的，但长日bot 还是连不上，可能是安全软件拦了长日bot。请把这个窗口截图发给作者。' -ForegroundColor Red
    }
  }
}
