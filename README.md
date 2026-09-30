# Universe 3D Modeling

可观测宇宙高精度三维建模分布式计算平台。参照 BOINC/MilkyWay@home 分布式计算策略与千禧年模拟（Millennium Simulation）方法，基于 Gaia / DESI / SDSS / Euclid / TESS / LIGO / STScI-MAST 等巡天数据库，通过 HTCondor 高通量计算框架进行大规模数据处理。

## 项目结构

```
universe/
├── configs/                    # 配置：数据源注册表 + 计算组层级
│   ├── surveys.yaml            #   13个天文数据库注册表
│   └── compute_groups.yaml     #   5个计算组 × 13个子计算组定义
├── common/                     # 公共层
│   ├── db.py                   #   SQLite 状态库（files/tasks/results）
│   ├── tiling.py               #   天区分片（36×18 柱面 tile）
│   └── utils.py                #   工具函数（sha256, JSON I/O）
├── preprocessing/              # 数据前处理平台
│   ├── scanner.py              #   本地数据库扫描与巡天识别
│   ├── parsers/                #   FITS/CSV/HDF5/VOTable/GWF/ECSV 解析器
│   ├── matcher.py              #   数据-任务匹配器
│   ├── taskgen.py              #   HTCondor submit/DAG 生成器
│   ├── selfcheck.py            #   自检自验/错误重生成/下载清单
│   └── dashboard/              #   Web 仪表盘（任务热力图）
├── compute/                    # 计算核心
│   ├── algorithms/             #   三维坐标/CIC密度场/功率谱/2PCF/FoF/KS/BLS
│   ├── worker.py               #   HTCondor 计算节点工作程序
│   └── demo/                   #   算法演示（合成数据 → 可视化）
├── postprocessing/             # 数据后处理平台
│   ├── collector.py            #   结果回收与 SHA-256 校验
│   ├── reducer.py              #   全局科学归并（密度场/功率谱/晕目录）
│   ├── visualize.py            #   科学可视化（Mollweide/切片/曲线）
│   └── dashboard/              #   Web 仪表盘（进度热力图）
├── tests/                      # 端到端测试
│   └── test_e2e.py             #   合成数据全链路测试
├── scripts/
│   └── estimate_flops.py       #   算力规模测算脚本
├── docs/
│   ├── project_plan_report.pdf #   详细规划报告
│   └── flops_estimate.json     #   测算数据
└── demo_out/                   #   演示输出（可视化图）
```

## 计算组框架

| 计算组 | 目标 | 核心算法 | 输入数据 |
|--------|------|----------|----------|
| CG1 银河系近场 | 银盘/银晕三维恒星密度场 | 视差→距离→CIC | Gaia/2MASS/WISE |
| CG2 河外大尺度 | z<2 宇宙三维物质分布 | 红移→共动坐标→CIC→2PCF→FoF | DESI/SDSS/Euclid |
| CG3 弱引力透镜 | 投影物质分布质量图 | Kaiser-Squires | Euclid/MAST 形状 |
| CG4 瞬变与多信使 | 光变周期检测与宿主匹配 | BLS + 天区匹配 | TESS/LIGO |
| CG5 模拟对比 | 半解析验证与偏差校准 | HOD Mock + 交叉校验 | Millennium/TNG |

## 快速开始

### 环境准备

```bash
# 克隆仓库
git clone https://github.com/SourcePointRox/universe.git
cd universe

# 创建虚拟环境（推荐 Python 3.11+）
python -m venv .venv
.venv\Scripts\activate  # Windows
# source .venv/bin/activate  # Linux/macOS

# 安装依赖
pip install numpy astropy scipy flask pyyaml h5py matplotlib reportlab
```

### 1. 运行算法演示

```bash
python -m compute.demo.run_demo --n 20000 --outdir demo_out
```

输出：全天投影图、三维密度场切片、功率谱、两点相关函数。

### 2. 启动数据前处理平台

```bash
python -m preprocessing.dashboard.app --data-root "D:/astro_data" --workdir work --db state_pre.sqlite
```

访问 http://127.0.0.1:8601

操作步骤：
1. 输入数据库本地根目录 → 点击"扫描数据库"
2. 点击"生成任务与submit/DAG" → 自动生成 HTCondor 提交文件
3. 点击"自检自验" → 检查输入文件完整性
4. 点击"生成缺失数据下载清单" → 获取官方下载入口
5. 热力图实时显示任务生成/执行进度

### 3. 提交 HTCondor 计算

```bash
# 在 HTCondor 提交节点上
condor_submit_dag work/workflow.dag
```

或本地调试模式（无需 HTCondor）：
```bash
python -m compute.worker SG1.1__r00_d00 --config configs/compute_groups.yaml --workdir work --results results
```

### 4. 启动数据后处理平台

```bash
python -m postprocessing.dashboard.app --results results --db state_pre.sqlite
```

访问 http://127.0.0.1:8602

操作步骤：
1. 点击"收集并校验结果" → 回收计算节点输出
2. 点击"科学归并" → 全局密度场/功率谱/晕目录
3. 点击"生成科学可视化图像" → 输出 PNG 图表
4. 热力图实时显示各子组完成进度

### 5. 运行端到端测试

```bash
python tests/test_e2e.py
```

测试流程：合成数据 → 扫描 → 任务生成 → 本地计算 → 结果校验 → 归并 → 可视化。

## 算力规模估算

基于 13 个巡天数据库天体数量与算法复杂度测算：

| 计算组 | 天体数 | 总 FLOPs |
|--------|--------|----------|
| CG1 银河系近场 | 2.8×10⁹ | 2.2×10¹² |
| CG2 河外大尺度 | 1.6×10⁹ | 3.0×10¹² |
| CG3 弱引力透镜 | 1.5×10⁹ | 9.5×10¹⁰ |
| CG4 瞬变与多信使 | 5.0×10⁷ | 5.0×10¹⁴ |
| CG5 模拟对比 | 1.0×10¹⁰ | 1.1×10¹² |
| **合计** | | **≈0.5 PFLOPs** |

推荐配置：100 节点 × 64 核 × 256 GB 内存，约 3 个月完成全部计算。

详见 [算力测算脚本](scripts/estimate_flops.py) 和 [详细规划报告](docs/project_plan_report.pdf)。

## 数据来源

| 数据库 | 入口 | 关联度 |
|--------|------|--------|
| Gaia DR4 | https://gea.esac.esa.int/archive/ | 极高 |
| DESI DR1 | https://data.desi.lbl.gov/ | 极高 |
| SDSS DR19 | https://www.sdss.org/ | 极高 |
| Euclid | https://www.euclid-ec.org/ | 高 |
| STScI/MAST | https://archive.stsci.edu/ | 高 |
| TESS | https://archive.stsci.edu/tess/ | 中低 |
| LIGO/Virgo | https://gwosc.org/ | 中低 |
| Millennium/TNG | https://www.tng-project.org/ | 高 |

## 技术栈

- **语言**: Python 3.11+（全部组件）
- **核心库**: numpy, astropy, scipy, h5py, matplotlib
- **Web 框架**: Flask（无外部 JS 依赖，原生 Canvas 热力图）
- **计算调度**: HTCondor（DAGMan 工作流）
- **数据格式**: FITS, CSV, HDF5, VOTable, ECSV
- **状态存储**: SQLite

## License

MIT
