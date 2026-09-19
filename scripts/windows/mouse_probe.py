# -*- coding: utf-8 -*-
r"""读取鼠标在登录框内的位置，用于精确定位 GUI 控件坐标。

GUI 自动化定位坐标的辅助工具：我看不到截图（多模态不可用）时，让用户在
RDP 里把鼠标移到目标控件上，本脚本自动读「相对窗口的比例坐标」，据此校准
自动化脚本的点击坐标。

用法：先运行本脚本，脚本会提示「把鼠标移到目标控件，4 秒后自动读取」，
循环 5 次。用户依次把鼠标移到需要标定的控件（账号框/密码框/勾选框/按钮）上。
"""
import ctypes
import sys
import time


def main():
    import win32gui

    prefix = "国金"
    matches = []

    def _cb(hwnd, _):
        if not win32gui.IsWindowVisible(hwnd):
            return
        title = win32gui.GetWindowText(hwnd) or ""
        cls = win32gui.GetClassName(hwnd)
        if cls == "Qt5QWindowIcon" and title.strip().startswith(prefix):
            r = win32gui.GetWindowRect(hwnd)
            if (r[2] - r[0]) < 800 and (r[3] - r[1]) < 600:
                matches.append((hwnd, r))

    win32gui.EnumWindows(_cb, None)
    if not matches:
        print("未找到登录框，请确认登录框已弹出")
        return 1

    hwnd, r = matches[0]
    w = r[2] - r[0]
    h = r[3] - r[1]
    print("登录框 rect=(%d,%d,%d,%d) 尺寸=%dx%d" % (r[0], r[1], r[2], r[3], w, h))

    for i in range(5):
        print("\n[%d/5] 把鼠标移到目标控件中心，%d 秒后自动读取..." % (i + 1, 4))
        time.sleep(4)
        pt = win32gui.GetCursorPos()
        rx = pt[0] - r[0]
        ry = pt[1] - r[1]
        print("    绝对屏幕坐标=(%d,%d) 相对登录框偏移=(%d,%d) 比例=(%.3f, %.3f)"
              % (pt[0], pt[1], rx, ry, rx / max(w, 1), ry / max(h, 1)))

    print("\n完成")
    return 0


if __name__ == "__main__":
    sys.exit(main())
