"""HEALPix 预分片管线：巡天目录 -> 按像素分区的 Parquet 数据集。

这是 v2.0 最重要的架构改造（对应问题清单 #8）：
旧流程是"每个任务读全量原始数据 -> 再筛自己的 tile"，I/O 复杂度
O(任务数 x 全量数据)；新流程是"一次性预分片 -> 每个任务只读自己的
像素分区"，I/O 复杂度 O(单分区)。

分区格式（对齐 LINCC HATS 思想的简化实现，DeLucchi+ 2025, arXiv:2501.02103）：

    <root>/<survey>/Norder=<k>/Npix=<j>.parquet     # 数据分区
    <root>/<survey>/_metadata.json                 # nside/列映射/各分区行数

- HEALPix RING 编号，nside 由配置决定（默认 16 -> 3072 分区，约 13 deg²/分区）
- Parquet + ZSTD 压缩；读取端支持列裁剪（只读需要的列，省 I/O 与内存）
- 分块流式写入：单文件再大也不会撑爆内存（默认每 50 万行 flush 一次）

用法:
    python -m preprocessing.shard --catalog data/raw/sdss_galaxies.fits \\
        --survey sdss --ra-col ra --dec-col dec --out data/shards --nside 16
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import numpy as np

from common.healpix import ang2pix, nside2npix
from common.logutil import get_logger

log = get_logger("shard")

FLUSH_ROWS = 500_000  # 单分区累计行数阈值，超过即追加写入


# ---------- 通用目录读取（分块） ----------
def iter_catalog_chunks(path, ra_col, dec_col, columns=None, chunk=2_000_000):
    """分块迭代读取 FITS/CSV/ECSV/Parquet 目录，产出 {col: array}。

    columns=None 表示保留文件中的全部列；否则为 ra/dec + 指定列。
    FITS 用 memmap 避免整表载入；Parquet 用 batch reader 原生流式。
    """
    p = Path(path)
    name = p.name.lower()
    want = None if columns is None else \
        list(dict.fromkeys([ra_col, dec_col, *columns]))
    if name.endswith((".fits", ".fit", ".fts")) or ".fits" in name:
        from astropy.io import fits
        with fits.open(p, memmap=True) as hdul:
            tab = None
            for h in hdul[1:]:
                if hasattr(h, "columns") and h.columns and \
                        ra_col in h.columns.names and dec_col in h.columns.names:
                    tab = h.data
                    break
            if tab is None:
                raise ValueError(f"{p}: 找不到含 {ra_col}/{dec_col} 的表 HDU")
            cols_read = tab.names if want is None else \
                [c for c in want if c in tab.names]
            n = len(tab)
            for i0 in range(0, n, chunk):
                sl = slice(i0, min(i0 + chunk, n))
                yield {c: np.asarray(tab[c][sl]) for c in cols_read}
    elif name.endswith(".parquet"):
        import pyarrow.parquet as pq
        pf = pq.ParquetFile(p)
        cols_avail = pf.schema_arrow.names if want is None else \
            [c for c in want if c in pf.schema_arrow.names]
        for batch in pf.iter_batches(batch_size=chunk, columns=cols_avail):
            yield {c: batch.column(c).to_numpy() for c in cols_avail}
    elif name.endswith((".csv", ".ecsv")):
        from astropy.table import Table
        t = Table.read(p, format="ascii.csv" if name.endswith(".csv") else "ascii.ecsv")
        cols_read = t.colnames if want is None else \
            [c for c in want if c in t.colnames]
        n = len(t)
        for i0 in range(0, n, chunk):
            sl = slice(i0, min(i0 + chunk, n))
            yield {c: np.asarray(t[c][sl]) for c in cols_read}
    else:
        raise ValueError(f"不支持的目录格式: {p}")


# ---------- 分片写入 ----------
class ShardWriter:
    """按 HEALPix 像素聚合行并写 Parquet 分区。"""

    def __init__(self, out_root, survey, nside=16):
        self.root = Path(out_root) / survey
        self.survey = survey
        self.nside = nside
        self.buffers: dict[int, dict[str, list]] = {}
        self.buffer_rows = 0
        self.counts: dict[int, int] = {}
        self.all_columns: list[str] = []

    def _buffer(self, pix: int):
        return self.buffers.setdefault(pix, {})

    def add(self, cols: dict, ra_col: str, dec_col: str):
        ra = np.asarray(cols[ra_col], dtype=float)
        dec = np.asarray(cols[dec_col], dtype=float)
        ok = np.isfinite(ra) & np.isfinite(dec)
        pix = ang2pix(self.nside, ra[ok], dec[ok])
        order = np.argsort(pix, kind="stable")
        pix_s = pix[order]
        bounds = np.searchsorted(pix_s, np.unique(pix_s))
        uniq = pix_s[bounds]
        for j, px in enumerate(uniq):
            sl = order[bounds[j]: bounds[j + 1] if j + 1 < len(bounds) else len(order)]
            buf = self._buffer(int(px))
            for c, arr in cols.items():
                buf.setdefault(c, []).append(np.asarray(arr)[ok][sl])
            n_add = len(sl)
            self.counts[int(px)] = self.counts.get(int(px), 0) + n_add
            self.buffer_rows += n_add
        for c in cols:
            if c not in self.all_columns:
                self.all_columns.append(c)
        if self.buffer_rows >= FLUSH_ROWS:
            self.flush()

    def flush(self):
        import pyarrow as pa
        import pyarrow.parquet as pq
        if not self.buffers:
            return
        nfiles = 0
        for px, cols in self.buffers.items():
            d = self.root / f"Norder={self.nside}"
            d.mkdir(parents=True, exist_ok=True)
            fp = d / f"Npix={px}.parquet"
            table = pa.table({c: np.concatenate(v) for c, v in cols.items()})
            if fp.exists():  # 追加：读出旧表合并重写（分区小，代价可控）
                old = pq.read_table(fp)
                table = pa.concat_tables([old, table], promote_options="default")
            pq.write_table(table, fp, compression="zstd")
            nfiles += 1
        self.buffers.clear()
        self.buffer_rows = 0
        log.info("flush: 写入 %d 个分区", nfiles)

    def finalize(self):
        self.flush()
        meta = {
            "survey": self.survey, "nside": self.nside,
            "scheme": "healpix-ring",
            "npix": nside2npix(self.nside),
            "n_partitions": len(self.counts),
            "n_rows": int(sum(self.counts.values())),
            "columns": self.all_columns,
            "partition_rows": {str(k): v for k, v in sorted(self.counts.items())},
            "created": time.time(),
        }
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / "_metadata.json").write_text(
            json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
        log.info("survey=%s 共 %d 行 -> %d 个分区",
                 self.survey, meta["n_rows"], meta["n_partitions"])
        return meta


def shard_catalog(path, survey, ra_col="ra", dec_col="dec", columns=None,
                  out_root="data/shards", nside=16):
    """对单个目录文件做 HEALPix 预分片。返回元数据 dict。"""
    w = ShardWriter(out_root, survey, nside)
    t0 = time.time()
    n_in = 0
    for cols in iter_catalog_chunks(path, ra_col, dec_col, columns):
        w.add(cols, ra_col, dec_col)
        n_in += len(cols[ra_col])
    meta = w.finalize()
    meta["source"] = str(path)
    meta["wall_time_s"] = round(time.time() - t0, 2)
    meta["rows_per_s"] = int(n_in / max(time.time() - t0, 1e-9))
    return meta


def load_partition(root, survey, pix, columns=None):
    """任务端读取：只读自己的像素分区，只读需要的列。"""
    import pyarrow.parquet as pq
    fp = Path(root) / survey / f"Norder={_read_nside(root, survey)}" / f"Npix={pix}.parquet"
    if not fp.exists():
        return None
    return pq.read_table(fp, columns=columns).to_pydict()


def _read_nside(root, survey):
    meta = json.loads((Path(root) / survey / "_metadata.json").read_text(encoding="utf-8"))
    return meta["nside"]


def occupied_pixels(root, survey):
    meta = json.loads((Path(root) / survey / "_metadata.json").read_text(encoding="utf-8"))
    return {int(k): v for k, v in meta["partition_rows"].items()}


def main():
    ap = argparse.ArgumentParser(description="HEALPix 预分片")
    ap.add_argument("--catalog", required=True)
    ap.add_argument("--survey", required=True)
    ap.add_argument("--ra-col", default="ra")
    ap.add_argument("--dec-col", default="dec")
    ap.add_argument("--columns", nargs="*", default=None,
                    help="除 ra/dec 外需要保留的列（默认全部）")
    ap.add_argument("--out", default="data/shards")
    ap.add_argument("--nside", type=int, default=16)
    args = ap.parse_args()
    meta = shard_catalog(args.catalog, args.survey, args.ra_col, args.dec_col,
                         args.columns, args.out, args.nside)
    print(json.dumps(meta, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
