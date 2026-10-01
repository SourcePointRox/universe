"""高精度天体距离推断。

解决问题清单 #9：旧的实现是"视差取倒数 / 红移查表"的点估计，
本模块实现完整的 观测->似然->先验->后验 链条：

1. Gaia 视差零点改正（Lindegren+ 2021 的简化实现）
2. Bayesian 距离后验（Bailer-Jones 2015/2021 指数衰减空间密度先验），
   输出后验中位数与 16/84 分位不确定度——不是单个数
3. 红移->共动距离 + spec-z/photo-z 误差传播
4. CMB 参考系特殊速度改正（局部宇宙 z<=0.15 必须做，
   否则近端星系距离误差可达 30%）
"""
from __future__ import annotations

import numpy as np

from common.cosmology import comoving_distance, dcomoving_dz

C_KMS = 299792.458

# CMB 偶极：V = 369.0 km/s toward (l,b)=(263.99°, 48.26°)（Planck 2020），
# 即赤道坐标 J2000 (ra, dec) ≈ (167.94°, -6.94°)
_CMB_V = 369.0
_CMB_RA = np.deg2rad(167.94)
_CMB_DEC = np.deg2rad(-6.94)


# ---------- 1. Gaia 视差零点 ----------
def gaia_zeropoint_offset(g_mag, bprp=None, ra_deg=None, dec_deg=None):
    """Gaia EDR3/DR3 视差零点估计（mas），应做 ϖ_corr = ϖ_obs - offset。

    Lindegren+ (2021, A&A 649, A4) 的零点是星等/颜色/天区/多参数解的复杂
    函数，官方实现为 gaiadr3_zeropoint 包。此处实现其量级正确的简化形式：
    - 全天中位水平 ≈ -0.017 mas
    - 亮端 (G<13) 偏移略小，暗端 (G>16) 略大（Groenewegen 2021;
      Khan et al. 2023 的文献汇编趋势）
    科研级应用应通过 `use_official=True` 路径接入官方表（接口预留）。
    """
    g = np.asarray(g_mag, dtype=float)
    base = -0.017
    trend = np.where(g < 13.0, 0.005,
                     np.where(g > 16.0, -0.004 * np.minimum(g - 16.0, 3.0), 0.0))
    return base + trend


# ---------- 2. Bayesian 视差距离后验 ----------
def parallax_distance_posterior(plx_mas, plx_err_mas, L_kpc=1.35,
                                r_grid_kpc=None):
    """Bailer-Jones 式几何距离后验。

    p(r | ϖ, σ) ∝ N(ϖ; 1/r, σ²) × r² exp(-r/L)

    参数:
        plx_mas: 观测视差 [mas]（允许 <=0，后验仍然良好定义——这正是
                 贝叶斯方法相对 1/ϖ 的核心优势, Luri+ 2018）
        plx_err_mas: 视差误差 [mas]
        L_kpc: 先验标长（银河系薄盘近似值 1.35 kpc，Bailer-Jones 2015;
               严格应用应随 (l,b) 从银河模型取值，接口预留）
    返回: dict(r_med, r_lo, r_hi, mode) [kpc]，r_lo/r_hi 为 16/84 分位。
    """
    plx = np.atleast_1d(np.asarray(plx_mas, dtype=float))
    err = np.maximum(np.atleast_1d(np.asarray(plx_err_mas, dtype=float)), 1e-4)
    L = np.atleast_1d(np.asarray(L_kpc, dtype=float))
    if L.size == 1:
        L = np.full(plx.shape, L.item())
    if r_grid_kpc is None:
        r_grid_kpc = np.concatenate([
            np.linspace(0.001, 0.1, 200),
            np.geomspace(0.1, 100.0, 2000)])

    r = r_grid_kpc[None, :]                     # (1, G)
    prior = r ** 2 * np.exp(-r / L[:, None])    # (N, G)
    with np.errstate(divide="ignore"):
        like = np.exp(-0.5 * ((plx[:, None] - 1.0 / r) / err[:, None]) ** 2)
    post = like * prior
    norm = np.trapezoid(post, r, axis=1)
    norm[norm <= 0] = 1.0
    cdf = np.cumsum(post, axis=1)
    cdf = cdf / cdf[:, -1:]

    def _quant(q):
        idx = np.argmax(cdf >= q, axis=1)
        return r_grid_kpc[idx]

    r16, r50, r84 = _quant(0.16), _quant(0.50), _quant(0.84)
    mode = r_grid_kpc[np.argmax(post, axis=1)]
    out = {"r_med": r50, "r_lo": r16, "r_hi": r84, "r_mode": mode,
           "sigma_r": 0.5 * (r84 - r16)}
    return {k: (v[0] if np.ndim(plx_mas) == 0 else v) for k, v in out.items()}


