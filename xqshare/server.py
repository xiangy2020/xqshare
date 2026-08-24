"""
XtQuant Share (xqshare) Server - Run on Windows to provide xtquant proxy service
"""

import rpyc
from rpyc.utils.server import ThreadedServer
import time
import os
import sys
import ssl
import inspect
import logging
import functools
import json
import threading
from datetime import datetime
from typing import Any, Dict, Optional

# 导入权限模块
from .auth import (
    PermissionChecker,
    PermissionError,
    AccountLevel,
    Permission,
    get_permission_checker,
)

# Import xtquant (only available on Windows)
try:
    import xtquant.xtdata as xtdata
    import xtquant.xttrader as xttrader
    import xtquant.xttype as xttype
    import xtquant.xtconstant as xtconstant
    from xtquant.xttrader import XtQuantTrader
    XTQUANT_AVAILABLE = True
except ImportError:
    XTQUANT_AVAILABLE = False
    xtdata = None
    xttrader = None
    xttype = None
    xtconstant = None
    XtQuantTrader = None

# xtview 模块单独导入（某些版本可能不存在）
try:
    import xtquant.xtview as xtview
    XTVIEW_AVAILABLE = True
except ImportError:
    xtview = None
    XTVIEW_AVAILABLE = False

# QmtDataReader 导入（用于 datadir 文件解析能力）
try:
    from xqshare.qmt_datadir import QmtDataReader
    QMTDATAREADER_AVAILABLE = True
except ImportError:
    QmtDataReader = None
    QMTDATAREADER_AVAILABLE = False


# ==================== 日志配置 ====================

