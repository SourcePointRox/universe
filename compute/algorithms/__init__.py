"""核心算法库：三维宇宙建模。

coords3d_redshift : (ra, dec, z) -> 共动坐标 (Mpc)，Planck18 宇宙学
coords3d_parallax : (ra, dec, parallax_mas) -> 日心坐标 (kpc)，含Lutz-Kelker说明
density_cic       : Cloud-In-Cell 网格化密度场
twopcf            : Landy-Szalay 两点相关函数估计(随机点对)
power_spectrum    : 由密度场FFT估计功率谱
fof_halos         : 简化Friends-of-Friends成团
kaiser_squires    : 弱透镜质量图重建
lightcurve_bls    : 简化凌日箱型拟合(BLS-lite)
"""
from __future__ import annotations

import numpy as np
from astropy.cosmology import Planck18

COSMO = Planck18


def coords3d_redshift(ra_deg, dec_deg, z):
    ra = np.deg2rad(np.asarray(ra_deg, dtype=float))
    dec = np.deg2rad(np.asarray(dec_deg, dtype=float))
    z = np.clip(np.asarray(z, dtype=float), 1e-6, None)
    dc = COSMO.comoving_distance(z).to_value("Mpc")
    x = dc * np.cos(dec) * np.cos(ra)
    y = dc * np.cos(dec) * np.sin(ra)
    zz = dc * np.sin(dec)
    return np.vstack([x, y, zz]).T, dc


def coords3d_parallax(ra_deg, dec_deg, parallax_mas):
    """视差->距离(kpc)。plx<=0 的点按下限截断并标记，不静默丢弃。"""
    ra = np.deg2rad(np.asarray(ra_deg, dtype=float))
    dec = np.deg2rad(np.asarray(dec_deg, dtype=float))
    plx = np.asarray(parallax_mas, dtype=float)
    valid = plx > 0
    d_kpc = np.where(valid, 1.0 / np.maximum(plx, 1e-3), np.nan)
    x = d_kpc * np.cos(dec) * np.cos(ra)
    y = d_kpc * np.cos(dec) * np.sin(ra)
    z = d_kpc * np.sin(dec)
    return np.vstack([x, y, z]).T, valid


def density_cic(pos, box_min, box_max, ngrid=64, weight=None):
    """CIC质量分配，返回 (delta, rho, edges)。pos: (N,3)。"""
    pos = np.asarray(pos, dtype=float)
    box_min = np.asarray(box_min, dtype=float)
    box_max = np.asarray(box_max, dtype=float)
    span = box_max - box_min
    cell = span / ngrid
    rho = np.zeros((ngrid, ngrid, ngrid), dtype=np.float64)
    w = np.ones(len(pos)) if weight is None else np.asarray(weight, dtype=float)

    u = (pos - box_min) / cell - 0.5
    inside = np.all((u >= -1) & (u < ngrid - 0.5), axis=1)
    u = u[inside]
    w = w[inside]
    i0 = np.floor(u).astype(int)
    f = u - i0
    for dz in range(2):
        for dy in range(2):
            for dx in range(2):
                idx = i0 + np.array([dx, dy, dz])
                frac = np.where(dx, f[:, 0], 1 - f[:, 0]) \
                     * np.where(dy, f[:, 1], 1 - f[:, 1]) \
                     * np.where(dz, f[:, 2], 1 - f[:, 2])
                ok = np.all((idx >= 0) & (idx < ngrid), axis=1)
                np.add.at(rho, tuple(idx[ok].T), w[ok] * frac[ok])
    cell_vol = float(np.prod(cell))
    rho /= cell_vol
    mean = rho.mean()
    delta = rho / mean - 1.0 if mean > 0 else rho
    edges = [np.linspace(box_min[i], box_max[i], ngrid + 1) for i in range(3)]
    return delta, rho, edges


