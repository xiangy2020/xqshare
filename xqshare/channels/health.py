# -*- coding: utf-8 -*-
"""
健康探测 —— 探测 miniQMT 与大QMT 通道可用性。

  - mini：进程 XtMiniQmt.exe + xtdata 连通性（get_trading_calendar 探活）
  - bigqmt：RPC ping（验证 Redis 连通 + 大QMT 策略进程存活）

探测结果汇成 ChannelHealth，供 ChannelRouter 做路由决策。
"""

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict

logger = logging.getLogger("xqshare.channels.health")


@dataclass
class ChannelHealth:
    """一次探测的通道健康状态。"""

    mini_available: bool = False
    bigqmt_available: bool = False
    checked_at: float = 0.0
    detail: Dict[str, Any] = field(default_factory=dict)

    @property
    def mode(self) -> str:
        """生效通道：mini 优先，其次 bigqmt，否则 none。"""
        if self.mini_available:
            return "mini"
        if self.bigqmt_available:
            return "bigqmt"
        return "none"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "mode": self.mode,
            "mini_available": self.mini_available,
            "bigqmt_available": self.bigqmt_available,
            "checked_at": self.checked_at,
            "detail": dict(self.detail),
        }


# ---------------------------------------------------------------------------
# mini 探测
# ---------------------------------------------------------------------------
def _probe_mini_process() -> bool:
    """进程探测：XtMiniQmt.exe 是否在运行（非 Windows 恒 False）。"""
    try:
        from xqshare.check_env import get_miniqmt_info
        return get_miniqmt_info().get("status") == "running"
    except Exception:
        return False


def _probe_xtdata_connectivity(timeout: float = 5.0) -> bool:
    """xtdata 连通性探活：get_full_tick 轻量调用，线程超时保护。

    注意：不用 get_trading_calendar —— 实测平安 miniQMT 对它返回
    「功能未实现」（func:commonControl function not realize），会误判
    mini 不可用；get_full_tick 单标的快照是联调验证过的可靠探活。
    """
    try:
        import xtquant.xtdata as xtdata
    except ImportError:
        return False

    result: list = []

    def _run():
        try:
            result.append(bool(xtdata.get_full_tick(["000001.SZ"])))
        except Exception:
            result.append(False)

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    t.join(timeout)
    if t.is_alive():
        return False
    return bool(result and result[0])


def probe_mini() -> bool:
    """mini 通道可用 = 进程在运行 且 xtdata 连通。"""
    process = _probe_mini_process()
    connectivity = _probe_xtdata_connectivity() if process else False
    return bool(process and connectivity)


# ---------------------------------------------------------------------------
# bigqmt 探测
# ---------------------------------------------------------------------------
def probe_bigqmt() -> bool:
    """bigqmt 通道可用 = RPC ping 成功（验证 Redis + 大QMT 策略进程）。"""
    from .bigqmt import BigQmtChannel
    return BigQmtChannel.ping()


# ---------------------------------------------------------------------------
# 统一探测器
# ---------------------------------------------------------------------------
class HealthProbe:
    """探测器：mini 与 bigqmt 各自可用性，可注入 mock 探测函数便于测试。"""

    def __init__(
        self,
        mini_probe_func: Callable[[], bool] = None,
        bigqmt_probe_func: Callable[[], bool] = None,
    ):
        self._mini_probe_func = mini_probe_func or probe_mini
        self._bigqmt_probe_func = bigqmt_probe_func or probe_bigqmt

    def probe(self) -> ChannelHealth:
        mini = False
        bigqmt = False
        detail: Dict[str, Any] = {}

        try:
            mini = bool(self._mini_probe_func())
        except Exception as exc:  # 探测异常不应打崩探测线程
            logger.warning("mini 探测异常: %s", exc)
        detail["mini"] = mini

        try:
            bigqmt = bool(self._bigqmt_probe_func())
        except Exception as exc:
            logger.warning("bigqmt 探测异常: %s", exc)
        detail["bigqmt"] = bigqmt

        return ChannelHealth(
            mini_available=mini,
            bigqmt_available=bigqmt,
            checked_at=time.time(),
            detail=detail,
        )
