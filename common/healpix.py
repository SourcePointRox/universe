"""HEALPix 天球像素化（纯 NumPy 实现，无 healpy 依赖）。

采用 RING 编号方案（自北极沿等余纬环递增）。HEALPix 保证所有像素等面积，
是大巡天分片的事实标准（HATS/LSDB、Planck、Euclid 均使用）。
Górski et al. 2005, ApJ, 622, 759。

设计说明：
- 选择 RING 而非 NESTED 是因为 RING 的 ang2pix/pix2ang 有简洁闭式表达，
  便于无依赖实现与审计；NESTED（分层四叉树序，HATS 兼容）作为后续升级路径。
- 接口命名与 healpy 对齐，将来可无缝替换为 healpy 后端。
"""
from __future__ import annotations

import numpy as np

__all__ = ["nside2npix", "npix2nside", "pix2ang", "ang2pix", "pix_area_deg2",
           "tile_id", "parse_tile_id", "all_tiles", "tile_bounds"]


def nside2npix(nside: int) -> int:
    return 12 * nside * nside


def npix2nside(npix: int) -> int:
    nside = int(round(np.sqrt(npix / 12.0)))
    if 12 * nside * nside != npix:
        raise ValueError(f"{npix} 不是合法的 HEALPix 像素数")
    return nside


def pix_area_deg2(nside: int) -> float:
    return 41252.96124941927 / nside2npix(nside)


def ang2pix(nside: int, ra_deg, dec_deg):
    """(ra, dec)[度] -> RING 像素号。向量化。dec 范围 [-90, 90]。"""
    ra = np.asarray(ra_deg, dtype=float)
    dec = np.asarray(dec_deg, dtype=float)
    theta = np.deg2rad(90.0 - np.clip(dec, -90.0, 90.0))
    phi = np.mod(np.deg2rad(ra), 2.0 * np.pi)

    z = np.cos(theta)
    za = np.abs(z)
    tt = phi / (0.5 * np.pi)  # in [0, 4)

    ncap = 2 * nside * (nside - 1)
    npix = 12 * nside * nside

    # ---- 赤道区 |z| <= 2/3 ----
    jp = np.floor(nside * (0.5 + tt - z * 0.75)).astype(np.int64)
    jm = np.floor(nside * (0.5 + tt + z * 0.75)).astype(np.int64)
    ir = nside + 1 + jp - jm  # 环号, 1..2nside+1
    kshift = 1 - (ir & 1)
    ip = (jp + jm - nside + kshift + 1) // 2
    ip = np.mod(ip, 4 * nside)
    pix_eq = ncap + (ir - 1) * 4 * nside + ip

    # ---- 极区 ----
    tp = tt - np.floor(tt)
    tmp = nside * np.sqrt(3.0 * np.maximum(0.0, 1.0 - za))
    jp2 = np.floor(tp * tmp).astype(np.int64)
    jm2 = np.floor((1.0 - tp) * tmp).astype(np.int64)
    ir2 = jp2 + jm2 + 1
    ip2 = np.floor(tt * ir2).astype(np.int64)
    ip2 = np.minimum(ip2, 4 * ir2 - 1)
    pix_north = 2 * (ir2 - 1) * ir2 + ip2
    pix_south = npix - 2 * ir2 * (ir2 + 1) + ip2
    pix_pol = np.where(z > 0, pix_north, pix_south)

    return np.where(za <= 2.0 / 3.0, pix_eq, pix_pol)


def pix2ang(nside: int, pix):
    """RING 像素号 -> (ra, dec)[度]（像素中心）。向量化。"""
    pix = np.asarray(pix, dtype=np.int64)
    npix = 12 * nside * nside
    if np.any(pix < 0) or np.any(pix >= npix):
        raise ValueError("像素号越界")
    ncap = 2 * nside * (nside - 1)
    fact2 = 4.0 / npix

    ra = np.empty(pix.shape, dtype=float)
    dec = np.empty(pix.shape, dtype=float)

    # ---- 北极帽 ----
    m = pix < ncap
    if m.any():
        p = pix[m]
        iring = np.floor(0.5 * (1.0 + np.sqrt(1.0 + 2.0 * p))).astype(np.int64)
        iphi = p - 2 * iring * (iring - 1) + 1
        z = 1.0 - iring * iring * fact2
        phi = (iphi - 0.5) * np.pi / (2.0 * iring)
        ra[m] = np.rad2deg(phi)
        dec[m] = 90.0 - np.rad2deg(np.arccos(z))

    # ---- 赤道区 ----
    m = (pix >= ncap) & (pix < npix - ncap)
    if m.any():
        p = pix[m] - ncap
        iring = p // (4 * nside) + nside
        iphi = p % (4 * nside) + 1
        fodd = 0.5 * (1 + ((iring + nside) & 1))
        z = (2 * nside - iring) / (1.5 * nside)
        phi = (iphi - fodd) * np.pi / (2.0 * nside)
        ra[m] = np.rad2deg(np.mod(phi, 2.0 * np.pi))
        dec[m] = 90.0 - np.rad2deg(np.arccos(np.clip(z, -1, 1)))

    # ---- 南极帽 ----
    m = pix >= npix - ncap
    if m.any():
        p = npix - pix[m]
        iring = np.floor(0.5 * (1.0 + np.sqrt(2.0 * p - 1.0))).astype(np.int64)
        iphi = 4 * iring + 1 - (p - 2 * iring * (iring - 1))
        z = -1.0 + iring * iring * fact2
        phi = (iphi - 0.5) * np.pi / (2.0 * iring)
        ra[m] = np.rad2deg(np.mod(phi, 2.0 * np.pi))
        dec[m] = 90.0 - np.rad2deg(np.arccos(np.clip(z, -1, 1)))

    return ra, dec


# ---------- 任务分片命名 ----------
def tile_id(pix) -> str:
    """像素号 -> tile 标识，如 'hp000123'。"""
    return f"hp{int(pix):06d}"


def parse_tile_id(tid: str) -> int:
    return int(tid[2:])


def all_tiles(nside: int) -> list[str]:
    return [tile_id(p) for p in range(nside2npix(nside))]


def tile_bounds(nside: int, pix: int, n_samples: int = 200):
    """返回像素中心与近似边界 (ra_min, ra_max, dec_min, dec_max)。

    通过在像素 1/√npix 角尺度的邻域内采样实现，仅用于数据过滤/可视化，
    不用于精确几何运算。
    """
    ra_c, dec_c = pix2ang(nside, pix)
    half = 0.6 * np.sqrt(pix_area_deg2(nside))  # 度
    d = np.linspace(-half, half, int(np.sqrt(n_samples)))
    dra, ddec = np.meshgrid(d, d)
    cosd = max(np.cos(np.deg2rad(dec_c)), 1e-3)
    ras = ra_c + dra.ravel() / cosd
    decs = dec_c + ddec.ravel()
    ok = ang2pix(nside, ras, decs) == pix
    if not ok.any():
        return float(ra_c), float(ra_c), float(dec_c), float(dec_c)
    return (float(np.min(ras[ok])), float(np.max(ras[ok])),
            float(np.min(decs[ok])), float(np.max(decs[ok])))
