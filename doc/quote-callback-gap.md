# 交接：xtdata 行情订阅回调缺口（使用方 QmtQuant 实测报告）

> 本文档由使用方项目 **QmtQuant** 的排查会话生成，交接给 xqshare 项目。
> xqshare 侧的 AI 读本文即可接手，无需追问背景。行号基于 2026-08 安装的 xqshare 版本，若已变动以实际代码为准。

## 零、最新状态（2026-08-19 更新）

**方向 2（server 端 quote 事件路由）已实现**，死锁根因已精确定位。要点：

- **死锁根因**：`subscribe_whole_quote(codes, callback=本地bound method)` 时，RPyC 的 `brine.dumpable` 只认简单类型，bound method 和 dict 都走 `LABEL_REMOTE_REF`，成为**反向 netref 引用**。于是 callback 是 netref、回调里的 `data` 也是 netref，客户端 `_on_xtdata_tick` 在 `with self._lock:` 内遍历 netref dict（`.items()`/`.get()` 全是同步 RPC 往返），持锁期间做网络 I/O，300 只 × 逐元素往返导致锁长期持有 → 服务卡死。实测（本地回环）300 只单批次即 170ms，跨 VM 网络到秒级。
- **修复**：server 端新增 `_QuoteEventCallback`，拦截 `xtdata.subscribe_whole_quote` / `subscribe_quote` 的 callback，把 tick 数据 **JSON 序列化成字符串**（str 按值传输）再反向推送；客户端 `RemoteQuoteCallback` 收到后 `json.loads` 还原成**本地 dict** 再分发。用户回调拿到的是值拷贝，锁内遍历是纯本地操作，死锁消除。
- **使用方接入**：见第八节。xqshare 侧新增 `xtdata.register_quote_callback(callback)` 与模块级 `xqshare.client.register_quote_callback(callback)`。

## 一、问题一句话

README 声称「✅ 异步回调 - 支持行情订阅等回调场景」，但实测 **xtdata 行情（quote）回调从未打通**：QmtQuant 实时行情页订阅 300 只股票，tick 数据推不回来，快照全是 null。

## 二、背景：使用方（QmtQuant）怎么用

QmtQuant 的 `dashboard/server/live_quote.py` 通过 `from env import xtdata`（env.py 统一走 xqshare 连接）做实时行情：

1. 启动时 `subscribe_quote(stock_code=300只, period='tick', callback=...)` 订阅默认组（hs300 + 持仓）
2. 期望回调收到 tick 后写入内存表，再 SSE 推给前端

预期回调数据格式：`{stock_code: tick_dict}`，tick_dict 字段 `lastPrice / lastClose / open / high / low / volume / amount / time`。

## 三、现象

- `subscribe_quote` 批量订阅 300 只，返回 `-1`（失败），tick 推不回来
- 快照接口返回的 300 只股票，所有字段（price/open/high/low/...）全是 `null`

## 四、根因（三层，由浅入深）

### 根因 1：subscribe_quote 是「单股订阅」接口，批量必失败

`xtdata.subscribe_quote` 的 `stock_code` 参数是 **string（单只）**，且官方文档明确「单股订阅数量建议不超过 50」。批量传 300 只的 list 必然返回 `-1`。

**正确接口是 `subscribe_whole_quote(code_list, callback)`（全推行情）**，`code_list` 支持合约代码列表批量订阅。实测它返回 `1`（订阅成功）。

### 根因 2：xqshare 只有 xttrader 回调，没有 xtdata quote 回调

xqshare 目前的回调机制只覆盖交易（xttrader），**没有任何 xtdata 行情回调**：

- `client.py:356` 只有 `register_trader_callback(event_name, callback)`，支持的事件仅 `on_stock_asset / on_stock_order / on_stock_trade / on_stock_position`（`client.py:214-216` 的 `RemoteTraderCallback.SUPPORTED_EVENTS`）
- `client.py:325-337` `_wrap_call` 里只对 `xttrader` 模块做特殊处理（`register_callback` 忽略、`subscribe/unsubscribe` 自动挂 `RemoteTraderCallback`）
- `server.py:589-607` `TargetProxy.__getattr__` 只对 `target_name == 'xttrader'` 做事件路由特殊处理，xtdata 的 `subscribe_quote` / `subscribe_whole_quote` 是**纯透传**（callback 参数原样交给 xtquant）
- `server.py:414-462` 只有 `TraderEventRouter`（xttrader 事件推送），无 quote 事件路由

**结论**：xqshare 的「异步回调」能力目前只实现了交易侧，行情侧是空白的。QmtQuant 侧的 PR14 曾调用 `xqshare.client.register_quote_callback(...)`，该 API **不存在**，回调注册永远抛 AttributeError。

### 根因 3（实测新增）：subscribe_whole_quote + 反向通道 callback 会死锁

QmtQuant 侧尝试用 `subscribe_whole_quote(codes, callback=self._on_xtdata_tick)`（callback 传本地 bound method），结果：

