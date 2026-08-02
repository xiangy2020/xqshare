# -*- coding: utf-8 -*-
"""
qmt_watchdog.py — Windows QMT/miniQMT 守护进程
================================================
持续监控以下两个进程，崩溃后自动重启：
  1. XtMiniQmt.exe       — 迅投 miniQMT 行情/交易终端
  2. xqshare server      — 跨平台 RPC 桥接服务（python -m xqshare.server）

用法（在 Windows 端激活虚拟环境后执行）：
  python config/qmt_watchdog.py

.env 配置项（放置于项目根目录 .env）：
  QMT_EXE_PATH              miniQMT 可执行文件路径（必填，缺失则跳过 miniQMT 监控）
                            示例：QMT_EXE_PATH=C:\\国金证券QMT交易端\\bin.x64\\XtItClient.exe
  WATCHDOG_CHECK_INTERVAL   检查间隔秒数（可选，默认 30）
  WATCHDOG_MAX_RETRIES      最大连续重启失败次数（可选，默认 3）
  WATCHDOG_STARTUP_WAIT     进程启动后等待就绪的秒数（可选，默认 5）

注意：此脚本仅支持在 Windows 上运行。
"""

import sys
import os
import time
import signal
import logging
import subprocess
from logging.handlers import RotatingFileHandler

# ============================================================
# 任务 5：Windows 平台检查（在所有导入之前尽早检查）
# ============================================================
if sys.platform != "win32":
    print("此脚本仅支持在 Windows 上运行。")
    sys.exit(0)

# Windows 专属依赖（psutil）
try:
    import psutil
except ImportError:
    print("缺少依赖 psutil，请执行：pip install psutil")
    sys.exit(1)

# ============================================================
# 任务 1：配置加载模块
# ============================================================

def _find_project_root() -> str:
    """
    从脚本所在目录（config/）向上查找项目根目录。
    判断依据：目录中存在 .env 文件，或存在 config/ 子目录。
    与 check_env.py 保持一致的路径查找逻辑。
    """
    current = os.path.dirname(os.path.abspath(__file__))
    # config/ 的上一级即为项目根目录
    parent = os.path.dirname(current)
    return parent


