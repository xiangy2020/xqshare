# miniQMT 停服适配方案（评估）

## 问题一句话

miniQMT（=大QMT 极简模式，本质同一）逐步停服，以后只剩大QMT；xqshare 基于 miniQMT 的行情（`xtdata`）与交易（`xttrader`）将失效。未来方向：**行情 = 解析大QMT 下载的 datadir + 第三方数据源；交易 = 信号桥接大QMT**。

## 背景（xqshare 当前怎么用 miniQMT）

xqshare 是「完全透明的 XtQuant 远程调用代理」：客户端（macOS/Linux）通过 RPyC 调 Windows 端的 xtquant 库。

```
Mac/Linux 客户端 ──RPyC──► Windows server ──import xtquant──► miniQMT/XtMiniQmt.exe
```

- 行情：`xqshare.xtdata` → 远端 `xtdata`（依赖 miniQMT 进程 + 登录）
- 交易：`xt.create_trader()` → 远端 `XtQuantTrader(userdata_mini, session_id)`（依赖 miniQMT + `userdata_mini`）
- 文件解析：`xqshare.datadir` → 远端 `QmtDataReader()` **直接读 .DAT 文件，无需任何进程**

## 核心结论

> **定位（重要）：当前只是「准备退路」（Plan B），不是抛弃 miniQMT。** miniQMT 继续用，万一政策/服务放开可随时切回。因此所有改造按「双通道并存 + 可切换」原则，不做替换/移除。
>
> **最终目标（2026-08-25）：大QMT ↔ miniQMT 无感切换** —— 使用方一行代码不改，xqshare 内部自动探测 miniQMT 可用性：可用走 mini（xtdata/xttrader），不可用自动切大QMT（datadir 解析 + 信号桥接），恢复后自动切回，上层完全无感知。

**miniQMT = 大QMT 的「极简模式」，本质是同一个东西，一起停服（用户澄清）。以后只剩大QMT（完整客户端 `XtItClient.exe`）。**

停服后 xtquant 的行情（`xtdata`）与交易（`xttrader`）宿主整体消失。**未来方向（用户已定）：**

| 能力 | 现状 | 停服后方向 |
|------|------|-----------|
| 行情 | `xtdata` 实时/在线接口 | **解析大QMT 下载到本地的 datadir 数据 + 第三方数据源**（akShare/TuShare/交易所直连） |
| 交易 | `xttrader` 直连下单 | **信号桥接**：拦截/桥接大QMT 的信号接口，把下单信号送进大QMT、回传委托/成交 |

对 xqshare 的影响：

1. **行情侧**：`xqshare.datadir` 文件解析从「补充数据源」升格为「主力行情源」，需大幅加强（K 线/板块/因子/除权，覆盖 xtdata 的常用面）。
2. **交易侧**：`xttrader` 通道废弃，需新增「信号桥接」通道替代下单/查询/回报。
3. **现有 miniQMT 硬编码**：`check_env.py`/`qmt_watchdog.py` 的 miniQMT 探测/守护将逐步失去意义；server 端 `xtdata`/`xttrader` 调用需按新方向降级或替换。

## 无感切换架构设计（目标态）

```
使用方（QmtQuant）── 客户端 API 不变 ──► xqshare client
                                            │ RPyC
                                     xqshare server
                                            │
                          ┌─────────────────┴─────────────────┐
                          │  通道抽象层（探测 + 自动路由）        │
                          │  miniQMT 可用 ?                    │
                          └──────────┬──────────────┬──────────┘
                               可用 ▼             不可用 ▼
                        ┌───────────────┐   ┌───────────────────┐
                        │ mini 通道      │   │ 大QMT 退路通道      │
                        │ xtdata/xttrader│   │ datadir + 信号桥接  │
                        └───────────────┘   └───────────────────┘
```

三个关键组件：

1. **统一通道抽象**：server 端在 xtdata/xttrader 之上加一层「数据源/交易通道」接口，两种实现（mini 直连 / 大QMT 退路）都实现同一接口，客户端看到的是同一套 API。
2. **健康探测 + 自动路由**：周期性探测 miniQMT 可用性（进程检测 `XtMiniQmt.exe` + `xtdata` 连通性探活），可用走 mini、不可用自动降级到大QMT 退路，恢复后自动切回。
3. **接口对齐**：两种通道的返回格式统一（DataFrame schema 对齐、交易对象字段对齐），保证客户端无感。

### 无感切换的难点（能力不对等）

| 能力 | mini 通道 | 大QMT 退路 | 无感难度 |
|------|----------|-----------|---------|
| 历史 K 线/板块/因子 | xtdata | datadir 解析（已实现大部分） | ✅ 低，schema 对齐即可 |
| 实时 tick/盘口 | xtdata 订阅 | ❌ 无（需三方源补） | ⚠ 高，只能降级或旁路 |
| 下单/撤单 | xttrader | 信号桥接（文件/Redis） | ✅ 中，异步化后对齐 |
| 委托/成交回报 | xttrader 回调事件 | 信号桥接回报文件轮询 | ⚠ 中，事件模型需对齐 |
| 持仓/资产查询 | xttrader 同步查询 | 信号桥接回报文件 | ✅ 中，读缓存回报 |

> 结论：**「历史数据 + 下单/查询」可做到无感；「实时行情 + 交易回调事件」是能力不对等的关键点**，需靠第三方实时源 + 事件模型适配兜底，可能存在「降级但有感」的边界。无感切换的目标范围需明确到「哪些接口无感、哪些接口降级」。

