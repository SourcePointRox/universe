"""宇宙学工具：Planck18 背景演化、线性增长因子、Eisenstein-Hu 功率谱。

统一在此模块管理宇宙学计算，避免各算法模块各自为政。
- Planck Collaboration 2020, A&A, 641, A6
- Eisenstein & Hu 1998, ApJ, 496, 605（无 BAO 振荡拟合，用于模拟初始条件）
- Carroll, Press & Turner 1992, ARA&A, 30, 499（增长因子积分形式）
"""
from __future__ import annotations

import numpy as np
from astropy.cosmology import Planck18

COSMO = Planck18
H0 = COSMO.H0.value          # km/s/Mpc
H0_INV_MPC = 299792.458 / H0  # 哈勃半径 c/H0, Mpc
OM = COSMO.Om0
OL = COSMO.Ode0
OB = COSMO.Ob0
NS = 0.9665                  # Planck18 标量谱指数
SIGMA8 = 0.8102
T_CMB = 2.7255


def ez(z):
    """无量纲哈勃参量 E(z) = H(z)/H0。"""
    z = np.asarray(z, dtype=float)
    return np.sqrt(OM * (1 + z) ** 3 + OL + (1 - OM - OL) * (1 + z) ** 2)


def hz(z):
    """H(z)，km/s/Mpc。"""
    return H0 * ez(z)


def comoving_distance(z):
    """视线共动距离，Mpc。"""
    return COSMO.comoving_distance(z).to_value("Mpc")


def dcomoving_dz(z):
    """d(d_c)/dz = c/H(z)，Mpc。"""
    return 299792.458 / hz(z)


def growth_factor(a):
    """线性增长因子 D(a)，归一化 D(a=1)=1（积分形式，精确优于 CPT 近似）。

    D(a) ∝ H(a) ∫₀ᵃ da' / (a' H(a'))³
    """
    a = np.atleast_1d(np.asarray(a, dtype=float))
    out = np.empty_like(a)
    from scipy.integrate import quad

    def _g(ai):
        f = lambda x: 1.0 / (x * ez(1.0 / x - 1.0)) ** 3
        val, _ = quad(f, 0.0, ai, epsabs=1e-12)
        return ez(1.0 / ai - 1.0) * val

    for i, ai in enumerate(a):
        out[i] = _g(max(ai, 1e-6))
    out /= _g(1.0)
    return out if out.size > 1 else float(out[0])


def growth_rate_f(a):
    """对数增长率 f = d ln D / d ln a，数值微分。"""
    a = float(a)
    eps = 1e-4
    d1 = growth_factor(a * (1 + eps))
    d2 = growth_factor(a * (1 - eps))
    return float(np.log(d1 / d2) / np.log((1 + eps) / (1 - eps)))


def _eh_transfer(k):
    """Eisenstein-Hu 无振荡转移函数 T(k)，k 单位 h/Mpc。"""
    k = np.asarray(k, dtype=float)
    h = COSMO.h
    # 形状参数（含重子修正，EH98 常用简化）
    gamma = OM * h * np.exp(-OB * (1.0 + np.sqrt(2.0 * h) / OM))
    q = k / gamma
    l0 = np.log(1.0 + 2.34 * q) / (2.34 * q)
    c0 = (1.0 + 3.89 * q + (16.1 * q) ** 2 + (5.46 * q) ** 3 + (6.71 * q) ** 4) ** (-0.25)
    return np.where(k > 1e-8, l0 * c0, 1.0)


def eisenstein_hu_pk(k, normalize=True):
    """Eisenstein-Hu 无振荡线性功率谱 P(k)，k 单位 h/Mpc，P 单位 (Mpc/h)³。

    返回的是 h 单位制；调用方自行转换。normalize=True 时归一化到 sigma8。
    """
    scalar = np.ndim(k) == 0
    k = np.atleast_1d(np.asarray(k, dtype=float))
    tk = _eh_transfer(k)
    pk = k ** NS * tk ** 2
    if normalize:
        # 顶帽滤波 8 Mpc/h 处归一化到 sigma8；对数等距点用对数梯形积分
        kf = np.logspace(-4, 2, 4000)
        pf = kf ** NS * _eh_transfer(kf) ** 2
        r = 8.0
        w = 3.0 * (np.sin(kf * r) - kf * r * np.cos(kf * r)) / (kf * r) ** 3
        w[kf * r < 1e-3] = 1.0
        s2 = np.trapezoid(pf * w ** 2 * kf ** 3 / (2 * np.pi ** 2), np.log(kf))
        pk *= SIGMA8 ** 2 / s2
    return float(pk[0]) if scalar else pk


def pk_mpc(k_mpc):
    """线性功率谱，输入 k 单位 1/Mpc，输出 P 单位 Mpc³（z=0）。"""
    h = COSMO.h
    k_h = np.asarray(k_mpc, dtype=float) * h  # 1/Mpc -> h/Mpc
    pk_h = eisenstein_hu_pk(np.maximum(k_h, 1e-6))
    return pk_h / h ** 3  # (Mpc/h)^3 -> Mpc^3


def sigma_v_los(z):
    """spec-z 典型红移不确定度对应的视线速度宽度，km/s（参考值）。"""
    return 30.0 + 0.0 * np.asarray(z, dtype=float)
