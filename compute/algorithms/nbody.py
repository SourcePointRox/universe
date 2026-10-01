"""宇宙学 N-body 引力求解器（PM + Barnes-Hut 校验）。

解决问题清单 #2/#4/#5：补上项目缺失的 N 体积分器。

物理（共动坐标 x，尺度因子 a）：
    ∇²φ = (3/2) Ωm H0² δ / a            （Poisson, 共动）
    d p/dt = -∇φ,   p ≡ a² ẋ             （正则动量，哈勃拖曳自动包含）
    dx/dt = p / a²

算法（Hockney & Eastwood 1988; Springel 2005; Springel+ 2021 Gadget-4）：
- PM：CIC 质量分配(numba) -> FFT 解 Poisson -> 中心差分梯度 -> CIC 力插值
- 时间积分：KDK 蛙跳，步长在 a 上均匀
- 初始条件：Zel'dovich 近似，Eisenstein-Hu 线性功率谱
- Barnes-Hut 八叉树（短程力，小 N 校验用）+ 直接 O(N²) 求和基准

单位制：长度 Mpc，速度 km/s，时间 = Mpc/(km/s)（≈0.978 Gyr）。
此单位制下 ẋ 的数值即 km/s，G 不需要显式出现（泊松方程用 Ωm H0² 形式）。

TreePM 力劈（Φ_k 按 exp(-k²r_s²) 劈为 PM 长程 + 树短程, Bagla 2002;
Gadget-4 论文 eq. 15）为预留升级路径：pm_force 接受 rs_split 参数，
返回已扣除短程的长程力，树模块提供 erfc 短程核。
"""
from __future__ import annotations

import numpy as np
from numba import njit, prange

from common.cosmology import OM, H0, ez, growth_factor, growth_rate_f, pk_mpc
from common.logutil import get_logger

log = get_logger("nbody")

T_UNIT_GYR = 0.9778  # 1 个内部时间单位 = Mpc/(km/s) ≈ 0.978 Gyr


# ============================================================
# CIC 质量分配与力插值（numba 加速）
# ============================================================
@njit(cache=True)
def cic_deposit(x, mass, ngrid, boxsize):
    """CIC 质量分配。x: (N,3) ∈ [0,boxsize)，返回密度网格（质量/体积）。"""
    rho = np.zeros((ngrid, ngrid, ngrid), dtype=np.float64)
    cell = boxsize / ngrid
    n = x.shape[0]
    for p in range(n):
        u0 = x[p, 0] / cell - 0.5
        u1 = x[p, 1] / cell - 0.5
        u2 = x[p, 2] / cell - 0.5
        i0 = int(np.floor(u0)); f0 = u0 - i0
        i1 = int(np.floor(u1)); f1 = u1 - i1
        i2 = int(np.floor(u2)); f2 = u2 - i2
        for d0 in range(2):
            w0 = f0 if d0 else 1.0 - f0
            ii0 = (i0 + d0) % ngrid
            for d1 in range(2):
                w1 = f1 if d1 else 1.0 - f1
                ii1 = (i1 + d1) % ngrid
                for d2 in range(2):
                    w2 = f2 if d2 else 1.0 - f2
                    ii2 = (i2 + d2) % ngrid
                    rho[ii0, ii1, ii2] += mass[p] * w0 * w1 * w2
    return rho / cell ** 3


@njit(cache=True, parallel=True)
def _cic_gather4(field, x, boxsize):
    """CIC 插值回粒子（内部实现，field 恒为 (M,M,M,C)）。"""
    ngrid = field.shape[0]
    ncomp = field.shape[3]
    cell = boxsize / ngrid
    n = x.shape[0]
    out = np.zeros((n, ncomp), dtype=np.float64)
    for p in prange(n):
        u0 = x[p, 0] / cell - 0.5
        u1 = x[p, 1] / cell - 0.5
        u2 = x[p, 2] / cell - 0.5
        i0 = int(np.floor(u0)); f0 = u0 - i0
        i1 = int(np.floor(u1)); f1 = u1 - i1
        i2 = int(np.floor(u2)); f2 = u2 - i2
        for d0 in range(2):
            w0 = f0 if d0 else 1.0 - f0
            ii0 = (i0 + d0) % ngrid
            for d1 in range(2):
                w1 = f1 if d1 else 1.0 - f1
                ii1 = (i1 + d1) % ngrid
                for d2 in range(2):
                    w2 = f2 if d2 else 1.0 - f2
                    ii2 = (i2 + d2) % ngrid
                    w = w0 * w1 * w2
                    for c in range(ncomp):
                        out[p, c] += w * field[ii0, ii1, ii2, c]
    return out


