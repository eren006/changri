"""长日bot。"""


def _ensure_ca_bundle() -> None:
    """自带的 Python（python-build-standalone）自己不带根证书：
    - Linux 只去 /etc/ssl/cert.pem 和 /etc/ssl/certs 找。Ubuntu / Debian 有；阿里云的 Alibaba Cloud Linux、CentOS、Rocky 等
      放在 /etc/pki/tls/...，找不到。
    - Windows 只读证书库里「已经装好」的根证书。Windows 自己是用到哪个根证书才临时下载哪个，Python 不会触发这个下载，
      所以刚装好的服务器（比如阿里云 Windows）上常常还没有 Let's Encrypt 的根证书。
    找不到 → 用 urllib 的地方（在线更新、公告、用量上报……）全部报 CERTIFICATE_VERIFY_FAILED（表现为 URLError）。
    httpx 等库自带 certifi 所以不受影响。这里在默认路径没有证书文件时，指给系统里别处的证书包，再不行用 certifi 的
    （Windows 上证书库照样会读，certifi 是补充）。"""
    import os
    import ssl
    if os.environ.get("SSL_CERT_FILE") or ssl.get_default_verify_paths().cafile:
        return
    for p in ("/etc/pki/tls/certs/ca-bundle.crt",                      # RHEL / CentOS / Alibaba Cloud Linux / Fedora
              "/etc/pki/ca-trust/extracted/pem/tls-ca-bundle.pem",
              "/etc/ssl/certs/ca-certificates.crt",                    # Debian / Ubuntu（以防 cert.pem 被删）
              "/etc/ssl/ca-bundle.pem"):                               # openSUSE
        if os.path.isfile(p):
            os.environ["SSL_CERT_FILE"] = p
            return
    try:
        import certifi
        os.environ["SSL_CERT_FILE"] = certifi.where()
    except ImportError:
        pass


_ensure_ca_bundle()
