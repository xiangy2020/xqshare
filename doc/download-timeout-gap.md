# 交接：download_* 同步阻塞下载无超时缺口（盘后挂起堵死后续下载）

> 本文档由使用方项目 **QmtQuant** 的排查会话生成，交接给 xqshare 项目。
> xqshare 侧的 AI 读本文即可接手，无需追问背景。行号基于 2026-09 安装的 xqshare 版本（v1.2.41+），若已变动以实际代码为准。

## 零、一句话结论

xqshare server 上**所有 `download_*` 类调用**（`download_sector_data` / `download_history_data2` / `download_financial_data` / `download_history_data` / `download_metatable_data` / `download_holiday_data`）都是**同步透传到底层 xtquant，没有超时、没有取消机制**。盘后平安 QMT 挂起时，这些调用会**永久阻塞 xqshare worker 线程**（实测 28 小时，直到 QMT 进程被杀才被迫返回），并堵死后续所有下载请求。需要给 download 类调用统一加「超时 + 独立线程隔离」。

## 一、问题一句话

QmtQuant 每日盘后调度（17:00 核心数据链、20:00 分钟线、21:00 扩展节点）触发 download 下载时，若平安 QMT 盘后服务器不响应，download 调用在 xqshare server 侧挂起，QmtQuant 侧 300 秒 RPC 超时只能「放弃等待」，但 xqshare 底层线程仍永久占用，导致当天及后续所有下载节点失败。

## 二、背景：使用方（QmtQuant）怎么调 download

QmtQuant 通过 `env.py` 统一走 xqshare 连接（`from env import xtdata`），下载入口分散在 `data_manager/sync_extended.py`：

| 下载调用 | 所在函数 | 调度节点 |
|---------|---------|---------|
| `xtdata.download_sector_data()` | `sync_index_weight` | index/weight |
| `xtdata.download_financial_data(symbols, [sub])` | `sync_financial_data` | stock/financial |
| `xtdata.download_history_data(sym, "etfiopv1d", ...)` | `sync_etf_iopv` | meta/etf-iopv |
| `xtdata.download_metatable_data()` | `sync_factors` | factor/value |
| `xtdata.download_holiday_data()` | `sync_holidays` | meta/holiday |
| `xtdata.download_history_data(北向, "northfinancechange1d", ...)` | `sync_north_finance` | stock/northsouth |

QmtQuant 侧的 xqshare client 已配 `sync_request_timeout: 300`（`xqshare/client.py:643`），300 秒超时后抛 `TimeoutError: result expired`，**放弃等待**——但这是「客户端不等了」，不是「服务端取消底层调用」。

## 三、现象（2026-09-17/18 生产实测）

xqshare 日志（`C:\Install\xqshare\logs\xtquant_service_20260916.log`）关键证据：

```
2026-09-17 19:25:39  [CALL] download_sector_data   ← 发起后永久挂起
2026-09-18 19:06:42  [CALL] download_sector_data   ← 又挂起一个
2026-09-18 20:52:53  [CALL] download_history_data2 ← 1m 分钟线也被堵死
2026-09-18 23:25:33  [OK] download_sector_data | elapsed=100796810.21ms   ← 挂 28 小时，关机才返回
2026-09-18 23:25:33  [OK] download_sector_data | elapsed=15530681.38ms    ← 挂 4.3 小时
```

同时段 QmtQuant 侧报错（`logs/nssm-stderr.log`）：

```
2026-09-17 19:30:39  [ERROR] xtdata.download_sector_data | 300012.27ms | TimeoutError: result expired
2026-09-18 19:11:42  [ERROR] xtdata.download_sector_data | 300014.27ms | TimeoutError: result expired
```

后果链：`download_sector_data` 盘后挂起 → 堵死 QMT 下载通道 → 后续 `download_history_data2`（1m 分钟线）也卡死 → 当天 19:17/21:02 两轮调度任务失败。

## 四、根因：xqshare 侧 download 调用同步透传、无超时

download 调用在 xqshare server 侧有**两条路径**，都无超时：

### 路径 1：显式 exposed 方法（sector_data / history_data2）

```python
# xqshare/server.py:1242  exposed_download_sector_data
#   ...
#   server.py:1356
result = self._xtdata.download_sector_data()   # ← 直接同步调用，无超时

# xqshare/server.py:1407  exposed_download_history_data2
#   ...
#   server.py:1445
self._xtdata.download_history_data2(stock_list, period, start_time, end_time, **kwargs)  # ← 无超时
```

### 路径 2：LoggingProxy 直通（financial_data / history_data / metatable / holiday）

未单独 exposed 的 download 方法（`download_financial_data`、`download_history_data`、`download_metatable_data`、`download_holiday_data`）走 `LoggingProxy.__getattr__` 的通用 wrapper：

```python
# xqshare/server.py:658  LoggingProxy.__getattr__
#   ...（无 exposed_<name> 时）attr = getattr(target, name)
#   server.py:743
result = _log_call(full_name, get_client_info, attr, *args, **kwargs)   # ← 直接同步调用，无超时
```

### 根因归纳

1. **平安 QMT 盘后挂起**（券商客户端层，使用方无法直接修）：盘后券商行情服务器对板块/财务/分钟线下载请求不响应，xtquant 的 C 库调用无限挂起。
2. **xqshare server 同步透传无超时**（本文档要修的）：上述两条路径都是「直接调 `self._xtdata.download_*`」或「直接 `_log_call` 透传」，无超时、无线程隔离、无取消。
3. **使用方 300s 超时治标不治本**：`sync_request_timeout: 300` 只让 QmtQuant client 放弃等待，xqshare server 的 worker 线程仍堵在 download 里，且会占住下载通道堵死后续请求。

## 五、xqshare 侧出路（供排期）

