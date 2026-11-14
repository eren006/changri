# 报错接收服务部署（只在主页服务器 system.changri.work 上做一次）

长日bot 出错或崩溃后，恢复时会把报错（已抹掉密钥和长数字，不含聊天内容）POST 到 `https://system.changri.work/api/errors`。这个服务负责收下来存成文件。

1. 把 `tools/errors_service.py` 上传到服务器，例如 `/opt/changri_errors/errors_service.py`。
2. 建数据目录：`sudo mkdir -p /var/lib/changri_errors && sudo chown www-data /var/lib/changri_errors`
3. systemd 单元 `/etc/systemd/system/changri-errors.service`（口令自己编一串，只写在服务器这个文件里，不要进仓库；不想开放查看就删掉 TOKEN 那行，直接 `tail` 数据文件）：
```
[Unit]
Description=changri error receiver
After=network.target
[Service]
Environment=CHANGRI_ERRORS_DIR=/var/lib/changri_errors
Environment=CHANGRI_ERRORS_TOKEN=换成你自己的口令
ExecStart=/usr/bin/python3 /opt/changri_errors/errors_service.py
Restart=always
User=www-data
[Install]
WantedBy=multi-user.target
```
4. nginx 的 system.changri.work 站点里加：
```
location /api/errors {
    proxy_pass http://127.0.0.1:5198;
    proxy_set_header X-Real-IP $remote_addr;
    client_max_body_size 64k;
}
```
5. `sudo systemctl enable --now changri-errors && sudo nginx -t && sudo systemctl reload nginx`
6. 验证：
```
curl -s -XPOST https://system.changri.work/api/errors -H 'Content-Type: application/json' -d '{"app":"test","errors":[{"ts":1,"kind":"error","source":"t","message":"hello"}]}'
curl -s https://system.changri.work/api/errors -H 'Authorization: Bearer 你的口令'
```

只收文本，请求体最多 64KB、每次最多 50 条，每个来源每分钟最多 10 次；数据文件超过 5MB 自动换新文件。