def setup_logging(log_dir: str = None, log_level: str = "INFO"):
    """配置日志系统"""
    if log_dir is None:
        log_dir = os.environ.get("XQSHARE_LOG_DIR", "logs")
    os.makedirs(log_dir, exist_ok=True)
    
    formatter = logging.Formatter(
        fmt='%(asctime)s.%(msecs)03d | %(levelname)-8s | %(name)s | %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    
    root_logger = logging.getLogger()
    root_logger.setLevel(getattr(logging, log_level.upper()))
    
    import io
    # 强制 stdout 使用 UTF-8（解决 Windows 后台启动时日志乱码）
    if sys.stdout and hasattr(sys.stdout, 'buffer'):
        utf8_stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    else:
        utf8_stdout = sys.stdout
    console_handler = logging.StreamHandler(utf8_stdout)
    console_handler.setFormatter(formatter)
    console_handler.setLevel(logging.INFO)
    root_logger.addHandler(console_handler)
    
    file_handler = logging.FileHandler(
        os.path.join(log_dir, f"xtquant_service_{datetime.now().strftime('%Y%m%d')}.log"),
        encoding='utf-8'
    )
    file_handler.setFormatter(formatter)
    file_handler.setLevel(logging.DEBUG)
    root_logger.addHandler(file_handler)
    
    api_handler = logging.FileHandler(
        os.path.join(log_dir, f"api_calls_{datetime.now().strftime('%Y%m%d')}.log"),
        encoding='utf-8'
    )
    api_handler.setFormatter(formatter)
    api_logger = logging.getLogger('api')
    api_logger.addHandler(api_handler)
    api_logger.setLevel(logging.DEBUG)
    
    return logging.getLogger(__name__)


logger = None
api_logger = None


def _init_logging(log_level="INFO"):
    global logger, api_logger
    logger = setup_logging(log_level=log_level)
    api_logger = logging.getLogger('api')


# ==================== 日志装饰器 ====================

def _deliver(obj):
    """将 rpyc netref 对象本地化为真正的 Python 对象。
    xtquant 底层 C++ 扩展做严格类型检查，netref 不被识别为 list/dict 等原生类型，
    必须在调用前将参数本地化。
    """
    try:
        import rpyc
        if isinstance(obj, rpyc.BaseNetref):
            return rpyc.classic.obtain(obj)
    except Exception:
        pass
    # 对类 list 的可迭代容器做兜底转换（排除 str/bytes，避免被拆成字符数组）
    if (type(obj) not in (list, str, bytes)
            and hasattr(obj, '__iter__')
            and hasattr(obj, '__len__')):
        try:
            return list(obj)
        except Exception:
            pass
    return obj


def _log_call(name: str, client_info: str, func, *args, **kwargs):
    """通用的 API 调用日志记录函数"""
    # 将 rpyc netref 参数本地化，避免 C++ 扩展类型检查失败
    args = tuple(_deliver(a) for a in args)
    kwargs = {k: _deliver(v) for k, v in kwargs.items()}

    try:
        args_str = str(args)[:200] if args else ""
        kwargs_str = str(kwargs)[:200] if kwargs else ""
    except:
        args_str = "<unserializable>"
        kwargs_str = ""

    api_logger.info(f"[CALL] {name} | client={client_info} | args={args_str} | kwargs={kwargs_str}")

    start_time = time.perf_counter()
    try:
        result = func(*args, **kwargs)
        elapsed_ms = (time.perf_counter() - start_time) * 1000
        result_summary = _summarize_result(result)
        api_logger.info(f"[OK] {name} | elapsed={elapsed_ms:.2f}ms | result={result_summary}")
        return result
    except Exception as e:
        elapsed_ms = (time.perf_counter() - start_time) * 1000
        api_logger.error(f"[ERROR] {name} | elapsed={elapsed_ms:.2f}ms | error={type(e).__name__}: {str(e)[:200]}")
        raise


def log_api_call(func_name: str = None):
    """记录 API 调用的装饰器"""
    def decorator(func):
        def wrapper(self, *args, **kwargs):
            name = func_name or func.__name__
            client_info = getattr(self, '_client_info', 'unknown')
            return _log_call(name, client_info, func, self, *args, **kwargs)
        return wrapper
    return decorator


def _summarize_result(result: Any, max_len: int = 200) -> str:
    """生成返回值摘要"""
    try:
        if result is None:
            return "None"
        elif isinstance(result, (int, float, bool, str)):
            s = str(result)
            return s if len(s) <= max_len else s[:max_len] + "..."
        elif isinstance(result, (list, tuple)):
            return f"{type(result).__name__}[len={len(result)}]"
        elif isinstance(result, dict):
            keys = list(result.keys())[:5]
            return f"dict{{{', '.join(map(str, keys))}{'...' if len(result) > 5 else ''}}}"
        elif hasattr(result, '__class__'):
            return f"<{result.__class__.__module__}.{result.__class__.__name__}>"
        else:
            return str(type(result))
    except:
        return "<unserializable>"


# ==================== 异常定义 ====================

class AuthError(Exception):
    """认证错误"""
    pass


class CallbackManager:
    """服务端回调管理器

    管理客户端注册的回调函数，支持按 client_info 分组清理。
    当回调执行异常时自动注销。
    """

    def __init__(self):
        self._callbacks = {}
        self._lock = threading.Lock()

    def register(self, callback_id: str, callback, client_info: str):
        """注册回调"""
        with self._lock:
            self._callbacks[callback_id] = {
                'callback': callback,
                'client_info': client_info,
            }

    def unregister(self, callback_id: str):
        """注销回调"""
        with self._lock:
            self._callbacks.pop(callback_id, None)

    def invoke(self, callback_id: str, *args) -> bool:
        """调用回调，异常时自动注销并返回 False"""
        with self._lock:
            entry = self._callbacks.get(callback_id)
        if entry is None:
            return False
        try:
            entry['callback'](*args)
            return True
        except Exception:
            self.unregister(callback_id)
            return False

    def list_callbacks(self) -> dict:
        """列出所有回调 ID"""
        with self._lock:
            return dict(self._callbacks)

    def clear_client_callbacks(self, client_info: str):
        """清理指定客户端的所有回调"""
        with self._lock:
            to_remove = [
                cid for cid, entry in self._callbacks.items()
                if entry['client_info'] == client_info
            ]
            for cid in to_remove:
                self._callbacks.pop(cid, None)


# ==================== Trader 事件回调透传 ====================
# 需要序列化传输的类型标记
SERIALIZED_MARKER = "__xqshare_serialized__"


def _serialize_for_transfer(result):
    """将结果序列化以优化 RPyC 传输性能

    对于大型列表/字典/DataFrame/复杂对象，序列化后传输比逐元素传输快很多。

    Args:
        result: API 调用返回值

    Returns:
        序列化后的数据结构，包含类型标记和序列化数据
    """
    import io

    if result is None:
        return {SERIALIZED_MARKER: "none", "data": None}

    # DataFrame: 转为 CSV 字符串
    try:
        import pandas as pd
        if isinstance(result, pd.DataFrame):
            csv_str = result.to_csv(index=True)
            return {SERIALIZED_MARKER: "dataframe_csv", "data": csv_str}
    except ImportError:
        pass

    # 字典: 检查是否包含 DataFrame（递归检查）
    if isinstance(result, dict):
        try:
            import pandas as pd

            def has_dataframe_recursive(obj):
                """递归检查对象中是否包含 DataFrame"""
                if isinstance(obj, pd.DataFrame):
                    return True
                if isinstance(obj, dict):
                    return any(has_dataframe_recursive(v) for v in obj.values())
                if isinstance(obj, (list, tuple)):
                    return any(has_dataframe_recursive(item) for item in obj)
                return False

            def serialize_dataframes(obj):
                """递归序列化 DataFrame"""
                if isinstance(obj, pd.DataFrame):
                    return {"__df__": True, "csv": obj.to_csv(index=True)}
                if isinstance(obj, dict):
                    return {k: serialize_dataframes(v) for k, v in obj.items()}
                if isinstance(obj, (list, tuple)):
                    return [serialize_dataframes(item) for item in obj]
                return obj

            if has_dataframe_recursive(result):
                serialized_dict = serialize_dataframes(result)
                json_str = json.dumps(serialized_dict, ensure_ascii=False, default=str)
                return {SERIALIZED_MARKER: "dict_with_dataframe", "data": json_str}
        except ImportError:
            pass

        # 普通字典: JSON 序列化
        try:
            json_str = json.dumps(result, ensure_ascii=False, default=str)
            return {SERIALIZED_MARKER: "json", "data": json_str}
        except (TypeError, ValueError):
            pass

    # 列表: JSON 序列化
    if isinstance(result, (list, tuple)):
        try:
            json_str = json.dumps(result, ensure_ascii=False, default=str)
            return {SERIALIZED_MARKER: "json", "data": json_str}
        except (TypeError, ValueError):
            # 无法 JSON 序列化，检查是否需要包装列表元素
            # 对于包含复杂对象的列表，不进行序列化，让 RPyC 原样传输
            pass

    # 单个复杂对象（如 XtAsset/XtOrder/XtTrade）序列化为 dict
    serialized_obj = _serialize_object(result)
    if serialized_obj is not result:
        return {SERIALIZED_MARKER: "json", "data": json.dumps(serialized_obj, ensure_ascii=False, default=str)}

    # 其他类型原样返回
    return result


def _serialize_object(obj):
    """把 xtquant 返回的复杂对象转换为可 JSON 序列化的字典。

    优先读取对象的 __dict__；若无，则遍历 dir 收集可访问的公开属性。
    对于嵌套对象递归处理。失败时回退到 str()。
    """
    if obj is None:
        return None
    if isinstance(obj, (int, float, str, bool, type(None))):
        return obj
    if isinstance(obj, (list, tuple)):
        return [_serialize_object(item) for item in obj]
    if isinstance(obj, dict):
        return {k: _serialize_object(v) for k, v in obj.items()}

    try:
        import rpyc
        if isinstance(obj, rpyc.BaseNetref):
            return _serialize_object(rpyc.classic.obtain(obj))
    except Exception:
        pass

    data = getattr(obj, '__dict__', None)
    if data:
        return {"__class__": type(obj).__name__, **{k: _serialize_object(v) for k, v in data.items()}}

    # 兜底：收集常见公开属性
    try:
        attrs = {}
        for name in dir(obj):
            if name.startswith('_'):
                continue
            try:
                value = getattr(obj, name)
                if callable(value):
                    continue
                attrs[name] = _serialize_object(value)
            except Exception:
                pass
        if attrs:
            return {"__class__": type(obj).__name__, **attrs}
    except Exception:
        pass

    try:
        return str(obj)
    except Exception:
        return {"__class__": type(obj).__name__, "__error__": "unserializable"}


# ==================== Trader 事件回调透传 ====================

class _TraderEventCallback:
    """服务端收集 xtquant 交易事件并路由给远端客户端。

    同一个 trader 实例可服务多个远端客户端；按 account_id 把事件分发给
    注册了该账号回调的客户端 netref。
    """

    EVENT_NAMES = (
        "on_stock_asset", "on_stock_order", "on_stock_trade", "on_stock_position"
    )

    def __init__(self):
        self._lock = threading.Lock()
        # account_id -> set(client_callback_netref)
        self._subscribers: Dict[str, set] = {}
        self._running = True
        self._queue = []
        self._queue_lock = threading.Lock()
        self._event_thread = threading.Thread(target=self._dispatch_loop, daemon=True)
        self._event_thread.start()

    def _enqueue(self, account_id: str, event_name: str, payload: Any):
        if not account_id:
            return
        serialized = _serialize_object(payload)
        with self._queue_lock:
            self._queue.append((account_id, event_name, serialized))
        logger.info(f"[TraderEvent] 事件入队: {event_name} account={account_id} queue_len={len(self._queue)}")

    def _dispatch_loop(self):
        while self._running:
            batch = []
            with self._queue_lock:
                if self._queue:
                    batch = self._queue
                    self._queue = []
            if not batch:
                time.sleep(0.01)
                continue
            logger.info(f"[TraderEvent] 分发批次: {len(batch)} 条事件")
            for account_id, event_name, payload in batch:
                with self._lock:
                    callbacks = list(self._subscribers.get(account_id, set()))
                logger.info(f"[TraderEvent] 推送 {event_name} account={account_id} -> {len(callbacks)} 个客户端")
                for cb in callbacks:
                    try:
                        cb.exposed_on_trader_event(event_name, account_id, payload)
                        logger.debug(f"[TraderEvent] 推送成功: {event_name} account={account_id}")
                    except Exception as e:
                        logger.warning(f"[TraderEvent] 推送到客户端失败: {event_name} {account_id} {e}")

    def register(self, account_id: str, client_callback):
        account_id = str(account_id)
        with self._lock:
            self._subscribers.setdefault(account_id, set()).add(client_callback)
        logger.info(f"[TraderEvent] 注册订阅者: account={account_id} total_subscribers={len(self._subscribers.get(account_id, set()))}")

    def unregister(self, account_id: str, client_callback):
        account_id = str(account_id)
        with self._lock:
            subs = self._subscribers.get(account_id, set())
            subs.discard(client_callback)
            if not subs:
                self._subscribers.pop(account_id, None)

    def stop(self):
        self._running = False

    def on_stock_asset(self, asset):
        account_id = getattr(asset, 'account_id', None)
        logger.info(f"[TraderEvent] xtquant 回调 on_stock_asset account_id={account_id}")
        self._enqueue(account_id, "on_stock_asset", asset)

    def on_stock_order(self, order):
        account_id = getattr(order, 'account_id', None)
        logger.info(f"[TraderEvent] xtquant 回调 on_stock_order account_id={account_id}")
        self._enqueue(account_id, "on_stock_order", order)

    def on_stock_trade(self, trade):
        account_id = getattr(trade, 'account_id', None)
        logger.info(f"[TraderEvent] xtquant 回调 on_stock_trade account_id={account_id}")
        self._enqueue(account_id, "on_stock_trade", trade)

    def on_stock_position(self, position):
        account_id = getattr(position, 'account_id', None)
        logger.info(f"[TraderEvent] xtquant 回调 on_stock_position account_id={position.stock_code if hasattr(position, 'stock_code') else '?'}")
        self._enqueue(account_id, "on_stock_position", position)

    def on_connected(self):
        logger.debug("[TraderEventCallback] 交易连接已建立")

    def on_disconnected(self):
        logger.debug("[TraderEventCallback] 交易连接已断开")


# ==================== Trader 连接回调 ====================

class _TraderConnectionCallback(_TraderEventCallback):
    """用于等待 XtQuantTrader 建立真实交易连接的回调对象

    继承 _TraderEventCallback，因此在等待连接的同时也能收集并路由
    on_stock_* 业务事件给远端客户端。

    xtquant 的 connect() 是异步的，返回 0 仅代表请求发送成功，
    真正的连接成功需要通过 on_connected 回调确认。
    """

    def __init__(self):
        super().__init__()
        self._connected_event = threading.Event()
        self._disconnected_event = threading.Event()

    def on_connected(self):
        logger.debug("[TraderCallback] 交易连接已建立")
        super().on_connected()
        self._connected_event.set()

    def on_disconnected(self):
        logger.debug("[TraderCallback] 交易连接已断开")
        super().on_disconnected()
        self._disconnected_event.set()

    def wait_for_connected(self, timeout: float = 10.0) -> bool:
        """等待交易连接建立

        Args:
            timeout: 最长等待时间（秒）

        Returns:
            True 表示连接成功，False 表示超时
        """
        return self._connected_event.wait(timeout)


# ==================== Quote 行情事件回调透传 ====================

class _QuoteEventCallback:
    """服务端收集 xtquant 行情 tick 并路由给远端客户端。

    xtdata 是模块级单例，所有客户端共享同一份订阅，因此本路由器为
    XtQuantService 类级单例（区别于 per-trader 的 _TraderEventCallback）。

    xtquant 的原生 subscribe_whole_quote / subscribe_quote 注册本对象作为
    callback；tick 到来时把数据 JSON 序列化成字符串（str 在 RPyC 中按值
    传输），再通过反向通道推送给已注册的客户端。这样客户端拿到的是值拷贝
    而非 netref，避免在锁内遍历 netref dict 逐元素 RPC 导致死锁。
    """

    def __init__(self):
        self._lock = threading.Lock()
        # key 用 id(netref)（本地内存地址）而非 netref 本身：
        # set 的 add/discard 会对 netref 求 hash（触发 RPC），客户端断开后
        # 该 RPC 会抛 EOFError，导致 dispatch 线程崩溃。id() 是纯本地操作。
        self._subscribers = {}  # id(client_callback_netref) -> client_callback_netref
        self._running = True
        self._queue = []
        self._queue_lock = threading.Lock()
        self._event_thread = threading.Thread(target=self._dispatch_loop, daemon=True)
        self._event_thread.start()

    def __call__(self, data):
        """使路由器可被 xtquant 作为函数式 callback 调用。

        xtquant 的 subscribe_whole_quote / subscribe_quote 通过
        subscribe_callback_wrapper 包装后以 `callback(datas)` 形式调用，
        因此需要 __call__（区别于 xttrader 的具名方法回调）。
        """
        self.on_data(data)

    def on_data(self, data):
        """xtquant tick 回调入口。data = {stock_code: tick_dict}"""
        if not data:
            return
        try:
            payload = json.dumps(_serialize_object(data), ensure_ascii=False, default=str)
        except Exception as e:
            logger.warning(f"[QuoteEvent] tick 序列化失败: {e}")
            return
        with self._queue_lock:
            self._queue.append(payload)
        logger.debug(f"[QuoteEvent] tick 入队 queue_len={len(self._queue)}")

    def _dispatch_loop(self):
        while self._running:
            batch = []
            with self._queue_lock:
                if self._queue:
                    batch = self._queue
                    self._queue = []
            if not batch:
                time.sleep(0.01)
                continue
            for payload in batch:
                with self._lock:
                    callbacks = list(self._subscribers.values())
                for cb in callbacks:
                    try:
                        cb.exposed_on_quote_event(payload)
                    except Exception as e:
                        # 客户端断开后 netref 调用会失败，自动移除失效订阅者
                        logger.warning(f"[QuoteEvent] 推送到客户端失败，自动移除: {e}")
                        self.unregister(cb)

    def register(self, client_callback):
        with self._lock:
            self._subscribers[id(client_callback)] = client_callback
        logger.info(f"[QuoteEvent] 注册订阅者 total={len(self._subscribers)}")

    def unregister(self, client_callback):
        with self._lock:
            self._subscribers.pop(id(client_callback), None)

    def stop(self):
        self._running = False


# 各订阅接口的 callback 参数位置（0-based），用于 LoggingProxy 拦截时提取客户端回调
_QUOTE_SUBSCRIBE_CALLBACK_POS = {
    'subscribe_whole_quote': 1,
    'subscribe_quote': 5,
    'subscribe_quote2': 6,
}


# ==================== Trader 连接断开处理 ====================

class LoggingProxy:
    """通用代理：拦截模块/对象的方法调用并记录日志，支持递归包装返回对象和权限检查

    当 service_instance 不为空时，会优先查找服务端 XtQuantService 上暴露的
    exposed_<name> 方法。这样可以让原生模块调用（如 xtdata.download_sector_data）
    自动落到服务端自定义封装版本，而不是直接透传给底层 xtquant。
    """

    def __init__(self, target, target_name: str, client_info_getter, permission_checker=None, account_level=None, service_instance=None, event_callback=None, quote_event_callback=None):
        object.__setattr__(self, '_target', target)
        object.__setattr__(self, '_target_name', target_name)
        object.__setattr__(self, '_get_client_info', client_info_getter)
        object.__setattr__(self, '_permission_checker', permission_checker)
        object.__setattr__(self, '_account_level', account_level)
        object.__setattr__(self, '_service_instance', service_instance)
        object.__setattr__(self, '_event_callback', event_callback)
        object.__setattr__(self, '_quote_event_callback', quote_event_callback)

    def __getattr__(self, name):
        target = object.__getattribute__(self, '_target')
        target_name = object.__getattribute__(self, '_target_name')
        get_client_info = object.__getattribute__(self, '_get_client_info')
        permission_checker = object.__getattribute__(self, '_permission_checker')
        account_level = object.__getattribute__(self, '_account_level')
        service_instance = object.__getattribute__(self, '_service_instance')
        event_callback = object.__getattribute__(self, '_event_callback')
        quote_event_callback = object.__getattribute__(self, '_quote_event_callback')

        # 优先使用服务端暴露的封装方法（如果存在）
        if service_instance is not None:
            exposed_name = f"exposed_{name}"
            exposed_attr = getattr(service_instance, exposed_name, None)
            if exposed_attr is not None and callable(exposed_attr):
                attr = exposed_attr
            else:
                attr = getattr(target, name)
        else:
            attr = getattr(target, name)

        # 如果是可调用对象，包装成带日志和权限检查的版本
        if callable(attr):
            def wrapper(*args, **kwargs):
                full_name = f"{target_name}.{name}"

                # 权限检查
                if permission_checker and account_level:
                    error = permission_checker.check_api_permission(
                        account_level, full_name, args, kwargs
                    )
                    if error:
                        api_logger.warning(f"[权限拒绝] {full_name} | client={get_client_info()} | {error}")
                        raise error

                # 对 trader 的事件注册和订阅做特殊处理，把远端回调绑定到本地事件路由器
                if target_name == 'xttrader' and event_callback is not None:
                    if name == 'register_callback':
                        # 忽略原生 register_callback，改由事件路由器统一接管
                        return 0
                    if name == 'subscribe':
                        account = _deliver(args[0]) if args else kwargs.get('account')
                        client_callback = kwargs.pop('callback', None)
                        if account is not None:
                            account_id = getattr(account, 'account_id', str(account))
                            if client_callback is not None:
                                event_callback.register(account_id, client_callback)
                        return attr(*args, **kwargs)
                    if name == 'unsubscribe':
                        account = _deliver(args[0]) if args else kwargs.get('account')
                        client_callback = kwargs.pop('callback', None)
                        if account is not None and client_callback is not None:
                            account_id = getattr(account, 'account_id', str(account))
                            event_callback.unregister(account_id, client_callback)
                        return attr(*args, **kwargs)

                # 对 xtdata 的行情订阅做特殊处理，拦截回调并统一路由到服务端事件路由器，
                # 避免客户端本地 callback 以 netref 反向引用传递导致锁内 RPC 死锁。
                if target_name == 'xtdata' and quote_event_callback is not None:
                    if name in _QUOTE_SUBSCRIBE_CALLBACK_POS:
                        pos = _QUOTE_SUBSCRIBE_CALLBACK_POS[name]
                        client_callback = kwargs.pop('callback', None)
                        if client_callback is not None:
                            # 客户端传了接收器（netref），注册到路由器
                            quote_event_callback.register(client_callback)
                            kwargs['callback'] = quote_event_callback
                        elif len(args) > pos:
                            args = list(args)
                            if args[pos] is not None:
                                quote_event_callback.register(args[pos])
                            args[pos] = quote_event_callback
                            args = tuple(args)
                        else:
                            # 未传回调：仍用服务端路由器接管（此时无订阅者，数据被丢弃）
                            kwargs['callback'] = quote_event_callback

                result = _log_call(full_name, get_client_info(), attr, *args, **kwargs)

                # 如果返回的是复杂对象（非基本类型），递归包装
                if result is not None and hasattr(result, '__class__'):
                    if not isinstance(result, (int, float, str, bool, list, dict, tuple, type(None), bytes)):
                        if not result.__class__.__module__.startswith('builtins'):
                            return LoggingProxy(result, full_name, get_client_info, permission_checker, account_level, service_instance)

                # 处理列表：检查是否包含复杂对象
                if isinstance(result, list):
                    wrapped_list = []
                    has_complex_obj = False
                    for item in result:
                        if item is not None and hasattr(item, '__class__'):
                            if not isinstance(item, (int, float, str, bool, dict, tuple, type(None), bytes)):
                                if not item.__class__.__module__.startswith('builtins'):
                                    wrapped_list.append(LoggingProxy(item, full_name, get_client_info, permission_checker, account_level, service_instance))
                                    has_complex_obj = True
                                    continue
                        wrapped_list.append(item)
                    if has_complex_obj:
                        return wrapped_list

                # 序列化传输优化：将列表/字典/DataFrame 序列化以减少远程调用
                return _serialize_for_transfer(result)
            wrapper.__name__ = name
            return wrapper

        return attr

    def __setattr__(self, name, value):
        return setattr(object.__getattribute__(self, '_target'), name, value)

    def __dir__(self):
        return dir(object.__getattribute__(self, '_target'))

    def __repr__(self):
        return repr(object.__getattribute__(self, '_target'))

# 兼容别名
LoggingModuleProxy = LoggingProxy


# ==================== 服务类 ====================

class XtQuantService(rpyc.Service):
    """完全透明代理服务"""

    _xtdata = xtdata
    _xttrader = xttrader
    _xttype = xttype
    _xtconstant = xtconstant
    _xtview = xtview
    _permission_checker = None  # 类级别的权限检查器
    _datadir_reader = None      # QmtDataReader 单例（server 级）
    _datadir_path = None        # datadir 路径（用于错误提示）
    _datadir_error = None       # 初始化错误信息（路径不存在等）
    _quote_event_callback = None  # 行情事件路由器单例（server 级，xtdata 共享）

    @classmethod
    def _get_quote_event_callback(cls):
        """懒初始化行情事件路由器单例（xtdata 为模块级单例，所有客户端共享）。"""
        if cls._quote_event_callback is None:
            cls._quote_event_callback = _QuoteEventCallback()
        return cls._quote_event_callback

    def on_connect(self, conn):
        self._conn = conn
        self._authenticated = False
        self._client_id = None
        self._account_level = AccountLevel.FREE  # 默认为免费等级
        self._traders = []  # 跟踪本次连接创建的所有 trader 实例
        # 权限检查器在服务启动时已加载
        # 兼容不同版本 rpyc：尝试获取客户端地址
        try:
            if hasattr(conn, 'peer'):
                self._client_info = f"{conn.peer}"
            elif hasattr(conn, '_channel') and hasattr(conn._channel, 'stream'):
                stream = conn._channel.stream
                if hasattr(stream, 'sock'):
                    peer = stream.sock.getpeername()
                    self._client_info = f"{peer[0]}:{peer[1]}"
                else:
                    self._client_info = "unknown"
            else:
                self._client_info = "unknown"
        except Exception:
            self._client_info = "unknown"
        logger.info(f"[连接] 客户端接入: {self._client_info}")

    def on_disconnect(self, conn):
        client_info = getattr(self, '_client_info', 'unknown')
        logger.info(f"[断开] 客户端离开: {client_info}")
        # 自动清理本次连接创建的所有 trader 实例，防止 session 资源泄漏
        traders = getattr(self, '_traders', [])
        for trader in traders:
            # disconnect() 在部分 xtquant 版本中不存在，仅作可选清理
            if hasattr(trader, 'disconnect'):
                try:
                    trader.disconnect()
                    logger.info(f"[清理Trader] 已自动 disconnect trader | client={client_info}")
                except Exception as e:
                    logger.warning(f"[清理Trader] disconnect trader 失败: {e} | client={client_info}")
            try:
                trader.stop()
                logger.info(f"[清理Trader] 已自动 stop trader | client={client_info}")
            except Exception as e:
                logger.warning(f"[清理Trader] stop trader 失败: {e} | client={client_info}")

    def _delayed_disconnect(self, delay: float = 0.5):
        """延迟断开连接，确保异常能传输到客户端"""
        import threading
        def _close():
            try:
                self._conn.close()
            except:
                pass
        threading.Timer(delay, _close).start()

    def _require_auth(self):
        """检查认证状态，未认证则抛出异常并断开连接"""
        if not self._authenticated:
            logger.warning(f"[未授权] 未认证的访问尝试: {self._client_info}")
            self._delayed_disconnect()
            raise AuthError("未授权访问，请先认证")

    # ==================== 认证接口 ====================

    @log_api_call("authenticate")
    def exposed_authenticate(self, client_id, client_secret):
        checker = XtQuantService._permission_checker

        # 检查配置文件是否变更，如果变更则重新加载
        checker.check_and_reload_if_changed()

        # 验证密钥并获取账号等级
        valid, account_level = checker.verify_secret(client_id, client_secret)

        if not valid:
            logger.warning(f"[认证失败] client_id={client_id}")
            self._delayed_disconnect()
            raise AuthError("认证失败：无效的客户端凭证")

        self._authenticated = True
        self._client_id = client_id
        self._account_level = account_level
        self._client_info = f"{client_id}@{self._client_info}"
        logger.info(f"[认证成功] client_id={client_id} | level={account_level.value}")
        return {"success": True, "level": account_level.value}

    @log_api_call("heartbeat")
    def exposed_heartbeat(self):
        return "pong"

    # ==================== 模块代理接口 ====================

    @log_api_call("get_xtdata")
    def exposed_get_xtdata(self):
        self._require_auth()
        return LoggingModuleProxy(
            self._xtdata, 'xtdata',
            lambda: self._client_info,
            XtQuantService._permission_checker,
            self._account_level,
            service_instance=self,
            quote_event_callback=XtQuantService._get_quote_event_callback()
        )

    @log_api_call("get_xttype")
    def exposed_get_xttype(self):
        self._require_auth()
        return self._xttype

    def exposed_get_xtconstant(self):
        self._require_auth()
        return self._xtconstant

    @log_api_call("get_datadir")
    def exposed_get_datadir(self):
        """
        获取 QmtDataReader 远程代理（文件解析能力）。

        Returns:
            LoggingProxy 包装的 QmtDataReader 实例

        Raises:
            AuthError: 未认证时抛出
            RuntimeError: datadir 不可用时抛出（含路径信息）
        """
        self._require_auth()
        if XtQuantService._datadir_reader is None:
            path_info = XtQuantService._datadir_path or "<未配置>"
            err_info = XtQuantService._datadir_error or "QmtDataReader 未初始化"
            raise RuntimeError(
                f"datadir 不可用：{err_info}\n"
                f"路径：{path_info}\n"
                f"请在 server 端 .env 中配置 QMT_DATADIR_PATH，"
                f"或确认 xtdata.get_data_dir() 可用。"
            )
        return LoggingProxy(
            XtQuantService._datadir_reader,
            'datadir',
            lambda: self._client_info,
            XtQuantService._permission_checker,
            self._account_level,
        )

    @log_api_call("get_xtview")
    def exposed_get_xtview(self):
        self._require_auth()
        if self._xtview is None:
            raise RuntimeError("xtview 模块不可用，请检查 xtquant 版本是否支持")
        return LoggingModuleProxy(
            self._xtview, 'xtview',
            lambda: self._client_info,
            XtQuantService._permission_checker,
            self._account_level
        )

    @log_api_call("create_trader")
    def exposed_create_trader(self, userdata_path: str = None, session_id: int = None):
        """
        创建交易实例（不自动启动，由客户端控制生命周期）

        Args:
            userdata_path: QMT 客户端 userdata_mini 目录路径（可选，可通过环境变量配置）
            session_id: 会话ID（可选，默认自动生成时间戳）

        Returns:
            XtQuantTrader 实例（需客户端调用 start() 和 connect()）
        """
        self._require_auth()
        # 检查 trade 权限
        if self._account_level:
            error = XtQuantService._permission_checker.check_api_permission(
                self._account_level, "create_xttrader"
            )
            if error:
                logger.warning(f"[权限拒绝] create_xttrader | client={self._client_info} | {error}")
                raise error
        if not XTQUANT_AVAILABLE:
            raise RuntimeError("xtquant 库未安装")

        # 从环境变量获取默认值
        if userdata_path is None:
            userdata_path = os.environ.get("QMT_USERDATA_PATH")
        if userdata_path is None:
            raise ValueError("必须提供 userdata_path 参数或设置 QMT_USERDATA_PATH 环境变量")

        # 自动生成 session_id（使用毫秒级时间戳，避免同一秒内多次创建时冲突）
        if session_id is None:
            session_id = int(time.time() * 1000) % 1000000  # 毫秒级，取后6位避免超出int范围

        # 创建 trader（不自动启动，由客户端控制生命周期）
        trader = XtQuantTrader(userdata_path, session_id)

        logger.info(f"[创建Trader] userdata_path={userdata_path} | session_id={session_id}")

        # 记录 trader 实例，供 on_disconnect 时自动清理
        self._traders.append(trader)

        # 每个 trader 独立一个事件路由器，用于把 on_stock_* 推送给远端客户端
        event_callback = _TraderEventCallback()
        trader.register_callback(event_callback)

        # 用 LoggingProxy 包装 trader，支持日志记录、序列化和事件回调透传
        return LoggingProxy(
            trader, 'xttrader',
            lambda: self._client_info,
            XtQuantService._permission_checker,
            self._account_level,
            event_callback=event_callback
        )

    @log_api_call("create_trader_and_connect")
    def exposed_create_trader_and_connect(
        self,
        userdata_path: str = None,
        session_id: int = None,
        connect_timeout: float = 10.0
    ):
        """创建交易实例并等待交易连接真正建立

        与 create_trader 不同，此方法会在服务端主动调用 start() 和 connect()，
        并等待 on_connected 回调确认连接建立后再返回。返回的 trader 已经处于
        可下单状态。

        Args:
            userdata_path: QMT 客户端 userdata_mini 目录路径
            session_id: 会话ID（可选）
            connect_timeout: 等待连接建立的最大时间（秒）

        Returns:
            已建立连接的 XtQuantTrader 实例
        """
        self._require_auth()
        if self._account_level:
            error = XtQuantService._permission_checker.check_api_permission(
                self._account_level, "create_xttrader"
            )
            if error:
                logger.warning(f"[权限拒绝] create_xttrader | client={self._client_info} | {error}")
                raise error
        if not XTQUANT_AVAILABLE:
            raise RuntimeError("xtquant 库未安装")

        if userdata_path is None:
            userdata_path = os.environ.get("QMT_USERDATA_PATH")
        if userdata_path is None:
            raise ValueError("必须提供 userdata_path 参数或设置 QMT_USERDATA_PATH 环境变量")

        if session_id is None:
            session_id = int(time.time() * 1000) % 1000000

        trader = XtQuantTrader(userdata_path, session_id)
        callback = _TraderConnectionCallback()
        trader.register_callback(callback)

        logger.info(f"[创建并连接Trader] userdata_path={userdata_path} | session_id={session_id}")

        start_result = trader.start()
        logger.info(f"[Trader] start() 返回: {start_result}")
        if start_result not in (0, None):
            raise RuntimeError(f"启动交易线程失败，错误码: {start_result}")

        connect_result = trader.connect()
        logger.info(f"[Trader] connect() 返回: {connect_result}")
        if connect_result not in (0, None):
            error_codes = {
                -1: "交易服务器未连接",
                -2: "账号未登录",
                -3: "请求超时",
                -4: "资金账号不存在",
            }
            raise ConnectionError(f"连接失败: {error_codes.get(connect_result, f'错误码 {connect_result}')}")

        # 等待真实的交易连接建立
        connected = callback.wait_for_connected(connect_timeout)
        logger.info(f"[Trader] on_connected 回调状态: {connected}")
        if not connected:
            # 某些版本的 xtquant 可能不会触发 on_connected 回调，
            # 尝试用 query_stock_asset 验证连接是否真正可用
            logger.info("[Trader] on_connected 未触发，尝试 query_stock_asset 验证连接...")
            try:
                from xtquant.xttype import StockAccount
                account_id = os.environ.get("QMT_ACCOUNT_ID")
                if account_id:
                    test_account = StockAccount(account_id)
                    asset = trader.query_stock_asset(test_account)
                    logger.info(f"[Trader] query_stock_asset 验证成功: {asset}")
                    connected = True
            except Exception as e:
                logger.warning(f"[Trader] query_stock_asset 验证失败: {e}")

        if not connected:
            trader.stop()
            raise TimeoutError(
                f"等待交易连接建立超时（{connect_timeout}秒），"
                "请检查 MiniQMT 是否已登录交易账号，或设置更长的 --connect-timeout"
            )

        self._traders.append(trader)

        return LoggingProxy(
            trader, 'xttrader',
            lambda: self._client_info,
            XtQuantService._permission_checker,
            self._account_level,
            event_callback=callback
        )

    # ==================== 辅助接口 ====================

    @log_api_call("get_all_stocks")
    def exposed_get_all_stocks(self):
        self._require_auth()
        return self._xtdata.get_stock_list_in_sector("沪深A股")

    @log_api_call("get_index_list")
    def exposed_get_index_list(self):
        self._require_auth()
        return self._xtdata.get_stock_list_in_sector("沪深指数")

    @log_api_call("download_sector_data")
    def exposed_download_sector_data(self, detail: bool = False):
        """下载行业板块数据（带下载状态反馈）

        Args:
            detail: 为 True 时返回完整的 sectors 列表，否则只返回统计摘要。
        """
        self._require_auth()

        def scan_directory(root: str, include_sectors: bool = False):
            """扫描指定目录下的板块数据，返回统计信息。"""
            summary = {
                "scanned_root": root,
                "exists": os.path.isdir(root),
                "categories": [],
                "category_count": 0,
                "sector_count": 0,
                "total_bytes": 0,
                "scan_error": None,
            }
            if include_sectors:
                summary["sectors"] = {}
            if not os.path.isdir(root):
                return summary
            try:
                for category in sorted(os.listdir(root)):
                    category_path = os.path.join(root, category)
                    if not os.path.isdir(category_path):
                        # 如果根目录下直接是文件，也记录为未分类板块
                        if os.path.isfile(category_path):
                            try:
                                size = os.path.getsize(category_path)
                            except OSError:
                                size = 0
                            uncategorized_key = "__uncategorized__"
                            if uncategorized_key not in summary["categories"]:
                                summary["categories"].append(uncategorized_key)
                                summary["category_count"] += 1
                                if include_sectors:
                                    summary["sectors"][uncategorized_key] = []
                            if include_sectors:
                                summary["sectors"][uncategorized_key].append({"name": category, "bytes": size})
                            summary["total_bytes"] += size
                            summary["sector_count"] += 1
                        continue
                    summary["categories"].append(category)
                    summary["category_count"] += 1
                    sectors = []
                    for name in sorted(os.listdir(category_path)):
                        fp = os.path.join(category_path, name)
                        if not os.path.isfile(fp):
                            continue
                        try:
                            size = os.path.getsize(fp)
                        except OSError:
                            size = 0
                        sectors.append({"name": name, "bytes": size})
                        summary["total_bytes"] += size
                    summary["sector_count"] += len(sectors)
                    if include_sectors:
                        summary["sectors"][category] = sectors
            except Exception as e:
                summary["scan_error"] = str(e)
            return summary

        def find_sector_candidates(base_dir: str):
            """查找可能存放板块数据的候选目录。"""
            candidates = []
            if not base_dir or not os.path.isdir(base_dir):
                return candidates
            # 优先路径
            for sub in ["Sector/Temple", "Sector", "sector", "sectors", "Sectors"]:
                path = os.path.join(base_dir, *sub.split("/"))
                if os.path.isdir(path):
                    candidates.append(path)
            # 递归扫描一层，寻找包含大量 txt/csv/dat 的目录
            try:
                for entry in sorted(os.listdir(base_dir)):
                    path = os.path.join(base_dir, entry)
                    if not os.path.isdir(path):
                        continue
                    count = 0
                    for _, _, files in os.walk(path):
                        for f in files:
                            if f.lower().endswith((".txt", ".csv", ".dat", ".json")):
                                count += 1
                        if count >= 10:
                            break
                    if count >= 10:
                        candidates.append(path)
            except Exception:
                pass
            return candidates

        def summarize(summary: dict) -> dict:
            """非 detail 模式下只保留关键摘要字段。"""
            return {
                "scanned_root": summary.get("scanned_root"),
                "exists": summary.get("exists"),
                "category_count": summary.get("category_count", 0),
                "sector_count": summary.get("sector_count", 0),
                "total_bytes": summary.get("total_bytes", 0),
                "scan_error": summary.get("scan_error"),
            }

        datadir = XtQuantService._datadir_path or ""
        candidates = find_sector_candidates(datadir)
        # 默认使用第一个候选目录，如果没有候选则使用默认路径（便于排查）
        sector_root = candidates[0] if candidates else os.path.join(datadir, "Sector", "Temple")

        before_full = scan_directory(sector_root, include_sectors=detail)
        before = before_full if detail else summarize(before_full)

        start = time.time()
        try:
            result = self._xtdata.download_sector_data()
        except Exception as e:
            return {
                "success": False,
                "native_result": None,
                "error": str(e),
                "elapsed_seconds": round(time.time() - start, 3),
                "datadir": datadir,
                "candidates": candidates,
                "before": before,
                "after": before,
                "changes": {},
            }
        elapsed = round(time.time() - start, 3)

        # 下载后重新探测候选目录，因为 download_sector_data 可能创建新目录
        candidates_after = find_sector_candidates(datadir)
        if candidates_after and sector_root not in candidates_after:
            # 如果下载后出现了新的候选目录，也扫描一遍；但仍用同一个 before 做对比
            sector_root = candidates_after[0]
        after_full = scan_directory(sector_root, include_sectors=detail)
        after = after_full if detail else summarize(after_full)

        # 计算变化
        changes = {
            "added_categories": [c for c in after_full["categories"] if c not in before_full["categories"]],
            "removed_categories": [c for c in before_full["categories"] if c not in after_full["categories"]],
            "sector_count_delta": after_full["sector_count"] - before_full["sector_count"],
            "bytes_delta": after_full["total_bytes"] - before_full["total_bytes"],
        }
        if detail:
            before_sectors = {s["name"] for cat in before_full["sectors"].values() for s in cat}
            after_sectors = {s["name"] for cat in after_full["sectors"].values() for s in cat}
            changes["added_sectors"] = sorted(after_sectors - before_sectors)
            changes["removed_sectors"] = sorted(before_sectors - after_sectors)

        return {
            "success": True,
            "native_result": result,
            "elapsed_seconds": elapsed,
            "datadir": datadir,
            "candidates": candidates,
            "candidates_after": candidates_after,
            "before": before,
            "after": after,
            "changes": changes,
        }

    # ==================== 服务端封装接口 ====================

    @log_api_call("download_history_data2")
    def exposed_download_history_data2(self, stock_list: list, period: str = "1d",
                                        start_time: str = "", end_time: str = "", incrementally: bool = None):
        """
        下载历史数据（服务端封装，避免回调传输问题）
        返回: {'finished': n, 'total': n, 'result': {...}}
        """
        status = {'finished': 0, 'total': 0, 'done': False, 'result': {}, 'message': ''}

        def on_progress(data):
            status['finished'] = data.get('finished', 0)
            status['total'] = data.get('total', 0)
            status['done'] = status['finished'] >= status['total']
            status['message'] = data.get('message', '')
            if 'result' in data:
                import datetime as dt
                from xtquant import xtbson as bson
                regino_result = bson.BSON.decode(data.get('result'))
                for stock, info in regino_result.items():
                    info['start_time'] = str(dt.datetime.fromtimestamp(info.get('start_time') / 1000))
                    info['end_time'] = str(dt.datetime.fromtimestamp(info.get('end_time') / 1000))
                    status['result'][stock] = info

        # 调用原始方法（incrementally 参数需要转换为 None 或 bool）
        # 注意：xtquant 的 download_history_data2 从 250807.1.2 才开始在
        # 二进制层接受 incrementally 关键字；老版本传这个 kwarg 会
        # TypeError。这里用 inspect 探测签名，只在底层支持时才传入。
        inc = incrementally
        kwargs = {"callback": on_progress}
        try:
            params = inspect.signature(
                self._xtdata.download_history_data2
            ).parameters
            if "incrementally" in params:
                kwargs["incrementally"] = inc
        except (TypeError, ValueError):
            # C 扩展拿不到签名（极少见），按新版本处理
            kwargs["incrementally"] = inc

        self._xtdata.download_history_data2(
            stock_list, period, start_time, end_time, **kwargs
        )

        return status

    # ==================== 服务状态 ====================

    @log_api_call("get_service_status")
    def exposed_get_service_status(self):
        self._require_auth()
        return {
            "uptime": time.time() - getattr(self, '_start_time', time.time()),
            "client_id": self._client_id,
        }

    @log_api_call("ping")
    def exposed_ping(self):
        return "pong"

    @log_api_call("test_async_callback")
    def exposed_test_async_callback(self, callback_func, delay: float = 2.0, count: int = 5):
        """
        测试 RPyC netref 异步回调机制
        :param callback_func: 客户端传递的回调函数（netref）
        :param delay: 每次回调间隔秒数
        :param count: 回调次数
        :return: 立即返回 "已启动"
        """
        self._require_auth()
        # 检查 callback 权限
        if self._account_level:
            error = XtQuantService._permission_checker.check_api_permission(
                self._account_level, "test_async_callback"
            )
            if error:
                logger.warning(f"[权限拒绝] test_async_callback | client={self._client_info} | {error}")
                raise error

        import threading
        import time

        def async_call():
            for i in range(count):
                time.sleep(delay)
                try:
                    result = callback_func(f"异步回调 #{i+1}/{count}，时间: {time.strftime('%H:%M:%S')}")
                    api_logger.info(f"[异步回调] #{i+1} 执行成功，返回: {result}")
                except Exception as e:
                    api_logger.error(f"[异步回调] #{i+1} 执行失败: {e}")

        thread = threading.Thread(target=async_call, daemon=True)
        thread.start()
        return f"已启动异步回调，共 {count} 次，间隔 {delay} 秒"


# ==================== clients.yaml 初始化 ====================

def _gen_secret(length: int = 32) -> str:
    """生成指定长度的随机 secret 字符串（字母+数字）"""
    import secrets
    import string
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))


