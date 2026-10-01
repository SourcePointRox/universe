# 数据下载指南

> ⚠️ **v2.0 注记（2026-10-01）**：本文数据清单基于 v1 目标（全天多巡天 59 亿天体）。
> v2.0 科学目标已修订为 z≤0.15 全天局部宇宙（~1 Gpc³），所需巡天子集与量级见
> `configs/surveys.yaml` 与 `configs/science.yaml`；下载后须先经
> `preprocessing/shard.py` 做 HEALPix 预分片再进入计算管线。

## 核心数据库下载入口

| 数据库 | 优先级 | 下载方式 | 建议子集 |
|--------|--------|----------|----------|
| Gaia DR3 | ★★★★★ | ADQL 查询 / 批量下载 | gaia_source 目录 (586 GB) |
| DESI DR1 | ★★★★★ | data.desi.lbl.gov | zcatalog + lss catalogs (~5 GB) |
| SDSS DR19 | ★★★★★ | CASjobs / SAS | photoObj + spAll (~10 GB) |
| Euclid Q1 | ★★★★ | Euclid SAS / Datalabs | MER catalog (~2 GB) |
| TESS | ★★★ | MAST Portal | 光变曲线（按需 sector） |
| LIGO | ★★★ | GWOSC | GWTC 事件目录（<100 MB） |

## 快速下载示例

### Gaia DR3 目录

```python
from astroquery.gaia import Gaia

# 查询河外星系样本（视差<0.1 mas 或 无有效视差）
query = """
SELECT ra, dec, parallax, phot_g_mean_mag, bp_rp
FROM gaiadr3.gaia_source
WHERE parallax < 0.1 OR parallax IS NULL
"""
job = Gaia.launch_job_async(query, dump_to_file=True, output_format='csv')
```

### DESI DR1 红移目录

```bash
# 下载 DR1 红移目录
wget -r -np -nH --cut-dirs=4 -R "index.html*" \
  https://data.desi.lbl.gov/public/dr1/spectro/redux/loa/zcatalog/
```

### SDSS DR19 光谱目录

```bash
# 通过 Globus 或 rsync
rsync -avz rsync://data.sdss.org/dr19/spectro/redux/v6_1_3/spAll* ./
```

## 存储空间规划

| 阶段 | 数据类型 | 预估大小 |
|------|----------|----------|
| Phase 0 | Gaia + DESI + SDSS 目录 | ~15 TB |
| Phase 1 | + Euclid Q1 | ~17 TB |
| Phase 2 | + 模拟目录 (TNG) | ~25 TB |
| 全部 | + 中间结果 | ~35 TB |

**注意**：全量原始图像数据（Euclid 100 PB, LSST 30 PB）超出单机存储能力，
建议仅下载目录级数据，图像数据按需通过 API 流式获取。
