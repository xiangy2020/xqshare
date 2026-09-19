# -*- coding: utf-8 -*-
"""
大QMT 退路通道封装 —— 复用开源 xtquant_big_convert（Redis RPC）。

把 xtquant_big_convert 客户端（bigqmt_signal_trader.xtquant_compat）封装成
xqshare 的「大QMT 通道」：

  - 数据源：BigQmtXtData（对应 xtdata，方法语义对齐）
  - 交易：BigQmtXtTrader（drop-in 替换 XtQuantTrader，构造 + 方法集对齐）
  - 回调适配：BigQmtCallbackAdapter 把 bigqmt 回调转发到 server 端事件路由
  - 下单闸门：XQSHARE_BIGQMT_ALLOW_ORDER 默认 false，拦全部写操作

仅当安装了 xtquant-big-convert 且 XQSHARE_BIGQMT_ENABLED=true 时可用。
"""

import logging
import os

logger = logging.getLogger("xqshare.channels.bigqmt")

# ---------------------------------------------------------------------------
# 导入 xtquant_big_convert（可选依赖，未安装时 BIGQMT_AVAILABLE=False）
# ---------------------------------------------------------------------------
try:
    from bigqmt_signal_trader.xtquant_compat import (
        BigQmtXtData,
        BigQmtXtTrader,
        XtQuantTraderCallback as _BigQmtTraderCallback,
        configure as _bigqmt_configure,
        xtdata as _bigqmt_xtdata_singleton,
    )
    BIGQMT_AVAILABLE = True
except ImportError:  # pragma: no cover - 依赖未安装
    BIGQMT_AVAILABLE = False
    BigQmtXtData = None
    BigQmtXtTrader = None
    _BigQmtTraderCallback = object
    _bigqmt_configure = None
    _bigqmt_xtdata_singleton = None


def _env_bool(name, default=False):
    """读布尔环境变量。"""
    raw = os.environ.get(name, "")
    if raw == "":
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def order_allowed():
    """xqshare 侧下单闸门（默认 false，双重门禁的第一道）。"""
    return _env_bool("XQSHARE_BIGQMT_ALLOW_ORDER", False)


# ---------------------------------------------------------------------------
# 合成事件对象（避免硬依赖 bigqmt 的 CompatObject，未安装时也可用）
# ---------------------------------------------------------------------------
class _SyntheticEvent:
    """轻量事件对象，字段即属性，可被 server 端 _serialize_object 序列化。"""

    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


# ---------------------------------------------------------------------------
# 下单闸门包装器：包在 BigQmtXtTrader 外，ALLOW_ORDER=false 时拦截写操作
# ---------------------------------------------------------------------------
_WRITE_METHODS = frozenset({
    "order_stock",
    "order_stock_result",
    "order_stock_async",
    "order_stock_batch",
    "cancel_order_stock",
    "cancel_order_stock_sysid",
    "cancel_order_stock_async",
    "cancel_order_stock_sysid_async",
    "smt_appointment_async",
})

_ORDER_DISABLED_MSG = (
    "下单被禁用：请设置 XQSHARE_BIGQMT_ALLOW_ORDER=true（且大QMT 端 "
    "rpc_allow_order_methods=true）后再下单"
)


class _GuardedTrader:
    """包在 BigQmtXtTrader 外，下单闸门关闭时拦截全部写操作。"""

    def __init__(self, trader):
        object.__setattr__(self, "_trader", trader)

    def __getattr__(self, name):
        if name in _WRITE_METHODS and not order_allowed():
            raise RuntimeError(_ORDER_DISABLED_MSG)
        return getattr(object.__getattribute__(self, "_trader"), name)

    def __setattr__(self, name, value):
        setattr(object.__getattribute__(self, "_trader"), name, value)

    def __dir__(self):
        return dir(object.__getattribute__(self, "_trader"))

    def __repr__(self):
        return repr(object.__getattribute__(self, "_trader"))


