# 交接：xqshare server 长时间运行内存泄漏（使用方 QmtQuant 实测报告）

> 本文档由使用方项目 **QmtQuant** 的排查会话生成，交接给 xqshare 项目。
> xqshare 侧的 AI 读本文即可接手，无需追问背景。行号基于 2026-09 安装的 xqshare 版本，若已变动以实际代码为准。

## 零、核心结论（TL;DR）

xqshare server（python）在 233 调试机上长跑后**虚拟内存膨胀到 27.6GB + 23.9GB**，触发 Windows 系统资源耗尽（Event 2004），最终导致整机卡死（Event 41 意外重启）。

**最可疑泄漏点**：`_QuoteEventCallback._subscribers`（`server.py:566`）的 netref 累积——

1. 每次 `subscribe_whole_quote` / `subscribe_quote` 服务端都执行 `quote_event_callback.register(client_callback)`（`server.py:794/799`）
2. `register` 用 `id(netref)` 作 key（`server.py:618`），客户端重连后新 netref 的 id 不同 → 旧 netref 不覆盖、直接累积
3. `on_disconnect`（`server.py:915`）**只清理 trader 实例，不清理 quote subscriber**
4. 唯一清理路径是「推送失败时 unregister」（`server.py:614`），需要 tick 推送才触发——收盘/停牌/无订阅行情时永不触发

反复「断线 → 重连 → 重订阅」，`_subscribers` 持续累积，netref 又反向持有 RPyC 连接引用，内存只增不减。

## 一、问题一句话

xqshare server 长时间运行后内存（虚拟内存）线性增长，最终膨胀到 27GB+，把宿主 Windows 机器（15.3GB 物理 + 4GB pagefile = 19GB commit limit）的内存耗尽，导致整机卡死。

## 二、背景：使用方（QmtQuant）的运行环境

- 宿主：Windows 调试机 `192.168.31.233`（平安证券 QMT），15.3GB 物理内存 + 4GB pagefile
- xqshare 实例：**两个**（平安 `xqshare-server` nssm 服务监听 18812；国金 `xqshare-gjzq` 计划任务监听 18813，2026-09-27 已随国金 QMT 卸载清理）
- 使用方客户端：QmtQuant Dashboard（`dashboard/server/live_quote.py`），通过 `from env import xtdata` 连 xqshare
- 客户端行为：`LiveQuoteHub._refresh_subscription` **每 30 秒**调一次 `subscribe_whole_quote`（B211 订阅刷新机制，用 seq 回退感知服务端重启）

## 三、现象

- 233 间歇卡死：SSH 登录不上、Dashboard 无响应，只能强制重启
- Windows 事件日志 `Event 2004（Resource-Exhaustion-Detector）` 点名元凶：

| 时间 | 进程 | 虚拟内存 |
|------|------|---------|
| 09-19 20:31 | `python.exe`（xqshare server ×2） | **27.6GB + 23.9GB** |
| 09-23 09:35 | `miniquote.exe`（QMT 客户端，另一元凶，非本仓）+ `python.exe` | 49.75GB + 5.3GB |

- `Event 41（Kernel-Power 意外重启）` 多次出现，印证卡死强制重启

> 注：miniquote.exe（迅投 QMT 客户端行情进程）是**另一个独立元凶**，属第三方程序不在本仓，已在 QmtQuant 侧通过卸载国金 QMT 缓解。本交接只覆盖 **python（xqshare server）** 这一路。

## 四、根因分析（由浅入深）

### 根因 1：quote subscriber 注册「只进不出」

`LoggingProxy.__getattr__`（`server.py:788-799`）拦截 xtdata 订阅，每次订阅都注册客户端回调：

```python
if target_name == 'xtdata' and quote_event_callback is not None:
    if name in _QUOTE_SUBSCRIBE_CALLBACK_POS:
        pos = _QUOTE_SUBSCRIBE_CALLBACK_POS[name]
        ...
        quote_event_callback.register(client_callback)   # 每次订阅都 register
```

而 `_QuoteEventCallback.register`（`server.py:616-619`）：

```python
def register(self, client_callback):
    with self._lock:
        self._subscribers[id(client_callback)] = client_callback   # key = id(netref)
```

