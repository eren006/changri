# 长日bot

一个独立的 QQ 官方机器人，用来跑「长日将尽」这类群恋综：角色、短信礼物、私约、心动信、关系线、论坛、季度，外加一部给玩家用的网页小手机。装好以后在网页后台管理，不用改代码。

一个机器人就是一套独立的系统，一台服务器上可以同时接好几个机器人，各管各的数据。

**安装包自带运行环境**：不用另外装 Python，也不用联网装依赖。支持 Windows 和 Linux（x86_64 / arm64）。

## 安装

完整说明（带下载按钮）：**https://system.changri.work/install**（海外也可以到 [GitHub Releases](https://github.com/eren006/changri/releases) 下载）

**Linux**：在服务器上粘贴这一行，回车：

```bash
curl -fsSL https://system.changri.work/get.sh | sudo sh
```

会自动下载、校验、装到 `/opt/changri-bot`、设为开机自启并启动，最后打印设置平台网址和首次设置验证码。装过老版本（0.19 及以前）的会自动把数据搬过来。需要 glibc 2.28 以上（Ubuntu 20.04+、Debian 10+、CentOS 8+）。

**Windows**：从安装页下载 zip，解压到固定位置（例如 `D:\changri-bot`），双击 `Start-Changri.bat`。想开机自动运行：右键 `Install-Autostart.bat`，选「以管理员身份运行」。

分步说明见 [docs/安装指南](docs/安装指南/)：[Linux](docs/安装指南/Linux.md) · [Windows](docs/安装指南/Windows.md) · [从老版本迁移](docs/安装指南/从老版本迁移.md)。

## 第一次使用

1. 云服务商控制台的「安全组 / 防火墙」放行 TCP **5091**（设置平台）；以后开小手机再放行 **5092**。
2. 浏览器打开 `http://服务器公网IP:5091`，输入安装时显示的验证码，建管理账号。
3. 跟着安装向导：填 AppID / AppSecret → 把服务器 IP 加进 QQ 开放平台的 IP 白名单 → 设 QQ 管理员 → 建第一个角色 → （可选）开小手机。

## 日常使用

| 想做的事 | 在哪里 |
|---|---|
| 管理角色、群、季度、心动信、论坛、群池 | 后台各页面 |
| 开关功能、调规则 | 后台「功能与设置」「扩展」 |
| 玩家的网页小手机 | 后台「小手机」页开启；所有机器人共用一个端口（默认 5092），玩家用激活码登录会自动进到自己的机器人 |
| 升级 | 后台「更新」一键升级（Linux 也可以 `sudo /opt/changri-bot/changri update`）。会先备份所有数据库；新版本起不来会自动退回旧版本 |
| 开 HTTPS（不用域名） | 后台「HTTPS」：先装 Caddy（Linux 粘贴页面上的命令；Windows 右键 `Install-HTTPS.bat` 以管理员身份运行），再点「开通」 |
| 自检 | Linux `sudo /opt/changri-bot/changri doctor`；Windows 双击 `Doctor.bat` |
| 备份 | 所有数据都在安装目录的 `data/` 里，复制它就是备份；也可以 `changri backup` |

## 安全提醒

- 后台默认是 http 明文。想加密就到后台「HTTPS」页一键开通；或在「远程访问」页只放行自己的 IP；或把 `data/config.toml` 里的 `host` 改成 `127.0.0.1` 只在本机打开。
- **扩展是 Python 代码，装上就有和主程序一样的权限**，只装信得过的人写的。
- 更新包有 Ed25519 签名，安装前校验，主页被改也推不进恶意代码。

## 常见问题

- **机器人一直不在线**：多半是 IP 白名单没加对。后台「机器人接入」点「检查连接」，它会告诉你该加哪个 IP。
- **玩家打不开小手机**：后台「小手机」页点「检查玩家能不能打开」。没通就是安全组没放行 5092。
- **看日志**：Linux `journalctl -u changri-bot -f`；Windows 双击运行时看那个窗口。
- **重启**：Linux `sudo systemctl restart changri-bot`；Windows 关掉窗口再双击 `Start-Changri.bat`（开机自启的在「任务计划程序」里结束再运行 ChangriBot）。

## 开发

```bash
python3.12 -m venv .venv && .venv/bin/pip install -e ".[dev]"
.venv/bin/python -m changri run                     # 前台运行（数据在 ./data）
.venv/bin/python -m unittest discover -s tests      # 全部测试
.venv/bin/python packaging/build.py --notes "…"     # 打包发布（要签名私钥），见 packaging/build.py 开头
```

代码结构、怎么加功能、怎么改数据库、怎么发版：[docs/架构.md](docs/架构.md)。写扩展：[src/changri/extdocs/写扩展.md](src/changri/extdocs/写扩展.md)。

## 仓库与下载

- **安装包**：只从 https://system.changri.work 下载（`get.sh`、`/downloads/`）。服务器上不存安装包，`/downloads/` 会跳转到 GitHub Release 的加速镜像；安装脚本和在线更新都校验 sha256 / 签名，不信任中转站。
- **公开仓库** https://github.com/eren006/changri ：放源码和每个版本的 Release 安装包，由作者在发新版后从开发仓库同步过来。装好之后升级走后台「更新」，不要在安装目录里 `git pull`。
- **提问题或建议**：到公开仓库提 Issue。开发过程在作者自己的私有仓库里进行，代码改动不接受直接推到公开仓库。
