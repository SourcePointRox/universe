# 架构设计（v2.0）

## 1. 数据流总览

```
巡天原始目录 (FITS/CSV, ~20 GB)
      │
      ▼  一次性预处理
┌─────────────────────────────┐
│ preprocessing/shard.py      │  HEALPix(nside=16) 预分片
│ data/shards/<survey>/       │  Parquet + ZSTD, ~11 GB
│   Norder=16/Npix=<j>.parquet│  + _metadata.json 清单
└─────────────────────────────┘
      │
      ▼  taskgen：pixel 任务(3072) + global 任务(每子组1个)
┌─────────────────────────────┐
│ compute/worker.py           │  只读本像素分区(列裁剪)
│ load → compute → write      │  三阶段 checkpoint + 心跳 + 租约
└─────────────────────────────┘
      │  HDF5 + manifest(sha256)
      ▼
postprocessing: collector(校验) → reducer(归并) → visualize
```

## 2. 任务两级结构

| 级别 | 粒度 | 适用算法 | I/O 特征 |
|------|------|----------|----------|
| pixel | 每 HEALPix 像素 1 个任务 | 坐标重建、形状目录、光变曲线 | O(单分区)，~MB 级 |
| global | 每子组 1 个任务，依赖上游全部 pixel 任务 | 密度场、2PCF、FoF、Wiener、N-body | 读上游 HDF5（已压缩的三维坐标） |

DAG 依赖：global 任务的 PARENT 是上游子组的全部 pixel 任务（HTCondor DAGMan /
Slurm `--dependency=afterok` 均支持）。

## 3. 容错与调度

- **checkpoint**：`checkpoint.json` 记录已完成阶段 + 输入指纹
  （路径+大小+mtime 的 sha256）。输入变化 → 自动全量重算。
- **心跳**：worker 每 5 s 刷新 `heartbeat` 时间戳（文件 + DB）；
  `db.requeue_stale()` 把心跳超时且未超 max_attempts 的任务重置为 pending，
  超限置 dead。
- **租约**：`lease_task(owner, ttl)` 原子抢占，防止多节点重复执行同一任务。
- **重试**：DAGMan `RETRY 3` 处理瞬时失败；僵尸任务由 requeue 兜底。
- **原子落盘**：结果先写 `*.tmp.h5` 再 rename；manifest 记 sha256，
  collector 二次校验。

## 4. 状态库与并发

默认 SQLite（WAL + busy_timeout=30s + synchronous=NORMAL + 批量事务），
适用于单机与小集群（≤32 并发写）。**单写者原则**：控制端（taskgen/collector）
与 worker 不同时持写连接。

生产集群（≥数十节点并发写）迁移 PostgreSQL：
1. `common/db.py` 的 SQL 均为标准 SQL（ON CONFLICT 语法两者兼容）；
2. 替换连接层为 psycopg，DSN 走环境变量 `UNIVERSE_DB_DSN`；
3. 建议托管在头节点，worker 通过内网访问；
4. SQLite 的 `lease_task` 原子 UPDATE 语义在 PostgreSQL 下天然成立。

## 5. 性能工程

- 热点（CIC 分配/插值、直接求和力）Numba `@njit(parallel=True)`，首次调用有
  数秒 JIT 编译（cache=True 落盘复用）。
- 大目录读取：FITS memmap、Parquet batch reader、CSV 分块；
  单任务内存峰值上限见 `docs/scale_estimate.json`。
- NumPy 全向量化；避免 Python 层逐对象循环。

## 6. 部署矩阵

| 场景 | 方案 |
|------|------|
| 单机开发/演示 | venv + 本地 worker + Flask 仪表盘（docker-compose 可选） |
| 校园 HTCondor 池 | taskgen 生成 DAG + RETRY；worker.bat/sh 包装 |
| HPC (Slurm) | Singularity 镜像 + 数组作业（deploy/*.sbatch） |
| 云原生 | Dockerfile + K8s Job（global 任务用 single Pod，pixel 用 indexed Job） |

监控：两个仪表盘均暴露 `/metrics`（Prometheus 文本格式）：
任务状态计数、完成率、心跳超时数；生产环境接 Grafana。

## 7. 数据布局与预算

```
data/
├── raw/            # 原始目录（只增不改）          ~20 GB
└── shards/         # HEALPix 分区（可再生成）      ~11 GB
work/               # 任务规格/submit/日志/checkpoint ~2 GB
results/            # HDF5 结果 + manifest          ~10 GB
```

合计 < 50 GB。每个数据产品都可由上游重新生成（raw 之外均无单点价值）。

## 8. 已知的后续升级路径（按优先级）

1. NESTED 序 + 自适应细分（全 HATS 兼容，对接 LSDB 生态）
2. PostgreSQL 后端实现（接口已抽象）
3. TreePM 力劈接入 Barnes-Hut 短程（PM 长程已就绪，erfc 核已文档化）
4. lognormal-Poisson 后验替代 Wiener 高斯先验（小尺度非高斯）
5. 弱透镜系统误差链（PSF 模型/shape bias/IA/photo-z 联合）
6. 官方 Gaia zeropoint 表与 Bailer-Jones (l,b) 相关标长 L(l,b) 接入