def _init_clients_yaml():
    """
    如果当前工作目录下不存在 clients.yaml，则动态生成一份带随机 secret 的模板。

    所有用户条目默认注释掉，secret 为随机生成的 32 位字符串，
    用户按需取消注释并修改即可，无需手动生成密钥。
    """
    target = os.path.join(os.getcwd(), "clients.yaml")
    if os.path.exists(target):
        return  # 已存在，跳过

    try:
        content = f"""# xqshare 客户端配置
# 由 xqshare-server 自动生成，secret 已预填随机密钥
#
# 使用方法：
#   1. 取消注释需要启用的用户条目
#   2. 将 secret 同步给对应客户端（客户端 .env 中设置 XQSHARE_SECRET=<secret>）
#   3. 重启服务使配置生效
#
# 权限等级说明：
# ┌─────────────┬───────────────────────────────────────────────────────────┐
# │ 等级         │ 权限说明                                                  │
# ├─────────────┼───────────────────────────────────────────────────────────┤
# │ free        │ 基础信息 + 日线数据                                        │
# │ plus        │ 基础信息 + 日线 + 分钟线数据                               │
# │ standard    │ 基础信息 + 日线 + 分钟线 + 实时行情 + 回调功能             │
# │ premium     │ standard + 交易查询（持仓、资产、委托等）                  │
# │ enterprise  │ premium + 完整交易（下单、撤单）                           │
# └─────────────┴───────────────────────────────────────────────────────────┘

clients:
  # ==================== 免费用户 ====================
  # 权限：基础信息 + 日线数据
  # free-user:
  #   secret: "{_gen_secret()}"
  #   level: free

  # ==================== 进阶用户 ====================
  # 权限：基础信息 + 日线 + 分钟线数据
  # plus-user:
  #   secret: "{_gen_secret()}"
  #   level: plus

  # ==================== 标准用户 ====================
  # 权限：基础信息 + 日线 + 分钟线 + 实时行情 + 回调功能
  # standard-user:
  #   secret: "{_gen_secret()}"
  #   level: standard

  # ==================== 高级用户 ====================
  # 权限：standard + 交易查询（持仓、资产、委托等）
  # premium-user:
  #   secret: "{_gen_secret()}"
  #   level: premium

  # ==================== 企业用户 ====================
  # 权限：premium + 完整交易（下单、撤单）
  # enterprise-user:
  #   secret: "{_gen_secret()}"
  #   level: enterprise
"""
        with open(target, "w", encoding="utf-8") as f:
            f.write(content)
        print(f"  \u2714 已生成 clients.yaml（所有用户已注释，secret 已预填随机密钥）")
        print(f"    请取消注释需要启用的用户条目，并将 secret 同步给对应客户端")
    except Exception as e:
        logger.warning(f"自动生成 clients.yaml 失败: {e}")