- 订阅成功（返回 1）
- 但 **服务整体卡死**：`sample` 显示主线程（uvicorn 事件循环）卡在 `rlock_acquire` 等 `LiveQuoteHub._lock`，多个线程同时等锁——tick 反向回调与事件循环发生锁竞争死锁

相关事实：
- `client.py:530` config 含 `allow_all_attrs: True`（反向通道无需 exposed_ 前缀）
- `client.py:549` `BgServingThread(self._conn)` 已启动（反向通道已就绪）

## 五、实测证据

| 项 | 结果 |
|----|------|
| `subscribe_quote(300只, callback=...)` | 返回 `-1`（失败） |
| `subscribe_whole_quote(300只, callback=...)` | 返回 `1`（成功） |
| callback 走 RPyC 反向通道 | 服务死锁，主线程卡 `rlock_acquire` |

## 六、xqshare 侧需要做的（3 个方向，供排期）

1. ✅ **排查 RPyC 反向通道死锁**（已完成）：根因是 bound method 和 tick dict 都被 RPyC 当成反向 netref，客户端在锁内遍历 netref 做逐元素同步 RPC。详见「零、最新状态」。
2. ~~**提供拉式接口**~~（未实施，方向 3 已根治，无需绕开）：`get_full_tick` 仍可作为轮询备选。
3. ✅ **server 端加 quote 事件路由**（已完成）：新增 `_QuoteEventCallback`（`server.py`），仿 `_TraderEventCallback`，在 server 端拦截 xtdata 订阅、把 tick 事件 JSON 序列化后转发给已注册的客户端回调，客户端 `RemoteQuoteCallback`（`client.py`）还原为本地 dict 分发。

## 七、相关代码位置索引

| 位置 | 内容 |
|------|------|
| `client.py:530` | RPyC config（`allow_all_attrs: True`） |
| `client.py:549` | `BgServingThread` 启动（反向通道） |
| `client.py:214-216` | `RemoteTraderCallback.SUPPORTED_EVENTS`（仅交易事件） |
| `client.py:325-337` | `_wrap_call` 的 xttrader 特殊处理 |
| `client.py:356` | `register_trader_callback`（仅 xttrader） |
| `server.py:414-462` | `TraderEventRouter`（仅 xttrader） |
| `server.py:589-607` | `TargetProxy.__getattr__` 的 xttrader 特殊处理（无 xtdata 对应） |

## 八、使用方侧（QmtQuant）的当前状态

- 实时行情体验改造已完成（收窄订阅、盘口字段、涨跌色、自选/行情拆分），但 tick 数据链路仍断（本缺口）
- QmtQuant 侧临时回退为「不传 callback 的订阅」，服务稳定但无 tick 数据
- 本缺口在 QmtQuant 侧登记为 backlog B007，xqshare 侧修好后，QmtQuant 侧需重新接入 `subscribe_whole_quote` 的正确回调

### 重新接入方式（xqshare 侧已就绪）

xqshare 新增行情回调能力后，QmtQuant 侧接入分两步：

```python
from env import xtdata

# 1. 注册行情回调（替代原 _xqclient.register_quote_callback(svc)）
xtdata.register_quote_callback(QuoteCallbackService(hub).on_quote)

# 2. 订阅全推行情（不再传本地 callback，由 xqshare 客户端自动接管）
xtdata.subscribe_whole_quote(codes)   # codes: list[str]
```

要点：

- **不要**再在 `subscribe_whole_quote` 里传本地 `callback=`（会重新引入 netref 反向引用死锁）；回调统一走 `register_quote_callback`。
- 回调签名仍为 `callback(data)`，`data = {stock_code: tick_dict}`，且 **data 是本地 dict**（服务端已 JSON 序列化、客户端已还原），`LiveQuoteHub._on_xtdata_tick` 里 `with self._lock:` 遍历 `data.items()` 是纯本地操作，无死锁风险。
- 模块级等价调用：`from xqshare.client import register_quote_callback` 后直接 `register_quote_callback(cb)`（操作全局连接，需先 `xqshare.connect()`）。

## 九、实现位置索引（本次改动）

| 位置 | 内容 |
|------|------|
| `xqshare/server.py` `_QuoteEventCallback` | 服务端行情事件路由器：接管 xtquant 原生 callback，JSON 序列化 tick 后反向推送 |
| `xqshare/server.py` `_QUOTE_SUBSCRIBE_CALLBACK_POS` | 各订阅接口 callback 参数位置映射 |
| `xqshare/server.py` `LoggingProxy.__getattr__` | 对 `xtdata.subscribe_whole_quote/subscribe_quote/subscribe_quote2` 拦截 callback |
| `xqshare/server.py` `XtQuantService._get_quote_event_callback` | 行情路由器懒初始化单例 |
| `xqshare/client.py` `RemoteQuoteCallback` | 客户端行情回调接收器：`exposed_on_quote_event` 还原 JSON 后分发 |
| `xqshare/client.py` `RemoteModule.register_quote_callback` | 注册行情回调（`xtdata.register_quote_callback`） |
| `xqshare/client.py` 模块级 `register_quote_callback` | 全局连接便捷函数 |
| `tests/test_quote_events.py` | 单元测试（9 例） |
