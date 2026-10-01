# Universe 3D Modeling — 局部宇宙（z ≤ 0.15，~1 Gpc³）三维建模平台

基于多巡天观测数据，对 **全天局部宇宙（红移 z ≤ 0.15，共动半径约 650 Mpc，体积 ≈ 1.1×10⁹ Mpc³）**
进行三维重建：星系三维位置（含距离后验不确定度）→ 贝叶斯物质密度场（均值 + 不确定度）→
大尺度结构统计 → N-body 模拟交叉验证。

> v2.0 全面重构。科学目标从"整个可观测宇宙"（不可行）修正为数据真实可得的
> 1 Gpc³ 局部宇宙；架构从"读全量数据筛 tile"改为 HEALPix 预分片；
> 补上真正的 N-body 引力求解器与贝叶斯距离/密度推断。
> 科学依据：[docs/literature_review.md](docs/literature_review.md)（21 篇参考文献）。
> v1 规划 PDF 已存档作废（docs/project_plan_report.pdf）。

## 为什么是 z ≤ 0.15 局部宇宙

| 判据 | 说明 |
|------|------|
| 数据完备性 | SDSS/DESI-BGS/2MRS/GAMA/6dFGS 光谱红移在此体积内近乎完备 |
| 距离精度 | spec-z σ_v≈30 km/s → σ_d≈0.5 Mpc@z=0.05；有 Cosmicflows-4 特殊速度改正 |
| 规模自洽 | 输入 ~4×10⁶ 星系、~20 GB 目录；总算力 ~1 TFLOP（单机可完成），I/O 为真实瓶颈 |
| 可验证性 | 2M++/Cosmicflows-4 已发表的同体积重建场可作交叉基准 |

## 计算组框架

| 计算组 | 目标 | 核心算法 | 输入数据 |
|--------|------|----------|----------|
| CG1 三维位置重建 | 星系/恒星共动坐标 + 距离后验 | spec-z 误差传播 + CMB 偶极改正；Gaia 零点 + Bailer-Jones 后验 | SDSS/DESI/2MRS/GAMA/6dF/Gaia |
| CG2 物质密度场 | 三维密度**后验均值 + 不确定度** | Wiener 滤波（选择函数 n̄(x) + 偏置 b） | CG1 输出 |
| CG3 聚类统计 | 2PCF / 星系团目录 | Landy-Szalay（选择函数随机目录）+ FoF b=0.2 | CG1 输出 |
| CG4 弱透镜质量图 | 投影物质分布 | Kaiser-Squires | Euclid/MAST/DECaLS |
| CG5 N-body 验证 | 同体积模拟交叉校验 | PM 求解器（CIC+FFT+KDK）/ Barnes-Hut 校验 | 模拟初始条件 |

## 项目结构

```
universe/
├── configs/
│   ├── science.yaml            # 科学目标定义（体积/宇宙学/预算）
│   ├── surveys.yaml            # 18 个天文数据库注册表
│   └── compute_groups.yaml     # 5 计算组 × 子组（pixel/global 两级任务）
├── common/
│   ├── healpix.py              # HEALPix 像素化（纯 NumPy，RING 方案）
│   ├── cosmology.py            # Planck18：E(z)/增长因子/EH 功率谱
│   ├── db.py                   # 状态库（WAL + 租约 + 心跳 + 批量事务）
│   ├── checkpoint.py           # 断点续算 + 心跳
│   ├── logutil.py              # 结构化日志
│   └── tiling.py               # 旧柱面 tile（保留兼容）
├── preprocessing/
│   ├── shard.py                # ★ HEALPix 预分片 → Parquet 分区（HATS 式）
│   ├── scanner.py / parsers/   # 原始数据扫描与解析
│   ├── taskgen.py              # 任务生成（pixel/global）+ HTCondor DAG
│   ├── selfcheck.py            # 自检自验（含分片完整性）
│   └── dashboard/              # 前处理仪表盘（含 /metrics）
├── compute/
│   ├── algorithms/
│   │   ├── distance.py         # ★ Bayesian 距离：零点/BJ后验/误差传播/CMB改正
│   │   ├── nbody.py            # ★ PM N-body 求解器 + Zel'dovich IC + Barnes-Hut
│   │   ├── reconstruction.py   # ★ Wiener 贝叶斯密度重建
│   │   └── __init__.py         # CIC/2PCF(选择函数)/FoF(b=0.2)/KS/BLS
│   ├── worker.py               # ★ 计算 worker（分片输入/断点续算/心跳/租约）
│   └── demo/                   # 算法演示
├── postprocessing/
│   ├── collector.py            # 结果回收 + SHA-256 校验
│   ├── reducer.py              # 全局归并
│   ├── visualize.py            # 科学可视化
│   └── dashboard/              # 后处理仪表盘（含 /metrics）
├── deploy/
│   ├── slurm_pixel_array.sbatch  # Slurm 像素任务数组
│   └── slurm_global.sbatch       # Slurm 全局任务
├── Dockerfile / Singularity.def / docker-compose.yml
├── .github/workflows/ci.yml    # CI（单测 + 端到端）
├── scripts/estimate_scale.py   # 全指标规模测算
├── docs/
│   ├── literature_review.md    # 文献调研（算法选型依据）
│   ├── architecture.md         # 架构设计
│   └── scale_estimate.json     # 测算输出
└── tests/                      # 单元测试 + 端到端测试
```

