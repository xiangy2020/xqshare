# -*- coding: utf-8 -*-
r"""截取国金 QMT 登录框并保存，用于定位账号/密码/独立交易/验证码框坐标。

输出已脱敏：截图会自动对「账号框」「密码框」区域打码（灰色遮罩），
避免泄露账号密码，只保留控件布局（标签/边框/按钮位置）用于坐标定位。

配合 mouse_probe.py 使用：截图留底 + mouse_probe 读鼠标坐标做精确标定。
"""
import ctypes
import sys

# 打码区域（相对登录框的比例坐标，x1,y1,x2,y2），遮住账号框和密码框
MASK_REGIONS = [
    (0.20, 0.39, 0.95, 0.47),   # 账号框
    (0.20, 0.64, 0.85, 0.72),   # 密码框
]


def main():
    import argparse
    import win32gui
    from PIL import Image, ImageDraw, ImageGrab

    parser = argparse.ArgumentParser(description="截取国金 QMT 登录框")
    parser.add_argument("--out", default=r"C:\Install\login_box.png",
                        help="输出文件路径（对比勾选/未勾选时用不同名字）")
    args = parser.parse_args()

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
                matches.append((hwnd, r, title))

    win32gui.EnumWindows(_cb, None)
    if not matches:
        print("未找到登录框（Qt5QWindowIcon 小窗），请确认登录框已弹出")
        return 1

    hwnd, r, title = matches[0]
    print("登录框: hwnd=%s rect=(%d,%d,%d,%d) title=%r" % (hwnd, r[0], r[1], r[2], r[3], title))

    img = ImageGrab.grab(bbox=(r[0], r[1], r[2], r[3]))
    w, h = img.size

    # 打码：遮住账号框和密码框
    draw = ImageDraw.Draw(img)
    for x1, y1, x2, y2 in MASK_REGIONS:
        draw.rectangle([x1 * w, y1 * h, x2 * w, y2 * h], fill=(128, 128, 128))

    img.save(args.out)
    print("已保存截图（账号/密码框已打码）:", args.out)
    print("尺寸:", img.size)
    return 0


if __name__ == "__main__":
    sys.exit(main())
