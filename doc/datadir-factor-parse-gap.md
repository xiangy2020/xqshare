# 交接：datadir 文件解析缺口（因子库 feather + 除权因子配置）

> 本文档由使用方项目 **QmtQuant** 的部署/排查会话生成，交接给 xqshare 项目。
> xqshare 侧的 AI 读本文即可接手，无需追问背景。行号/版本基于 2026-08 安装的 xqshare 版本，若已变动以实际代码为准。

## 零、一句话结论

QmtQuant 的 `factor/value`（因子库）和 `meta/divid-factor`（除权因子）两个扩展数据域在生产环境恒 empty。根因是 **datadir 文件解析能力有缺口，分两块**：

1. **配置缺口**：xqshare server 端（21.214.136.216 Windows VM）未配 `QMT_DATADIR_PATH`，`_init_datadir_reader()` 拿不到路径 → `QmtDataReader` 未初始化 → 客户端 `env.datadir` 恒 None（**除权因子 divid 解析代码已实现，仅被配置阻塞**）。
2. **实现缺口**：`QmtDataReader` 现有 `kline/sector/weight/divid/etf_list/market_list/increase_meta` 八个方法，**缺因子库 feather 文件解析方法**（`{datadir}/EP/{zh_name}_Xdat2/data.fe`）。因子库 8 张宽表 / 161 因子 / 3.2 亿行完全无法同步。

## 一、问题一句话

QmtQuant `data_manager/sync_extended.py::sync_factors()` 需要读 QMT datadir 下的因子 feather 文件，`sync_divid()` 需要读 DividData；但生产环境 `datadir` 不可用（未配路径）+ 无因子解析方法，导致这两个域落库 0 条（fail-soft 静默，表面 success 实际无数据）。

## 二、背景：使用方（QmtQuant）怎么用 datadir

QmtQuant 通过 `env.py` 统一走 xqshare 连接（`from env import xtdata, datadir`）：

```python
# env.py 关键逻辑（懒连接）
_datadir_real = None   # xqshare.datadir 真实代理对象（server 未配 QMT_DATADIR_PATH 时仍为 None）

def _init_xqshare():
    # ...
    try:
        _datadir_real = _xqshare.datadir   # ← server 端 exposed_get_datadir
        logger.info("[env] datadir 代理已就绪（xqshare.datadir）")
    except Exception as _datadir_e:
        _datadir_real = None               # ← server 未配路径时抛 RuntimeError，降级 None
```

两个消费点：

**① 除权因子（divid）** — `sync_extended.py:404-426`，走远程调用：
```python
from env import datadir, ensure_connected, is_datadir_available
if not ensure_connected() or not is_datadir_available():
    raise RuntimeError("datadir 数据源不可用（xqshare 未连接或未配置 QMT_DATADIR_PATH）")
df = datadir.divid()   # ← 远程调用 QmtDataReader.divid()，已实现
```

**② 因子库（factor/value）** — `sync_extended.py:632-716`，走「本地路径」写法：
```python
from env import xtdata, datadir
# 1) 更新因子列表（metatable）
xtdata.download_metatable_data()
metainfo = xtdata.get_metatable_list() or {}
# 2) 检查 datadir 是否可用
if not datadir or not _os.path.exists(datadir):   # ← 期望 datadir 是本地路径字符串！
    logger.warning(f"PR-G sync_factors: xtquant datadir 不存在或不可用：{datadir}")
    return 0
# 3) 逐因子类读 feather
for factor_class in target:
    zh_name = metainfo.get(factor_class, factor_class)
    feather_path = _os.path.join(datadir, "EP", zh_name + "_Xdat2", "data.fe")
    if not _os.path.exists(feather_path):
        continue
    df = pd.read_feather(feather_path)   # ← 本地读 feather
    if "date" in df.columns and "trade_date" not in df.columns:
        df = df.rename(columns={"date": "trade_date"})
    df = df[df["symbol"].isin(symbols)]
    _db.save_factor_value(factor_class, df)
```

