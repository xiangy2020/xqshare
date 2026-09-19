"""
因子库数据解析模块

数据来源：datadir/EP/{factor_class}_Xdat2/data.fe（feather 格式）

说明：
  - data.fe 是 QMT 客户端「扩展数据/因子数据」下载后生成的 feather 文件，
    路径与 schema 对齐 xtquant 官方 metatable/get_arrow.py 的实现：
        file_path = datadir/EP/{table}_Xdat2/data.fe
        schema 含 _time（毫秒时间戳）、_stock（股票代码）、以及若干因子数值列。
  - 未下载因子数据时只有 {factor}_Xdat/config（JSON 因子定义），没有 data.fe。
"""

import os
from typing import List, Optional

import pandas as pd


def _coerce_date(dt) -> Optional[str]:
    """把日期参数转成 'YYYYMMDD' 字符串，支持 '20260101' 或 '2026-01-01'。"""
    if dt is None:
        return None
    s = str(dt).strip()
    if not s:
        return None
    return s.replace('-', '')[:8]


def list_factor_classes(data_dir: str) -> List[str]:
    """
    列出 datadir 下已存在的因子表。

    同时扫描两类目录：
      - {factor}_Xdat2/data.fe  —— 已下载因子数据
      - {factor}_Xdat/config     —— 仅有因子定义（未下载）

    Returns:
        list[str]，去重排序后的因子表名（英文 modelName）。
    """
    ep_dir = os.path.join(data_dir, 'EP')
    if not os.path.isdir(ep_dir):
        return []

    classes = set()
    for name in os.listdir(ep_dir):
        full = os.path.join(ep_dir, name)
        if not os.path.isdir(full):
            continue
        if name.endswith('_Xdat2'):
            classes.add(name[:-len('_Xdat2')])
        elif name.endswith('_Xdat'):
            classes.add(name[:-len('_Xdat')])
    return sorted(classes)


def read_factor(data_dir: str,
                factor_class: str,
                symbols: Optional[List[str]] = None,
                start_date: Optional[str] = None,
                end_date: Optional[str] = None) -> pd.DataFrame:
    """
    读取因子库数据（feather 文件）。

    Args:
        data_dir:     datadir 根目录
        factor_class: 因子表名（英文 modelName，如 'factor_growth'）
        symbols:      可选，股票代码列表（如 ['600000.SH']），过滤
        start_date:   可选，起始日期（'YYYYMMDD' 或 'YYYY-MM-DD'）
        end_date:     可选，结束日期（同上）

    Returns:
        DataFrame，columns: symbol, date, 以及该类下的若干因子数值列。
        若因子数据未下载（无 data.fe），抛出 FileNotFoundError。

    Raises:
        FileNotFoundError: data.fe 不存在（因子数据未下载）
        ImportError:       未安装 pyarrow
    """
    ep_dir = os.path.join(data_dir, 'EP')
    feather_path = os.path.join(ep_dir, f'{factor_class}_Xdat2', 'data.fe')

    if not os.path.exists(feather_path):
        # 尝试仅定义目录，给出更友好的提示
        config_dir = os.path.join(ep_dir, f'{factor_class}_Xdat')
        hint = ''
        if os.path.isdir(config_dir):
            hint = (f'（检测到 {factor_class}_Xdat 仅含因子定义，'
                    f'请先在 QMT 客户端下载该因子的扩展数据）')
        raise FileNotFoundError(
            f"因子数据文件不存在: {feather_path}{hint}"
        )

    try:
        import pyarrow.feather as feather
    except ImportError as e:
        raise ImportError(
            "读取因子库需要 pyarrow，请安装：pip install pyarrow"
        ) from e

    table = feather.read_table(feather_path)
    df = table.to_pandas()

    # ── 列名标准化：_time → date，_stock → symbol ──
    if '_time' in df.columns:
        df = df.rename(columns={'_time': 'date'})
        df['date'] = pd.to_datetime(df['date'], unit='ms')
    if '_stock' in df.columns:
        df = df.rename(columns={'_stock': 'symbol'})
    elif 'symbol' not in df.columns:
        # 未知 schema，原样返回，交由使用方处理
        return df

    # ── symbols 过滤（兼容 '600000.SH' / '600000' 两种写法）──
    if symbols:
        sym_set = set()
        for s in symbols:
            s = str(s).strip()
            sym_set.add(s)
            if '.' in s:
                sym_set.add(s.split('.')[0])
            elif len(s) == 6:
                sym_set.add(f'{s}.SH')
                sym_set.add(f'{s}.SZ')
        df = df[df['symbol'].isin(sym_set)]

    # ── 日期过滤 ──
    start = _coerce_date(start_date)
    end = _coerce_date(end_date)
    if 'date' in df.columns and (start or end):
        try:
            date_norm = pd.to_datetime(df['date']).dt.strftime('%Y%m%d')
        except Exception:
            date_norm = None
        if date_norm is not None:
            mask = pd.Series(True, index=df.index)
            if start:
                mask &= date_norm >= start
            if end:
                mask &= date_norm <= end
            df = df[mask]

    # 统一列顺序：symbol / date 在前，其余因子列按原顺序
    if 'symbol' in df.columns and 'date' in df.columns:
        others = [c for c in df.columns if c not in ('symbol', 'date')]
        df = df[['symbol', 'date'] + others]

    return df.reset_index(drop=True)
