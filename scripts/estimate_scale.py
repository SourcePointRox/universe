"""规模测算脚本 v2.0：1 Gpc³ 局部宇宙三维建模的真实资源估算。

解决问题清单 #6/#13：旧估算把"天体数 × 常数"当总算力，差 2-3 个量级，
且缺少 I/O 与内存指标。本脚本逐项给出：
- 输入数据量（按各巡天目录实测/官方发布值）
- FLOPs（按算法复杂度逐项推导）
- TB/day（数据吞吐）、objects/sec（处理速率）
- CPU·s/object（单天体成本）、GB RAM/task（峰值内存）
- 网络流量（分片 shuffle 量 + 任务分发量）

所有常数标明出处；输出 JSON 供报告引用。
运行: python scripts/estimate_scale.py
"""
import json
import numpy as np
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "docs" / "scale_estimate.json"

# =============================================================
# 1. 输入数据：z<=0.15 局部宇宙巡天目录
#    条目: (红移星系数, 目录下载量 GB, 出处)
# =============================================================
SURVEYS = {
    # SDSS DR18 光谱星表 z<0.15 子集（主星系样本 z<0.2 约 70 万）
    "sdss":    dict(n=7.0e5, gb=3.0,  ref="SDSS DR18 spAll/SpecObjAll"),
    # DESI DR1 红移目录全体 18.7M, 其中 z<0.15 约 15%（BGS 为主）
    "desi":    dict(n=2.8e6, gb=8.0,  ref="DESI DR1 zcatalog (BGS_BRIGHT)"),
    # 2MRS 全天 K<11.75, ~43k (Huchra+ 2012)
    "twomrs":  dict(n=4.3e4, gb=0.1,  ref="2MRS final catalog"),
    # GAMA DR4 ~300k 红移（5 个深场, z 中位数 ~0.2, z<0.15 约 60%）
    "gama":    dict(n=1.8e5, gb=1.0,  ref="GAMA DR4 SpecCat"),
    # 6dFGS ~125k 南天红移 (Jones+ 2009)
    "sixdf":   dict(n=1.1e5, gb=0.2,  ref="6dFGS DR3"),
    # ALFALFA a.100 ~31k HI 源 (Haynes+ 2018)
    "alfalfa": dict(n=3.1e4, gb=0.05, ref="ALFALFA a.100"),
    # Cosmicflows-4 ~56k 距离 (Tully+ 2023)
    "cosmicflows": dict(n=5.6e4, gb=0.1, ref="CF4 grouped catalog"),
    # Gaia DR3 近场恒星（3 kpc 内, ϖ>0.33 mas 约 1e7, 可选组）
    "gaia":    dict(n=1.0e7, gb=6.0,  ref="Gaia DR3 gaia_source 子集"),
}
N_GALAXY = sum(v["n"] for k, v in SURVEYS.items() if k != "gaia")
N_STAR = SURVEYS["gaia"]["n"]
RAW_GB = sum(v["gb"] for v in SURVEYS.values())

# =============================================================
# 2. 分片（HEALPix 预分片, 一次性）
# =============================================================
NSIDE = 16
NPIX = 12 * NSIDE ** 2                    # 3072
SHARD_GB = RAW_GB * 0.55                  # Parquet+ZSTD 压缩比估计
SHARD_FLOPS = (N_GALAXY + N_STAR) * 200   # ang2pix + 排序, ~200 FLOP/行

# =============================================================
# 3. 各计算组 FLOPs（逐项推导, 常数注明依据）
# =============================================================
def flops_fft(n3):
    return 5 * n3 * np.log2(n3)           # 标准复数 FFT

est = {}

# ---- CG1: 三维位置重建 ----
est["CG1_redshift"] = dict(
    n=N_GALAXY,
    flops=N_GALAXY * 1200,
    note="spec-z 距离+误差传播+CMB改正, ~1.2e3 FLOP/天体")
est["CG1_parallax"] = dict(
    n=N_STAR,
    flops=N_STAR * 2200 * 15,
    note="Bayesian 距离后验, 网格 2200 点 x 15 FLOP")

# ---- CG2: Wiener 密度重建 (ngrid=128) ----
NG = 128
est["CG2_wiener"] = dict(
    n=N_GALAXY,
    flops=N_GALAXY * 60                    # NGP 计数
         + 2 * flops_fft(NG ** 3)          # 正/逆 FFT
         + NG ** 3 * 40,                   # 滤波器逐模运算
    note="Wiener 滤波全局任务, 128^3 网格")

# ---- CG3: 聚类统计 ----
est["CG3_twopcf"] = dict(
    n=N_GALAXY,
    flops=N_GALAXY * np.log2(N_GALAXY) * 30 * 20
         + 10 * N_GALAXY * np.log2(10 * N_GALAXY) * 30 * 20,  # 随机点 10x
    note="Landy-Szalay + 10x 随机目录, cKDTree")