> ⚠ **架构不一致点**：`sync_factors` 现写法把 `datadir` 当**本地路径字符串**（`os.path.exists` + `pd.read_feather`），而 `env.datadir` 实际是**远程代理对象**。这在 Linux 生产（feather 文件在 Windows 端）下永远走不通——即使 server 端配了路径，客户端也拿不到本地 feather。正确做法是 xqshare 侧在 `QmtDataReader` 加 factor 解析（读 feather 返回 DataFrame 经 RPyC 序列化），QmtQuant 侧同步改成 `datadir.factor(...)` 远程调用（见 §六）。

## 三、现象（2026-08-23 生产实测）

生产 xqshare server 21.214.136.216:18812（Windows VM）未配 `QMT_DATADIR_PATH`，QmtQuant 生产（21.6.51.30）`env` 导入冒烟输出：

```
[env] datadir 已加载：False
[env]   ⚠ datadir 不可用，如需文件解析能力请在 server 端配置 QMT_DATADIR_PATH
```

结果：数据中心页面「因子库」「除权因子」更新按钮正常跑完（节点 done、success=True），但落库 0 条。

## 四、根因：两块缺口

### 缺口 1：server 端未配 QMT_DATADIR_PATH（配置问题）

xqshare `server.py:1734 _init_datadir_reader()` 的初始化顺序：
1. 读环境变量 `QMT_DATADIR_PATH`（显式配置）
2. 否则 `xtdata.get_data_dir()` 自动推断（`userdata_mini\datadir` → 替换为 `datadir`）
3. 均不可用时 `_datadir_reader` 保持 None，`exposed_get_datadir` 抛 RuntimeError

生产 Windows VM 上既未配 `QMT_DATADIR_PATH`，自动推断也未命中，导致 `_datadir_reader = None`。

> 注意：`server.py:1559` 的 `ensure_env_file` 会生成 `.env` 模板（含 `QMT_DATADIR_PATH=` 空值注释），但生产 `.env` 未填实际路径。只需在 Windows VM 上确认 QMT 安装目录下的 `datadir` 真实路径并填入 `.env`，缺口 1 即解决（除权因子 divid 即可用）。

### 缺口 2：QmtDataReader 无因子库 feather 解析（实现缺口）

`xqshare/qmt_datadir/reader.py` 的 `QmtDataReader` 现有方法：`kline / sector_categories / sectors / sector / weight / divid / etf_list / market_list / increase_meta`。**没有 factor/feather 相关方法**。因子库同步所需的能力完全缺失。

## 五、API 契约（xqshare 侧需导出的形状）

使用方期望的因子库读取语义（对齐 QmtQuant `sync_factors` 现有落库逻辑）：

```python
# 方案 A（推荐）：QmtDataReader 加方法，远程调用返回 DataFrame
def factors(self, factor_class: str, symbols: Optional[list] = None,
            start_date: Optional[str] = None, end_date: Optional[str] = None) -> pd.DataFrame
```

- `factor_class`：因子类名，如 `factor_growth` / `factor_base_derivative` / `factor_metrics` / `factor_quality` 等（QmtQuant 侧 `FACTOR_TABLES` 的 8 个 key，见 `data_manager/db/financial.py:394`）
- 对应 feather 文件：`{datadir}/EP/{zh_name}_Xdat2/data.fe`，其中 `zh_name` 由 `xtdata.get_metatable_list()` 的 key→中文名映射得到
- 返回 `pd.DataFrame`，列：`symbol`、`date`（或 `trade_date`）、以及该类下的若干因子数值列
- 数据规模：全 A 股 × 8 类 × 历史日期，约 **3.2 亿行**——跨 RPyC 传输时**必须走 `_serialize_for_transfer` / `_deserialize_from_transfer` 机制**（不能直接返回裸 DataFrame / netref，否则重蹈 `get_divid_factors` 覆辙，见 `longhubang-context-api-gap.md` §六的序列化警告）

**除权因子 divid 契约**（已实现，只差配置）：
```python
def divid(self, exchange: Optional[str] = None, code: Optional[str] = None) -> pd.DataFrame
# 返回 columns: exchange, code, symbol, ex_date, ratio, raw_ts
```

