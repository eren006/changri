#!/usr/bin/env python3
"""长日bot 修复工具：打包、签名、（作者同意后）发布到官网。跟长日bot 的版本无关，可以随时单独发。

    .venv/bin/python packaging/build_fix.py --init-key        # 第一次：生成修复工具的签名私钥（已有就不动）
    .venv/bin/python packaging/build_fix.py                   # 打包、签名 → release_out/fix/site/
    .venv/bin/python packaging/build_fix.py --test-key        # 用临时密钥打包，本机测试用，不能发布
    .venv/bin/python packaging/build_fix.py --publish         # 打包并传到 system.changri.work（对外发布，要作者说了才做）

产出（按官网上的路径放）：
  fix.sh                          Linux：curl -fsSL <官网>/fix.sh | sudo bash
  fix/index.html                  修复工具页（网址 /fix/）
  fix/changri-fix-windows.zip     Windows 修复工具：Fix-Changri.bat + launcher.ps1 + fix.ps1 + README.txt
  fix/fix.ps1 + fix/fix.ps1.sig   Windows 修复工具每次打开先拉这两个，验过签名才用（不用重新下载压缩包就能用上新的修复办法）

签名用单独的一把 RSA 密钥（~/.changri_release/fix_rsa.pem），不是更新包那把 Ed25519：
Windows 自带的 PowerShell 5.1（.NET Framework）验不了 Ed25519，RSA 可以。公钥写进 launcher.ps1。
私钥丢了：重新 --init-key 会换一把新的，已经下载的旧压缩包验不过新签名，会退回用它自带的那份（不会出错，只是用不上新办法）。
Linux 的一行命令是直接从官网拉脚本执行的，没有签名可验，靠的是 HTTPS。
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import io
import re
import sys
import time
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
FIX = HERE / "fix"
sys.path.insert(0, str(HERE))
import build  # noqa: E402  复用 fill / crlf / check_no_secrets / 缓存目录

DEFAULT_KEY = Path.home() / ".changri_release" / "fix_rsa.pem"
WIN_ZIP = "changri-fix-windows.zip"


def log(msg: str) -> None:
    print(msg, flush=True)


def fix_version() -> str:
    """日期 + 修复脚本内容的指纹：内容没变，版本号就不变。"""
    h = hashlib.sha256()
    for name in ("fix.ps1", "fix.sh", "launcher.ps1.in", "Fix-Changri.bat"):
        h.update((FIX / name).read_bytes())
    h.update((ROOT / "src" / "changri" / "platform" / "firewall_helper.sh").read_bytes())     # 嵌进 fix.sh 的放行脚本
    return time.strftime("%Y%m%d") + "-" + h.hexdigest()[:8]


# ---------- 密钥 ----------

def init_key(path: Path) -> None:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    if path.exists():
        log(f"已经有签名私钥：{path}（不动它）")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    key = rsa.generate_private_key(public_exponent=65537, key_size=3072)
    path.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    path.chmod(0o600)
    log(f"已生成修复工具的签名私钥：{path}。别丢，也别放进仓库。")


def load_key(path: Path, test: bool):
    from cryptography.hazmat.primitives import serialization
    if test:
        path = build.CACHE / "test_fix_rsa.pem"
        if not path.exists():
            init_key(path)
    elif not path.exists():
        sys.exit(f"找不到修复工具的签名私钥 {path}。第一次先运行：packaging/build_fix.py --init-key；本机测试加 --test-key。")
    return serialization.load_pem_private_key(path.read_bytes(), password=None)


def pubkey_xml(key) -> str:
    """.NET 的 RSA.FromXmlString 认的格式（PowerShell 5.1 能直接用）。"""
    n = key.public_key().public_numbers()

    def b64(i: int) -> str:
        return base64.b64encode(i.to_bytes((i.bit_length() + 7) // 8, "big")).decode()
    return f"<RSAKeyValue><Modulus>{b64(n.n)}</Modulus><Exponent>{b64(n.e)}</Exponent></RSAKeyValue>"


def sign(key, data: bytes) -> str:
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import padding
    return base64.b64encode(key.sign(data, padding.PKCS1v15(), hashes.SHA256())).decode()


# ---------- 打包 ----------

def utf8_bom_crlf(text: str) -> bytes:
    """PowerShell 5.1 读没有 BOM 的 UTF-8 会把中文读成乱码；Windows 的脚本也用 CRLF。"""
    return b"\xef\xbb\xbf" + build.crlf(text)


def build_site(key, base: str, out: Path) -> dict[str, Path]:
    ver = fix_version()
    site = out / "site"
    (site / "fix").mkdir(parents=True, exist_ok=True)
    ps1 = utf8_bom_crlf(build.fill((FIX / "fix.ps1").read_text(encoding="utf-8"), FIX_VERSION=ver))
    launcher = utf8_bom_crlf(build.fill((FIX / "launcher.ps1.in").read_text(encoding="utf-8"), BASE=base, FIX_PUBKEY_XML=pubkey_xml(key)))
    bat = build.crlf((FIX / "Fix-Changri.bat").read_text(encoding="utf-8"))           # .bat 不能带 BOM：第一行 @echo off 会被读坏
    readme = utf8_bom_crlf(build.fill((FIX / "README.txt").read_text(encoding="utf-8"), BASE=base))
    helper = (ROOT / "src" / "changri" / "platform" / "firewall_helper.sh").read_text(encoding="utf-8").rstrip("\n")
    sh = build.fill((FIX / "fix.sh").read_text(encoding="utf-8"), BASE=base, FIX_VERSION=ver, FW_HELPER_SCRIPT=helper).encode("utf-8")
    files = {"Fix-Changri.bat": bat, "launcher.ps1": launcher, "fix.ps1": ps1, "README.txt": readme, "fix.sh": sh}
    build.check_no_secrets(files)

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for name in ("Fix-Changri.bat", "launcher.ps1", "fix.ps1", "README.txt"):     # 文件名全是英文：Windows 自带的解压认不准 UTF-8 文件名
            info = zipfile.ZipInfo(f"changri-fix/{name}", date_time=time.localtime()[:6])
            info.compress_type = zipfile.ZIP_DEFLATED
            z.writestr(info, files[name])
    out_files = {
        "fix.sh": site / "fix.sh",
        "fix/fix.ps1": site / "fix" / "fix.ps1",
        "fix/fix.ps1.sig": site / "fix" / "fix.ps1.sig",
        f"fix/{WIN_ZIP}": site / "fix" / WIN_ZIP,
        "fix/index.html": site / "fix" / "index.html",
    }
    out_files["fix.sh"].write_bytes(sh)
    out_files["fix/fix.ps1"].write_bytes(ps1)
    out_files["fix/fix.ps1.sig"].write_text(sign(key, ps1), encoding="ascii")
    out_files[f"fix/{WIN_ZIP}"].write_bytes(buf.getvalue())
    install = (HERE / "site" / "install.html.in").read_text(encoding="utf-8")
    style = re.search(r"<style>.*?</style>", install, re.S).group(0)                 # 跟安装页同一套样式
    page = build.fill((FIX / "index.html.in").read_text(encoding="utf-8"), BASE=base, WIN_ZIP=WIN_ZIP,
                      WIN_SIZE=str(max(1, len(buf.getvalue()) // 1024)), FIX_VERSION=ver, STYLE=style)
    out_files["fix/index.html"].write_text(page, encoding="utf-8")
    log(f"修复工具 {ver}：{site}")
    for rel, p in out_files.items():
        log(f"  {rel}（{p.stat().st_size} 字节）")
    return out_files


# ---------- 发布（对外：作者说了才做） ----------

def publish(files: dict[str, Path], dry: bool) -> None:
    sys.path.insert(0, str(ROOT / "tools"))
    import release   # HOST / WEB / scp / ssh 和发版用同一套
    web = release.WEB
    release.ssh(f"sudo mkdir -p {web}/fix", dry=dry)
    tmp = "/tmp/_fix_upload"
    release.ssh(f"rm -rf {tmp} && mkdir -p {tmp}", dry=dry)
    for rel, p in files.items():
        release.scp(p, f"{tmp}/{rel.replace('/', '__')}", dry=dry)
    # fix.ps1 和它的签名必须一起换：先都传到临时目录，再一条命令一起挪过去
    moves = " && ".join(f"sudo cp {tmp}/{rel.replace('/', '__')} {web}/{rel} && sudo chmod a+r {web}/{rel}" for rel in files)
    release.ssh(f"{moves} && rm -rf {tmp}", dry=dry)
    log(f"已发布到 {release.BASE}/fix/ 和 {release.BASE}/fix.sh")


def main() -> None:
    ap = argparse.ArgumentParser(description="长日bot 修复工具：打包、签名、发布")
    ap.add_argument("--key", default=str(DEFAULT_KEY))
    ap.add_argument("--init-key", action="store_true", help="生成签名私钥（已有就不动）后退出")
    ap.add_argument("--test-key", action="store_true", help="用临时密钥：只给本机测试，不能发布")
    ap.add_argument("--base-url", default=build.BASE_URL)
    ap.add_argument("--out", default=str(ROOT / "release_out" / "fix"))
    ap.add_argument("--publish", action="store_true", help="打包后传到官网（对外发布）")
    ap.add_argument("--dry-run", action="store_true", help="--publish 时只打印要做什么")
    a = ap.parse_args()
    if a.init_key:
        init_key(Path(a.key))
        return
    if a.publish and a.test_key:
        sys.exit("测试密钥签的不能发布。")
    key = load_key(Path(a.key), a.test_key)
    files = build_site(key, a.base_url.rstrip("/"), Path(a.out) if not a.test_key else Path(a.out + "-test"))
    if a.publish:
        publish(files, a.dry_run)


if __name__ == "__main__":
    main()