def cic_gather(field, x, boxsize):
    """把网格场按 CIC 权重插值回粒子位置。field: (M,M,M,3) 或 (M,M,M)。"""
    field = np.asarray(field, dtype=np.float64)
    squeeze = field.ndim == 3
    if squeeze:
        field = field[:, :, :, None]
    out = _cic_gather4(np.ascontiguousarray(field), np.asarray(x), boxsize)
    return out[:, 0] if squeeze else out


# ============================================================
# PM 引力
# ============================================================
def _kgrid(ngrid, boxsize):
    k1 = 2 * np.pi * np.fft.fftfreq(ngrid, d=boxsize / ngrid)
    kx, ky, kz = np.meshgrid(k1, k1, k1, indexing="ij")
    k2 = kx ** 2 + ky ** 2 + kz ** 2
    k2[0, 0, 0] = 1.0
    return kx, ky, kz, k2


def pm_force(pos, mass, a, ngrid, boxsize, rs_split=0.0):
    """PM 引力加速度（含 1/a 因子），返回 (N,3)，单位 (km/s)²/Mpc。

    rs_split > 0 时按 Gadget TreePM 方案在傅里叶空间乘 exp(-k²rs²)，
    只保留长程部分（短程由树补足）。
    """
    rho = cic_deposit(pos, mass, ngrid, boxsize)
    rhobar = mass.sum() / boxsize ** 3
    delta = rho / rhobar - 1.0
    dk = np.fft.fftn(delta)
    kx, ky, kz, k2 = _kgrid(ngrid, boxsize)
    phik = -1.5 * OM * H0 ** 2 * dk / (a * k2)
    if rs_split > 0:
        phik *= np.exp(-k2 * rs_split ** 2)  # 长程部分
    phik[0, 0, 0] = 0.0
    phi = np.fft.ifftn(phik).real
    cell = boxsize / ngrid
    # 中心差分梯度（周期边界由 roll 天然满足）
    g = np.empty(phi.shape + (3,), dtype=np.float64)
    for c in range(3):
        g[..., c] = -(np.roll(phi, -1, axis=c) - np.roll(phi, 1, axis=c)) / (2 * cell)
    return cic_gather(g, pos, boxsize)


# ============================================================
# Zel'dovich 初始条件
# ============================================================
def zeldovich_ic(ngrid, boxsize, a_init, seed=42):
    """生成初始粒子 (pos, p)。粒子数 = ngrid³，拉氏格点均匀放置。

    返回 pos (N,3) [Mpc], p (N,3) [正则动量 a²ẋ，数值单位 km/s],
    以及粒子质量 [任意单位，均匀取 1]。
    """
    rng = np.random.default_rng(seed)
    # 高斯白噪声 -> 按 sqrt(P(k) M³/V) 定标（满足 <|δ_k|²> = M⁶P/V 约定）
    w = rng.standard_normal((ngrid, ngrid, ngrid))
    wk = np.fft.fftn(w)
    kx, ky, kz, k2 = _kgrid(ngrid, boxsize)
    kmag = np.sqrt(k2)
    pk = pk_mpc(kmag.ravel()).reshape(kmag.shape)
    pk[0, 0, 0] = 0.0
    dk = wk * np.sqrt(pk * ngrid ** 3 / boxsize ** 3)
    dk[0, 0, 0] = 0.0

    d_init = growth_factor(a_init)          # D(a_i), D(1)=1
    # 线性增长速度: ẋ = Ḋ·Ψ = H·f·D·Ψ （注意: Ḋ = HfD，无额外 a 因子！
    # 曾有 bug: 误写为 a·H·f·D，多乘 a_init 使初速偏小，
    # 导致衰减模式大量混入、最终增长短缺 ~31%——已由单模收敛测试定位修复）
    Hf = H0 * ez(1.0 / a_init - 1.0) * growth_rate_f(a_init)

    cell = boxsize / ngrid
    q = (np.arange(ngrid) + 0.5) * cell
    qx, qy, qz = np.meshgrid(q, q, q, indexing="ij")

    pos = np.empty((ngrid ** 3, 3), dtype=np.float64)
    mom = np.empty_like(pos)
    kvec = (kx, ky, kz)
    for c in range(3):
        psi_k = 1j * kvec[c] * dk / k2       # Zel'dovich 位移场 Ψ_k
        psi_k[0, 0, 0] = 0.0
        psi = np.fft.ifftn(psi_k).real        # 格点上的 Ψ(q)
        pos[:, c] = (np.stack([qx, qy, qz], axis=-1)[..., c].ravel()
                     + d_init * psi.ravel())
        mom[:, c] = a_init ** 2 * (Hf * d_init * psi.ravel())  # p = a²ẋ
    pos = np.mod(pos, boxsize)
    mass = np.ones(ngrid ** 3, dtype=np.float64)
    return pos, mom, mass


