# 文献调研报告：1 Gpc 局部宇宙高精度三维建模

> 本文档是项目 v2.0 重构的科学依据。所有算法选型均追溯到同行评议文献或行业标准实现。

## 1. 科学目标的重定义

### 1.1 原目标的问题

原方案宣称"对整个可观测宇宙进行高精度三维建模"，输入估算 5.9×10⁹ 天体、0.5 PFLOPs、
"100 节点 3 个月"，存在三个无法自洽的硬伤：

1. **数据不可得**：DESI DR1 光谱红移仅 ~1.87×10⁷ 个；Euclid 弱透镜形状目录尚在
   Q1 阶段；"10¹⁰ 模拟粒子对比"需要 PB 级存储与超级计算机，不是单机/小集群项目。
2. **规模估算失真**：估算脚本把"天体数 × 常数 FLOPs"当作总计算量，未计算 I/O
   （TB/day）、内存（GB RAM/task）、网络 shuffle 等真实瓶颈，结论差 2–3 个量级。
3. **科学定义不清**：星系红移目录给出的是**星系分布**（biased tracer），不是
   **物质分布**；"红移→共动距离"是点估计，不含任何不确定度。

### 1.2 新目标：z ≤ 0.15 全天局部宇宙（~1 Gpc³）

**定义**：以观测者为中心，全天 4π 立体角，红移 z ≤ 0.15 的球壳体积。

- Planck18 宇宙学下 z=0.15 对应共动距离 ≈ 650 Mpc，
  体积 V = 4π/3 × (650 Mpc)³ ≈ **1.1 × 10⁹ Mpc³ ≈ 1 Gpc³**。
- 这是"局部宇宙"（Local Universe）的标准研究体积，被 2MRS、2M++、GAMA、
  ALFALFA、Cosmicflows 系列工作反复使用。

**为什么选这里**（而非某个高红移深场）：

| 判据 | z≤0.15 全天 | 高红移深场 |
|------|------------|-----------|
| 光谱红移完备性 | 高（多巡天拼接近乎完备） | 低（选择函数复杂） |
| 距离精度 | spec-z σ_v ~ 30 km/s → σ_d ~ 0.5 Mpc | photo-z 主导，σ_d ~ 50-100 Mpc |
| 特殊速度改正 | 有 Cosmicflows-4 实测改正 | 无法改正，RSD 严重 |
| 数据量 | ~10⁶ 星系，几十 GB | 10⁸–10⁹，TB 级 |
| 检验基准 | CF4/2M++ 已发表重建场可交叉验证 | 无独立基准 |

**科学产出**（与原"CG 计算组"对应，但全部落到实处）：

1. 星系三维位置目录（含距离后验不确定度，不是点估计）
2. 三维物质密度场的**后验均值 + 不确定度图**（Wiener 滤波，带选择函数与偏置）
3. 大尺度结构统计：2PCF / 功率谱（带选择函数随机目录）
4. 星系团/群目录（标准 b=0.2 FoF）
5. N-body 自洽性验证：同体积 PM 模拟 vs 重建场

## 2. 关键文献与算法选型

### 2.1 距离估计（解决问题清单 #9）

**视差零点改正**：Gaia EDR3/DR3 视差存在 ~−17 μas 量级的系统零点偏移，
随星等、颜色、天区位置变化（Lindegren et al. 2021, A&A 649, A4）。
不改正会使远处恒星距离被系统性低估。本项目实现其简化形式
（G 星等分段改正），并保留接入官方 `gaiadr3_zeropoint` 表的接口。

**Bayesian 距离后验**：直接取 1/ϖ 只在 ϖ/σ_ϖ ≳ 5–10 时无偏
（Luri et al. 2018, A&A 616, A9）。正确做法是后验推断：

p(r | ϖ, σ_ϖ) ∝ p(ϖ | r, σ_ϖ) × p(r)