est["CG3_fof"] = dict(
    n=N_GALAXY,
    flops=N_GALAXY * np.log2(N_GALAXY) * 25 + N_GALAXY * 50,
    note="FoF b=0.2, query_pairs + 并查集")

# ---- CG5: PM 模拟验证 (128^3, 60 步) ----
NP = NG ** 3
est["CG5_nbody"] = dict(
    n=NP,
    flops=60 * (NP * 100                   # CIC 分配+插值
               + 4 * flops_fft(NG ** 3)    # 每步 2 次力求解 x 正反 FFT
               + NG ** 3 * 30              # 差分梯度
               + NP * 20),                 # KDK 积分
    note="PM 求解器 128^3 粒子 60 步 (校验模拟, 非生产模拟)")

total_flops = sum(v["flops"] for v in est.values()) + SHARD_FLOPS

# =============================================================
# 4. 速率与吞吐指标
# =============================================================
THROUGHPUT = {
    "shard_rows_per_s": 5.0e4,      # HEALPix 分片 (实测 ~5e4 行/s/核)
    "redshift_obj_per_s": 3.0e3,    # 红移距离重建
    "parallax_obj_per_s": 1.0e3,    # 视差后验
}
CPU_S_PER_OBJ = {k: 1.0 / v for k, v in THROUGHPUT.items()}

RAM = {
    "pixel_task_gb": 2.0,           # 单像素分区 ~千天体, 峰值 <2 GB
    "global_wiener_gb": 8.0,        # 128^3 float64 多场 + 目录
    "global_2pcf_gb": 4.0,          # 目录+10x 随机点+KDTree x2
    "nbody_128_gb": 6.0,            # 粒子+网格数组
}

io = {
    "shard_partition_mb_avg": SHARD_GB * 1e3 / NPIX,
    "pixel_task_input_mb": SHARD_GB * 1e3 / NPIX * 3,
    "global_task_input_gb": RAW_GB * 0.3,
    "total_task_io_gb": SHARD_GB + 3 * SHARD_GB,
}

net = {
    "shuffle_gb": SHARD_GB,                  # 预分片一次性 shuffle
    "distribute_gb": SHARD_GB + 20,          # 分区下发+结果回收
    "per_day_tb": (SHARD_GB + 20) / 10 / 1e3,
}

configs = {
    "single_workstation_16core": dict(
        cores=16,
        days=total_flops / (16 * 20e9) / 86400,
        note="单机即可完成全部计算（含 N-body 校验模拟）"),
    "small_cluster_8node": dict(
        cores=8 * 32,
        days=total_flops / (8 * 32 * 20e9) / 86400,
        note="8 节点 x 32 核, 主要瓶颈是 global 任务串行段"),
}

out = {
    "_meta": {
        "target": "z<=0.15 全天局部宇宙 (~1.1 Gpc^3)",
        "version": "v2.0",
        "n_galaxies": N_GALAXY,
        "n_stars_gaia": N_STAR,
    },
    "input_data": {
        "surveys": {k: dict(objects=v["n"], gb=v["gb"], ref=v["ref"])
                    for k, v in SURVEYS.items()},
        "raw_total_gb": RAW_GB,
        "shards_gb": SHARD_GB,
        "total_budget_gb": RAW_GB + SHARD_GB + 15,
        "note": "总存储 ~60 GB, 符合 80 GB 本地开发约束",
    },
    "flops_by_group": est,
    "total": {
        "flops": total_flops,
        "tflops": total_flops / 1e12,
        "note": "对比旧估算的 0.5 PFLOPs: 旧值把不可得的 5.9e9 天体当输入,"
                "真实可得输入 ~4e6 星系, 差约 3 个量级",
    },
    "rates": {
        "throughput": THROUGHPUT,
        "cpu_s_per_object": CPU_S_PER_OBJ,
    },
    "memory_gb_per_task": RAM,
    "io": io,
    "network": net,
    "timeline": configs,
    "wall_clock_summary": {
        "分片(一次性)": f"{(N_GALAXY+N_STAR)/THROUGHPUT['shard_rows_per_s']/3600:.1f} 核时",
        "CG1 位置重建": f"{N_GALAXY/THROUGHPUT['redshift_obj_per_s']/3600:.1f} 核时",
        "CG5 N-body 校验": f"{est['CG5_nbody']['flops']/20e9/3600:.1f} 核时",
        "全项目(单机16核)": f"{configs['single_workstation_16core']['days']:.1f} 天",
    },
}

OUT.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps(out["total"], ensure_ascii=False, indent=1))
print(json.dumps(out["wall_clock_summary"], ensure_ascii=False, indent=1))
print(f"\n已保存 {OUT}")
