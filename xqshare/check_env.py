# -*- coding: utf-8 -*-
"""
miniQMT 环境探测模块（从 QmtQuant/utils/check_env.py 集成）。

提供以下核心功能：
- get_miniqmt_info()       探测 miniQMT 进程和安装路径
- extract_account_from_title()  从窗口标题提取资金账号
- get_target_from_shortcut()    解析 .lnk 快捷方式目标路径

仅 Windows 下有效，非 Windows 平台调用将返回 not_found。
"""
import os
import sys
import subprocess


# ── Windows API 延迟初始化 ──────────────────────────────────────
_user32 = None


def _get_user32():
    """延迟加载 user32.dll（仅 Windows），避免非 Windows 平台 import 报错。"""
    global _user32
    if _user32 is not None:
        return _user32
    if sys.platform != "win32":
        return None
    try:
        import ctypes
        from ctypes import wintypes

        dll = ctypes.WinDLL("user32", use_last_error=True)
        dll.GetWindowThreadProcessId.argtypes = (
            wintypes.HWND, ctypes.POINTER(wintypes.DWORD),
        )
        dll.GetWindowTextLengthW.argtypes = (wintypes.HWND,)
        dll.GetWindowTextW.argtypes = (wintypes.HWND, wintypes.LPWSTR, ctypes.c_int)
        dll.EnumWindows.argtypes = (
            ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM),
            wintypes.LPARAM,
        )
        _user32 = dll
    except Exception:
        _user32 = None
    return _user32


# ── 窗口标题获取 ───────────────────────────────────────────────
def get_window_title_by_pid(pid):
    """枚举所有窗口，找到属于指定 PID 的主窗口标题（仅 Windows）。"""
    user32 = _get_user32()
    if not user32:
        return None
    try:
        import ctypes
        from ctypes import wintypes

        titles = []

        def callback(hwnd, _lparam):
            pid_ptr = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid_ptr))
            if pid_ptr.value == pid:
                length = user32.GetWindowTextLengthW(hwnd)
                if length > 0:
                    buf = ctypes.create_unicode_buffer(length + 1)
                    user32.GetWindowTextW(hwnd, buf, length + 1)
                    t = buf.value.strip()
                    if t:
                        titles.append(t)
            return True

        WNDENUMPROC = ctypes.WINFUNCTYPE(
            ctypes.c_bool, wintypes.HWND, wintypes.LPARAM,
        )
        user32.EnumWindows(WNDENUMPROC(callback), 0)
        return titles[0] if titles else None
    except Exception:
        return None


# ── 快捷方式解析 ───────────────────────────────────────────────
def get_target_from_shortcut(lnk_path):
    """解析 .lnk 快捷方式的目标路径（仅 Windows）。"""
    if sys.platform != "win32":
        return None
    cmd = (
        f'powershell -command '
        f'"$s=(New-Object -ComObject WScript.Shell).CreateShortcut(\'{lnk_path}\'); '
        f'$s.TargetPath"'
    )
    try:
        result = subprocess.run(
            cmd, shell=True, capture_output=True, text=True, timeout=5,
        )
        return result.stdout.strip() if result.returncode == 0 else None
    except Exception:
        return None


# ── 核心：探测 miniQMT ─────────────────────────────────────────
def get_miniqmt_info():
    """
    检查 miniQMT 是否运行，返回安装目录和窗口标题。

    返回 dict：
      install_dir    str   安装目录（bin.x64），如 'C:\\install\\国金证券QMT交易端\\bin.x64'
                           未找到时为 '未找到'
      process_name   str   窗口标题 或 '未运行'
      status         str   'running' | 'installed' | 'not_found'
    """
    if sys.platform != "win32":
        return {"install_dir": "未找到", "process_name": "未运行", "status": "not_found"}

    try:
        import psutil
    except ImportError:
        return {"install_dir": "未找到", "process_name": "未运行", "status": "not_found"}

    # 1. 尝试从进程列表查找
    for proc in psutil.process_iter(["name", "exe", "pid"]):
        if (proc.info["name"] or "").lower() == "xtminiqmt.exe":
            try:
                exe_path = proc.info["exe"] or ""
                pid = proc.info["pid"]
                install_dir = os.path.dirname(exe_path) if exe_path else None
                window_title = get_window_title_by_pid(pid)
                return {
                    "install_dir": install_dir or "未找到",
                    "process_name": window_title or f"XtMiniQmt.exe (PID: {pid})",
                    "status": "running",
                }
            except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
                continue

    # 2. 快捷方式 fallback（遍历桌面上的 .lnk 文件）
    desktop = os.path.expanduser("~/Desktop")
    if os.path.isdir(desktop):
        for fname in os.listdir(desktop):
            if fname.lower().endswith(".lnk") and "qmt" in fname.lower():
                shortcut_path = os.path.join(desktop, fname)
                target = get_target_from_shortcut(shortcut_path)
                if target and os.path.exists(target):
                    install_dir = os.path.dirname(target)
                    return {
                        "install_dir": install_dir,
                        "process_name": "未运行",
                        "status": "installed",
                    }

    return {"install_dir": "未找到", "process_name": "未运行", "status": "not_found"}


# ── 提取资金账号 ───────────────────────────────────────────────
def extract_account_from_title(title):
    """
    从窗口标题提取账号，例如 '55011888 - 国金QMT交易端模拟 1.0.0.36251'。
    注意：平安QMT窗口标题显示的是登录用户名（如 P_YP001），不是资金账号，
    返回 None 触发手动输入。
    """
    if title and " - " in title:
        account = title.split(" - ")[0].strip()
        if account.isdigit():  # 只接受纯数字的资金账号
            return account
    return None