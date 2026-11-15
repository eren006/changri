# 在 Linux 服务器上安装长日bot

适用：Ubuntu 20.04+、Debian 10+、CentOS 8+（或其他 glibc 2.28 以上的系统），x86_64 或 arm64。2 核 2G 内存够用。

## 1. 一条命令安装

登录服务器（腾讯云可以在控制台点「登录」打开网页终端），粘贴下面这一行，回车：

```bash
curl -fsSL https://system.changri.work/get.sh | sudo sh
```

它会：

1. 按这台机器的类型下载安装包（自带 Python，不用另外装），校验完整；
2. 解压到 `/opt/changri-bot`，下载的压缩包装完就删；
3. 建一个专门运行它的系统用户 `changri`，设为开机自启并启动；
4. 在本机防火墙（ufw / firewalld，开着的话）放行 5091、5092；
5. 打印设置平台的网址和**首次设置验证码**——记下来。

想装到别的目录：`curl -fsSL https://system.changri.work/get.sh | sudo INSTALL_DIR=/srv/changri sh`

## 2. 放行端口

云服务商的「安全组」程序碰不到，要自己到网页控制台放行（腾讯云轻量服务器叫「防火墙」）：

| 端口 | 用途 | 什么时候需要 |
|---|---|---|
| TCP 5091 | 设置平台 | 一开始就要 |
| TCP 5092 | 玩家的小手机 | 开了小手机才需要（所有机器人共用这一个） |
| TCP 80、443 | 开 HTTPS 时申请证书 | 用「HTTPS」功能才需要 |

## 3. 打开设置平台

浏览器打开 `http://服务器公网IP:5091`，输入验证码，建管理账号，然后跟着安装向导走。

## 常用命令

```bash
sudo /opt/changri-bot/changri doctor          # 自检：端口、能不能连上 QQ、公网 IP
sudo /opt/changri-bot/changri update          # 升级（也可以在设置平台「更新」页点）
sudo systemctl restart changri-bot            # 重启（升级后要重启才生效；在网页上升级会自动重启）
journalctl -u changri-bot -f                  # 看日志
sudo /opt/changri-bot/changri backup          # 马上备份所有数据库到 data/backups/
sudo /opt/changri-bot/changri service uninstall   # 取消开机自启并停止（数据不动）
```

## 手动安装（不用一键脚本）

从 https://system.changri.work/install 下载对应的 `.tar.gz`，然后：

```bash
sudo mkdir -p /opt/changri-bot
sudo tar -xzf changri-bot-*-linux-x64.tar.gz -C /opt/changri-bot --strip-components=1
sudo /opt/changri-bot/changri service install
```

没有 systemd 的机器：`/opt/changri-bot/changri run` 前台运行（配合 screen / tmux）。

## 备份和搬家

所有数据都在 `/opt/changri-bot/data/`。备份 = 复制这个文件夹；搬到新服务器 = 新服务器装好后停掉服务（`sudo systemctl stop changri-bot`），把 `data/` 整个拷过去覆盖，再启动。
