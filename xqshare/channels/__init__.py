# -*- coding: utf-8 -*-
"""
xqshare.channels —— 大QMT ↔ miniQMT 无感切换的通道抽象层。

复用开源 xtquant_big_convert（Redis RPC）作为大QMT 退路通道：

  - bigqmt.BigQmtChannel  大QMT 通道封装（数据源 + 交易 + 回调适配 + 下单闸门）
  - health.HealthProbe    健康探测（mini 进程/连通性 + bigqmt RPC ping）
  - router.ChannelRouter  通道路由（周期探测 + 抖动保护 + 通道选择）
"""

from .bigqmt import (
    BIGQMT_AVAILABLE,
    BigQmtCallbackAdapter,
    BigQmtChannel,
    order_allowed,
)
from .health import ChannelHealth, HealthProbe
from .router import ChannelRouter

__all__ = [
    "BIGQMT_AVAILABLE",
    "BigQmtCallbackAdapter",
    "BigQmtChannel",
    "ChannelHealth",
    "ChannelRouter",
    "HealthProbe",
    "order_allowed",
]