def _init_env_file():
    """
    如果当前工作目录下不存在 .env 文件，则从包目录复制示例文件。

    这样用户 pip install 后首次运行即可得到一份带注释的 .env 模板，
    避免因缺少配置文件而导致服务无法正常启动。
    """
    import shutil
    target = os.path.join(os.getcwd(), ".env")
    if os.path.exists(target):
        return  # 已存在，跳过

    # 从包目录查找示例文件
    example_path = os.path.join(os.path.dirname(__file__), ".env.example")
    if not os.path.exists(example_path):
        return  # 示例文件也不存在（不应发生）

    try:
        shutil.copyfile(example_path, target)
        print(f"  \u2714 已生成 .env 配置文件")
        print(f"    ⚠ 请编辑 .env 文件，设置 QMT_USERDATA_PATH 等必要配置后重新启动服务")
    except Exception as e:
        logger.warning(f"自动复制 .env.example 失败: {e}")


# ==================== setup 向导 ====================

def _setup_detect_miniqmt():
    """
    自动探测 miniQMT 运行状态。
    委托给 xqshare.check_env 模块的核心函数实现，
    与 QmtQuant/utils/check_env.py 保持逻辑一致。
    返回 dict：
      install_dir  str | None   miniQMT 安装目录（bin.x64 所在目录）
      account      str | None   从窗口标题提取的资金账号（纯数字）
      status       'running' | 'installed' | 'not_found'
    """
    try:
        from xqshare.check_env import get_miniqmt_info, extract_account_from_title
    except ImportError:
        return {"install_dir": None, "account": None, "status": "not_found"}

    info = get_miniqmt_info()
    install_dir = info.get("install_dir")
    title = info.get("process_name")
    status = info.get("status", "not_found")

    # 规范化 install_dir：'未找到' → None
    if install_dir and install_dir == "未找到":
        install_dir = None

    # 从窗口标题提取资金账号
    account = None
    if title and title != "未运行":
        account = extract_account_from_title(title)

    return {"install_dir": install_dir, "account": account, "status": status}