- 似然：以真视差 1/r 为中心的高斯分布
- 先验：Bailer-Jones (2015, PASP 127, 994) 的指数衰减空间密度先验
  p(r) ∝ r² exp(−r/L)，L 为随银经银纬变化的标长；
  Bailer-Jones et al. (2021, AJ 161, 147) 对全部 Gaia EDR3 给出几何/测光几何距离，
  后验以 16/50/84 分位数摘要——本项目采用同一摘要规范。

**红移→距离与特殊速度**：

- 观测红移 z_obs = (1+z_cos)(1+v_pec/c) − 1。在 z≤0.15 局部宇宙，
  特殊速度（典型 300 km/s）造成的距离误差 δd/d ≈ v_pec/(cz) 在 z=0.01 处可达 30%。
- 改正链：CMB 参考系转换（CMB 偶极 369 km/s toward l=264°, b=48°，
  Planck 2020）→ 速度场模型改正（Cosmicflows-4, Courtois et al. 2023,
  A&A 670, L15；2M++, Lavaux & Hudson 2011, MNRAS 416, 2840）。
- 误差传播：σ_z → σ_d = |d(d_c)/dz| σ_z；photo-z 样本保留完整 p(z) 用于分层采样。

### 2.2 星系分布 → 物质分布（解决 #10、#12）

星系是物质的有偏示踪：n_g(x) = b × ρ_m(x) × 选择函数。
从星系计数反演物质场的标准方法是**贝叶斯场重建**：

- **Wiener 滤波**（ Zaroubi et al. 1995, ApJ 449, 446；2M++ 重建即此法）：
  假设高斯随机场先验，后验均值 δ̂(k) = [P(k)/(P(k)+b⁻²n̄⁻¹)] × d(k)/b，
  其中 n̄(x) 是选择函数（期待星系数密度），P(k) 是线性功率谱先验。
  后验协方差对角元给出逐像素不确定度——这正是"观测→likelihood→prior→
  posterior→mean+uncertainty"链条（问题 #12）的最小正确实现。
- 更完整的方案是 lognormal-Poisson 后验（Kitaura & Enßlin 2008）与
  HMC 前向建模（BORG, Jasche & Lavaux 2019；CF4 重建，Courtois et al. 2023），
  作为后续升级路径记录在案。

### 2.3 N-body 求解器（解决 #2、#4、#5）

真正的宇宙学 N-body 仿真需要求解共动坐标下的 Vlasov-Poisson 方程。
参照 Gadget-4（Springel et al. 2021, MNRAS 506, 2871）与教科书
（Hockney & Eastwood 1988）：

- **PM（Particle-Mesh）**：CIC 质量分配 → FFT 解泊松方程
  Φ_k = −(3/2)Ω_m H₀² δ_k/(a k²) → 4 点差分梯度 → CIC 力插值回粒子。
  周期边界，复杂度 O(N + M³ log M)。
- **TreePM 力劈**（Bagla 2002；Springel 2005）：Φ_k 按 exp(−k²r_s²) 劈为
  长程（PM/FFT）+ 短程（树，erfc 核），是 Gadget/AREPO 的标准方案。
  本项目实现 PM 为主体，Barnes-Hut 八叉树（Barnes & Hut 1986, Nature 324, 446）
  短程力用于小 N 校验，TreePM 耦合接口预留。
- **时间积分**：KDK 蛙跳（Drift-Kick-Drift），含哈勃拖曳项；
  初始条件用 Zel'dovich 近似从线性功率谱（Eisenstein & Hu 1998 无振荡拟合）生成。
- **验证基准**：测量的 P(k,z) 增长率与线性理论 D(a) 对比，是 PM 求解器
  正确性的金标准（本项目测试用例之一）。

定位说明：本求解器用于**方法学验证与模拟对比**（原 CG5），
在单节点跑 256³ 粒子 / 1.2 Gpc 盒子（粒子质量 ~10¹¹ M☉）需要数小时，
这与 Millennium（2160³，超算数月）有本质规模差异，README 中如实声明。

### 2.4 大星表分片：HATS 方案（解决 #8——最高优先级架构改造）

原方案"任务读全量原始数据再筛 tile"会造成 I/O 爆炸。
行业标准方案是 LINCC Frameworks 的 **HATS**（Hierarchical Adaptive Tiling
Scheme，DeLucchi et al. 2025, arXiv:2501.02103；用于 Rubin/LSST 数据分发）：

