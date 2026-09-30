"""算力规模严谨测算脚本。
按计算组分解任务，用实测/文献常数计算 FLOPs、存储、网络需求。
输出 JSON 供报告引用。
"""
import json
import numpy as np

# ============ 输入参数（来自调研） ============
SURVEY_OBJECTS = {
    # 数据库: (天体数, 主要数据产品大小TB, 目录大小TB)
    "gaia_dr4":  (2.8e9,  500, 0.6),   # DR4 ~500TB, gaia_source 586GB
    "desi_dr1":  (1.87e7, 270, 0.05),  # DR1 270TB
    "sdss":      (1.0e9,  652, 0.2),   # 累计652TB
    "euclid":    (1.5e9,  100e3, 2.0), # 任务期100PB, Q1 35TB
    "mast":      (2.0e8,  4000, 1.0),  # 4PB+
    "tess":      (5.0e7,  251, 0.1),   # FFI 251TB+
    "ligo":      (390,    1,   0.001), # 事件目录
    "lsst":      (2.0e10, 30e3, 10.0), # 10年30PB (未来)
}

# ============ 每对象计算成本 (FLOPs) ============
# 依据: 单次坐标转换~50 FLOPs (三角+开方); CIC分配~100 FLOPs;
# KDTree查询 O(logN)~20次×10 FLOPs; FFT网格 M^3 log2(M^3)
COST = {
    "coords3d_redshift": 50,       # 红移积分+投影, 每对象
    "coords3d_parallax": 20,       # 简单倒数
    "density_cic":       100,      # 8邻域插值
    "twopcf":            None,     # O(N logN) 依赖样本量
    "fof":               None,     # O(N logN)
    "kaiser_squires":    None,     # FFT
    "lightcurve_bls":    None,     # 光变曲线点数依赖
}

def flops_coords3d(n_obj):        return n_obj * 50
def flops_density(n_obj):         return n_obj * 100
def flops_kdtree_op(n_obj, ops=20):
    return n_obj * np.log2(max(n_obj,2)) * ops
def flops_fft(ngrid):
    m = ngrid**3
    return 5 * m * np.log2(m)  # 标准复数FFT常数
def flops_bls(n_points, n_periods=500, n_bins=50):
    return n_periods * n_points * 10  # 每周期分箱+卡方

# ============ 任务分解 ============
TILES = 648  # 36 ra_bins x 18 dec_bins

est = {}

# ---- CG1 银河系近场 (Gaia为主) ----
n_gaia = SURVEY_OBJECTS["gaia_dr4"][0]
est["CG1"] = {
    "desc": "银河系近场结构 (Gaia DR4 + 2MASS + WISE)",
    "n_objects": n_gaia,
    "per_tile": TILES,
    "SG1.1_coords": flops_coords3d(n_gaia),              # 2.8e9 * 50
    "SG1.2_density": flops_density(n_gaia),              # 2.8e9 * 100
    "SG1.3_stream": flops_kdtree_op(n_gaia),             # 邻域搜索
}
est["CG1"]["total_flops"] = sum(v for k,v in est["CG1"].items() if isinstance(v,(int,float)) and k!="per_tile")

# ---- CG2 河外大尺度结构 (DESI+SDSS+Euclid) ----
n_gal = SURVEY_OBJECTS["desi_dr1"][0] + SURVEY_OBJECTS["sdss"][0]*0.1 + SURVEY_OBJECTS["euclid"][0]
est["CG2"] = {
    "desc": "河外大尺度结构 (DESI+SDSS+Euclid)",
    "n_objects": n_gal,
    "SG2.1_coords": flops_coords3d(n_gal),
    "SG2.2_density": flops_density(n_gal),
    "SG2.3_twopcf": flops_kdtree_op(n_gal, 30),
    "SG2.4_fof": flops_kdtree_op(n_gal, 25),
}
est["CG2"]["total_flops"] = sum(v for k,v in est["CG2"].items() if isinstance(v,(int,float)))

# ---- CG3 弱引力透镜 (Euclid形状目录) ----
n_shape = SURVEY_OBJECTS["euclid"][0]
est["CG3"] = {
    "desc": "弱引力透镜质量图 (Euclid形状目录)",
    "n_objects": n_shape,
    "SG3.1_catalog": flops_coords3d(n_shape),
    "SG3.2_ks": flops_fft(512),  # 512x512 天空网格 (2D)
}
est["CG3"]["total_flops"] = sum(v for k,v in est["CG3"].items() if isinstance(v,(int,float)))