## 需调研的关键技术点（决定具体改造方案）

| # | 问题 | 影响 |
|---|------|------|
| 1 | 大QMT 的「信号桥接」具体机制是什么？（信号文件监听 / TCP 接口 / 策略交易信号接口 / 篮子交易？）下单信号怎么送进去、回报怎么回传？ | 交易通道改造方案 |
| 2 | 大QMT 下载到 datadir 的数据覆盖哪些？除 K 线/板块外，是否有因子/除权/财务/实时 tick？更新频率？ | 行情侧 datadir 解析的覆盖范围 |
| 3 | 第三方数据源的实时 tick/盘口能否补上 datadir 无实时行情的缺口？ | 实时行情兜底 |

## 需适配的 server 端耦合点

| # | 位置 | 现状 | 停服后改法（「并存 + 可切回」） |
|---|------|------|------|
| 1 | `xqshare/check_env.py:122` `get_miniqmt_info()` | 探测 `xtminiqmt.exe` | 保留；如需探测大QMT 再新增，不删 |
| 2 | `xqshare/server.py:1522` `_setup_generate_env()` | 写死 `userdata_mini` | 保留（xttrader 并存期间仍需要） |
| 3 | `xqshare/server.py:1755` datadir 自动推断 | `userdata_mini\datadir → datadir` | 保留；datadir 是并行/退路行情源，可直接读大QMT 的 `datadir` |
| 4 | `xqshare/qmt_watchdog.py:6,13` 守护进程 | 监控 `XtMiniQmt.exe` | 保留；如需守护大QMT 再新增监控项 |
| 5 | 交易通道 `exposed_create_trader*` | 透传 userdata | 保留 xttrader；新增信号桥接通道并存 |

## 实测验证（2026-08-24，192.168.31.233 平安大QMT）

环境：平安证券大QMT `C:\Install\pazq_qmt\pazq_qmt_2.0.8.0`，`XtItClient.exe`（PID 16720，RDP 会话）在跑，登录用户 `P_YP001`（资金账号 `307100903096`）；`XtMiniQmt.exe` / `minibroker.exe` / `miniquote.exe` 均**未**运行。

结果：

| 验证项 | 结果 |
|--------|------|
| `xtdata.get_full_tick(['000001.SZ'])` | ❌ 报「无法连接xtquant服务，请检查QMT-投研版或QMT-极简版是否开启」 |
| `XtQuantTrader(userdata, 50).connect()` | ❌ `-1`（asset=None） |
| `XtQuantTrader(userdata_mini, 50).connect()` | ❌ `-1`（asset=None） |

结论：**完整客户端 `XtItClient.exe` 跑起来 ≠ xtquant 可用**。xtdata 的宿主明确是「QMT-投研版」或「QMT-极简版」，两者都不是完整客户端。未来方向已定（见上「未来方向」），投研版不再作为重点。

## 缓冲与兜底

- **datadir 升格为主力行情源**：`datadir` 直接读大QMT 下载的 .DAT 文件、无需任何进程，停服后是行情主通道 → 与既有 backlog「datadir 因子/除权缺口」互补，应优先补全（K 线/板块/因子/除权覆盖 xtdata 常用面）。
- **第三方数据源补实时缺口**：datadir 无实时 tick/盘口，实时行情靠第三方（akShare/TuShare/交易所直连）补。

## 建议落地顺序（目标：大QMT ↔ miniQMT 无感切换）

分三阶段，前两阶段是「底层能力」，第三阶段是「无感切换」的收口：

**阶段一：底层能力补全（已在推进）**

1. 【行情】补全 `xqshare.datadir` 解析（因子✅/除权/K线/板块），对齐 xtdata 返回 schema（见 backlog_datadir_factor）。
2. 【交易】实现信号桥接通道（文件/Redis），对齐 xttrader 的下单/查询/回报（见 doc/daqmt-signal-bridge.md）。

**阶段二：通道抽象层**

3. 【核心】server 端新增「数据源/交易通道」抽象层：mini 通道（xtdata/xttrader）与退路通道（datadir/信号桥接）实现同一接口。
4. 【核心】健康探测 + 自动路由：探测 `XtMiniQmt.exe` + xtdata 连通性，可用走 mini、不可用自动降级、恢复自动切回。

**阶段三：接口对齐与收口**

5. 【对齐】统一两种通道返回格式（DataFrame schema、交易对象字段）；交易回调事件模型适配（xttrader 事件 vs 信号桥接回报轮询）。
6. 【评估】第三方数据源补实时 tick/盘口缺口（无感切换的最大能力缺口）。
7. 【收尾】miniQMT 探测/守护保留，作为健康探测的输入。

## 代码位置索引

- `xqshare/check_env.py:102-151` — `get_miniqmt_info()` 进程探测（写死 XtMiniQmt.exe）
- `xqshare/server.py:1472-1501` — `_setup_detect_miniqmt()`
- `xqshare/server.py:1504-1584` — `_setup_generate_env()`（写死 userdata_mini）
- `xqshare/server.py:1739-1772` — datadir 自动推断（写死 userdata_mini\datadir）
- `xqshare/qmt_watchdog.py:1-18` — 守护进程（监控 XtMiniQmt.exe）
- `xqshare/server.py:942-1040` — `exposed_create_trader` / `exposed_create_trader_and_connect`（userdata 路径透传）
