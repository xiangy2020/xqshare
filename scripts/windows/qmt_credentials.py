# -*- coding: utf-8 -*-
r"""QMT 登录凭据管理（Windows DPAPI 加密存储）。

用 Windows 数据保护 API（DPAPI）加密账号密码，绑定当前 Windows 用户身份，
只有本机当前用户能解密，无需管理密钥。避免密码明文出现在命令行/进程列表。

用法：
  python qmt_credentials.py set --account 25010003 --password 你的密码
  python qmt_credentials.py get     # 读取验证（密码打码显示）
  python qmt_credentials.py clear   # 删除凭据文件

被 qmt_autologin.py / qmt_mode_manager.py 复用：未传 --account/--password 时
自动从此加密文件读取。
"""
import argparse
import json
import os
import sys

CREDENTIALS_FILE = r"C:\Install\qmt_credentials.enc"


def _encrypt(data: bytes) -> bytes:
    import win32crypt
    return win32crypt.CryptProtectData(data, None, None, None, None, 0)


def _decrypt(blob: bytes) -> bytes:
    import win32crypt
    return win32crypt.CryptUnprotectData(blob, None, None, None, 0)[1]


def save_credentials(account: str, password: str):
    """加密保存账号密码到凭据文件。"""
    payload = json.dumps({"account": account, "password": password},
                         ensure_ascii=False).encode("utf-8")
    with open(CREDENTIALS_FILE, "wb") as f:
        f.write(_encrypt(payload))


def load_credentials():
    """读取并解密凭据，返回 (account, password)。文件不存在返回 (None, None)。"""
    if not os.path.exists(CREDENTIALS_FILE):
        return None, None
    try:
        with open(CREDENTIALS_FILE, "rb") as f:
            blob = f.read()
        data = json.loads(_decrypt(blob).decode("utf-8"))
        return data.get("account", "").strip(), data.get("password", "")
    except Exception:
        return None, None


def clear_credentials():
    if os.path.exists(CREDENTIALS_FILE):
        os.remove(CREDENTIALS_FILE)


def main():
    parser = argparse.ArgumentParser(description="QMT 登录凭据管理（DPAPI 加密）")
    parser.add_argument("action", choices=("set", "get", "clear"))
    parser.add_argument("--account", default="")
    parser.add_argument("--password", default="")
    args = parser.parse_args()

    if args.action == "set":
        if not args.account or not args.password:
            print("请用 --account 和 --password 传参")
            return 1
        save_credentials(args.account, args.password)
        print("凭据已加密保存:", CREDENTIALS_FILE)
        return 0

    if args.action == "clear":
        clear_credentials()
        print("凭据文件已删除")
        return 0

    # get
    account, password = load_credentials()
    if account is None:
        print("未找到凭据文件（请先 set）")
        return 1
    print("账号:", account)
    print("密码:", ("*" * len(password)) if password else "(空)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