- `client_callback` 从服务端看是 RPyC **反向 netref**（指向客户端 `RemoteQuoteCallback`）
- 客户端重连（RPyC 连接重建）后，同一逻辑回调在服务端拿到的是**新的 netref 对象**，`id()` 不同 → 旧 entry 不覆盖、直接新增
- 于是 `_subscribers` 随「断线→重连→重订阅」单调累积

### 根因 2：on_disconnect 不清理 quote subscriber

`XtQuantService.on_disconnect`（`server.py:915-932`）只遍历清理 `self._traders`（本次连接的 trader 实例），**完全没有碰类级单例 `_QuoteEventCallback._subscribers`**。

对比：`_TraderEventCallback` 有 `unregister(account_id, callback)`（`server.py:470`）且在断开/取消订阅时调用（`server.py:783`）；`_QuoteEventCallback.unregister`（`server.py:621-623`）存在，但**只在「推送到客户端失败」时被调用**（`server.py:614`）。

### 根因 3：唯一清理路径依赖「有 tick 推送」

`_QuoteEventCallback._dispatch_loop`（`server.py:595-614`）只在队列非空时才遍历 subscribers 推送，推送失败才 `unregister`：

```python
for cb in callbacks:
    try:
        cb.exposed_on_quote_event(payload)
    except Exception as e:
        self.unregister(cb)   # 只有推送失败才清理
```

- 客户端断开后，若**没有 tick 到达**（收盘、停牌、订阅集空、或客户端已不订阅任何活跃标的），`_dispatch_loop` 队列空 → 永远不推送 → 永远不触发清理
- 断开的客户端 netref 永久残留

### 根因 4（次要）：日志量巨大

- `api_logger` 设为 DEBUG（`server.py:114`），`_log_call` 对每次 API 调用都打 `[CALL]/[OK]/[ERROR]`（`server.py:166/173/177`）
- `_QuoteEventCallback.on_data` 每次 tick 打 `logger.debug`（`server.py:593`）
- 实测 `nssm-stderr.log` 膨胀到 253MB，`api_calls_YYYYMMDD.log` 每日 8-12MB
- 不直接吃内存，但高频字符串格式化 + 磁盘 I/O 拖慢服务、放大问题

### 泄漏链（完整串联）

```
Dashboard 每 30s subscribe_whole_quote（B211 刷新）
   → 服务端 register(client_callback)   # netref 入 _subscribers
xqshare 连接抖动 / Dashboard 重启
   → on_disconnect 只清 trader，quote subscriber 残留
   → 重连后重新 register 新 netref（id 不同，旧的不覆盖）
   → _subscribers 累积（旧 netref 反向持有 RPyC 连接，内存不释放）
无 tick 时永不触发 unregister → 泄漏不可逆
长跑数天 → 虚拟内存 27GB+ → 系统 commit 超限 → 整机卡死
```

## 五、实测证据

| 项 | 结果 |
|----|------|
| xqshare server 虚拟内存峰值 | 27.6GB + 23.9GB（Event 2004，09-19） |
| 宿主 commit limit | 19.16GB（15.3GB 物理 + 4GB pagefile） |
| Dashboard 订阅刷新频率 | 每 30 秒一次 `subscribe_whole_quote`（nssm-stderr.log 实证） |
| api_calls 日志 | 每日 8-12MB，DEBUG 级 |
| `_subscribers` 清理 | 仅「推送失败」时触发，on_disconnect 不清理 |

## 六、xqshare 侧建议的修复方向（供排期，按性价比排序）

1. **【高】`on_disconnect` 清理 quote subscriber**：在 `server.py:915` 的 `on_disconnect` 里，把「本连接注册过的 quote 回调」从 `_QuoteEventCallback._subscribers` 移除。需要维护「client_info → 回调 id」映射，或让 register 时记录 client_info。
2. **【高】register 幂等化**：同一 `client_info` 重复 register 时覆盖旧 entry（而非按 `id(netref)` 累积），从源头避免累积。
3. **【中】`_subscribers` 加水位/上限告警**：注册数超过阈值打 warning，便于观察是否仍有泄漏。
4. **【中】日志降噪**：`api_logger` 从 DEBUG 降到 INFO（或对 `subscribe_whole_quote` 这类高频调用单独降级），`on_data` 的每-tick debug 日志改成可关闭或采样。
5. **【低】`_queue` 背压**：`_dispatch_loop` 推送到慢客户端时，确认不会阻塞队列消费导致 `_queue` 累积（当前 batch 内逐条同步推送，慢客户端会拖慢整批，极端时队列增长）。

