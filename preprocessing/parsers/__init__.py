"""文件类型解析器：FITS / CSV / HDF5 / VOTable / GWF / ECSV。
每个解析器返回统一元数据 dict：
{survey_hint, columns, n_rows, ra?, dec?, z?, header_keys}
"""
from pathlib import Path

import numpy as np

RA_NAMES = {"ra", "raj2000", "ra_deg", "alpha", "rightascension", "ra_icrs"}
DEC_NAMES = {"dec", "dej2000", "dec_deg", "delta", "declination", "dec_icrs"}
Z_NAMES = {"z", "redshift", "z_spec", "z_phot", "photoz", "zphot"}
PLX_NAMES = {"parallax", "plx"}
E1_NAMES = {"e1", "gamma1", "g1"}
E2_NAMES = {"e2", "gamma2", "g2"}
TIME_NAMES = {"time", "bjd", "jd", "mjd"}
FLUX_NAMES = {"flux", "pdcsap_flux", "sap_flux", "counts"}


def _classify_columns(cols):
    lowered = {c.lower(): c for c in cols}

    def pick(names):
        for n in names:
            if n in lowered:
                return lowered[n]
        return None

    return {
        "ra": pick(RA_NAMES), "dec": pick(DEC_NAMES), "z": pick(Z_NAMES),
        "parallax": pick(PLX_NAMES), "e1": pick(E1_NAMES), "e2": pick(E2_NAMES),
        "time": pick(TIME_NAMES), "flux": pick(FLUX_NAMES),
    }


def _header_hints(header_keys):
    keys = {k.upper() for k in header_keys}
    hints = []
    for tag in ["GAIA", "DESI", "SDSS", "BOSS", "APOGEE", "EUCLID", "TESS",
                "HST", "JWST", "2MASS", "WISE", "PLANCK", "PANSTARRS"]:
        if any(tag in k for k in keys):
            hints.append(tag.lower())
    return hints


def parse_fits(path):
    from astropy.io import fits
    meta = {"format": "fits", "columns": [], "n_rows": 0, "header_keys": []}
    with fits.open(path, memmap=True) as hdul:
        keys = set()
        for h in hdul[:4]:
            keys.update(h.header.keys())
        meta["header_keys"] = sorted(k for k in keys if k and not k.startswith("HISTORY"))[:80]
        for h in hdul[1:]:
            if hasattr(h, "columns") and h.columns is not None and len(h.columns) > 0:
                meta["columns"] = list(h.columns.names)
                meta["n_rows"] = int(getattr(h, "nrows", 0) or 0)
                break
        if not meta["columns"] and hdul[0].data is not None:
            d = hdul[0].data
            meta["image_shape"] = list(d.shape)
    meta["survey_hints"] = _header_hints(meta["header_keys"])
    meta.update(_classify_columns(meta["columns"]))
    return meta


def parse_csv(path, max_rows=200000):
    import csv
    meta = {"format": "csv", "columns": [], "n_rows": 0, "header_keys": []}
    with open(path, newline="", encoding="utf-8", errors="ignore") as f:
        sample = f.read(4096)
        f.seek(0)
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=",\t;|")
        except csv.Error:
            dialect = csv.excel
        reader = csv.reader(f, dialect)
        try:
            header = next(reader)
        except StopIteration:
            return meta
        meta["columns"] = [c.strip() for c in header]
        n = 0
        for _ in reader:
            n += 1
            if n >= max_rows:
                meta["n_rows_truncated"] = True
                break
        meta["n_rows"] = n
    meta.update(_classify_columns(meta["columns"]))
    meta["survey_hints"] = []
    return meta


def parse_hdf5(path):
    import h5py
    meta = {"format": "hdf5", "columns": [], "n_rows": 0, "header_keys": []}
    cols = []
    with h5py.File(path, "r") as f:
        meta["header_keys"] = sorted(f.attrs.keys())
        def visit(name, obj):
            if hasattr(obj, "dtype") and obj.dtype.fields:
                for fld in obj.dtype.fields:
                    cols.append(f"{name}/{fld}")
            elif name.count("/") <= 2 and hasattr(obj, "shape") and obj.ndim == 1:
                cols.append(name.split("/")[-1])
        f.visititems(visit)
        if cols:
            meta["columns"] = cols[:200]
    meta["survey_hints"] = _header_hints(meta["header_keys"])
    meta.update(_classify_columns([c.split("/")[-1] for c in meta["columns"]]))
    return meta


def parse_votable(path):
    from astropy.io.votable import parse as vot_parse
    meta = {"format": "votable", "columns": [], "n_rows": 0, "header_keys": []}
    vot = vot_parse(str(path))
    for table in vot.iter_tables():
        meta["columns"] = [f.name for f in table.fields]
        try:
            meta["n_rows"] = int(table.array.shape[0])
        except Exception:
            pass
        break
    meta.update(_classify_columns(meta["columns"]))
    meta["survey_hints"] = []
    return meta


def parse_gwf(path):
    meta = {"format": "gwf", "columns": [], "n_rows": 0, "header_keys": ["GWOSC", "strain"],
            "survey_hints": ["ligo"]}
    meta["survey_hints"] = ["ligo"]
    return meta


def parse_ecsv(path):
    meta = {"format": "ecsv", "columns": [], "n_rows": 0, "header_keys": []}
    cols = []
    with open(path, encoding="utf-8", errors="ignore") as f:
        for line in f:
            if line.startswith("#") and "name:" in line and "dtype" not in line:
                pass
            if not line.startswith("#") and line.strip():
                cols = [c.strip() for c in line.split()]
                break
    meta["columns"] = cols
    meta.update(_classify_columns(cols))
    meta["survey_hints"] = ["gaia"] if "source_id" in {c.lower() for c in cols} else []
    return meta


PARSERS = {
    ".fits": parse_fits, ".fit": parse_fits, ".fts": parse_fits,
    ".csv": parse_csv, ".tsv": parse_csv,
    ".hdf5": parse_hdf5, ".h5": parse_hdf5,
    ".vot": parse_votable, ".votable": parse_votable, ".xml": parse_votable,
    ".gwf": parse_gwf,
    ".ecsv": parse_ecsv,
}


def parse_file(path):
    ext = Path(path).suffix.lower()
    if ext == ".gz":
        ext = Path(Path(path).stem).suffix.lower() + ".gz"
        if ext.startswith("."):
            ext = ext.replace(".gz", "")
    parser = PARSERS.get(ext)
    if parser is None:
        return None
    try:
        return parser(path)
    except Exception as e:
        return {"format": ext.lstrip("."), "error": str(e), "columns": [],
                "n_rows": 0, "header_keys": [], "survey_hints": []}
