# 交接：行情回调在服务端未生效（server 未升级到含 `_QuoteEventCallback` 的版本）

> 本文档由使用方项目 **QmtQuant** 的排查会话生成（2026-08-24），交接给 xqshare 项目。
> xqshare 侧 AI 读本文即可接手确认与修复，无需追问背景。行号基于本地 working tree（版本号 1.2.41），若已变动以实际代码为准。

## 零、一句话结论

QmtQuant 生产环境（Linux 客户端 21.6.51.30）已升级到 1.2.41，但 **xqshare 服务端（Windows VM 21.214.136.216:18812）仍运行旧版 server.py（无 `_QuoteEventCallback`）**，导致 `subscribe_whole_quote` 的 tick 数据推不回来，实时行情页所有价格为空。

**根因本质**：行情回调能力（server 端 `_QuoteEventCallback` + client 端 `RemoteQuoteCallback`/`register_quote_callback`）目前**只存在于本地 working tree 的未提交改动里，从未进入任何 git tag**。当前仓库唯一 tag 是 `v1.2.38`，其 server.py/client.py **完全不含**这些符号。若 Windows 服务端是按 git tag（或任何已提交版本）部署的，就一定没有行情回调能力。

## 一、背景

使用方 QmtQuant 已按 `doc/quote-callback-gap.md` 第八节「重新接入方式」正确接入：

```python
# QmtQuant dashboard/server/live_quote.py
self._xtdata.register_quote_callback(svc.on_quote)   # 注册行情回调
self._xtdata.subscribe_whole_quote(codes)            # 不传本地 callback，由 xqshare 客户端自动接管
```

客户端（Linux）装的是 `xtquant_share-1.2.41`（从本地 dist wheel 安装，wheel 含完整的行情回调代码）。

## 二、现象（生产实测）

| 项 | 结果 |
|----|------|
| `live-quote/status` | `state=connected`，`subscribed_count=25`，`xqshare_registered=true`，**`tick_count=0`** |
| `live-quote/snapshot` | 25 只股票 `price/open/high/low/volume/amount` 全为 `null`（均为预填占位，`seq=0`） |
| 服务启动时间 | 2026-08-24 00:37:56 起，`reconnect_count=0`（从未断线，也从未收到 tick） |

## 三、分层验证证据（关键）

在 QmtQuant 服务器上用独立进程直连 xqshare 做了分层隔离，结论明确：

### 证据 1：数据源正常（拉式能拿到实时行情）

```python
xtdata.get_full_tick(['600879.SH'])
# → {"600879.SH": {"timetag": "20260824 13:13:21", "lastPrice": 13.97,
#                  "open": 14.33, "high": 14.43, "low": 13.9, "lastClose": 14.56,
#                  "amount": 792818200, "volume": 561944, ...}}
```

QMT 数据源有实时行情，不是数据源/交易时段问题。

### 证据 2：推式回调完全收不到（0 tick）

```python
xtdata.register_quote_callback(on_quote)        # 成功，无异常
seq = xtdata.subscribe_whole_quote(['600879.SH'])  # → 147（订阅成功）
# 等待 12 秒，on_quote 一次都没被调用 → ticks=0
```

订阅调用返回正常 seq（147），但 tick 回调一次都没触发。

### 证据 3：能力是未提交改动，从未进 tag

```bash
git tag -l                        # → 仅 v1.2.38
git show v1.2.38:xqshare/server.py | grep -c "_QuoteEventCallback"   # → 0
git show v1.2.38:xqshare/client.py | grep -cE "register_quote_callback|RemoteQuoteCallback"  # → 0
git diff --stat xqshare/server.py xqshare/client.py   # → 252 行新增（全部未提交）
```

`_QuoteEventCallback`、`_QUOTE_SUBSCRIBE_CALLBACK_POS`、`_get_quote_event_callback`、`RemoteQuoteCallback`、`register_quote_callback` 全是 `git diff` 里的 `+` 新增行，从未 commit、从未打 tag。`1.2.41` 只是本地 `pyproject.toml`/`__init__.py` 的版本号（同样未提交）与 dist wheel 文件名，**不是 git tag**。

## 四、根因

客户端 1.2.41 的 `_wrap_call`（`client.py`）会在 `subscribe_whole_quote` 时自动把 `RemoteQuoteCallback` 实例作为 `callback=` 参数传给服务端：

