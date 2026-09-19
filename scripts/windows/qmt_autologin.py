# -*- coding: utf-8 -*-
r"""
国金 QMT 自动登录脚本（物理输入，必须在 RDP 交互会话里运行）。

模拟真实点击/打字登录 QMT 客户端。「独立交易」勾上后登录，客户端以
miniQMT 极简模式运行（XtItClient 主界面退出，只留 XtMiniQmt+miniquote，
xtdata 58610 可用）。

用法（在 RDP 会话里的命令行执行）：
  python qmt_autologin.py --account 25010003 --password 你的密码

流程：
  1. 启动 XtItClient.exe（如未运行）
  2. 等登录框出现（Qt5QWindowIcon 小窗，标题前缀「国金」）
  3. 点账号框 → 输账号；点密码框 → 剪贴板粘贴密码
  4. 点「独立交易」勾选框
  5. 回车登录
  6. 等 miniQMT 进程（XtMiniQmt/miniquote）+ 58610 就绪

验证码偶发：检测到登录框仍停留时提示人工处理（返回码 2）。

依赖：pywin32 + psutil + pillow（国金 venv 已装）。
"""
import argparse
import ctypes
import os
import sys
import time

# 登录框控件相对坐标（基于 624x419 实测截图，国金 2.0.8.300）
ACCOUNT_XY = (0.61, 0.43)              # 账号框中心（实测输入正确）
PASSWORD_XY = (0.434, 0.680)           # 密码框中心（mouse_probe 实测）
INDEPENDENT_TRADE_XY = (0.587, 0.752)  # 独立交易勾选框（mouse_probe 实测）
LOGIN_XY = (0.36, 0.82)                # 登录按钮（待实测，暂用 Enter 替代）

user32 = ctypes.windll.user32


def _find_login_window(prefix):
    import win32gui
    matches = []

    def _cb(hwnd, _):
        if not win32gui.IsWindowVisible(hwnd):
            return
        title = win32gui.GetWindowText(hwnd) or ""
        cls = win32gui.GetClassName(hwnd)
        if cls == "Qt5QWindowIcon" and title.strip().startswith(prefix):
            r = win32gui.GetWindowRect(hwnd)
            if (r[2] - r[0]) < 800 and (r[3] - r[1]) < 600:
                matches.append(hwnd)

    win32gui.EnumWindows(_cb, None)
    return matches[0] if matches else None


def _main_window_up(prefix):
    import win32gui
    found = []

    def _cb(hwnd, _):
        if not win32gui.IsWindowVisible(hwnd):
            return
        title = win32gui.GetWindowText(hwnd) or ""
        cls = win32gui.GetClassName(hwnd)
        if cls == "Qt5QWindowIcon" and title.strip().startswith(prefix):
            r = win32gui.GetWindowRect(hwnd)
            if (r[2] - r[0]) >= 800 or (r[3] - r[1]) >= 600:
                found.append(hwnd)

    win32gui.EnumWindows(_cb, None)
    return bool(found)


def _click(fx, fy):
    user32.SetCursorPos(fx, fy)
    time.sleep(0.15)
    user32.mouse_event(0x0002, 0, 0, 0, 0)  # LEFTDOWN
    time.sleep(0.05)
    user32.mouse_event(0x0004, 0, 0, 0, 0)  # LEFTUP
    time.sleep(0.3)


def _type(text):
    for ch in str(text):
        user32.keybd_event(ord(ch), 0, 0, 0)
        time.sleep(0.03)
        user32.keybd_event(ord(ch), 0, 2, 0)
        time.sleep(0.06)


def _select_all():
    import win32con
    user32.keybd_event(win32con.VK_CONTROL, 0, 0, 0)
    user32.keybd_event(ord("A"), 0, 0, 0)
    user32.keybd_event(ord("A"), 0, 2, 0)
    user32.keybd_event(win32con.VK_CONTROL, 0, 2, 0)
    time.sleep(0.2)


def _set_clipboard(text):
    import win32clipboard
    win32clipboard.OpenClipboard()
    try:
        win32clipboard.EmptyClipboard()
        win32clipboard.SetClipboardText(str(text))
    finally:
        win32clipboard.CloseClipboard()
    time.sleep(0.1)


def _paste():
    import win32con
    user32.keybd_event(win32con.VK_CONTROL, 0, 0, 0)
    user32.keybd_event(ord("V"), 0, 0, 0)
    user32.keybd_event(ord("V"), 0, 2, 0)
    user32.keybd_event(win32con.VK_CONTROL, 0, 2, 0)
    time.sleep(0.3)