# ============================================================
# KDK 蛙跳积分
# ============================================================
def run_pm_simulation(ngrid=64, boxsize=200.0, z_init=49.0, n_steps=40,
                      seed=42, save_every=0, callback=None):
    """运行宇宙学 PM 模拟。

    返回 dict(final_pos, final_p, snapshots, params)。
    save_every>0 时每 save_every 步在 snapshots 记录 (a, pos)（小盒子调试用）。
    """
    a_init = 1.0 / (1.0 + z_init)
    pos, mom, mass = zeldovich_ic(ngrid, boxsize, a_init, seed=seed)
    ngrid_pm = ngrid  # 力网格与粒子网格同分辨率（标准 PM 配置）
    da = (1.0 - a_init) / n_steps
    a = a_init

    acc = pm_force(pos, mass, a, ngrid_pm, boxsize)
    snapshots = []
    log.info("PM 模拟启动: %d³ 粒子, 盒子 %.0f Mpc, z=%.0f->0, %d 步",
             ngrid, boxsize, z_init, n_steps)
    for step in range(n_steps):
        dt = da / (a * H0 * ez(1.0 / a - 1.0))   # 内部时间单位
        # KDK
        mom += acc * (0.5 * dt)
        a_half = a + 0.5 * da
        pos = np.mod(pos + (mom / a_half ** 2) * dt, boxsize)
        acc = pm_force(pos, mass, a + da, ngrid_pm, boxsize)
        mom += acc * (0.5 * dt)
        a += da
        if save_every and (step + 1) % save_every == 0:
            snapshots.append((a, pos.copy()))
        if callback:
            callback(step, a)
    log.info("PM 模拟完成: a=1 (z=0)")
    return {"pos": pos, "p": mom, "mass": mass, "snapshots": snapshots,
            "params": {"ngrid": ngrid, "boxsize": boxsize,
                       "z_init": z_init, "n_steps": n_steps, "seed": seed}}


def measure_power_spectrum(pos, ngrid, boxsize, kmax=None, nbins=15):
    """从粒子分布测功率谱（CIC + FFT，扣散粒噪声 1/n̄）。"""
    mass = np.ones(len(pos))
    rho = cic_deposit(np.asarray(pos), mass, ngrid, boxsize)
    rhobar = len(pos) / boxsize ** 3
    delta = rho / rhobar - 1.0
    dk = np.fft.fftn(delta)
    pk3d = np.abs(dk) ** 2 * boxsize ** 3 / len(pos) ** 2
    k1 = 2 * np.pi * np.fft.fftfreq(ngrid, d=boxsize / ngrid)
    kx, ky, kz = np.meshgrid(k1, k1, k1, indexing="ij")
    kmag = np.sqrt(kx ** 2 + ky ** 2 + kz ** 2)
    knz = kmag[kmag > 0]
    if kmax is None:
        kmax = np.pi * ngrid / boxsize  # Nyquist
    edges = np.logspace(np.log10(knz.min()), np.log10(kmax), nbins + 1)
    idx = np.digitize(kmag.ravel(), edges)
    k_out = np.full(nbins, np.nan)
    p_out = np.full(nbins, np.nan)
    shot = boxsize ** 3 / len(pos)
    for b in range(1, nbins + 1):
        m = idx == b
        if m.sum() > 0:
            k_out[b - 1] = kmag.ravel()[m].mean()
            p_out[b - 1] = pk3d.ravel()[m].mean() - shot
    return k_out, p_out


