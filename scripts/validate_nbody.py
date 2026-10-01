"""N-body PM 求解器正式验证：64³ 粒子、400 Mpc 盒子、z=19→0。

产出：demo_out/nbody_validation.png（测量 P(k) vs 线性理论 + 密度切片）
     demo_out/nbody_validation.json（数值指标）
"""
import json
import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from common.cosmology import growth_factor, pk_mpc
from compute.algorithms.nbody import (measure_power_spectrum,
                                      run_pm_simulation)

OUT = ROOT / "demo_out"
OUT.mkdir(exist_ok=True)

NGRID, BOX, Z_INIT, NSTEPS = 64, 400.0, 19.0, 30

t0 = time.time()
res = run_pm_simulation(NGRID, BOX, Z_INIT, NSTEPS, seed=7)
wall = time.time() - t0

k, pk_sim = measure_power_spectrum(res["pos"], NGRID, BOX)
a_i = 1.0 / (1.0 + Z_INIT)
d_ratio = growth_factor(1.0) / growth_factor(a_i)
pk_lin0 = pk_mpc(k)

# 线性区一致性
lin = (k < 0.14) & np.isfinite(pk_sim) & (pk_sim > 0)
ratio = pk_sim[lin] / pk_lin0[lin]
metrics = {
    "n_particles": int(NGRID ** 3),
    "box_mpc": BOX,
    "z_init": Z_INIT,
    "n_steps": NSTEPS,
    "wall_time_s": round(wall, 1),
    "linear_bins": int(lin.sum()),
    "pk_sim_over_pk_lin_median": float(np.median(ratio)),
    "pk_sim_over_pk_lin_range": [float(ratio.min()), float(ratio.max())],
    "note": "非线性增长使 z=0 实测功率 >= 线性外推; PM 低分辨率压低小尺度",
}
(OUT / "nbody_validation.json").write_text(
    json.dumps(metrics, ensure_ascii=False, indent=1), encoding="utf-8")

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from compute.algorithms.nbody import cic_deposit

fig, axes = plt.subplots(1, 2, figsize=(13, 5.5))

ax = axes[0]
ok = np.isfinite(pk_sim) & (pk_sim > 0)
ax.loglog(k[ok], pk_sim[ok], "o-", label="PM sim (z=0, measured)")
ax.loglog(k, pk_lin0, "--", label="Linear theory (z=0)")
ax.loglog(k, pk_lin0 / d_ratio ** 2, ":", label=f"Linear (z={Z_INIT}, IC)")
ax.set_xlabel("k [1/Mpc]")
ax.set_ylabel("P(k) [Mpc^3]")
ax.set_title(f"Power spectrum: {NGRID}^3 particles, {BOX:.0f} Mpc box")
ax.legend()
ax.grid(alpha=0.3)

ax = axes[1]
rho = cic_deposit(res["pos"], res["mass"], NGRID, BOX)
sl = np.log10(rho[:, :, NGRID // 2] * (BOX / NGRID) ** 3 + 1e-2)
im = ax.imshow(sl.T, origin="lower", cmap="viridis",
               extent=[0, BOX, 0, BOX])
ax.set_xlabel("x [Mpc]")
ax.set_ylabel("y [Mpc]")
ax.set_title("Density slice at z=0 (log10 mass)")
fig.colorbar(im, ax=ax)
fig.tight_layout()
fig.savefig(OUT / "nbody_validation.png", dpi=140)
print(json.dumps(metrics, ensure_ascii=False, indent=1))
print(f"saved: {OUT / 'nbody_validation.png'}")