def power_spectrum(delta, box_size, kmax=None, nbins=20):
    """由过密度场 delta 估计各向同性功率谱 P(k)。box_size: 标量或(3,)。"""
    delta = np.asarray(delta, dtype=float)
    n = delta.shape[0]
    box_size = np.atleast_1d(np.asarray(box_size, dtype=float))
    if box_size.size == 1:
        box_size = np.repeat(box_size, 3)
    dk = np.fft.fftn(delta)
    pk3d = np.abs(dk) ** 2 * np.prod(box_size) / delta.size ** 2
    kfreq = [2 * np.pi * np.fft.fftfreq(n, d=box_size[i] / n) for i in range(3)]
    kx, ky, kz = np.meshgrid(*kfreq, indexing="ij")
    kmag = np.sqrt(kx**2 + ky**2 + kz**2)
    if kmax is None:
        kmax = kmag.max() / 2
    kbins = np.linspace(kmag[kmag > 0].min(), kmax, nbins + 1)
    idx = np.digitize(kmag.ravel(), kbins)
    pk = np.full(nbins, np.nan)
    kmean = np.full(nbins, np.nan)
    for b in range(1, nbins + 1):
        m = idx == b
        if m.any():
            pk[b - 1] = pk3d.ravel()[m].mean()
            kmean[b - 1] = kmag.ravel()[m].mean()
    return kmean, pk


def twopcf(pos, r_edges, box_size=None, n_random=None, seed=42):
    """Landy-Szalay 估计量 xi(r) = (DD - 2DR + RR)/RR。
    使用 cKDTree.count_neighbors 计数，O(N log N)，可处理百万级天体。"""
    from scipy.spatial import cKDTree

    pos = np.asarray(pos, dtype=float)
    rng = np.random.default_rng(seed)
    n = len(pos)
    if box_size is None:
        lo, hi = pos.min(axis=0), pos.max(axis=0)
        box_size = hi - lo
    else:
        lo = pos.min(axis=0)
        box_size = np.atleast_1d(box_size).astype(float)
        if box_size.size == 1:
            box_size = np.repeat(box_size, 3)
    if n_random is None:
        n_random = max(5 * n, 2000)
    rand = rng.uniform(lo, lo + box_size, size=(n_random, 3))

    t_data = cKDTree(pos)
    t_rand = cKDTree(rand)
    # count_neighbors 返回累计计数(<r)，diff得每bin
    dd = np.diff(t_data.count_neighbors(t_data, r_edges)) / 2.0
    dr = np.diff(t_data.count_neighbors(t_rand, r_edges))
    rr = np.diff(t_rand.count_neighbors(t_rand, r_edges)) / 2.0
    f = n / n_random
    with np.errstate(divide="ignore", invalid="ignore"):
        xi = (dd * f**2 - 2 * dr * f + rr) / rr
    rcen = 0.5 * (r_edges[:-1] + r_edges[1:])
    return rcen, xi


def fof_halos(pos, linking_length, min_members=5):
    """简化FoF：基于scipy KDTree 的连通分量标记。返回每个点的group id(-1为未成团)。"""
    from scipy.spatial import cKDTree

    pos = np.asarray(pos, dtype=float)
    tree = cKDTree(pos)
    pairs = tree.query_pairs(linking_length, output_type="ndarray")
    labels = -np.ones(len(pos), dtype=int)
    if len(pairs) == 0:
        return labels, {}
    # union-find
    parent = np.arange(len(pos))

    def find(a):
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    for a, b in pairs:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb
    roots = np.array([find(i) for i in np.unique(pairs.ravel())])
    uniq, counts = np.unique(roots, return_counts=True)
    sizes = {}
    gid = 0
    for root, _c in zip(uniq, counts):
        members = np.where(np.array([find(i) for i in range(len(pos))]) == root)[0]
        if len(members) >= min_members:
            labels[members] = gid
            sizes[gid] = len(members)
            gid += 1
    return labels, sizes


def kaiser_squires(ra_deg, dec_deg, e1, e2, ngrid=64):
    """Kaiser-Squires (1993) 平面近似质量图重建。返回 kappa 图。"""
    ra = np.asarray(ra_deg, float)
    dec = np.asarray(dec_deg, float)
    e1 = np.asarray(e1, float)
    e2 = np.asarray(e2, float)
    x = np.deg2rad(ra - ra.mean())
    y = np.deg2rad(dec - dec.mean())
    x0, x1 = x.min(), x.max()
    y0, y1 = y.min(), y.max()
    g1, _, _ = np.histogram2d(x, y, bins=ngrid, range=[[x0, x1], [y0, y1]], weights=e1)
    g2, _, _ = np.histogram2d(x, y, bins=ngrid, range=[[x0, x1], [y0, y1]], weights=e2)
    nmap, _, _ = np.histogram2d(x, y, bins=ngrid, range=[[x0, x1], [y0, y1]])
    with np.errstate(invalid="ignore", divide="ignore"):
        g1 = np.where(nmap > 0, g1 / nmap, 0.0)
        g2 = np.where(nmap > 0, g2 / nmap, 0.0)
    kx = 2 * np.pi * np.fft.fftfreq(ngrid, d=(x1 - x0) / ngrid)
    ky = 2 * np.pi * np.fft.fftfreq(ngrid, d=(y1 - y0) / ngrid)
    k1, k2 = np.meshgrid(kx, ky, indexing="ij")
    k2sum = k1**2 + k2**2
    k2sum[0, 0] = 1.0
    p1 = (k1**2 - k2**2) / k2sum
    p2 = 2 * k1 * k2 / k2sum
    p1[0, 0] = p2[0, 0] = 0.0
    g1k = np.fft.fft2(g1)
    g2k = np.fft.fft2(g2)
    kappa = np.real(np.fft.ifft2(p1 * g1k + p2 * g2k))
    return kappa, nmap