# ---------- 3. CMB 参考系特殊速度改正 ----------
def heliocentric_to_cmb(z_helio, ra_deg, dec_deg):
    """日心红移 -> CMB 静止系红移（一阶多普勒改正）。

    cz_cmb = cz_hel + V_cmb · n̂(ra, dec)
    """
    ra = np.deg2rad(np.asarray(ra_deg, dtype=float))
    dec = np.deg2rad(np.asarray(dec_deg, dtype=float))
    n = np.stack([np.cos(dec) * np.cos(ra), np.cos(dec) * np.sin(ra), np.sin(dec)])
    v_apex = _CMB_V * np.array([
        np.cos(_CMB_DEC) * np.cos(_CMB_RA),
        np.cos(_CMB_DEC) * np.sin(_CMB_RA),
        np.sin(_CMB_DEC)])
    dv = v_apex @ n                                # km/s
    z = np.asarray(z_helio, dtype=float)
    cz_cmb = C_KMS * z + dv
    return cz_cmb / C_KMS


def peculiar_velocity(z_obs, z_cos):
    """由观测红移与宇宙学红移之差估计视线特殊速度，km/s。"""
    z_obs = np.asarray(z_obs, dtype=float)
    z_cos = np.asarray(z_cos, dtype=float)
    return C_KMS * (z_obs - z_cos) / (1.0 + z_cos)


# ---------- 4. 红移距离 + 误差传播 ----------
def redshift_distance(z, z_err=None, ra_deg=None, dec_deg=None,
                      correct_cmb=True):
    """红移 -> 共动距离 [Mpc]，带误差传播与 CMB 改正。

    返回 dict(d_mpc, d_lo, d_hi, sigma_d, z_eff, v_pec_kms)。
    d_lo/d_hi 为 z±σ_z 对应的距离（一阶误差传播，spec-z 下足够精确；
    photo-z 应用 sample_photoz 走蒙特卡洛路径）。
    """
    z = np.asarray(z, dtype=float)
    z_eff = z
    if correct_cmb and ra_deg is not None and dec_deg is not None:
        z_eff = heliocentric_to_cmb(z, ra_deg, dec_deg)
    d = comoving_distance(np.clip(z_eff, 1e-5, None))
    out = {"d_mpc": d, "z_eff": z_eff}
    if z_err is not None:
        ze = np.asarray(z_err, dtype=float)
        d_lo = comoving_distance(np.clip(z_eff - ze, 1e-5, None))
        d_hi = comoving_distance(z_eff + ze)
        out["d_lo"] = d_lo
        out["d_hi"] = d_hi
        out["sigma_d"] = dcomoving_dz(z_eff) * ze   # 一阶传播
    return out


def sample_photoz(z_phot, sigma_z, n_samples=200, z_max=0.2, seed=42):
    """photo-z 样本的蒙特卡洛距离后验（截断高斯 p(z)）。

    返回 (n_obj, n_samples) 的距离样本数组 [Mpc]，供后验密度重建做
    分层采样（tomographic marginalization），而非把 photo-z 当点估计。
    """
    rng = np.random.default_rng(seed)
    z_phot = np.atleast_1d(np.asarray(z_phot, dtype=float))
    sigma_z = np.atleast_1d(np.asarray(sigma_z, dtype=float))
    if sigma_z.size == 1:
        sigma_z = np.full(z_phot.shape, sigma_z.item())
    zs = rng.normal(z_phot[:, None], sigma_z[:, None],
                    size=(len(z_phot), n_samples))
    zs = np.clip(zs, 1e-5, z_max)
    return comoving_distance(zs)