## 六、xqshare 侧出路（供排期）

### 步骤 1（配置，立即可做，无需写代码）

在 Windows VM 的 xqshare server `.env` 中配置：
```
QMT_DATADIR_PATH=C:\install\国金证券QMT交易端\datadir
```
（确认 QMT 安装根目录下 `datadir` 目录真实存在；重启 xqshare server 后 `exposed_get_datadir` 即可返回 QmtDataReader，除权因子 divid 立即恢复。）

### 步骤 2（实现因子库解析，需写代码）

在 `xqshare/qmt_datadir/` 新增 factor 解析（如 `factor.py`），并在 `QmtDataReader` 暴露 `factors()` 方法：

1. 定位 feather 文件：`{data_dir}/EP/{zh_name}_Xdat2/data.fe`（`zh_name` 与 `factor_class` 的映射来自 QmtQuant 传入的 metatable，或 xqshare 侧自行 `get_metatable_list` 建映射）
2. `pd.read_feather` 读文件，标准化 `date` → `trade_date`
3. 按 `symbol` 过滤（QmtQuant 传入的股票池）、按 `trade_date` 增量过滤
4. 返回 DataFrame，经 `_serialize_for_transfer` 序列化跨 RPyC 传输

### 步骤 3（QmtQuant 侧配套改造，xqshare 完成后）

`sync_factors` 从「本地路径写法」改为远程调用：
```python
df = datadir.factors(factor_class, symbols=symbols)
# 替代现有的 os.path.exists(datadir) + pd.read_feather(feather_path)
```
此改动由 QmtQuant 侧完成，xqshare 侧只需保证 §五 契约。

## 七、使用方（QmtQuant）侧落库表结构

因子库落库走 `_db.save_factor_value(factor_class, df)`，8 张因子表（`FACTOR_TABLES`，`data_manager/db/financial.py:394`）：

| 因子类 | 因子数（示例字段） |
|--------|------|
| `factor_growth` | fin_cash_flow_growth / net_profit_growth_parent / net_assets_growth / total_profit_growth |
| `factor_base_derivative` | 19 个 ttm 财务指标（fin_exp_ttm / total_rev_ttm / pb 相关…） |
| `factor_metrics` | 13 个市值/每股指标（total_mv / eps_ttm / pb_ratio…） |
| `factor_quality` | 质量/偿债/营运比率（eq_ratio / roa 相关…） |
| 其余 4 类 | 见 `FACTOR_TABLES` 完整定义 |

除权因子落库走 `datadir.divid()` → `DividData` LevelDB 解析，落 `divid_factor` 相关表（`save_divid_factor`）。

## 八、相关文档位置索引

| 位置 | 内容 |
|------|------|
| QmtQuant `data_manager/sync_extended.py:404-426` | divid 同步入口（远程 `datadir.divid()`） |
| QmtQuant `data_manager/sync_extended.py:632-716` | 因子库同步入口（feather 读取 + 落库） |
| QmtQuant `data_manager/db/financial.py:394` | `FACTOR_TABLES` 8 张因子表定义 |
| QmtQuant `data_manager/db/financial.py::save_factor_value` | 因子落库函数 |
| QmtQuant `env.py:143/167/291-299` | `_datadir_real` / `is_datadir_available` / datadir 代理初始化 |
| QmtQuant `docs/PR-G-factor-tables.md` | 因子表设计（8 宽表 / 161 因子 / 3.2 亿行） |
| QmtQuant `docs/PR-xqshare-data-source-boundary.md` | 数据源边界实测（divid-factor 序列化 bug 见 §3.5/§3.6） |
| xqshare `xqshare/server.py:1734` | `_init_datadir_reader()`（读 QMT_DATADIR_PATH） |
| xqshare `xqshare/server.py:900` | `exposed_get_datadir`（datadir 代理出口） |
| xqshare `xqshare/qmt_datadir/reader.py` | `QmtDataReader`（现有 8 方法，缺 factor） |
| xqshare `xqshare/qmt_datadir/divid.py` | `read_divid`（已实现的除权因子解析） |