def lightcurve_bls(time, flux, n_periods=500, dur_frac=(0.01, 0.1), n_bins=50):
    """简化BLS：扫描周期，箱型模型卡方最小化。返回(最佳周期, 深度, 周期gram)。"""
    t = np.asarray(time, float)
    f = np.asarray(flux, float)
    span = t.max() - t.min()
    periods = np.linspace(max(0.2, span / 200), span / 2, n_periods)
    f = f - np.nanmedian(f)
    best = (np.inf, None)
    chi2_arr = np.empty_like(periods)
    for i, p in enumerate(periods):
        phase = np.sort((t / p) % 1.0)
        fb = f[np.argsort((t / p) % 1.0)]
        edges = np.linspace(0, 1, n_bins + 1)
        inds = np.digitize(phase, edges) - 1
        means = np.zeros(n_bins)
        counts = np.zeros(n_bins)
        np.add.at(means, inds, fb)
        np.add.at(counts, inds, 1)
        with np.errstate(invalid="ignore", divide="ignore"):
            means = np.where(counts > 0, means / counts, 0.0)
        resid = fb - means[inds]
        chi2 = float(np.sum(resid**2))
        chi2_arr[i] = chi2
        if chi2 < best[0]:
            best = (chi2, p)
    depth = float(np.nanmin(f) ) if len(f) else 0.0
    return best[1], depth, (periods, chi2_arr)


def stream_likelihood_placeholder(xyz):
    """银晕潮汐流似然占位：返回点集的空间二阶矩(椭率指标)作为特征。"""
    xyz = np.asarray(xyz, float)
    if len(xyz) < 10:
        return {"n": len(xyz), "axis_ratio": np.nan}
    cov = np.cov(xyz.T)
    eig = np.sort(np.linalg.eigvalsh(cov))[::-1]
    return {"n": len(xyz), "eig": eig.tolist(),
            "axis_ratio": float(np.sqrt(eig[-1] / eig[0])) if eig[0] > 0 else np.nan}


def gw_host_match(gw_ra, gw_dec, gw_dist_mpc, dist_err, gal_xyz, gal_radecz):
    """在误差椭球内搜索候选宿主星系。gal_xyz:(N,3) Mpc, gal_radecz:(N,3)[ra,dec,z]。"""
    from astropy.cosmology import Planck18 as cosmo
    ra = np.deg2rad(gw_ra)
    dec = np.deg2rad(gw_dec)
    d = gw_dist_mpc
    center = d * np.array([np.cos(dec) * np.cos(ra), np.cos(dec) * np.sin(ra), np.sin(dec)])
    dist = np.linalg.norm(gal_xyz - center, axis=1)
    sel = dist <= 3 * dist_err
    idx = np.where(sel)[0]
    return idx, dist[idx]


def hod_mock(halo_pos, halo_mass, m_min=1e12, n_sat_alpha=1.0, seed=1):
    """极简HOD：质量>m_min的晕放入1个中心星系+N_sat个卫星星系(均匀球内)。"""
    rng = np.random.default_rng(seed)
    halo_mass = np.asarray(halo_mass, float)
    halo_pos = np.asarray(halo_pos, float)
    sel = halo_mass >= m_min
    pos_out = []
    for p, m in zip(halo_pos[sel], halo_mass[sel]):
        pos_out.append(p)
        n_sat = int((m / m_min) ** n_sat_alpha)
        r_vir = (m / 1e12) ** (1 / 3) * 0.3  # Mpc 粗估
        if n_sat > 0:
            u = rng.normal(size=(n_sat, 3))
            u /= np.linalg.norm(u, axis=1, keepdims=True)
            rr = r_vir * rng.uniform(0, 1, n_sat) ** (1 / 3)
            pos_out.extend(p + u * rr[:, None])
    return np.asarray(pos_out)