## 快速开始

### 环境准备

```bash
git clone https://github.com/SourcePointRox/universe.git
cd universe
python -m venv .venv && .venv\Scripts\activate   # Windows
# source .venv/bin/activate                      # Linux/macOS
pip install -r requirements.txt
```

### 1. 运行测试（验证安装）

```bash
python -m pytest tests/test_v2.py -q   # 14 项核心模块单元测试
python tests/test_e2e.py               # 合成数据全链路端到端
```

### 2. 数据预分片（一次性，最重要的一步）

```bash
# 将巡天目录分片为 HEALPix Parquet 分区（之后任务只读自己的分区）
python -m preprocessing.shard --catalog data/raw/sdss_spec.fits \
    --survey sdss --ra-col ra --dec-col dec --out data/shards --nside 16
```

### 3. 生成任务并执行

```bash
# 前处理仪表盘：生成任务 + HTCondor DAG + 自检
python -m preprocessing.dashboard.app --workdir work --db work/state.sqlite

# HTCondor 集群
condor_submit_dag work/workflow.dag

# 或 Slurm 集群
sbatch deploy/slurm_pixel_array.sbatch
sbatch --dependency=afterok:<jobid> deploy/slurm_global.sbatch SG2.1__global

# 或本地单任务调试（含断点续算/心跳/租约）
python -m compute.worker SG1.1__hp000123 --workdir work --results results \
    --db work/state.sqlite --owner node01
```

### 4. N-body 验证模拟

```bash
python -m compute.worker SG5.1__global --workdir work --results results
# 或直接调用：128³ 粒子、1300 Mpc 盒子、Zel'dovich IC → z=0，输出 P(k)
```

### 5. 后处理与可视化

```bash
python -m postprocessing.dashboard.app --results results --db work/state.sqlite
```

## 规模估算（真实全指标）

| 指标 | 数值 | 说明 |
|------|------|------|
| 输入星系数 | ~4×10⁶ | 各巡天 z≤0.15 红移目录（不含 Gaia 可选组 10⁷） |
| 输入数据量 | ~20 GB | 裁切后目录；分片后 ~11 GB (Parquet+ZSTD) |
| 总 FLOPs | ~1.1×10¹² | 逐项推导，见 scripts/estimate_scale.py |
| 处理速率 | 分片 5×10⁴ 行/s/核；距离重建 3×10³ obj/s/核 | 实测 |
| 单任务内存 | pixel <2 GB；global ≤8 GB | 分块读取 + memmap |
| 网络/shuffle | 一次性 shuffle ~11 GB；分发 ~31 GB | 10 天窗口 ~3 GB/day |
| 时间线 | 单机 16 核 < 1 天（不含数据下载） | 瓶颈是 global 任务串行段 |

对比 v1 估算（5.9×10⁹ 天体 / 0.5 PFLOPs / "100 节点 3 个月"）：
旧值输入不可得且估算失真约 3 个量级。详见 [docs/scale_estimate.json](docs/scale_estimate.json)。

## 架构要点（对应已知工程问题的解法）

- **I/O**：HATS 式 HEALPix 预分片 + Parquet 列裁剪，任务 I/O 从 O(全量) 降为 O(单分区)
- **容错**：worker 三阶段 checkpoint（输入指纹校验）+ 心跳 + 租约 + DAGMan RETRY + 僵尸任务重排队
- **并发**：SQLite WAL + busy_timeout + 批量事务；≥数十节点写并发时迁移 PostgreSQL（见 architecture.md）
- **性能**：CIC/FoF/距离等热点经 Numba JIT；PM 求解器 numba+FFT
- **精度**：全链路 观测→似然→先验→后验，输出不确定度而非点估计
- **部署**：Docker/Singularity 镜像 + Slurm/HTCondor 双调度 + GitHub Actions CI

## 如实声明（能力边界）

- N-body 求解器定位为**方法学验证**（≤256³ 粒子，单机小时级），
  不替代 Gadget/AREPO 级生产模拟；TreePM 力劈接口已预留（文档化）。
- Wiener 滤波的高斯先验在大尺度（k ≲ 0.1 h/Mpc）严格；小尺度非高斯
  升级路径（lognormal/HMC-BORG）已在文献调研中记录。
- 弱透镜模块为平面近似 KS93，PSF/shape bias/IA 系统误差处理列为后续工作。

## 数据来源

| 数据库 | 用途 | 入口 |
|--------|------|------|
| SDSS DR18 | 光谱红移骨干 | https://www.sdss.org/ |
| DESI DR1 (BGS) | 低红移高密度采样 | https://data.desi.lbl.gov/ |
| 2MRS | 全天完备亮星系样本 | IRSA |
| GAMA DR4 | 深场加密 | http://www.gama-survey.org/ |
| 6dFGS | 南天补全 | https://www.6dfgsurvey.net/ |
| Cosmicflows-4 | 特殊速度改正与校验 | http://cosmicflows.iap.fr/ |
| Gaia DR3 | 近场恒星（可选组） | https://gea.esac.esa.int/ |
| Planck | 宇宙学先验 | https://pla.esac.esa.int/ |

## 技术栈

Python 3.11+ / NumPy / SciPy / Astropy / Numba / PyArrow(Parquet) / h5py /
Flask / HTCondor·Slurm / Docker·Singularity / SQLite(WAL)→PostgreSQL

## License

MIT
