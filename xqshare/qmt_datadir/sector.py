"""
板块成分股 & 指数权重解析模块

数据来源：
  - 板块成分股：datadir/Sector/Temple/{分类}/{板块名}
    格式：逗号分隔的股票代码，如 '000022.SZ,000088.SZ,...'
  - 指数权重：datadir/Weight/systemSectorWeightData.txt
    格式（每行）：{板块名};{代码1},{权重1};{代码2},{权重2};...
"""

import os


def read_sector(filepath: str) -> list:
    """
    读取单个板块成分股文件，返回股票代码列表。

    Args:
        filepath: 板块文件绝对路径

    Returns:
        list[str]，如 ['000022.SZ', '000088.SZ', ...]
    """
    content = open(filepath, 'r', encoding='utf-8', errors='replace').read()
    return [c.strip() for c in content.split(',') if c.strip()]


def read_all_sectors(data_dir: str, category: str = '申万行业') -> dict:
    """
    读取某分类下所有板块的成分股。

    Args:
        data_dir: datadir 根目录
        category: 板块分类名，如 '申万行业' 或 '证监会行业'

    Returns:
        dict[str, list[str]]，板块名 → 成分股代码列表
        如 {'SW1银行': ['600000.SH', '600015.SH', ...], ...}
    """
    sector_dir = os.path.join(data_dir, 'Sector', 'Temple', category)
    if not os.path.isdir(sector_dir):
        raise FileNotFoundError(f"板块目录不存在: {sector_dir}")

    result = {}
    for name in sorted(os.listdir(sector_dir)):
        fp = os.path.join(sector_dir, name)
        if os.path.isfile(fp):
            try:
                result[name] = read_sector(fp)
            except Exception:
                result[name] = []
    return result


def list_sector_categories(data_dir: str) -> list:
    """
    列出所有可用的板块分类名称。

    Returns:
        list[str]，如 ['申万行业', '证监会行业']
    """
    temple_dir = os.path.join(data_dir, 'Sector', 'Temple')
    if not os.path.isdir(temple_dir):
        return []
    return sorted(os.listdir(temple_dir))


def read_weight(data_dir: str) -> dict:
    """
    读取指数/板块权重数据。

    文件格式（每行）：{板块名};{代码1},{权重1};{代码2},{权重2};...
    如：上证A股;600051.SH,1;605090.SH,1;...

    Args:
        data_dir: datadir 根目录

    Returns:
        dict[str, list[dict]]，板块名 → [{'symbol': str, 'weight': float}, ...]
    """
    fp = os.path.join(data_dir, 'Weight', 'systemSectorWeightData.txt')
    if not os.path.exists(fp):
        raise FileNotFoundError(f"权重文件不存在: {fp}")

    result = {}
    with open(fp, 'r', encoding='utf-8', errors='replace') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            parts = line.split(';')
            if len(parts) < 2:
                continue
            sector_name = parts[0]
            members = []
            for item in parts[1:]:
                item = item.strip()
                if not item:
                    continue
                kv = item.split(',')
                if len(kv) == 2:
                    try:
                        members.append({
                            'symbol': kv[0].strip(),
                            'weight': float(kv[1].strip()),
                        })
                    except ValueError:
                        pass
            if members:
                result[sector_name] = members
    return result