def _setup_generate_env(cwd, install_dir, account):
    """
    根据探测结果生成 .env 文件。
    install_dir: miniQMT 的 bin.x64 目录（XtMiniQmt.exe 所在目录）
    account:     资金账号字符串
    返回写入的路径，失败返回 None。

    路径推断逻辑与 QmtQuant/utils/check_env.py 的 save_env_file() 保持一致：
    install_dir 通常是 <root>\\bin.x64，上一级就是安装根目录，
    userdata_mini 和 datadir 都在安装根目录下。
    """
    env_path = os.path.join(cwd, ".env")

    # ── 路径推断（与 check_env.py 逻辑一致）──
    if install_dir:
        # install_dir = C:\\install\\国金证券QMT交易端\\bin.x64
        # base_dir   = C:\\install\\国金证券QMT交易端
        base_dir = os.path.dirname(install_dir.rstrip("\\/"))
        userdata_path = os.path.join(base_dir, "userdata_mini")
        datadir_path = os.path.join(base_dir, "datadir")
        # QMT_EXE_PATH：XtItClient.exe（用于守护进程拉起）
        exe_path = os.path.join(install_dir, "XtItClient.exe")
    else:
        userdata_path = ""
        datadir_path = ""
        exe_path = ""

    try:
        with open(env_path, "w", encoding="utf-8") as f:
            f.write("# xqshare 服务端环境变量配置（由 xqshare-server setup 自动生成）\n")
            f.write("# 请根据实际情况修改后重启服务\n\n")

            # ── 服务配置 ──
            f.write("# ==================== 服务配置 ====================\n")
            f.write("# 服务监听端口\n")
            f.write("XQSHARE_PORT=18812\n\n")

            # ── QMT 配置 ──
            f.write("# ==================== QMT 配置 ====================\n")
            if userdata_path:
                f.write(f"QMT_USERDATA_PATH={userdata_path}\n")
            else:
                f.write("# QMT 客户端 userdata_mini 目录路径（必填）\n")
                f.write("# 示例：QMT_USERDATA_PATH=C:\\install\\国金证券QMT交易端\\userdata_mini\n")
                f.write("QMT_USERDATA_PATH=\n")
            f.write("\n")

            if account:
                f.write(f"QMT_ACCOUNT_ID={account}\n")
            else:
                f.write("# 默认资金账号（必填，纯数字）\n")
                f.write("# 示例：QMT_ACCOUNT_ID=307100903095\n")
                f.write("QMT_ACCOUNT_ID=\n")
            f.write("\n")

            if datadir_path:
                f.write(f"QMT_DATADIR_PATH={datadir_path}\n")
            else:
                f.write("# QMT datadir 文件解析路径（用于 xqshare.datadir 功能，可选）\n")
                f.write("# 示例：QMT_DATADIR_PATH=C:\\install\\国金证券QMT交易端\\datadir\n")
                f.write("# QMT_DATADIR_PATH=\n")
            f.write("\n")

            # ── 守护进程配置 ──
            f.write("# ==================== 守护进程配置 ====================\n")
            if exe_path:
                f.write(f"QMT_EXE_PATH={exe_path}\n")
            else:
                f.write("# miniQMT 可执行文件路径（qmt_watchdog.py 需要，可选）\n")
                f.write("# 示例：QMT_EXE_PATH=C:\\install\\国金证券QMT交易端\\bin.x64\\XtItClient.exe\n")
                f.write("# QMT_EXE_PATH=\n")
            f.write("# WATCHDOG_CHECK_INTERVAL=30\n")
            f.write("# WATCHDOG_MAX_RETRIES=3\n")
            f.write("# WATCHDOG_STARTUP_WAIT=5\n\n")

            # ── 日志配置 ──
            f.write("# ==================== 日志配置 ====================\n")
            f.write("# 日志级别: DEBUG, INFO, WARNING, ERROR\n")
            f.write("LOG_LEVEL=INFO\n")

        return env_path
    except Exception as e:
        print(f"  ❌ 写入 .env 失败: {e}")
        return None


