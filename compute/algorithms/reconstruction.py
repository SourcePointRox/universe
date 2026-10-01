"""贝叶斯三维物质密度场重建（Wiener 滤波）。

解决问题清单 #10/#12：从"星系计数 -> 单个密度值"升级为
"观测 -> 似然 -> 先验 -> 后验均值 + 后验不确定度"。

模型：
    n_g(x) = n̄(x) [1 + b δ_m(x)] + Poisson 噪声
    先验: δ_m 为高斯随机场, 功率谱 P(k)（线性理论, Planck18）

Wiener 滤波后验（Zaroubi+ 1995; 2M++ 重建, Lavaux & Hudson 2011 同款）：
    数据向量 d(x) = n_g(x)/n̄(x) - 1     （选择函数改正后的星系过密度）
    傅里叶空间对角近似:
      δ̂_m(k) = F(k) · d(k) / b,   F(k) = b²P(k) / (b²P(k) + 1/n̄)
    后验方差（逐 k 模）:
      σ²(k) = P(k) · (1-F(k))

适用条件与局限（如实声明）：
- 高斯先验在大尺度 (k ≲ 0.1 h/Mpc) 良好；小尺度非高斯,
  升级路径是 lognormal-Poisson 后验 (Kitaura & Enßlin 2008)
  或 HMC 前向建模 (BORG, Jasche & Lavaux 2019)。
- 非均匀 n̄(x) 下 N 算子非对角，本实现用局部 n̄ 的几何均值近似;
  巡天 mask 边界通过把 n̄=0 区域数据置零并只输出 mask 内结果处理。
"""
from __future__ import annotations

import numpy as np

from common.cosmology import pk_mpc
from common.logutil import get_logger

log = get_logger("reconstruction")


def wiener_reconstruct(galaxy_pos, box_min, box_size, ngrid=128,
                       nbar=None, bias=1.0, pk_func=None):
    """Wiener 滤波密度重建。

    参数:
        galaxy_pos: (N,3) 星系共动坐标 [Mpc]（已做选择函数体积元对应）
        box_min: (3,) 盒子角点 [Mpc]
        box_size: 标量或 (3,) 盒边长 [Mpc]
        ngrid: 重建网格数
        nbar: 选择函数——每 cell 的期望星系数 (ngrid³ 数组)。
              None 时按均匀 n̄ = N/V_cell 估计（仅适合完备巡天）。
        bias: 线性星系偏置 b
        pk_func: P(k) 先验函数（k 单位 1/Mpc -> P 单位 Mpc³），
                 默认 Planck18 线性谱
    返回: dict(delta_mean, delta_sigma, cell_counts, nbar, edges)
        delta_mean: 物质过密度后验均值场
        delta_sigma: 逐 cell 后验标准差（不确定度图）
    """
    pk_func = pk_func or pk_mpc
    box_size = np.atleast_1d(np.asarray(box_size, dtype=float))
    if box_size.size == 1:
        box_size = np.repeat(box_size, 3)
    box_min = np.asarray(box_min, dtype=float)

    pos = np.asarray(galaxy_pos, dtype=float)
    cell = box_size / ngrid
    # ---- 计数场（NGP 足够：Wiener 滤波本身平滑；CIC 可选）----
    ijk = np.floor((pos - box_min) / cell).astype(int)
    inside = np.all((ijk >= 0) & (ijk < ngrid), axis=1)
    counts = np.zeros((ngrid,) * 3, dtype=np.float64)
    np.add.at(counts, tuple(ijk[inside].T), 1.0)

    if nbar is None:
        nbar = np.full((ngrid,) * 3, inside.sum() / ngrid ** 3, dtype=np.float64)
    else:
        nbar = np.asarray(nbar, dtype=float)

    mask = nbar > 0
    nbar_eff = float(np.exp(np.mean(np.log(nbar[mask]))))  # 几何均值近似

    d = np.zeros_like(counts)
    d[mask] = counts[mask] / nbar[mask] - 1.0

    dk = np.fft.fftn(d)
    k1 = 2 * np.pi * np.fft.fftfreq(ngrid, d=cell[0])
    kx, ky, kz = np.meshgrid(k1, k1, k1, indexing="ij")
    kmag = np.sqrt(kx ** 2 + ky ** 2 + kz ** 2)
    pk = pk_func(kmag.ravel()).reshape(kmag.shape)
    pk[0, 0, 0] = 0.0

    shot = box_size.prod() / max(float(nbar.sum()), 1.0)  # P_noise = 1/n̄_density [Mpc³]
    f_k = bias ** 2 * pk / (bias ** 2 * pk + shot)
    delta_k = f_k * dk / bias
    delta_mean = np.fft.ifftn(delta_k).real

    # 后验方差（对角近似）：C(x,x) = (1/V) Σ_k σ²(k)，σ²(k) = P(k)(1-F(k))。
    # 均匀噪声近似下为常数场（非均匀 n̄ 的空间变化不确定度图需要
    # 共轭梯度求解 (S+N)⁻¹，列为升级路径）。
    var_scalar = float((pk * (1.0 - f_k)).sum() / box_size.prod())
    delta_sigma = np.full_like(counts, np.sqrt(max(var_scalar, 0.0)))
    delta_sigma[~mask] = np.nan
    delta_mean[~mask] = np.nan

    edges = [box_min[i] + np.linspace(0, box_size[i], ngrid + 1) for i in range(3)]
    log.info("Wiener 重建: N=%d, n̄_eff=%.3e/cell, b=%.2f, mask 覆盖 %.1f%%",
             int(inside.sum()), nbar_eff, bias, 100.0 * mask.mean())
    return {"delta_mean": delta_mean, "delta_sigma": delta_sigma,
            "cell_counts": counts, "nbar": nbar, "edges": edges,
            "nbar_eff": nbar_eff, "bias": bias}