def _enter():
    import win32con
    user32.keybd_event(win32con.VK_RETURN, 0, 0, 0)
    time.sleep(0.03)
    user32.keybd_event(win32con.VK_RETURN, 0, 2, 0)
    time.sleep(0.3)


def _wait_process(names, timeout=120):
    import psutil
    deadline = time.time() + timeout
    while time.time() < deadline:
        for p in psutil.process_iter(["name"]):
            try:
                n = (p.info["name"] or "").lower()
                for target in names:
                    if n == target or n == target + ".exe":
                        return True
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        time.sleep(1)
    return False


def _wait_port(port, timeout=120):
    import socket
    deadline = time.time() + timeout
    while time.time() < deadline:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(0.5)
        try:
            s.connect(("127.0.0.1", port))
            s.close()
            return True
        except OSError:
            s.close()
            time.sleep(1)
    return False


# 独立交易勾选状态记录（「独立交易」勾选持久化，checkbox 点击是 toggle，
# 无法可靠检测当前状态，故用状态文件记录脚本上次设置的状态，按需 toggle）
_STATE_FILE = r"C:\Install\.independent_trade_state"


def _read_independent_trade_state():
    try:
        with open(_STATE_FILE, "r", encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return "unchecked"  # 全新登录框默认未勾选


def _write_independent_trade_state(state):
    with open(_STATE_FILE, "w", encoding="utf-8") as f:
        f.write(state)


def _stop_qmt():
    """停止所有 QMT 相关进程（XtItClient/XtMiniQmt/miniquote）。"""
    import subprocess
    for name in ("XtItClient", "XtMiniQmt", "miniquote"):
        subprocess.call(["taskkill", "/F", "/IM", name + ".exe"],
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(3)


def _do_login(account, password, bin_dir, exe, prefix, click_independent):
    """执行一次完整登录。click_independent 表示是否点击独立交易 checkbox。"""
    import win32con
    import win32gui
    import psutil
    import subprocess

    # 1. 启动 XtItClient（如未运行）
    running = any(
        (p.info["name"] or "").lower() in ("xtitclient.exe", "xtitclient")
        for p in psutil.process_iter(["name"])
    )
    if not running:
        print("[1] 启动 XtItClient.exe ...")
        subprocess.Popen(
            [exe], cwd=bin_dir,
            creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP,
        )

    # 2. 等登录框
    print("[2] 等待登录框出现 ...")
    deadline = time.time() + 120
    handle = None
    while time.time() < deadline:
        if _main_window_up(prefix):
            print("[2] 主界面已出现（自动登录完成），无需输入凭据")
            return True
        handle = _find_login_window(prefix)
        if handle:
            break
        time.sleep(2)

    if handle is None:
        print("[2] 登录框未在 120s 内出现")
        return False

    user32.keybd_event(0x12, 0, 0, 0)  # Alt down
    user32.keybd_event(0x12, 0, 2, 0)  # Alt up
    time.sleep(0.2)
    win32gui.SetWindowPos(handle, win32con.HWND_TOPMOST, 0, 0, 0, 0,
                          win32con.SWP_NOMOVE | win32con.SWP_NOSIZE | win32con.SWP_SHOWWINDOW)
    win32gui.ShowWindow(handle, win32con.SW_RESTORE)
    user32.SetForegroundWindow(handle)
    time.sleep(0.8)
    if user32.GetForegroundWindow() != handle:
        print("[2] 无法将登录框置前，可能有遮挡窗口，请关闭后重试")
        return False

    rect = win32gui.GetWindowRect(handle)
    wx, wy = rect[0], rect[1]
    w = max(rect[2] - rect[0], 1)
    h = max(rect[3] - rect[1], 1)
    print("[2] 登录框 rect=(%d,%d,%d,%d)" % (rect[0], rect[1], rect[2], rect[3]))

    print("[3] 输入账号密码 ...")
    _click(wx + int(ACCOUNT_XY[0] * w), wy + int(ACCOUNT_XY[1] * h))
    _select_all()
    _type(account)
    _click(wx + int(PASSWORD_XY[0] * w), wy + int(PASSWORD_XY[1] * h))
    _set_clipboard(password)
    _paste()

    if click_independent:
        print("[3.5] 点击独立交易切换勾选 ...")
        _click(wx + int(INDEPENDENT_TRADE_XY[0] * w), wy + int(INDEPENDENT_TRADE_XY[1] * h))

    print("[4] 回车登录 ...")
    _enter()
    return True


def main():
    parser = argparse.ArgumentParser(description="国金 QMT 自动登录")
    parser.add_argument("--account", default=os.environ.get("QMT_LOGIN_ACCOUNT", ""))
    parser.add_argument("--password", default=os.environ.get("QMT_LOGIN_PASSWORD", ""))
    parser.add_argument("--dir", default=os.environ.get("QMT_INSTALL_DIR", r"C:\Install\gjzqqmt_bin"))
    parser.add_argument("--prefix", default=os.environ.get("QMT_WINDOW_PREFIX", "国金"))
    parser.add_argument("--mode", choices=("mini", "big"), default="mini",
                        help="mini=勾独立交易(miniQMT 模式，XtItClient 退出)；big=不勾独立交易(大QMT 模式)")
    parser.add_argument("--sync-state", choices=("checked", "unchecked"), default=None,
                        help="手动同步独立交易勾选状态（当状态记录失准时用）")
    args = parser.parse_args()

    # 手动同步状态
    if args.sync_state:
        _write_independent_trade_state(args.sync_state)
        print("独立交易状态已同步为:", args.sync_state)
        return 0

    account = (args.account or "").strip()
    password = args.password or ""
    # 未传账号密码时，从加密凭据文件读取（DPAPI）
    if not account or not password:
        _scripts_dir = os.path.dirname(os.path.abspath(__file__))
        if _scripts_dir not in sys.path:
            sys.path.insert(0, _scripts_dir)
        try:
            from qmt_credentials import load_credentials
            c_account, c_password = load_credentials()
            account = account or (c_account or "")
            password = password or (c_password or "")
            if account and password:
                print("[凭据] 已从加密配置文件读取账号密码")
        except Exception as exc:
            print("[凭据] 读取加密配置失败:", exc)
    bin_dir = os.path.join(args.dir, "bin.x64")
    exe = os.path.join(bin_dir, "XtItClient.exe")

    if not account or not password:
        print("缺少账号/密码：请用 --account/--password 传参，或先 qmt_credentials.py set")
        return 1

    import win32con
    import win32gui
    import psutil
    import subprocess

    target_state = "checked" if args.mode == "mini" else "unchecked"

    # 登录 + 验证 + 校正重试（最多 2 次：状态文件可能失准，第一次点反则反向重试）
    for attempt in range(2):
        if attempt > 0:
            print("\n=== 模式不符，校正状态并重试 ===\n")
            _stop_qmt()

        cur_state = _read_independent_trade_state()
        click_independent = (cur_state != target_state)
        print("[状态] 目标=%s 当前=%s %s" % (
            target_state, cur_state, "→点击切换" if click_independent else "→无需点击"))

        ok = _do_login(account, password, bin_dir, exe, args.prefix, click_independent)
        if not ok:
            print("登录流程失败")
            return 1
        if click_independent:
            _write_independent_trade_state(target_state)

        # 验证实际进入的模式：登录后 miniQMT 进程（XtMiniQmt/miniquote）是否起来
        print("[验证] 等待 8s 检测实际模式 ...")
        time.sleep(8)
        is_mini = _wait_process(["XtMiniQmt", "miniquote"], timeout=15)
        actual = "mini" if is_mini else "big"
        print("[验证] 实际模式 = %s (miniQMT 进程%s)" % (actual, "在跑" if is_mini else "未起"))

        if actual == args.mode:
            if args.mode == "mini":
                ok_port = _wait_port(58610, timeout=60)
                print("[5] 58610 端口%s" % ("已就绪" if ok_port else "未监听"))
                if ok_port:
                    print("SUCCESS: miniQMT 模式就绪")
                    return 0
            else:
                ok_port = _wait_port(58600, timeout=60)
                print("[5] FormulaServer 58600 端口%s" % ("已就绪" if ok_port else "未监听"))
                if ok_port:
                    print("SUCCESS: 大QMT 模式就绪")
                    return 0
            print("WARN: 端口未就绪，请检查 QMT 客户端状态")
            return 3
        else:
            # 点反了：反向校正状态文件
            _write_independent_trade_state("checked" if cur_state == "unchecked" else "unchecked")

    print("重试后仍未能进入目标模式，请人工检查登录框的独立交易状态")
    return 3


if __name__ == "__main__":
    sys.exit(main())