def _setup_copy_watchdog(cwd):
    """
    将 qmt_watchdog.py 从 QmtQuant/utils/ 拷贝到当前目录。
    优先从包内置资源查找，其次从 QmtQuant 项目目录查找。
    返回 True 表示成功。
    """
    import shutil

    target = os.path.join(cwd, "qmt_watchdog.py")
    if os.path.exists(target):
        print(f"  ✔ qmt_watchdog.py 已存在，跳过拷贝")
        return True

    # 1. 优先从包内置资源查找（打包时放入 xqshare/ 目录）
    pkg_dir = os.path.dirname(__file__)
    pkg_watchdog = os.path.join(pkg_dir, "qmt_watchdog.py")
    if os.path.exists(pkg_watchdog):
        shutil.copyfile(pkg_watchdog, target)
        print(f"  ✔ 已拷贝 qmt_watchdog.py 到当前目录")
        return True

    # 2. 从 QmtQuant/utils/ 查找（开发环境）
    candidates = [
        os.path.join(pkg_dir, "..", "..", "QmtQuant", "utils", "qmt_watchdog.py"),
        os.path.join(os.path.expanduser("~"), "Documents", "AIWork", "QmtQuant", "utils", "qmt_watchdog.py"),
    ]
    for src in candidates:
        src = os.path.normpath(src)
        if os.path.exists(src):
            shutil.copyfile(src, target)
            print(f"  ✔ 已拷贝 qmt_watchdog.py 到当前目录")
            return True

    print(f"  ⚠ 未找到 qmt_watchdog.py，请手动拷贝守护进程脚本")
    return False