def _read_env(env_path: str) -> dict:
    """解析 .env 文件，返回 key→value 字典（忽略注释和空行）"""
    result = {}
    if not os.path.exists(env_path):
        return result
    with open(env_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" not in line:
                continue
            key, _, val = line.partition("=")
            result[key.strip()] = val.strip()
    return result


def load_config() -> dict:
    """
    从项目根目录 .env 加载守护进程配置。
    返回包含以下键的字典：
      qmt_exe_path        str | None
      check_interval      int
      max_retries         int
      startup_wait        int
      project_root        str
      env_path            str
    """
    project_root = _find_project_root()
    env_path = os.path.join(project_root, ".env")
    env = _read_env(env_path)

    qmt_exe_path = env.get("QMT_EXE_PATH", "").strip() or None

    config = {
        "qmt_exe_path": qmt_exe_path,
        "check_interval": int(env.get("WATCHDOG_CHECK_INTERVAL", 30)),
        "max_retries": int(env.get("WATCHDOG_MAX_RETRIES", 3)),
        "startup_wait": int(env.get("WATCHDOG_STARTUP_WAIT", 5)),
        "project_root": project_root,
        "env_path": env_path,
    }
    return config


# ============================================================
# 任务 2：日志系统
# ============================================================

def setup_logger(project_root: str) -> logging.Logger:
    """
    配置双通道 Logger：
      - 控制台（StreamHandler）
      - 文件（RotatingFileHandler，5 MB / 最多 3 个历史文件）
    日志文件路径：<project_root>/logs/qmt_watchdog.log
    """
    logs_dir = os.path.join(project_root, "logs")
    os.makedirs(logs_dir, exist_ok=True)
    log_file = os.path.join(logs_dir, "qmt_watchdog.log")

    logger = logging.getLogger("qmt_watchdog")
    logger.setLevel(logging.DEBUG)

    fmt = logging.Formatter(
        fmt="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # 控制台 Handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(fmt)

    # 文件 Handler（自动轮转）
    file_handler = RotatingFileHandler(
        log_file,
        maxBytes=5 * 1024 * 1024,  # 5 MB
        backupCount=3,
        encoding="utf-8",
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(fmt)

    logger.addHandler(console_handler)
    logger.addHandler(file_handler)
    return logger


# ============================================================
# 任务 3：ProcessWatcher 类
# ============================================================

class ProcessWatcher:
    """
    监控单个目标进程，崩溃后自动重启。

    支持两种匹配模式：
      - match_by_name：按进程名匹配（用于 XtMiniQmt.exe）
      - match_by_cmdline：按命令行参数包含指定字符串匹配（用于 xqshare.server）
    """

    def __init__(
        self,
        name: str,
        logger: logging.Logger,
        max_retries: int,
        startup_wait: int,
        process_name: str = None,
        cmdline_keyword: str = None,
        start_cmd: list = None,
        start_cwd: str = None,
    ):
        """
        :param name:            友好名称，用于日志显示
        :param logger:          Logger 实例
        :param max_retries:     连续重启失败上限
        :param startup_wait:    启动后等待就绪的秒数
        :param process_name:    按进程名匹配（如 'XtMiniQmt.exe'）
        :param cmdline_keyword: 按命令行参数匹配（如 'xqshare.server'）
        :param start_cmd:       启动命令列表（如 ['python', '-m', 'xqshare.server']）
        :param start_cwd:       启动时的工作目录
        """
        self.name = name
        self.logger = logger
        self.max_retries = max_retries
        self.startup_wait = startup_wait
        self.process_name = process_name
        self.cmdline_keyword = cmdline_keyword
        self.start_cmd = start_cmd
        self.start_cwd = start_cwd

        self._last_running: bool = None   # 上一次检测到的运行状态（None 表示首次）
        self._fail_count: int = 0         # 连续重启失败次数
        self.suspended: bool = False      # 是否已暂停重启

    def is_running(self) -> bool:
        """检测目标进程是否存在"""
        try:
            for proc in psutil.process_iter(["name", "cmdline"]):
                try:
                    if self.process_name:
                        if proc.info["name"] and \
                                proc.info["name"].lower() == self.process_name.lower():
                            return True
                    elif self.cmdline_keyword:
                        cmdline = proc.info.get("cmdline") or []
                        if any(self.cmdline_keyword in arg for arg in cmdline):
                            return True
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    continue
        except Exception as e:
            self.logger.error(f"[{self.name}] 进程检测异常: {e}")
        return False

    def try_restart(self) -> bool:
        """
        尝试重启目标进程。
        返回 True 表示启动命令已成功发出（不代表进程最终存活）。
        """
        if not self.start_cmd:
            self.logger.warning(f"[{self.name}] 未配置启动命令，跳过重启")
            return False

        try:
            self.logger.info(f"[{self.name}] 正在启动: {' '.join(self.start_cmd)}")
            subprocess.Popen(
                self.start_cmd,
                cwd=self.start_cwd,
                creationflags=subprocess.CREATE_NEW_CONSOLE,  # Windows：新控制台窗口
            )
            self.logger.info(
                f"[{self.name}] 启动命令已发出，等待 {self.startup_wait} 秒就绪..."
            )
            time.sleep(self.startup_wait)
            return True
        except FileNotFoundError:
            self.logger.error(f"[{self.name}] 启动失败：可执行文件不存在 → {self.start_cmd[0]}")
        except PermissionError:
            self.logger.error(f"[{self.name}] 启动失败：权限不足")
        except Exception as e:
            self.logger.error(f"[{self.name}] 启动失败：{e}")
        return False

    def check_and_recover(self):
        """
        执行一次检测 + 恢复逻辑：
          1. 检测进程是否存活
          2. 记录状态变化日志
          3. 若不存活且未暂停，尝试重启并更新失败计数
        """
        if self.suspended:
            return

        running = self.is_running()

        # 状态变化日志
        if self._last_running is None:
            # 首次检测，仅记录初始状态
            status_str = "运行中" if running else "未运行"
            self.logger.info(f"[{self.name}] 初始状态：{status_str}")
        elif running and not self._last_running:
            self.logger.info(f"[{self.name}] 进程已恢复运行")
            self._fail_count = 0  # 恢复后重置失败计数
        elif not running and self._last_running:
            self.logger.warning(f"[{self.name}] 进程已退出，准备重启...")

        self._last_running = running

        # 进程不存活时尝试重启
        if not running:
            success = self.try_restart()
            if not success:
                self._fail_count += 1
                self.logger.error(
                    f"[{self.name}] 重启失败（连续失败 {self._fail_count}/{self.max_retries} 次）"
                )
                if self._fail_count >= self.max_retries:
                    self.suspended = True
                    self.logger.critical(
                        f"[{self.name}] 连续重启失败已达上限 {self.max_retries} 次，"
                        f"暂停重启，请人工介入检查！"
                    )
            else:
                # 启动命令发出成功，重置失败计数
                self._fail_count = 0


# ============================================================
# 任务 4：主守护循环与优雅退出
# ============================================================

_stop_flag = False


def _handle_signal(signum, frame):
    """SIGTERM / SIGINT 信号处理器，设置停止标志"""
    global _stop_flag
    _stop_flag = True


def main():
    # ── 加载配置 ──────────────────────────────────────────────
    config = load_config()

    # ── 初始化日志 ────────────────────────────────────────────
    logger = setup_logger(config["project_root"])

    # ── 注册信号处理 ──────────────────────────────────────────
    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    # ── 构建监控目标列表 ──────────────────────────────────────
    watchers = []

    # 1. miniQMT 监控
    qmt_exe = config["qmt_exe_path"]
    if qmt_exe and os.path.exists(qmt_exe):
        watchers.append(ProcessWatcher(
            name="miniQMT",
            logger=logger,
            max_retries=config["max_retries"],
            startup_wait=config["startup_wait"],
            process_name="XtMiniQmt.exe",
            start_cmd=[qmt_exe],
            start_cwd=os.path.dirname(qmt_exe),
        ))
    else:
        if not qmt_exe:
            logger.warning("QMT_EXE_PATH 未配置，跳过 miniQMT 监控")
        else:
            logger.warning(f"QMT_EXE_PATH 路径不存在：{qmt_exe}，跳过 miniQMT 监控")

    # 2. xqshare server 监控
    watchers.append(ProcessWatcher(
        name="xqshare-server",
        logger=logger,
        max_retries=config["max_retries"],
        startup_wait=config["startup_wait"],
        cmdline_keyword="xqshare.server",
        start_cmd=[sys.executable, "-m", "xqshare.server"],
        start_cwd=config["project_root"],
    ))

    # ── 启动摘要日志 ──────────────────────────────────────────
    target_names = [w.name for w in watchers]
    logger.info(
        f"守护进程已启动 | "
        f"检查间隔={config['check_interval']}s | "
        f"最大重试={config['max_retries']} | "
        f"启动等待={config['startup_wait']}s | "
        f"监控目标={target_names}"
    )
    logger.info(f"配置文件：{config['env_path']}")

    # ── 主守护循环 ────────────────────────────────────────────
    global _stop_flag
    try:
        while not _stop_flag:
            for watcher in watchers:
                if _stop_flag:
                    break
                watcher.check_and_recover()
            # 分段 sleep，使 Ctrl+C 能快速响应
            for _ in range(config["check_interval"]):
                if _stop_flag:
                    break
                time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        logger.info("守护进程已停止")


# ============================================================
# 任务 5：脚本入口
# ============================================================
if __name__ == "__main__":
    main()
