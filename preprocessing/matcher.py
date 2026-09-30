"""数据-任务匹配器：根据扫描到的文件与解析元数据，
为每个子计算组匹配所需的输入数据文件。

匹配逻辑：
- 任务的 requires.surveys：该数据库下所有已识别文件均为候选；
- 任务的 requires.columns：文件必须解析出这些语义列（如 ra/dec/z）才计入；
- 空间型任务额外要求文件覆盖对应 tile（通过抽样行判断，抽样失败则记为"待验证"）。
"""
import numpy as np
import yaml

from common.tiling import tile_of


def load_groups(cfg_path):
    with open(cfg_path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def file_covers_column(meta, col):
    return bool(meta.get(col))


def sample_tile_ids(file_row, meta, ra_bins=36, dec_bins=18, max_rows=5000):
    """从文件抽样 (ra,dec) 行，返回覆盖的 tile 集合。失败返回 None。"""
    import json
    m = meta if isinstance(meta, dict) else json.loads(meta)
    ra_col, dec_col = m.get("ra"), m.get("dec")
    if not ra_col or not dec_col:
        return None
    path = file_row["path"]
    fmt = file_row.get("dtype", "")
    try:
        if fmt == "fits":
            from astropy.io import fits
            from astropy.table import Table
            with fits.open(path, memmap=True) as hdul:
                for h in hdul[1:]:
                    if hasattr(h, "columns") and h.columns and ra_col in h.columns.names:
                        t = Table.read(h)
                        break
                else:
                    return None
        elif fmt == "csv":
            from astropy.table import Table
            t = Table.read(path, format="ascii.csv")
        elif fmt == "ecsv":
            from astropy.table import Table
            t = Table.read(path, format="ascii.ecsv")
        else:
            return None
        if len(t) > max_rows:
            idx = np.linspace(0, len(t) - 1, max_rows).astype(int)
            t = t[idx]
        ras = np.asarray(t[ra_col], dtype=float)
        decs = np.asarray(t[dec_col], dtype=float)
        ok = np.isfinite(ras) & np.isfinite(decs)
        return {tile_of(r, d, ra_bins, dec_bins) for r, d in zip(ras[ok], decs[ok])}
    except Exception:
        return None


def match_files_for_subgroup(subgroup, files):
    """返回 {survey_id: [file_row,...]}，按任务需求过滤。"""
    req = subgroup.get("requires", {})
    surveys_req = req.get("surveys", [])
    cols_req = req.get("columns", [])
    matched = {}
    for f in files:
        if f["survey"] not in surveys_req:
            continue
        import json
        meta = json.loads(f["meta_json"]) if f.get("meta_json") else {}
        if cols_req and not all(file_covers_column(meta, c) for c in cols_req):
            continue
        matched.setdefault(f["survey"], []).append(f)
    return matched