```python
# client.py _wrap_call（1.2.41 已装）
if self._module_name == 'xtdata':
    pos = _QUOTE_SUBSCRIBE_CALLBACK_POS.get(func_name)  # subscribe_whole_quote → 1
    if pos is not None:
        cb = self._get_quote_callback()   # RemoteQuoteCallback
        ...
        else:
            kwargs['callback'] = cb        # 注入 RemoteQuoteCallback 作为 callback
```

新版服务端（含 `_QuoteEventCallback`）会在 `LoggingProxy.__getattr__` 拦截这个 callback：

```python
# server.py（1.2.41，未提交改动）
if target_name == 'xtdata' and quote_event_callback is not None:
    if name in _QUOTE_SUBSCRIBE_CALLBACK_POS:
        pos = _QUOTE_SUBSCRIBE_CALLBACK_POS[name]
        client_callback = kwargs.pop('callback', None)
        if client_callback is not None:
            quote_event_callback.register(client_callback)   # 登记客户端回调
            kwargs['callback'] = quote_event_callback        # 换成服务端本地路由器
        ...
```

**旧版服务端没有这段拦截**，会把 `RemoteQuoteCallback` 的 netref 原样透传给 xtquant 原生 `subscribe_whole_quote`。xtquant 拿到的是一个不可调用的 remote reference，tick 到来时无法回调 → tick 全部静默丢失（无异常、无日志，正是 `tick_count=0` 且 `state=connected` 的表象）。

## 五、xqshare 侧需要做的

1. **确认 Windows VM（21.214.136.216）上 xqshare server 的当前版本与部署方式**（pip install 的包？git clone 跑的源码？哪个 tag/commit？）。

   快速确认命令（在 Windows 服务端环境执行）：
   ```python
   python -c "import xqshare; print(xqshare.__version__)"
   # 或直接看服务端运行代码里是否有该符号：
   grep -n "_QuoteEventCallback" xqshare/server.py   # 无输出 = 旧版
   ```

2. **升级服务端到含 `_QuoteEventCallback` 的版本**。可选项：
   - 把本地 working tree 的未提交改动**提交 + 打 tag**（如 `v1.2.41`），Windows 侧拉取该 tag 部署；或
   - 直接用本地构建的 wheel：`~/Documents/AIWork/xqshare/dist/xtquant_share-1.2.41-py3-none-any.whl`（已确认 wheel 内含 `_QuoteEventCallback`），Windows 侧 `pip install --force-reinstall --no-deps <wheel>`。

3. **重启 xqshare server 进程**（关键：不重启，旧代码仍在内存运行）。

## 六、验证通过标准（升级后逐项确认）

- [ ] 服务端日志出现 `[QuoteEvent] 注册订阅者 total=1`（客户端 subscribe 后，`server.py` `_QuoteEventCallback.register` 的 INFO 日志）
- [ ] QmtQuant `live-quote/status` 的 `tick_count` 开始增长（> 0）
- [ ] QmtQuant `live-quote/snapshot` 的 25 只股票 `price/open/high/low` 不再是 `null`
- [ ] QmtQuant `live-quote` 页面价格正常跳动

   服务端侧独立验证（可选，Windows 上起一个临时客户端）：
   ```python
   xtdata.register_quote_callback(lambda d: print("tick", list(d.keys())))
   xtdata.subscribe_whole_quote(['600879.SH'])
   # 交易时段内应打印 tick
   ```

## 七、代码位置索引（本地 working tree，1.2.41）

| 位置 | 内容 |
|------|------|
| `xqshare/server.py:538` | `_QuoteEventCallback`：服务端行情事件路由器（JSON 序列化后反向推送） |
| `xqshare/server.py:619` | `_QUOTE_SUBSCRIBE_CALLBACK_POS`：各订阅接口 callback 参数位置映射 |
| `xqshare/server.py:705` | `LoggingProxy.__getattr__`：对 `xtdata.subscribe_whole_quote` 拦截 callback |
| `xqshare/server.py:782` | `XtQuantService._get_quote_event_callback`：行情路由器懒初始化单例 |
| `xqshare/server.py:887` | 构造 `LoggingProxy` 时注入 `quote_event_callback` |
| `xqshare/client.py:282` | `RemoteQuoteCallback`：客户端行情回调接收器 |
| `xqshare/client.py:396` | `_wrap_call`：订阅时自动注入 `RemoteQuoteCallback` |
| `xqshare/client.py:465` | `register_quote_callback`：注册行情回调 |

> 关联文档：`doc/quote-callback-gap.md`（行情回调缺口的历史根因与方向 2 实现说明）。