def _cmd_setup(args):
    """
    xqshare-server setup — 初始化向导

    自动完成以下操作：
      1. 探测 miniQMT 运行状态，自动填充 QMT_USERDATA_PATH / QMT_ACCOUNT_ID
      2. 生成 .env 配置文件（已存在时询问是否覆盖）
      3. 生成 clients.yaml（已存在时跳过）
      4. 拷贝 qmt_watchdog.py 到当前目录（已存在时跳过）
    """
    cwd = os.getcwd()

    print()
    print("=" * 60)
    print("  xqshare-server setup — 初始化向导")
    print("=" * 60)
    print(f"  工作目录: {cwd}")
    print()

    # ── 步骤 1：探测 miniQMT ──────────────────────────────────
    print("【步骤 1/4】探测 miniQMT 运行状态...")
    if sys.platform != "win32":
        print("  ℹ 当前系统为非 Windows，跳过自动探测")
        print("    请在 .env 中手动填写 QMT_USERDATA_PATH 和 QMT_ACCOUNT_ID")
        miniqmt = {"install_dir": None, "account": None, "status": "not_found"}
    else:
        miniqmt = _setup_detect_miniqmt()
        if miniqmt["status"] == "running":
            print(f"  ✔ 检测到 miniQMT 正在运行")
            print(f"    安装目录: {miniqmt['install_dir'] or '未知'}")
            if miniqmt["account"]:
                print(f"    资金账号: {miniqmt['account']} （从窗口标题自动提取）")
            else:
                print("    ⚠ 无法从窗口标题自动提取资金账号（平安QMT等显示登录名而非账号）")
        elif miniqmt["status"] == "installed":
            print(f"  ✔ 检测到 miniQMT 已安装（未运行）")
            print(f"    安装目录: {miniqmt['install_dir'] or '未知'}")
            print("    ⚠ miniQMT 未运行，无法自动提取资金账号")
        else:
            print("  ⚠ 未检测到 miniQMT")
            print("    请先安装并启动 miniQMT，或在生成的 .env 中手动填写配置")

    # 如果账号未自动提取，交互式询问
    account = miniqmt["account"]
    if sys.platform == "win32" and miniqmt["status"] in ("running", "installed") and not account:
        try:
            inp = input("    请输入资金账号（纯数字，如 307100903095，直接回车跳过）: ").strip()
            if inp.isdigit():
                account = inp
        except (EOFError, KeyboardInterrupt):
            pass

    print()

    # ── 步骤 2：生成 .env ─────────────────────────────────────
    print("【步骤 2/4】生成 .env 配置文件...")
    env_path = os.path.join(cwd, ".env")
    if os.path.exists(env_path):
        try:
            ans = input(f"  .env 已存在，是否覆盖？[y/N] ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            ans = "n"
        if ans != "y":
            print("  ✔ 保留现有 .env，跳过生成")
        else:
            result = _setup_generate_env(cwd, miniqmt["install_dir"], account)
            if result:
                print(f"  ✔ 已生成 .env: {result}")
    else:
        result = _setup_generate_env(cwd, miniqmt["install_dir"], account)
        if result:
            print(f"  ✔ 已生成 .env: {result}")

    print()

    # ── 步骤 3：生成 clients.yaml ─────────────────────────────
    print("【步骤 3/4】生成 clients.yaml...")
    _init_clients_yaml()
    print()

    # ── 步骤 4：拷贝 qmt_watchdog.py ─────────────────────────
    print("【步骤 4/4】拷贝 qmt_watchdog.py（守护进程）...")
    _setup_copy_watchdog(cwd)
    print()

    # ── 完成提示 ──────────────────────────────────────────────
    print("=" * 60)
    print("  ✅ 初始化完成！后续步骤：")
    print()
    print("  1. 检查并编辑 .env，确认以下配置正确：")
    print("       QMT_USERDATA_PATH  — QMT userdata_mini 目录")
    print("       QMT_ACCOUNT_ID     — 资金账号")
    print("       QMT_EXE_PATH       — miniQMT 可执行文件路径（守护进程用）")
    print()
    print("  2. 编辑 clients.yaml，设置客户端密钥和权限等级")
    print()
    print("  3. 启动服务：")
    print("       xqshare-server start")
    print()
    print("  4. （可选）启动守护进程，自动重启 miniQMT 和 xqshare-server：")
    print("       python qmt_watchdog.py")
    print("=" * 60)
    print()


# ==================== datadir 初始化 ====================

def _init_datadir_reader():
    """
    初始化 QmtDataReader 单例（server 级，仅执行一次）。

    路径解析优先级：
      1. 环境变量 QMT_DATADIR_PATH（显式配置）
      2. xtdata.get_data_dir() 自动推断（userdata_mini\\datadir → datadir）
      3. 均不可用时记录 WARNING，_datadir_reader 保持 None
    """
    if not QMTDATAREADER_AVAILABLE:
        logger.warning("[datadir] QmtDataReader 不可用（qmt_datadir 未安装或 pandas 缺失），datadir 功能已禁用")
        return

    # 步骤1：从环境变量读取
    datadir_path = os.environ.get("QMT_DATADIR_PATH", "").strip()

    # 步骤2：自动推断
    if not datadir_path and xtdata is not None:
        try:
            default_dir = xtdata.get_data_dir()
            # 复用 env.py 中的替换逻辑：userdata_mini\datadir → datadir
            inferred = default_dir.replace(r'\userdata_mini\datadir', r'\datadir')
            if inferred != default_dir:
                datadir_path = inferred
                logger.info(f"[datadir] 自动推断 datadir 路径（主数据目录）：{datadir_path}")
            else:
                datadir_path = default_dir
                logger.info(f"[datadir] 自动推断 datadir 路径（默认目录）：{datadir_path}")
        except Exception as _e:
            logger.warning(f"[datadir] xtdata.get_data_dir() 调用失败：{_e}")

    XtQuantService._datadir_path = datadir_path or None

    if not datadir_path:
        logger.warning(
            "[datadir] 未配置 QMT_DATADIR_PATH 且无法自动推断，datadir 功能不可用。\n"
            "请在 .env 中添加：QMT_DATADIR_PATH=D:\\QMT\\datadir"
        )
        XtQuantService._datadir_error = "未配置 QMT_DATADIR_PATH 且无法自动推断路径"
        return

    if not os.path.isdir(datadir_path):
        logger.error(f"[datadir] 配置的路径不存在：{datadir_path}，datadir 功能不可用")
        XtQuantService._datadir_error = f"路径不存在：{datadir_path}"
        return

    try:
        XtQuantService._datadir_reader = QmtDataReader(datadir_path)
        logger.info(f"[datadir] QmtDataReader 初始化成功：{datadir_path}")
    except Exception as _e:
        logger.error(f"[datadir] QmtDataReader 初始化失败：{_e}")
        XtQuantService._datadir_error = str(_e)


# ==================== 服务启动 ====================

def create_ssl_context(certfile=None, keyfile=None):
    if not certfile or not keyfile:
        return None
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(certfile, keyfile)
    return ctx


def start_server(host="0.0.0.0", port=None, use_ssl=False, certfile=None, keyfile=None, log_level="INFO", env_file=None):
    """启动服务

    Args:
        host: 监听地址
        port: 监听端口
        use_ssl: 是否启用 SSL
        certfile: SSL 证书文件
        keyfile: SSL 密钥文件
        log_level: 日志级别
        env_file: 环境变量文件路径（None 时自动查找 .env）
    """
    # 加载环境变量文件（None 时自动查找 .env）
    try:
        from dotenv import load_dotenv
        load_dotenv(env_file)
    except ImportError:
        pass

    if port is None:
        port = int(os.environ.get("XQSHARE_PORT", "18812"))

    if not XTQUANT_AVAILABLE:
        print("错误: xtquant 库未安装，请先安装 xtquant")
        return

    _init_logging(log_level)
    XtQuantService._start_time = time.time()

    print("=" * 70)
    print("  XtQuant Share (xqshare) 服务")
    print("=" * 70)
    print(f"  监听地址: {host}:{port}")
    print(f"  SSL 加密: {'启用' if use_ssl else '禁用'}")
    print(f"  日志级别: {log_level}")
    print("=" * 70)
    
    # ── 自动生成 .env（首次启动时拷贝模板） ───────────────────────
    _init_env_file()

    # ── 自动生成 clients.yaml ──────────────────────────────────────
    _init_clients_yaml()

    # 预加载权限检查器（加载 clients.yaml 配置）
    if XtQuantService._permission_checker is None:
        XtQuantService._permission_checker = get_permission_checker()

    # ── 初始化 QmtDataReader 单例 ──────────────────────────────────
    _init_datadir_reader()

    logger.info(f"服务启动 | host={host} | port={port} | ssl={use_ssl}")
    
    config = {
        'allow_public_attrs': True,
        'allow_pickle': True,
        'allow_getattr': True,
        'allow_setattr': True,
        'allow_delattr': True,
        'allow_all_attrs': True,
        'sync_request_timeout': 300,
    }
    
    ssl_context = None
    if use_ssl:
        ssl_context = create_ssl_context(certfile, keyfile)
        if ssl_context:
            logger.info("SSL 证书加载成功")
            print("  ✓ SSL 证书加载成功")
        else:
            logger.warning("SSL 证书加载失败")
            print("  ⚠ SSL 证书加载失败")
    
    # 构建 ThreadedServer 参数（兼容不同 rpyc 版本）
    server_kwargs = {
        'hostname': host,
        'port': port,
        'protocol_config': config,
    }
    
    # 尝试使用 ssl_context（新版本 rpyc）
    try:
        server = ThreadedServer(XtQuantService, ssl_context=ssl_context, **server_kwargs)
    except TypeError:
        # 旧版本 rpyc 不支持 ssl_context，使用其他方式
        if ssl_context:
            # 对于旧版本，通过 protocol_config 传递 SSL
            import socket
            import ssl as ssl_module
            
            # 创建 SSL 包装的 socket
            class SSLThreadedServer(ThreadedServer):
                def _accept_method(self, sock):
                    try:
                        return ssl_context.wrap_socket(sock, server_side=True)
                    except Exception as e:
                        logger.error(f"SSL 包装失败: {e}")
                        raise
            
            server = SSLThreadedServer(XtQuantService, **server_kwargs)
            logger.info("使用兼容模式启动 SSL")
        else:
            server = ThreadedServer(XtQuantService, **server_kwargs)
    
    print("\n  服务已启动，等待客户端连接...")
    print("  按 Ctrl+C 停止服务\n")
    
    try:
        server.start()
    except KeyboardInterrupt:
        logger.info("服务停止（用户中断）")
        print("\n  服务已停止")
        server.close()
    except Exception as e:
        logger.error(f"服务异常: {e}")
        raise


def _get_pid_file() -> str:
    """获取 PID 文件路径（与当前工作目录绑定）"""
    return os.path.join(os.getcwd(), ".xqshare-server.pid")


def _get_log_file() -> str:
    """获取日志文件路径（与当前工作目录绑定）"""
    return os.path.join(os.getcwd(), "xqshare-server.log")


def _cmd_background(args):
    """后台启动 server"""
    import subprocess

    pid_file = _get_pid_file()

    # 检查是否已在运行
    if os.path.exists(pid_file):
        with open(pid_file) as f:
            old_pid = f.read().strip()
        try:
            old_pid_int = int(old_pid)
            # 检查进程是否存在
            if sys.platform == "win32":
                import ctypes
                handle = ctypes.windll.kernel32.OpenProcess(0x0400, False, old_pid_int)
                if handle:
                    ctypes.windll.kernel32.CloseHandle(handle)
                    print(f"  xqshare-server 已在运行 (PID: {old_pid})")
                    print(f"  如需重启请先执行: xqshare-server stop")
                    return
            else:
                os.kill(old_pid_int, 0)
                print(f"  xqshare-server 已在运行 (PID: {old_pid})")
                print(f"  如需重启请先执行: xqshare-server stop")
                return
        except (ValueError, OSError, AttributeError):
            # 进程不存在，清理旧 PID 文件
            os.remove(pid_file)

    log_file = _get_log_file()

    # 构建启动命令（透传所有参数）
    # Windows 上后台子进程用 pythonw.exe，避免弹出控制台窗口
    if sys.platform == "win32":
        pythonw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
        bg_executable = pythonw if os.path.exists(pythonw) else sys.executable
    else:
        bg_executable = sys.executable
    cmd = [bg_executable, "-m", "xqshare.server", "fg"]
    if args.host != "0.0.0.0":
        cmd += ["--host", args.host]
    if args.port:
        cmd += ["--port", str(args.port)]
    if args.ssl:
        cmd.append("--ssl")
    if args.cert:
        cmd += ["--cert", args.cert]
    if args.key:
        cmd += ["--key", args.key]
    if args.log_level != "INFO":
        cmd += ["--log-level", args.log_level]
    # 始终透传 env_file（已经是绝对路径或确定的路径）
    cmd += ["--env-file", args.env_file]

    with open(log_file, "a", encoding="utf-8") as log_f:
        if sys.platform == "win32":
            proc = subprocess.Popen(
                cmd,
                stdout=log_f,
                stderr=log_f,
                creationflags=subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS | subprocess.CREATE_NO_WINDOW,
                close_fds=True,
            )
        else:
            proc = subprocess.Popen(
                cmd,
                stdout=log_f,
                stderr=log_f,
                start_new_session=True,
                close_fds=True,
            )

    with open(pid_file, "w") as f:
        f.write(str(proc.pid))

    print(f"  xqshare-server 已在后台启动 (PID: {proc.pid})")
    print(f"  日志文件: {log_file}")
    print(f"  查看日志: xqshare-server logs -f")
    print(f"  停止服务: xqshare-server stop")


def _cmd_stop(args):
    """停止后台运行的 server"""
    import signal

    pid_file = _get_pid_file()

    if not os.path.exists(pid_file):
        print("  xqshare-server 未在运行（未找到 PID 文件）")
        return

    with open(pid_file) as f:
        pid_str = f.read().strip()

    try:
        pid = int(pid_str)
    except ValueError:
        print(f"  PID 文件内容无效: {pid_str}")
        os.remove(pid_file)
        return

    try:
        if sys.platform == "win32":
            import subprocess
            subprocess.call(["taskkill", "/F", "/PID", str(pid)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        else:
            os.kill(pid, signal.SIGTERM)
        print(f"  xqshare-server 已停止 (PID: {pid})")
    except OSError:
        print(f"  进程 {pid} 不存在（可能已停止）")
    finally:
        if os.path.exists(pid_file):
            os.remove(pid_file)


def _cmd_logs(args):
    """查看 server 日志"""
    log_file = _get_log_file()

    if not os.path.exists(log_file):
        print(f"  日志文件不存在: {log_file}")
        print("  请先启动 server: xqshare-server background")
        return

    if args.follow:
        # 实时跟踪日志（类似 tail -f）
        import time
        print(f"  正在跟踪日志: {log_file}  (按 Ctrl+C 退出)\n")
        with open(log_file, "r", encoding="utf-8", errors="replace") as f:
            # 先输出最后 N 行
            lines = f.readlines()
            for line in lines[-args.lines:]:
                print(line, end="")
            # 持续跟踪新内容
            try:
                while True:
                    line = f.readline()
                    if line:
                        print(line, end="", flush=True)
                    else:
                        time.sleep(0.3)
            except KeyboardInterrupt:
                print("\n  已退出日志跟踪")
    else:
        # 只输出最后 N 行
        with open(log_file, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
        for line in lines[-args.lines:]:
            print(line, end="")


def _cmd_status(args):
    """查看 server 运行状态"""

    pid_file = _get_pid_file()
    log_file = _get_log_file()

    if not os.path.exists(pid_file):
        print("  状态: 未运行")
        return

    with open(pid_file) as f:
        pid_str = f.read().strip()

    try:
        pid = int(pid_str)
        running = False
        if sys.platform == "win32":
            import ctypes
            handle = ctypes.windll.kernel32.OpenProcess(0x0400, False, pid)
            if handle:
                ctypes.windll.kernel32.CloseHandle(handle)
                running = True
        else:
            os.kill(pid, 0)
            running = True
    except (ValueError, OSError, AttributeError):
        running = False

    if running:
        print(f"  状态: 运行中 ✓")
        print(f"  PID:  {pid}")
        print(f"  日志: {log_file}")
        print(f"  查看日志: xqshare-server logs -f")
    else:
        print(f"  状态: 已停止（PID 文件残留: {pid}）")
        print(f"  执行 xqshare-server stop 清理残留文件")


def _default_env_file():
    """默认 .env 文件路径：优先查找 exe/脚本所在目录，其次当前目录"""
    # 获取可执行文件所在目录（pip install 后是 Scripts/ 目录）
    exe_dir = os.path.dirname(os.path.abspath(sys.executable))
    exe_env = os.path.join(exe_dir, ".env")
    if os.path.exists(exe_env):
        return exe_env
    return ".env"


def main():
    """命令行入口函数"""
    import argparse

    # Windows 终端默认 GBK，统一切换 stdout/stderr 为 UTF-8，避免中文乱码
    if sys.platform == "win32":
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
        except AttributeError:
            import io
            sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
            sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(
        description="XtQuant Share (xqshare) 服务",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  xqshare-server setup                # 初始化向导（首次使用推荐）
  xqshare-server start                # 后台启动（推荐）
  xqshare-server start --port 18813   # 指定端口后台启动
  xqshare-server stop                 # 停止后台服务
  xqshare-server status               # 查看运行状态
  xqshare-server logs                 # 查看最近 50 行日志
  xqshare-server logs -f              # 实时跟踪日志（tail -f）
  xqshare-server logs -n 100          # 查看最近 100 行日志
  xqshare-server fg                   # 前台启动（调试用）

环境变量:
  XQSHARE_PORT      服务端口 (默认: 18812)
  QMT_USERDATA_PATH QMT userdata_mini 目录路径

.env 文件查找顺序:
  1. xqshare-server.exe 所在目录（如 C:\\install\\qmt_venv\\Scripts\\.env）
  2. 当前工作目录
        """
    )

    subparsers = parser.add_subparsers(dest="command")

    # 公共参数（start 和 fg 共用）
    def add_server_args(p):
        p.add_argument("--host", default="0.0.0.0", help="监听地址 (默认: 0.0.0.0)")
        p.add_argument("--port", type=int, default=None, help="监听端口 (默认: 18812 或 XQSHARE_PORT)")
        p.add_argument("--ssl", action="store_true", help="启用 SSL 加密")
        p.add_argument("--cert", help="SSL 证书文件")
        p.add_argument("--key", help="SSL 私钥文件")
        p.add_argument("--log-level", default="INFO", help="日志级别 (默认: INFO)")
        p.add_argument("--env-file", default=None, help="环境变量文件 (默认: 自动查找 exe 目录或当前目录的 .env)")

    # start 子命令（后台，推荐）
    start_parser = subparsers.add_parser("start", help="后台启动 server（推荐）", aliases=["background", "bg"])
    add_server_args(start_parser)

    # fg 子命令（前台，调试用）
    fg_parser = subparsers.add_parser("fg", help="前台启动 server（调试用）", aliases=["foreground"])
    add_server_args(fg_parser)

    # help 子命令
    subparsers.add_parser("help", help="显示帮助信息")

    # stop 子命令
    subparsers.add_parser("stop", help="停止后台运行的 server")

    # status 子命令
    subparsers.add_parser("status", help="查看 server 运行状态")

    # setup 子命令
    subparsers.add_parser("setup", help="初始化向导：自动探测 miniQMT 并生成 .env / clients.yaml / qmt_watchdog.py")

    # logs 子命令
    logs_parser = subparsers.add_parser("logs", help="查看 server 日志")
    logs_parser.add_argument("-f", "--follow", action="store_true", help="实时跟踪日志（类似 tail -f）")
    logs_parser.add_argument("-n", "--lines", type=int, default=50, help="显示最后 N 行 (默认: 50)")

    # 顶层也保留 server 参数（兼容旧用法：xqshare-server --port 18813）
    parser.add_argument("--host", default="0.0.0.0", help="监听地址 (默认: 0.0.0.0)")
    parser.add_argument("--port", type=int, default=None, help="监听端口 (默认: 18812 或 XQSHARE_PORT)")
    parser.add_argument("--ssl", action="store_true", help="启用 SSL 加密")
    parser.add_argument("--cert", help="SSL 证书文件")
    parser.add_argument("--key", help="SSL 私钥文件")
    parser.add_argument("--log-level", default="INFO", help="日志级别 (默认: INFO)")
    parser.add_argument("--env-file", default=None, help="环境变量文件 (默认: 自动查找 exe 目录或当前目录的 .env)")

    args = parser.parse_args()

    # --env-file 未指定时自动查找
    if not hasattr(args, 'env_file') or args.env_file is None:
        args.env_file = _default_env_file()

    if args.command == "help":
        parser.print_help()
    elif args.command == "setup":
        _cmd_setup(args)
    elif args.command in ("start", "background", "bg"):
        _cmd_background(args)
    elif args.command == "stop":
        _cmd_stop(args)
    elif args.command == "status":
        _cmd_status(args)
    elif args.command == "logs":
        _cmd_logs(args)
    else:
        # fg / foreground 或无子命令，均为前台启动
        start_server(
            host=args.host,
            port=args.port,
            use_ssl=args.ssl,
            certfile=args.cert,
            keyfile=args.key,
            log_level=args.log_level,
            env_file=args.env_file
        )


if __name__ == "__main__":
    main()