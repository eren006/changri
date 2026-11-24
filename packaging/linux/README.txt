长日bot @VERSION@（Linux）

一键安装（推荐）：curl -fsSL @BASE@/get.sh | sudo sh

手动安装：
  sudo mkdir -p /opt/changri-bot
  sudo tar -xzf 本安装包.tar.gz -C /opt/changri-bot --strip-components=1
  sudo /opt/changri-bot/changri service install      # 设为开机自启并启动，会打印设置平台网址和首次设置验证码

常用：
  sudo ./changri doctor            自检（端口、能不能连上 QQ、公网 IP）
  ./changri run                    前台运行（不装开机自启时用；Ctrl+C 结束）
  sudo ./changri service status    看开机自启状态
  sudo ./changri reset-owner       主账号忘了密码：显示一个新密码，登录后到「账号」页改掉
  journalctl -u changri-bot -f     看日志
  数据在 data/ 里，备份 = 复制这个文件夹。升级在设置平台「更新」页一键完成。

云服务器要在安全组放行 TCP 5091（设置平台）和 5092（小手机，默认开着）。

使用说明：@BASE@/install
