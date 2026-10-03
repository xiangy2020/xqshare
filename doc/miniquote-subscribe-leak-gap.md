# 交接：miniquote 内存泄漏根因 —— Dashboard 重复订阅（xqshare 侧诊断，QmtQuant 侧改造）

> 本文档由 xqshare 项目排查生成，交接给使用方项目 **QmtQuant**。
> QmtQuant 侧读本文即可接手，无需追问背景。结论基于 2026-10-03 对 win-debug（192.168.31.233）的实机诊断。

## 零、核心结论（TL;DR）

win-debug 反复卡死的根因是 **miniquote.exe（迅投 QMT 行情进程）内存泄漏到 35GB**，而泄漏的触发源是 **Dashboard 每 30 秒重复调用 `subscribe_whole_quote`**：

1. Dashboard 的 B211 订阅刷新机制，每 30 秒调一次 `subscribe_whole_quote(['688523.SH'])`
2. 每次订阅，xtquant 都会向 miniquote **新建一个订阅并返回新 seq（订阅号）**，需 `unsubscribe_quote(seq)` 显式取消
3. xqshare 拦截订阅时每次都转发给 miniquote，但**从不取消旧订阅**（旧版 xqshare）
4. 9-27 ~ 9-30 三天累计 **8629 次订阅**，miniquote 侧订阅累积 → 35.4GB → 拖垮整机

**xqshare 侧已做**（治本 + 防御，待发布新版）：
- 新增 `get_instance_id()` 接口，让客户端能**零副作用**地感知 server 重启
- 订阅幂等化：同一 client 重复订阅相同 code_list 时，先自动 `unsubscribe_quote(旧 seq)` 再订阅，从源头阻断 miniquote 累积

**QmtQuant 侧需做**（治本，见第八节）：把 Dashboard 的「每 30 秒重复订阅」改为「`get_instance_id()` 感知重启，仅在重启后重新订阅」。

## 一、问题一句话

Dashboard 用「每 30 秒重复订阅行情」来感知 xqshare server 重启，这个探测动作本身会向 miniquote 堆积订阅，导致 miniquote 内存泄漏到 35GB，拖垮整机。

## 二、背景：Dashboard 的订阅刷新机制

- Dashboard 通过 xqshare 连接 miniQMT 的 xtdata 行情（`C:\Install\pazq_qmt` 的 miniquote 进程，行情端口 58610）
- `LiveQuoteHub._refresh_subscription` 每 30 秒调一次 `subscribe_whole_quote`（B211 订阅刷新机制）
- 该机制的原始目的：**感知 xqshare server 重启**——server 重启会丢失内存态订阅，Dashboard 用「重新订阅拿到的 seq 是否回退」来判断 server 是否重启过，从而在重启后恢复订阅

## 三、现象

- win-debug 反复整机卡死（Event 41 意外重启）
- Windows 事件日志 `Event 2004（Resource-Exhaustion-Detector）` 点名元凶：

| 时间 | 进程 | 内存使用 |
|------|------|---------|
| 09-23 09:35 | miniquote.exe | **49.75GB** |
| 09-30 09:42 | miniquote.exe | **35.4GB** |

- 9-30 之后 miniquote 卡死：`download_history_data2` / `subscribe_whole_quote` 均报「无法连接xtquant服务」
- 10-02 20:28 整机卡死（Event 41）

## 四、根因分析

### 根因 1：subscribe_whole_quote 是「有状态、需显式取消」的订阅

`xtquant.xtdata.subscribe_whole_quote(code_list, callback)` 每次调用都向 miniquote 发送订阅请求并返回一个 **seq（订阅号）**，miniquote 为每个 seq 分配行情推送缓冲区。取消订阅必须显式调用 `unsubscribe_quote(seq)`。

```python
# xtquant/xtdata.py（site-packages 内实测）
def subscribe_whole_quote(code_list, callback=None):
    ...
    return get_client().subscribe_whole_quote(code_list, _BSON_.BSON.encode(param), callback)
    # 返回 int seq 订阅号

def unsubscribe_quote(seq):
    return get_client().unsubscribe_quote(seq)
```

### 根因 2：Dashboard 每 30 秒订阅，且从不取消

Dashboard 每 30 秒调一次 `subscribe_whole_quote(['688523.SH'])`，每次拿到新 seq，但**从不调用 `unsubscribe_quote`**。旧 seq 对应的订阅在 miniquote 侧永久残留。

### 根因 3：旧版 xqshare 无条件转发订阅，不做幂等

xqshare 的 `LoggingProxy` 拦截订阅时，每次都调用底层 `xtdata.subscribe_whole_quote`（把 callback 换成服务端路由器后转发），把 8629 次订阅原样打到 miniquote，且不维护、不取消 seq。