## 七、相关代码位置索引

| 位置 | 内容 |
|------|------|
| `xqshare/server.py:566` | `_QuoteEventCallback._subscribers`（netref 累积点） |
| `xqshare/server.py:568` | `_QuoteEventCallback._queue`（tick 队列） |
| `xqshare/server.py:573-580` | `__call__` → `on_data`（tick 入口） |
| `xqshare/server.py:582-593` | `on_data`：JSON 序列化 + 入队 + debug 日志 |
| `xqshare/server.py:595-614` | `_dispatch_loop`：消费队列，推送失败才 unregister |
| `xqshare/server.py:616-623` | `register`/`unregister`（按 `id(netref)`） |
| `xqshare/server.py:630-634` | `_QUOTE_SUBSCRIBE_CALLBACK_POS`（订阅接口 callback 参数位置） |
| `xqshare/server.py:788-799` | `LoggingProxy` 拦截 xtdata 订阅并 `register` |
| `xqshare/server.py:879` | `_quote_event_callback` 类级单例初始化 |
| `xqshare/server.py:891-932` | `on_connect`/`on_disconnect`（仅清理 trader，漏 quote） |
| `xqshare/server.py:114` | `api_logger` DEBUG 级 |
| `xqshare/client.py:373-377` | `_get_quote_callback`（客户端侧单例，复用不累积） |
| `xqshare/client.py:418-434` | `_wrap_call` 对 xtdata 订阅自动挂 quote 回调 |

## 八、使用方侧（QmtQuant）当前状态

- 已卸载国金 QMT，233 只剩平安 QMT 一套（行情+交易都走平安），少了一个 xqshare 实例，泄漏面减半
- 国金 xqshare 残留（计划任务 `xqshare-gjzq` + `.env.gjzq` + 进程）已清理
- 平安 miniquote（第三方 QMT 客户端）是另一独立泄漏源，QmtQuant 侧持续观察，必要时加内存监控预警 / 定时重启
- 本缺口在 QmtQuant 侧登记为 backlog B280，xqshare 侧修好后 QmtQuant 验证「长跑内存稳定」即可闭环

## 九、xqshare 侧修复记录（2026-09-27）

已按本文第六节「修复方向」落地前三项（高/中性价比），其余为排期项：

1. **【高】register 幂等化 + 记录 client_info**：`_QuoteEventCallback._subscribers` 的 value 从裸 netref 改为 `{callback, client_info}`；`register(client_callback, client_info)` 对同一 `client_info` 先移除旧 entry 再写入，断线重连后的新 netref 不再累积。`LoggingProxy` 拦截订阅时传入 `get_client_info()`。
2. **【高】on_disconnect 清理 quote subscriber**：`XtQuantService.on_disconnect` 调用 `_QuoteEventCallback.clear_client_callbacks(client_info)`，按连接移除该客户端注册的全部 quote 回调（quote 单例未初始化时跳过，不懒创建）。
3. **【中】水位告警**：新增 env `XQSHARE_MAX_QUOTE_SUBSCRIBERS`（默认 500），超过阈值打 WARNING（回落复位，可反复告警）。

4. **【高】`_TraderEventCallback` 线程泄漏**（排查过程中发现的连带泄漏，非文档第六节所列）：每个 trader 独立创建的事件路由器含一个 daemon dispatch 线程，线程持回调对象与 netref，`on_disconnect` 只 stop 了 trader 没 stop 回调 → 线程随断线累积。修复：`self._traders` 改存 `(trader, event_callback)` 元组，`on_disconnect` 一并 `event_callback.stop()`；`create_trader_and_connect`（mini/bigqmt）的 start/connect/超时失败路径也在抛异常前 `callback.stop()`。

未做（排期）：
5. 日志降噪（api_logger DEBUG → INFO、on_data 每-tick debug 采样）。
6. `_queue` 背压（慢客户端拖慢整批的极端场景）。

验证：`tests/test_quote_events.py` 新增幂等注册 / 按连接清理 / on_disconnect 清理 / 未初始化跳过 4 组用例；`tests/test_server.py` 新增 `TestTraderCallbackCleanup`（on_disconnect 停线程 / stop 终止线程 / stop 幂等）3 组用例，全绿。QmtQuant 侧需重启 xqshare server 并在 233 长跑验证内存稳定后闭环 B280。