# ---- CG4 瞬变与多信使 ----
n_lc = SURVEY_OBJECTS["tess"][0]
est["CG4"] = {
    "desc": "瞬变与多信使 (TESS光变曲线 + LIGO事件)",
    "n_objects": n_lc,
    "SG4.1_bls": flops_bls(n_lc * 2000),  # 每星2000个光子点
    "SG4.2_gw": SURVEY_OBJECTS["ligo"][0] * flops_kdtree_op(1000, 20),
}
est["CG4"]["total_flops"] = sum(v for k,v in est["CG4"].items() if isinstance(v,(int,float)))

# ---- CG5 模拟对比 (Millennium/TNG) ----
n_sim = 1e10  # Millennium 2160^3 ≈ 1e10
est["CG5"] = {
    "desc": "模拟对比与半解析验证 (Millennium/TNG目录)",
    "n_objects": n_sim,
    "SG5.1_hod": flops_coords3d(n_sim),
    "SG5.2_crossval": flops_kdtree_op(min(n_sim, 1e9), 20),
}
est["CG5"]["total_flops"] = sum(v for k,v in est["CG5"].items() if isinstance(v,(int,float)))

# ============ 汇总 ============
total_flops = sum(v["total_flops"] for v in est.values())
est["_TOTAL"] = {
    "total_flops": total_flops,
    "total_petaflops": total_flops / 1e15,
    "total_exaflops": total_flops / 1e18,
}

# ============ 时间估算 ============
# 假设单节点双精度 ~50 GFLOPS (现代多核CPU); HTCondor池100节点
NODE_GFLOPS = 50
N_NODES = 100
pool_flops = NODE_GFLOPS * 1e9 * N_NODES  # 5e12 FLOPS = 5 TFLOPS
est["_TIME"] = {
    "single_node_hours": total_flops / (NODE_GFLOPS*1e9) / 3600,
    "pool_100node_hours": total_flops / pool_flops / 3600,
    "pool_100node_days": total_flops / pool_flops / 86400,
    "pool_1000node_hours": total_flops / (pool_flops*10) / 3600,
}

# ============ 存储估算 ============
storage_tb = sum(v[1] for v in SURVEY_OBJECTS.values())  # 原始数据
catalog_tb = sum(v[2] for v in SURVEY_OBJECTS.values())   # 目录
est["_STORAGE"] = {
    "raw_data_tb": storage_tb,
    "catalog_tb": catalog_tb,
    "intermediate_tb": catalog_tb * 0.5,   # 中间结果约为目录一半
    "total_recommended_tb": catalog_tb * 2 + catalog_tb * 0.5,  # 目录+中间+备份
}

# ============ 网络 ============
# 每任务输入 ~10MB 目录切片, 输出 ~1MB HDF5
est["_NETWORK"] = {
    "per_task_input_mb": 10,
    "per_task_output_mb": 1,
    "total_input_tb": TILES * 5 * 10 / 1e6,   # 5组 x 648 tiles x 10MB
    "total_output_tb": TILES * 5 * 1 / 1e6,
}

# ============ 硬件规模 ============
est["_HARDWARE"] = {
    "min": {
        "nodes": 10,
        "cpu_cores_per_node": 32,
        "ram_gb_per_node": 128,
        "storage_tb": 5,
        "network_gbps": 10,
        "note": "最小可行配置: 仅处理DESI+SDSS目录级数据 (河外大尺度结构)",
    },
    "recommended": {
        "nodes": 100,
        "cpu_cores_per_node": 64,
        "ram_gb_per_node": 256,
        "storage_tb": 20,
        "network_gbps": 25,
        "note": "推荐配置: 全量巡天目录 + 模拟对比, ~3个月完成全部计算",
    },
    "ambitious": {
        "nodes": 1000,
        "cpu_cores_per_node": 64,
        "ram_gb_per_node": 512,
        "gpu_per_node": 2,
        "storage_pb": 1,
        "network_gbps": 100,
        "note": "雄心配置: 含LSST未来数据 + 实时处理, ~2周完成",
    },
}

# 输出
out = json.dumps(est, ensure_ascii=False, indent=2, default=str)
print(out)
with open(r"d:\N体仿真开发\universe\docs\flops_estimate.json", "w", encoding="utf-8") as f:
    f.write(out)
print(f"\n已保存到 docs/flops_estimate.json")