### 泄漏链（完整串联）

```
Dashboard 每 30s subscribe_whole_quote(['688523.SH'])     # B211 刷新，为感知重启
   → xqshare LoggingProxy 每次转发给 miniquote           # 不幂等、不取消
   → miniquote 每次新建订阅 seq，分配推送缓冲区            # 旧 seq 永久残留
   → 3 天累计 8629 个订阅累积                              # miniquote 35.4GB
   → 系统 commit 超限 → miniquote 卡死 → 整机卡死(Event 41)
```

## 五、实测证据

| 项 | 结果 |
|----|------|
| `subscribe_whole_quote` 调用次数（9-27 ~ 9-30） | **8629 次**（api_calls_20260927.log 统计） |
| 订阅间隔 | 每 30 秒一次（日志时间戳相邻 30s） |
| 订阅标的 | `['688523.SH']`（单标的，反复订阅同一标的） |
| miniquote 内存峰值 | 35.4GB（09-30 Event 2004）、49.75GB（09-23） |
| 9-30 后订阅是否继续 | 否——09:42:47 server 重启后 subscribe 报「无法连接xtquant服务」，miniquote 已卡死 |
| `unsubscribe_quote` 调用 | 从未出现（旧版 xqshare 无此逻辑） |
| 当前 miniquote（10-02 重启后 12h） | 私有内存仅 316MB（佐证泄漏是「订阅累积」而非匀速泄漏） |

## 六、xqshare 侧已做的修复（待发布新版）

1. **`get_instance_id()` 接口**：server 进程启动时生成唯一标识（`uuid.uuid4().hex`），通过 `conn.root.get_instance_id()` 获取。server 每次重启该值变化，客户端可用它**零副作用**感知重启。
2. **订阅幂等化**：`_QuoteEventCallback` 维护 `client_info -> {code_key -> seq}` 映射。同一 client 重复订阅相同 code_list 时，先 `unsubscribe_quote(旧 seq)` 再订阅；`on_disconnect` 时清理该 client 的所有 seq。从源头阻断 miniquote 订阅累积。

> 即使 QmtQuant 侧暂不改动，订阅幂等也能**防御性**地阻止 miniquote 累积（8629 次订阅 → 恒定为 1 个有效订阅）。但「每 30 秒重复订阅」本身仍是多余开销，建议一并改为轻量探测。

## 七、代码位置索引（xqshare 侧）

| 位置 | 内容 |
|------|------|
| `xqshare/server.py` `_INSTANCE_ID` | server 进程唯一实例标识 |
| `xqshare/server.py` `exposed_get_instance_id` | 暴露 `get_instance_id()` 接口 |
| `xqshare/server.py` `_normalize_code_key` | 订阅 code_list 规范化去重 |
| `xqshare/server.py` `_unsubscribe_safely` | 安全取消旧订阅 |
| `xqshare/server.py` `_QuoteEventCallback.get_subscription_seq / record_subscription / clear_client_subscriptions` | seq 管理 |
| `xqshare/server.py` `LoggingProxy.__getattr__` 订阅拦截块 | 重复订阅先 unsubscribe 旧 seq |
| `xqshare/server.py` `on_disconnect` | 断线清理该 client 所有订阅 |
| `doc/xqshare-api.md` §2.5 / §5.3 | 订阅幂等 + `get_instance_id()` 文档 |

## 八、QmtQuant 侧需要做的

1. **升级 xqshare** 到包含上述修复的新版本（发布后同步；注意 Windows pip 走清华镜像会滞后，需 `--index-url https://pypi.org/simple/` 直连官方源）。
2. **改造 Dashboard 订阅刷新逻辑**（`LiveQuoteHub._refresh_subscription` / B211 机制）：
   - 去掉「每 30 秒无条件重复订阅」
   - 改为：周期（或复用现有心跳）调用 `conn.root.get_instance_id()`，**对比 instance_id 变化**
   - 仅当 instance_id 变化（server 重启）或连接重建时，才重新订阅一次
3. 订阅后若需感知服务端状态，优先用「连接断开检测 + 重连后重订阅」，或「instance_id 对比」，不要再用「重复订阅 + seq 回退」。

参考伪代码见 `doc/xqshare-api.md` §5.3。

## 九、遗留

- miniquote（迅投 QMT 行情进程）是第三方闭源程序，即使订阅幂等后，其自身是否仍有**其他**泄漏路径（如连接券商数据服务器的缓存累积）未完全排除。建议 xqshare 新版部署后，在 win-debug 长跑观察 miniquote 内存是否稳定（当前 8GB 阈值主动重启兜底仍在，见 `qmt_mode_manager.py watch` 的内存守护）。
