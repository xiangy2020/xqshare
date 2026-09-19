# -*- coding: utf-8 -*-
r"""QMT 模式管理：探测当前模式 + 切换 miniQMT ↔ 大QMT。

「独立交易」= miniQMT 模式，与大QMT 互斥：
  - mini 模式（勾独立交易）：XtMiniQmt + miniquote 在跑（xtdata 58610），XtItClient 退出
  - big 模式（不勾独立交易）：XtItClient 在跑（FormulaServer 58600），策略自动恢复，miniQMT 不起

用法（在 RDP 交互会话里执行，登录动作需要物理输入）：
  python qmt_mode_manager.py status
  python qmt_mode_manager.py mini --account 25010003 --password 你的密码
  python qmt_mode_manager.py big --account 25010003 --password 你的密码

依赖：本模块 + 同目录 qmt_autologin.py（登录动作复用其逻辑）。
"""
import argparse
import os
import subprocess
import sys
import time


def detect_mode():
    """探测当前 QMT 模式：'mini' | 'big' | 'none'。"""
    import psutil
    procs = set()
    for p in psutil.process_iter(["name"]):
        try:
            n = (p.info["name"] or "").lower()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
        if n in ("xtminiqmt.exe", "miniquote.exe"):
            procs.add("mini")
        elif n in ("xtitclient.exe",):
            procs.add("big")
    if "mini" in procs:
        return "mini"
    if "big" in procs:
        return "big"
    return "none"


def switch_to(mode, account, password, install_dir, prefix):
    """切到目标模式：复用 qmt_autologin.py 的登录逻辑（含验证 + 自动校正）。"""
    script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "qmt_autologin.py")
    cmd = [
        sys.executable, script,
        "--mode", mode,
        "--account", account,
        "--password", password,
        "--dir", install_dir,
        "--prefix", prefix,
    ]
    return subprocess.call(cmd)


def watch(mode, account, password, install_dir, prefix, interval):
    """常驻守护：周期检测目标模式进程，异常退出自动重新登录拉起。"""
    # 防重复：启动前先清理旧的 watch 实例（排除自身），避免 ONLOGON 多次触发累积
    killed = _kill_watch_processes()
    if killed:
        print("已清理旧的 watch 实例:", killed, "个")
    print("守护启动：目标模式=%s 检测间隔=%ss（Ctrl+C 停止）" % (mode, interval))
    while True:
        try:
            cur = detect_mode()
            if cur != mode:
                print("[%s] 检测到模式 %s（期望 %s），自动拉起 ..." % (
                    time.strftime("%H:%M:%S"), cur, mode))
                switch_to(mode, account, password, install_dir, prefix)
            else:
                print("[%s] 模式正常（%s）" % (time.strftime("%H:%M:%S"), mode))
        except KeyboardInterrupt:
            print("\n守护已停止")
            return 0
        except Exception as exc:
            print("[%s] 检测异常: %s" % (time.strftime("%H:%M:%S"), exc))
        time.sleep(interval)


# schtasks 任务名（watch 常驻注册时的固定任务名）
WATCH_TASK_NAME = "qmt-watch-mini"


def _kill_watch_processes():
    """杀掉正在运行的 watch 守护进程（排除自身）。"""
    import psutil
    me = os.getpid()
    killed = 0
    for p in psutil.process_iter(["name", "cmdline"]):
        try:
            name = (p.info["name"] or "").lower()
            cmdline = " ".join(p.info["cmdline"] or [])
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
        if name == "python.exe" and "qmt_mode_manager.py watch" in cmdline and p.pid != me:
            try:
                p.kill()
                killed += 1
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
    return killed


def watch_stop():
    """临时取消常驻拉起：禁用 schtasks 任务 + 杀掉 watch 进程。可随时 watch-start 恢复。"""
    subprocess.call(["schtasks", "/Change", "/TN", WATCH_TASK_NAME, "/DISABLE"],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    print("已禁用 schtasks 任务:", WATCH_TASK_NAME)
    killed = _kill_watch_processes()
    print("已停止 watch 进程:", killed, "个")
    print("（恢复：qmt_mode_manager.py watch-start）")
    return 0


def watch_start(run_now=False):
    """恢复常驻拉起：启用 schtasks 任务（可选立即触发一次）。"""
    subprocess.call(["schtasks", "/Change", "/TN", WATCH_TASK_NAME, "/ENABLE"],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    print("已启用 schtasks 任务:", WATCH_TASK_NAME)
    if run_now:
        subprocess.call(["schtasks", "/Run", "/TN", WATCH_TASK_NAME],
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        print("已立即触发一次")
    else:
        print("（下次登录时自动触发；或 schtasks /Run /TN %s 手动触发）" % WATCH_TASK_NAME)
    return 0


def main():
    parser = argparse.ArgumentParser(description="QMT 模式管理（探测 + 切换 + 守护）")
    parser.add_argument("action", choices=("status", "mini", "big", "watch", "watch-stop", "watch-start"))
    parser.add_argument("--account", default=os.environ.get("QMT_LOGIN_ACCOUNT", ""))
    parser.add_argument("--password", default=os.environ.get("QMT_LOGIN_PASSWORD", ""))
    parser.add_argument("--dir", default=os.environ.get("QMT_INSTALL_DIR", r"C:\Install\gjzqqmt_bin"))
    parser.add_argument("--prefix", default=os.environ.get("QMT_WINDOW_PREFIX", "国金"))
    parser.add_argument("--mode", choices=("mini", "big"), default="mini",
                        help="watch 时守护的目标模式")
    parser.add_argument("--interval", type=int, default=30,
                        help="watch 检测间隔（秒）")
    parser.add_argument("--run-now", action="store_true",
                        help="watch-start 时立即触发一次")
    args = parser.parse_args()

    if args.action == "status":
        print("当前 QMT 模式:", detect_mode())
        return 0

    if args.action == "watch-stop":
        return watch_stop()

    if args.action == "watch-start":
        return watch_start(run_now=args.run_now)

    account = (args.account or "").strip()
    password = args.password or ""
    # 未传账号密码时，从加密凭据文件读取（DPAPI）
    if not account or not password:
        try:
            from qmt_credentials import load_credentials
            c_account, c_password = load_credentials()
            account = account or (c_account or "")
            password = password or (c_password or "")
            if account and password:
                print("[凭据] 已从加密配置文件读取账号密码")
        except Exception as exc:
            print("[凭据] 读取加密配置失败:", exc)
    if not account or not password:
        print("切换/守护需要账号密码：请用 --account/--password 传参，或先 qmt_credentials.py set")
        return 1

    if args.action == "watch":
        return watch(args.mode, account, password, args.dir, args.prefix, args.interval)

    print("切换到 %s 模式 ..." % args.action)
    return switch_to(args.action, account, password, args.dir, args.prefix)


if __name__ == "__main__":
    sys.exit(main())