### 核心：download 类调用统一加「超时 + 独立 daemon 线程隔离」

在 xqshare server 侧加一个超时包装器，download 类调用统一经它执行：

```python
import threading

# 需要节流/超时的 download 类方法名
DOWNLOAD_METHODS = {
    'download_sector_data', 'download_history_data', 'download_history_data2',
    'download_financial_data', 'download_metatable_data', 'download_holiday_data',
}
DOWNLOAD_TIMEOUT_SECONDS = 600   # 建议可配置（env 变量 XQSHARE_DOWNLOAD_TIMEOUT）

def _call_download_with_timeout(fn, *args, timeout=DOWNLOAD_TIMEOUT_SECONDS, **kwargs):
    """在独立 daemon 线程执行下载，主线程带超时等待。

    超时后返回/抛错释放 rpyc 响应，底层 daemon 线程继续挂起（进程退出自动回收），
    不影响后续其他下载/查询请求。
    """
    holder = {}
    def _worker():
        try:
            holder['result'] = fn(*args, **kwargs)
        except Exception as e:
            holder['error'] = str(e)
    name = getattr(fn, '__name__', 'download')
    t = threading.Thread(target=_worker, daemon=True, name=f"xq-download-{name}")
    t.start()
    t.join(timeout)
    if t.is_alive():
        raise TimeoutError(f"{name} 超时（{timeout}s），底层 QMT 调用仍在挂起")
    if 'error' in holder:
        raise RuntimeError(holder['error'])
    return holder.get('result')
```

**落地两处**：

1. **LoggingProxy.wrapper**（`server.py:743` 附近）：对 `target_name == 'xtdata' and name in DOWNLOAD_METHODS` 的方法，把 `_log_call(...)` 包进 `_call_download_with_timeout`。这样 `download_financial_data` / `download_history_data` / `download_metatable_data` / `download_holiday_data` 四类直通方法自动获得超时。

2. **两个显式 exposed 方法**内部（不走 LoggingProxy wrapper）：
   - `exposed_download_sector_data`（`server.py:1356`）：`result = _call_download_with_timeout(self._xtdata.download_sector_data)`
   - `exposed_download_history_data2`（`server.py:1445`）：`self._call_download_with_timeout(self._xtdata.download_history_data2, stock_list, period, start_time, end_time, **kwargs)`

### 超时后对使用方的契约

超时时抛 `TimeoutError`（或返回 `{"success": False, "error": "...超时..."}`，与 `exposed_download_sector_data` 现有失败结构一致）。QmtQuant 侧已有 try/except（`sync_extended.py` 各 `download_*` 调用处都有 `except Exception → logger.warning`），超时会被捕获并计为失败，不会中断整批调度。**无需改 QmtQuant 侧**，只需保证抛的是 Exception 子类。

## 六、难点与边界（重要，务必读）

1. **xtquant C 库调用无法被 Python 强制中断**：`download_sector_data` 等是 xtquant 的 C 扩展调用，Python 侧 `thread.join(timeout)` 超时后，底层线程**无法真正取消**，只能「放弃等待 + 释放 rpyc 响应」。挂起的 daemon 线程会残留，直到 QMT 进程（或 xqshare 进程）重启才清干净。

2. **挂起线程会累积**：每次超时都会留下一个挂起的 daemon 线程。虽然 daemon 线程不阻塞新请求（每次 `_call_download_with_timeout` 新建线程），但若 QMT 长期盘后挂起且每天触发，会累积挂起线程（每个只占少量栈内存，风险可控，但需知晓）。

3. **超时阈值选择**：正常盘后下载可能耗时较长（板块/财务全量下载），`DOWNLOAD_TIMEOUT_SECONDS` 建议设 600s（10 分钟）起步，并支持 env 变量覆盖。设太短会误伤正常下载。

4. **治本不在本缺口**：本缺口解决「挂起不堵死后续」，但「盘后为何挂起」是平安 QMT 券商侧行为。真正的规避是 QmtQuant 侧把含 download 的调度从 17:00 盘后改到盘中（QmtQuant 侧已另行评估）。

## 七、相关文档位置索引

| 位置 | 内容 |
|------|------|
| xqshare `xqshare/server.py:639-771` | `LoggingProxy`（`__getattr__` wrapper，`server.py:743` 同步调用点） |
| xqshare `xqshare/server.py:1242-1402` | `exposed_download_sector_data`（`server.py:1356` 无超时调用） |
| xqshare `xqshare/server.py:1407-1449` | `exposed_download_history_data2`（`server.py:1445` 无超时调用） |
| xqshare `xqshare/server.py:2009-2017` | `sync_request_timeout: 300`（protocol_config，仅影响 rpyc 层超时） |
| xqshare `xqshare/client.py:643` | 客户端 `sync_request_timeout: 300` |
| QmtQuant `data_manager/sync_extended.py` | 各 download 调用入口（`sync_index_weight` / `sync_financial_data` / `sync_etf_iopv` / `sync_factors` / `sync_holidays` / `sync_north_finance`） |
| QmtQuant `dashboard/server/updater.py:68-79` | `_EXT_NODE_MAP`（节点 → 扩展 sync 函数映射） |
| QmtQuant `dashboard/server/scheduler_api.py` | 调度任务 CRUD（233 机器任务配置：核心数据 17:00 / 分钟线 20:00 / 扩展节点 21:00 周六） |

## 八、补充：QmtQuant 侧已做的配套（不依赖本缺口）

- 已给 `download_sector_data` / `download_financial_data` 加「每周最多一次」节流（`sync_extended.py` 的 `_should_download_meta` / `_mark_meta_downloaded`），降低卡死触发频率，但**不解决卡死本身**。
- 本缺口修好后，节流仍建议保留（减少无谓下载），两者互补。