- HEALPix 等面积像素作为分区键（NESTED 索引），
  目录结构 `Norder=k/Npix=j/part.parquet`；
- Parquet 列式存储（ZSTD 压缩），任务只读取自己像素的文件，
  只读取需要的列（column pruning）；
- 分区大小均衡（本实现采用固定 nside 分区 + 元数据清单；
  自适应细分留作 HATS 全兼容升级路径）。

效果：任务输入 I/O 从 O(全量数据) 降为 O(单像素数据)，
648 tile 柱面方案下平均每任务多读 ~648 倍数据的问题被根除。

### 2.5 两点相关函数与选择函数（解决 #11）

Landy-Szalay 估计量 ξ = (DD−2DR+RR)/RR 只在随机目录 R 忠实反映
巡天选择函数（角向 mask × 径向 n(z)）时才无偏。
本项目实现：角向 mask（HEALPix 覆盖图）× 径向样条 n(z) 的随机目录生成器，
替代原实现中"在数据 bounding box 内均匀撒点"的错误做法。

FoF 链长：标准定义 b = 0.2 × n̄^(−1/3)（平均粒子间距的 0.2 倍，
Davis et al. 1985），替代原"坐标范围的 1/50"的任意设定。

### 2.6 工程化文献（解决 #1、#7）

- 状态存储：SQLite 在 WAL 模式下可支撑单写多读，但 100 节点并发写入
  必须用客户端-服务器数据库（PostgreSQL）——本实现保留 SQLite（WAL +
  busy_timeout）作为单机默认，DB 层抽象出接口并给出 PostgreSQL 迁移文档。
- 断点续算：worker 分阶段 checkpoint（阶段名+输入哈希），重启时跳过已完成阶段；
  心跳文件供调度器检测僵尸任务；DAGMan 原生支持 RETRY。
- 容器化：Dockerfile + Singularity.def（HPC 标准）+ Slurm/HTCondor 双模板。

## 3. 参考文献

1. Bailer-Jones, C. A. L. 2015, PASP, 127, 994 — 指数先验距离推断
2. Bailer-Jones et al. 2021, AJ, 161, 147 — Gaia EDR3 几何/测光几何距离
3. Lindegren et al. 2021, A&A, 649, A4 — Gaia EDR3 视差零点
4. Luri et al. 2018, A&A, 616, A9 — Gaia 视差的正确使用
5. Lavaux & Hudson 2011, MNRAS, 416, 2840 — 2M++ 密度/速度场重建
6. Courtois et al. 2023, A&A, 670, L15 — Cosmicflows-4 引力场重建
7. Tully et al. 2023, ApJ, 944, 94 — Cosmicflows-4 星表
8. Zaroubi et al. 1995, ApJ, 449, 446 — Wiener 滤波密度重建
9. Kitaura & Enßlin 2008, MNRAS, 389, 497 — lognormal 贝叶斯重建
10. Jasche & Lavaux 2019, A&A, 625, A64 — BORG 前向建模
11. Springel et al. 2021, MNRAS, 506, 2871 — Gadget-4 方法论文
12. Springel 2005, MNRAS, 364, 1105 — TreePM 算法
13. Barnes & Hut 1986, Nature, 324, 446 — 八叉树算法
14. Hockney & Eastwood 1988 — PM 方法教科书
15. Eisenstein & Hu 1998, ApJ, 496, 605 — 无振荡转移函数
16. Davis et al. 1985, ApJ, 292, 371 — FoF b=0.2 链长
17. Landy & Szalay 1993, ApJ, 412, 64 — 2PCF 估计量
18. Kaiser & Squires 1993, ApJ, 404, 441 — 弱透镜质量图
19. DeLucchi et al. 2025, arXiv:2501.02103 — HATS/LSDB 分片格式
20. Górski et al. 2005, ApJ, 622, 759 — HEALPix
21. Planck Collaboration 2020, A&A, 641, A6 — 宇宙学参数