def nbar_from_radial_selection(galaxy_pos, box_min, box_size, ngrid=128,
                               observer=(0.0, 0.0, 0.0), n_rbins=32,
                               angular_mask=None):
    """由数据自身估计径向选择函数 n̄(x)（亮度选择导致的径向稀薄化）。

    角向 mask: 可选 (ngrid³ bool)；None 时假设盒子全天完备。
    返回平滑后的 n̄ 网格（每 cell 期望计数）。
    """
    box_size = np.atleast_1d(np.asarray(box_size, dtype=float))
    if box_size.size == 1:
        box_size = np.repeat(box_size, 3)
    box_min = np.asarray(box_min, dtype=float)
    cell = box_size / ngrid

    pos = np.asarray(galaxy_pos, dtype=float)
    r = np.linalg.norm(pos - np.asarray(observer, dtype=float), axis=1)
    r_edges = np.linspace(0.0, r.max() * 1.0001, n_rbins + 1)
    n_r, _ = np.histogram(r, bins=r_edges)
    # 壳层 cell 数（几何），密度 = 计数/体积
    vol_r = 4.0 / 3.0 * np.pi * (r_edges[1:] ** 3 - r_edges[:-1] ** 3)
    nbar_r = n_r / np.maximum(vol_r, 1e-30)      # 每 Mpc³

    # 网格化：每 cell 中心距 -> 插值 n̄(r) -> 期望计数
    cc = [box_min[i] + (np.arange(ngrid) + 0.5) * cell[i] for i in range(3)]
    gx, gy, gz = np.meshgrid(*cc, indexing="ij")
    rc = np.sqrt((gx - observer[0]) ** 2 + (gy - observer[1]) ** 2 +
                 (gz - observer[2]) ** 2)
    dens = np.interp(rc, 0.5 * (r_edges[1:] + r_edges[:-1]), nbar_r,
                     left=nbar_r[0], right=0.0)
    nbar = dens * cell.prod()
    if angular_mask is not None:
        nbar = nbar * np.asarray(angular_mask, dtype=float)
    return nbar
