"""
qmt_datadir —— QMT 本地 datadir 解析包

支持的数据类型：
  1. K线数据        SH/SZ/{period}/*.DAT
  2. 板块成分股      Sector/Temple/{分类}/{板块名}
  3. 指数权重        Weight/systemSectorWeightData.txt
  4. 分红除权        DividData/*.ldb（LevelDB SSTable 直接扫描）
  5. ETF成分股列表   TradeDateAndETFStockListCache
  6. 交易所列表      marketlistinfo（Base64）
  7. 涨跌幅快照元数据 increase/{market}（ZiPeDiT+zlib 压缩位图）
  8. 因子库         EP/{factor}_Xdat2/data.fe（feather，需 pyarrow）

快速使用：
    from xqshare.qmt_datadir import QmtDataReader

    reader = QmtDataReader('~/Downloads/pazq_qmt/.../userdata_mini/datadir')
    df = reader.kline('600000.SH', '1d')

底层函数直接使用：
    from xqshare.qmt_datadir import read_kline_dir, read_divid, read_all_sectors
"""

# ── 统一入口类 ────────────────────────────────────────────────────────────────
from .reader import QmtDataReader

# ── K线 ──────────────────────────────────────────────────────────────────────
from .kline import (
    read_kline,
    read_kline_dir,
    FILE_HEADER_SIZE,
    RECORD_SIZE,
    PERIOD_MAP,
)

# ── 板块 & 权重 ───────────────────────────────────────────────────────────────
from .sector import (
    read_sector,
    read_all_sectors,
    list_sector_categories,
    read_weight,
)

# ── 分红除权 ──────────────────────────────────────────────────────────────────
from .divid import read_divid

# ── 因子库 ────────────────────────────────────────────────────────────────────
from .factor import read_factor, list_factor_classes

# ── 杂项 ──────────────────────────────────────────────────────────────────────
from .misc import (
    read_etf_list,
    read_market_list,
    read_increase_meta,
)

__all__ = [
    # 统一入口
    'QmtDataReader',
    # K线
    'read_kline',
    'read_kline_dir',
    'FILE_HEADER_SIZE',
    'RECORD_SIZE',
    'PERIOD_MAP',
    # 板块 & 权重
    'read_sector',
    'read_all_sectors',
    'list_sector_categories',
    'read_weight',
    # 分红除权
    'read_divid',
    # 因子库
    'read_factor',
    'list_factor_classes',
    # 杂项
    'read_etf_list',
    'read_market_list',
    'read_increase_meta',
]
