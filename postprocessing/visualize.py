"""科学可视化：从全局产品生成图像。
- mollweide 全天天体分布投影
- 三维密度场切片图
- 功率谱与两点相关函数曲线
- 弱透镜质量图
"""
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import h5py


def _cjk():
    from matplotlib import font_manager
    names = {f.name for f in font_manager.fontManager.ttflist}
    for n in ["Microsoft YaHei", "SimHei", "SimSun", "Noto Sans CJK SC"]:
        if n in names:
            plt.rcParams["font.sans-serif"] = [n]
            break
    plt.rcParams["axes.unicode_minus"] = False


def plot_sky_mollweide(xyz_or_radec, out_png, title="天体全天分布 (Mollweide投影)"):
    _cjk()
    arr = np.asarray(xyz_or_radec, dtype=float)
    if arr.shape[1] == 3:  # xyz -> ra/dec
        r = np.linalg.norm(arr, axis=1)
        dec = np.arcsin(np.clip(arr[:, 2] / np.maximum(r, 1e-12), -1, 1))
        ra = np.arctan2(arr[:, 1], arr[:, 0])
    else:
        ra, dec = np.deg2rad(arr[:, 0]), np.deg2rad(arr[:, 1])
        ra = np.where(ra > np.pi, ra - 2 * np.pi, ra)
    fig = plt.figure(figsize=(11, 5.5))
    ax = fig.add_subplot(111, projection="mollweide")
    ax.scatter(ra, dec, s=0.3, c=np.linalg.norm(arr, axis=1) if arr.shape[1] == 3 else "steelblue",
               cmap="viridis", alpha=0.6)
    ax.set_title(title)
    ax.grid(True, alpha=0.3)
    fig.tight_layout()
    fig.savefig(out_png, dpi=140)
    plt.close(fig)
    return str(out_png)


def plot_density_slices(delta, out_png, title="三维密度场切片 (过密度 δ)"):
    _cjk()
    n = delta.shape[0]
    fig, axes = plt.subplots(1, 3, figsize=(15, 4.6))
    vm = np.nanpercentile(np.abs(delta), 98)
    for ax, (axis, lab) in zip(axes, [(2, "XY"), (1, "XZ"), (0, "YZ")]):
        sl = delta.mean(axis=axis)
        im = ax.imshow(sl.T, origin="lower", cmap="RdBu_r", vmin=-vm, vmax=vm)
        ax.set_title(f"{lab} 投影均值")
        fig.colorbar(im, ax=ax, shrink=0.8)
    fig.suptitle(title)
    fig.tight_layout()
    fig.savefig(out_png, dpi=140)
    plt.close(fig)
    return str(out_png)


def plot_power_spectrum(k, pk, out_png, title="三维功率谱 P(k)"):
    _cjk()
    fig, ax = plt.subplots(figsize=(7, 5))
    m = np.isfinite(pk) & (pk > 0)
    ax.loglog(k[m], pk[m], "o-", color="darkred")
    ax.set_xlabel("k [h/Mpc 量级, 任意归一]")
    ax.set_ylabel("P(k)")
    ax.set_title(title)
    ax.grid(True, which="both", alpha=0.3)
    fig.tight_layout()
    fig.savefig(out_png, dpi=140)
    plt.close(fig)
    return str(out_png)


def plot_xi(r, xi, out_png, title="两点相关函数 ξ(r)"):
    _cjk()
    fig, ax = plt.subplots(figsize=(7, 5))
    m = np.isfinite(xi)
    ax.plot(r[m], r[m] ** 2 * xi[m], "s-", color="navy")
    ax.set_xscale("log")
    ax.set_xlabel("r [Mpc]")
    ax.set_ylabel(r"$r^2 \xi(r)$")
    ax.set_title(title)
    ax.grid(True, alpha=0.3)
    fig.tight_layout()
    fig.savefig(out_png, dpi=140)
    plt.close(fig)
    return str(out_png)


def plot_mass_map(kappa, out_png, title="弱透镜收敛场 κ (Kaiser-Squires)"):
    _cjk()
    fig, ax = plt.subplots(figsize=(7, 6))
    vm = np.nanpercentile(np.abs(kappa), 98)
    im = ax.imshow(kappa.T, origin="lower", cmap="inferno", vmin=-vm, vmax=vm)
    fig.colorbar(im, ax=ax, label="κ")
    ax.set_title(title)
    fig.tight_layout()
    fig.savefig(out_png, dpi=140)
    plt.close(fig)
    return str(out_png)


def make_all_plots(global_h5, out_dir):
    from pathlib import Path
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    made = {}
    with h5py.File(global_h5, "r") as h:
        xyz = h["xyz"][:]
        if len(xyz) > 200000:
            idx = np.random.default_rng(0).choice(len(xyz), 200000, replace=False)
            xyz_plot = xyz[idx]
        else:
            xyz_plot = xyz
        made["sky"] = plot_sky_mollweide(xyz_plot, out / "sky_mollweide.png")
        made["slices"] = plot_density_slices(h["delta"][:], out / "density_slices.png")
        made["pk"] = plot_power_spectrum(h["pk_k"][:], h["pk"][:], out / "power_spectrum.png")
    return made
