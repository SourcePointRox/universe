"""天区分片：等间距柱面 tile 划分与解析。

tile_id 形如 "r12_d05"，表示 ra_bin=12 (120-130 deg), dec_bin=5 (-40..-30 deg)。
升级为 HEALPix 时只需替换本模块。
"""
import math

import yaml


def load_tiling(cfg_path):
    with open(cfg_path, encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    t = cfg["sky_tiling"]
    return t["ra_bins"], t["dec_bins"]


def all_tiles(ra_bins=36, dec_bins=18):
    tiles = []
    for d in range(dec_bins):
        for r in range(ra_bins):
            tiles.append(f"r{r:02d}_d{d:02d}")
    return tiles


def tile_bounds(tile_id, ra_bins=36, dec_bins=18):
    r = int(tile_id[1:3])
    d = int(tile_id[5:7])
    ra0 = r * 360.0 / ra_bins
    ra1 = (r + 1) * 360.0 / ra_bins
    dec0 = -90.0 + d * 180.0 / dec_bins
    dec1 = -90.0 + (d + 1) * 180.0 / dec_bins
    return ra0, ra1, dec0, dec1


def tile_of(ra_deg, dec_deg, ra_bins=36, dec_bins=18):
    ra = math.fmod(ra_deg + 360.0, 360.0)
    dec = min(89.9999, max(-90.0, dec_deg))
    r = int(ra / 360.0 * ra_bins) % ra_bins
    d = int((dec + 90.0) / 180.0 * dec_bins)
    d = min(dec_bins - 1, d)
    return f"r{r:02d}_d{d:02d}"