# ---------------------------------------------------------------------------
# 回调适配器：把 bigqmt 回调转发到 server 端事件路由
# ---------------------------------------------------------------------------
class BigQmtCallbackAdapter(_BigQmtTraderCallback):
    """实现 bigqmt XtQuantTraderCallback 全集，转发到 xqshare 事件路由。

    server 端 _TraderEventCallback 提供 on_stock_order/on_stock_trade/
    on_stock_asset/on_stock_position；本适配器补齐 bigqmt 额外的
    on_order_error/on_cancel_error/on_account_status/on_*_async_response。

    on_order_error / on_cancel_error 合成 order_status=57（废单）的
    on_stock_order 事件，字段对齐原生 XtOrder，供上层策略统一处理。
    """

    def __init__(self, server_event_callback, account_id="", logger=None):
        super().__init__()
        self._server_cb = server_event_callback
        self._account_id = str(account_id or "")
        self._log = (logger.info if logger is not None else logger) or (lambda msg: None)

    def on_stock_order(self, order):
        self._server_cb.on_stock_order(order)

    def on_stock_trade(self, trade):
        self._server_cb.on_stock_trade(trade)

    def on_order_error(self, order_error):
        self._server_cb.on_stock_order(_SyntheticEvent(
            account_id=self._account_id,
            stock_code=str(getattr(order_error, "stock_code", "") or ""),
            order_type=0,
            order_status=57,  # ORDER_JUNK（废单）
            order_volume=0,
            traded_volume=0,
            price=0.0,
            order_sysid=str(getattr(order_error, "order_sysid", "") or ""),
            order_id=str(getattr(order_error, "order_id", "") or ""),
            strategy_name=str(getattr(order_error, "strategy_name", "") or ""),
            order_remark=str(getattr(order_error, "order_remark", "") or ""),
            status_msg=str(getattr(order_error, "error_msg", "") or "委托失败"),
            order_time=0,
        ))

    def on_cancel_error(self, cancel_error):
        self._server_cb.on_stock_order(_SyntheticEvent(
            account_id=self._account_id,
            stock_code=str(getattr(cancel_error, "stock_code", "") or ""),
            order_type=0,
            order_status=57,  # ORDER_JUNK（撤单失败按废单处理）
            order_volume=0,
            traded_volume=0,
            price=0.0,
            order_sysid=str(getattr(cancel_error, "order_sysid", "") or ""),
            order_id=str(getattr(cancel_error, "order_id", "") or ""),
            strategy_name="",
            order_remark=str(getattr(cancel_error, "order_remark", "") or ""),
            status_msg="撤单失败: " + str(getattr(cancel_error, "error_msg", "") or ""),
            order_time=0,
        ))

    def on_account_status(self, status):
        self._log("bigqmt on_account_status: account=%s status=%s" % (
            getattr(status, "account_id", ""), getattr(status, "status", "")))

    def on_order_stock_async_response(self, response):
        self._log("bigqmt on_order_stock_async_response: seq=%s" % getattr(response, "seq", ""))

    def on_cancel_order_stock_async_response(self, response):
        self._log("bigqmt on_cancel_order_stock_async_response: seq=%s" % getattr(response, "seq", ""))

    def on_disconnected(self):
        self._log("bigqmt on_disconnected")


# ---------------------------------------------------------------------------
# 大QMT 通道（模块级单例，惰性配置）
# ---------------------------------------------------------------------------
class BigQmtChannel:
    """大QMT 通道统一入口。"""

    @classmethod
    def enabled(cls):
        return BIGQMT_AVAILABLE and _env_bool("XQSHARE_BIGQMT_ENABLED", False)

    @classmethod
    def account_id(cls):
        return os.environ.get("BIGQMT_ACCOUNT_ID", "").strip()

    @classmethod
    def _ensure_configured(cls):
        """重新 configure，读最新 BIGQMT_* 环境变量（load_dotenv 之后）。"""
        if not BIGQMT_AVAILABLE:
            return False
        try:
            _bigqmt_configure()
            return True
        except Exception as exc:  # pragma: no cover - 配置异常
            logger.warning("大QMT 通道配置失败: %s", exc)
            return False

    @classmethod
    def data_source(cls):
        """返回大QMT 数据源（BigQmtXtData 单例），未启用/不可用返回 None。"""
        if not cls.enabled():
            return None
        if not cls._ensure_configured():
            return None
        return _bigqmt_xtdata_singleton

    @classmethod
    def create_trader(cls, userdata_path=None, session_id=None):
        """创建大QMT 交易通道实例（已带下单闸门），drop-in 替换 XtQuantTrader。"""
        if not cls.enabled():
            raise RuntimeError("大QMT 通道未启用（XQSHARE_BIGQMT_ENABLED）")
        if not cls._ensure_configured():
            raise RuntimeError("大QMT 通道配置失败（检查 BIGQMT_* 环境变量）")
        trader = BigQmtXtTrader(path=userdata_path, session_id=session_id)
        return _GuardedTrader(trader)

    @classmethod
    def ping(cls, timeout=5.0):
        """RPC ping 探活：验证 Redis 连通 + 大QMT 策略进程存活。"""
        if not cls.enabled():
            return False
        if not cls.account_id():
            return False
        if not cls._ensure_configured():
            return False
        try:
            client = _bigqmt_xtdata_singleton.client
            return client.call("ping", {}, timeout_seconds=timeout) is not None
        except Exception:
            return False