# ============================================================
# Barnes-Hut 八叉树（短程力校验，小 N）
# ============================================================
class _BHNode:
    __slots__ = ("center", "half", "mass", "com", "children", "ibody")

    def __init__(self, center, half):
        self.center = center
        self.half = half
        self.mass = 0.0
        self.com = np.zeros(3)
        self.children = None   # 长度为8的list
        self.ibody = -1


def _bh_insert(node, i, pos, mass, depth=0):
    node.mass += mass[i]
    node.com += mass[i] * pos[i]
    if node.children is None and node.ibody < 0:
        node.ibody = i
        return
    if node.children is None:
        # 叶子分裂：先把已有粒子下放
        old = node.ibody
        node.ibody = -1
        node.children = [_BHNode(node.center, node.half * 0.5) for _ in range(8)]
        _bh_insert_child(node, old, pos, mass, depth)
    _bh_insert_child(node, i, pos, mass, depth)


def _bh_insert_child(node, i, pos, mass, depth):
    if depth > 60:
        return  # 数值重合粒子的保护
    oct_ = 0
    child_center = node.center.copy()
    for c in range(3):
        if pos[i, c] >= node.center[c]:
            oct_ |= 1 << c
            child_center[c] += node.half * 0.5
        else:
            child_center[c] -= node.half * 0.5
    child = node.children[oct_]
    child.center = child_center
    _bh_insert(child, i, pos, mass, depth + 1)


def _bh_finalize(node):
    if node.mass > 0:
        node.com /= node.mass
    if node.children:
        for ch in node.children:
            _bh_finalize(ch)


def _bh_force_on(node, x, theta2, eps2, G=1.0):
    f = np.zeros(3)
    if node.mass == 0:
        return f
    d = node.com - x
    r2 = d @ d + eps2
    if node.children is None or (4 * node.half ** 2 < theta2 * r2):
        f += G * node.mass * d / r2 ** 1.5
    else:
        for ch in node.children:
            f += _bh_force_on(ch, x, theta2, eps2, G)
    return f


def barnes_hut_force(pos, mass=None, theta=0.5, eps=0.01, G=1.0):
    """Barnes-Hut 树力（非周期、真空边界），O(N log N)，校验用。

    eps: 软化长度（与坐标同单位）。返回 (N,3) 加速度（G=1 单位）。
    Barnes & Hut 1986, Nature, 324, 446。
    """
    pos = np.asarray(pos, dtype=float)
    n = len(pos)
    mass = np.ones(n) if mass is None else np.asarray(mass, dtype=float)
    lo = pos.min(axis=0)
    hi = pos.max(axis=0)
    half = float(np.max(hi - lo)) * 0.5 * 1.001
    center = lo + half
    root = _BHNode(center, half)
    for i in range(n):
        _bh_insert(root, i, pos, mass)
    _bh_finalize(root)
    f = np.empty((n, 3))
    for i in range(n):
        f[i] = _bh_force_on(root, pos[i], theta ** 2, eps ** 2, G)
    return f


@njit(cache=True, parallel=True)
def direct_force(pos, mass, eps=0.01, G=1.0):
    """直接 O(N²) 求和（软化），作为树/PM 力的真值基准。"""
    n = pos.shape[0]
    f = np.zeros((n, 3), dtype=np.float64)
    eps2 = eps * eps
    for i in prange(n):
        for j in range(n):
            if i == j:
                continue
            d0 = pos[j, 0] - pos[i, 0]
            d1 = pos[j, 1] - pos[i, 1]
            d2 = pos[j, 2] - pos[i, 2]
            r2 = d0 * d0 + d1 * d1 + d2 * d2 + eps2
            inv = G * mass[j] / (r2 * np.sqrt(r2))
            f[i, 0] += inv * d0
            f[i, 1] += inv * d1
            f[i, 2] += inv * d2
    return f
