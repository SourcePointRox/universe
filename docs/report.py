"""生成中文规划报告 PDF (reportlab)。"""
import json
import os
import sys
from pathlib import Path

from reportlab.lib.colors import HexColor
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import cm, mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (KeepTogether, PageBreak, Paragraph, SimpleDocTemplate,
                                Spacer, Table, TableStyle)

# 注册中文字体
font_paths = [
    r"C:\Windows\Fonts\msyh.ttc",
    r"C:\Windows\Fonts\simsun.ttc",
    r"C:\Windows\Fonts\simhei.ttf",
]
CJK_FONT = None
for fp in font_paths:
    if os.path.exists(fp):
        CJK_FONT = "CJKFont"
        pdfmetrics.registerFont(TTFont(CJK_FONT, fp, subfontIndex=0))
        break
if CJK_FONT is None:
    print("未找到中文字体")
    sys.exit(1)

# 样式
TITLE = ParagraphStyle("T", fontName=CJK_FONT, fontSize=24, leading=32, textColor=HexColor("#1a365d"), alignment=1, wordWrap="CJK")
SUB = ParagraphStyle("S", fontName=CJK_FONT, fontSize=13, leading=18, textColor=HexColor("#4a5568"), alignment=1, wordWrap="CJK")
H1 = ParagraphStyle("H1", fontName=CJK_FONT, fontSize=18, leading=26, textColor=HexColor("#1a365d"), spaceBefore=18, spaceAfter=10, wordWrap="CJK")
H2 = ParagraphStyle("H2", fontName=CJK_FONT, fontSize=14, leading=20, textColor=HexColor("#2b6cb0"), spaceBefore=14, spaceAfter=6, wordWrap="CJK")
BODY = ParagraphStyle("B", fontName=CJK_FONT, fontSize=10.5, leading=17, textColor=HexColor("#2d3748"), spaceAfter=8, wordWrap="CJK")
CAP = ParagraphStyle("C", fontName=CJK_FONT, fontSize=9, leading=12, textColor=HexColor("#718096"), alignment=1, spaceBefore=4, spaceAfter=8, wordWrap="CJK")


def P(text, style=BODY):
    return Paragraph(text, style)


def line():
    from reportlab.platypus import HRFlowable
    return HRFlowable(width="100%", thickness=1, color=HexColor("#e2e8f0"), spaceBefore=6, spaceAfter=6)


def tbl(data, colw=None):
    t = Table(data, colWidths=colw, repeatRows=1)
    t.setStyle(TableStyle([
        ("BACKGROUND", (0,0), (-1,0), HexColor("#2b6cb0")),
        ("TEXTCOLOR", (0,0), (-1,0), HexColor("#ffffff")),
        ("FONTNAME", (0,0), (-1,-1), CJK_FONT),
        ("FONTSIZE", (0,0), (-1,0), 10),
        ("FONTSIZE", (0,1), (-1,-1), 9.5),
        ("ALIGN", (0,0), (-1,-1), "LEFT"),
        ("VALIGN", (0,0), (-1,-1), "MIDDLE"),
        ("BOTTOMPADDING", (0,0), (-1,0), 8),
        ("TOPPADDING", (0,0), (-1,0), 8),
        ("BOTTOMPADDING", (0,1), (-1,-1), 6),
        ("TOPPADDING", (0,1), (-1,-1), 6),
        ("BACKGROUND", (0,1), (-1,-1), HexColor("#f7fafc")),
        ("ROWBACKGROUNDS", (0,1), (-1,-1), [HexColor("#f7fafc"), HexColor("#ffffff")]),
        ("GRID", (0,0), (-1,-1), 0.5, HexColor("#e2e8f0")),
    ]))
    return t


def build_report(out_pdf, flops_json):
    with open(flops_json, encoding="utf-8") as f:
        F = json.load(f)

    doc = SimpleDocTemplate(str(out_pdf), pagesize=A4,
        leftMargin=2*cm, rightMargin=2*cm, topMargin=2*cm, bottomMargin=2*cm)
    story = []

    # ========== 封面 ==========
    story.append(Spacer(1, 4*cm))
    story.append(P("可观测宇宙高精度三维建模", TITLE))
    story.append(P("分布式计算项目规划报告", TITLE))
    story.append(Spacer(1, 0.8*cm))
    story.append(P("参照 BOINC/MilkyWay@home 分布式策略与千禧年模拟方法", SUB))
    story.append(Spacer(1, 2*cm))
    story.append(P("基于 Gaia / DESI / SDSS / Euclid / TESS / LIGO / STScI-MAST 等巡天数据库", SUB))
    story.append(Spacer(1, 0.5*cm))
    story.append(P("日期: 2026年9月30日", SUB))
    story.append(PageBreak())

    # ========== 摘要 ==========
    story.append(P("摘要", H1))
    story.append(P("""本项目旨在仿照 BOINC 上 MilkyWay@home 的分布式计算策略，结合千禧年模拟（Millennium Simulation）
的 N 体模拟与半解析建模方法，利用全球主要巡天数据库（Gaia、DESI、SDSS、Euclid、STScI/MAST、TESS、LIGO 等）
的开放科学数据，对可观测宇宙进行高精度三维建模。项目分为 5 个计算组（CG1–CG5），
涵盖银河系近场结构、河外大尺度结构、弱引力透镜质量图、瞬变与多信使事件、以及模拟对比验证。
每个计算组按天区 36×18 柱面分片拆分为子计算任务，任务通过 HTCondor 高通量计算框架分发给局域网内的计算节点执行。

项目规划了完整的算力规模测算：基于各数据库天体数量与算法复杂度，总浮点运算量约 <b>5×10¹⁴ FLOPs</b>（0.5 PFLOPs），
推荐配置为 100 节点 × 64 核 × 256 GB 内存的局域网集群，可在约 3 个月内完成全部目录级数据处理。
原始数据总量约 <b>135 PB</b>，但实际科学处理仅需星表目录（约 <b>14 TB</b>）及中间结果（约 <b>7 TB</b>），
推荐存储规模约 <b>35 TB</b>（含备份）。""", BODY))
    story.append(line())

    # ========== 1. 项目背景 ==========
    story.append(P("1. 项目背景与目标", H1))
    story.append(P("""现代宇宙学巡天项目已进入"多信使、多波段、大样本"时代。Gaia DR4 预计包含约 28 亿个源的天体测量数据；
DESI DR1 提供了 1870 万个光谱红移，是迄今最大的三维宇宙图；Euclid 卫星在 5 年任务期内将产生约 100 PB 数据；
SDSS 累计数据超过 650 TB。面对如此庞大的数据量，单台工作站已无法完成全样本的三维重建。

BOINC（Berkeley Open Infrastructure for Network Computing）是志愿者分布式计算的典范平台。
MilkyWay@home 利用 BOINC 的冗余计算机制，将 SDSS 数据分发给全球志愿者计算机，
通过遗传算法、差分进化和粒子群优化（PSO）搜索银河系潮汐流的最佳拟合参数，
达到了约 0.5 PFLOPs 的分布式计算规模。千禧年模拟（Millennium Simulation, Springel et al. 2005）
首次在 500 h⁻¹ Mpc 盒内模拟了 10¹⁰ 个暗物质粒子的引力演化，使用 Tree-PM 算法（GADGET-2）
结合 Friends-of-Friends（FoF）和 SUBFIND 子晕识别，构建了现代宇宙大尺度结构的理论基准。

本项目的目标是将这两个范式结合：<b>以 BOINC 的分布式任务分发与冗余验证思想为调度框架，
以千禧年模拟的 N 体引力计算和密度场重建方法为算法核心，
对多源巡天数据进行统一三维重建</b>。最终产品包括：
(1) 银河系恒星三维密度场与速度场；(2) 河外星系三维分布、两点相关函数与功率谱；
(3) 弱引力透镜质量图；(4) 多信使事件宿主星系目录；(5) 与 Millennium/IllustrisTNG 模拟的交叉验证报告。""", BODY))

    # ========== 2. 数据库调研 ==========
    story.append(P("2. 天文开放数据库调研", H1))
    story.append(P("""本项目涉及 13 个主要天文开放数据库，覆盖天体测量、光谱红移、成像巡天、引力波和宇宙微波背景。
下表汇总各数据库的数据规模、格式、访问方式与三维建模关联度。""", BODY))

    data_db = [
        ["数据库", "机构", "数据总量", "核心产品", "格式", "API", "关联度"],
        ["Gaia DR4", "ESA", "~400–500 TB", "天体测量/测光/视向速度", "ECSV/TAP", "ADQL/TAP", "极高"],
        ["DESI DR1", "LBNL", "~270 TB", "光谱/红移/大尺度结构目录", "FITS/CSV", "data.desi.lbl.gov", "极高"],
        ["SDSS DR19", "SDSS", "累计~652 TB", "成像/光谱/APOGEE/MaNGA", "FITS/CSV", "CASjobs/SkyServer", "极高"],
        ["Euclid", "ESA", "Q1 35 TB;任务~100 PB", "VIS/NISP成像/测光红移/光谱", "FITS", "SAS/Datalabs", "高"],
        ["STScI/MAST", "NASA", "~4 PB+", "HST/JWST/TESS/Kepler图像光谱", "FITS/VOT", "astroquery.mast", "高"],
        ["TESS", "NASA/MIT", "FFI>251 TB", "全帧图像/光变曲线", "FITS", "MAST Portal", "中低"],
        ["LIGO/Virgo", "LVK", "事件目录+", "应变数据/参数估计", "GWF/HDF5", "GWOSC", "中低"],
        ["2MASS", "NASA/IPAC", "~24 TB", "J/H/Ks近红外全天空目录", "FITS/CSV", "IRSA FTP", "中"],
        ["WISE", "NASA/IPAC", "目录~15 TB", "中红外源目录/Atlas Images", "FITS", "IRSA TAP", "中"],
        ["Pan-STARRS", "IfA", "DR2 ~1.6 PB", "多色图像/星表(grizy)", "FITS/CSV", "PS1 MAST API", "中"],
        ["DECaLS/LSST", "NOIRLab", "DR8~45 TB;LSST~30 PB", "拼接图像/Tractor星表", "FITS", "Legacy Survey", "高"],
        ["Planck", "ESA", "~32 TB", "CMB温度/偏振/成分图", "FITS", "ESDC/IRSA", "中低"],
        ["ALMA", "ESO/NAOJ/NRAO", "~2 PB", "毫米/亚毫米图像/谱线", "FITS/HDF5", "ALMA Archive", "中"],
    ]
    story.append(tbl(data_db, colw=[3.2*cm,2.2*cm,3.0*cm,4.2*cm,2.0*cm,3.0*cm,1.5*cm]))
    story.append(P("表1: 主要天文开放数据库概览", CAP))
    story.append(P("""<b>数据获取策略：</b>对于目录级数据（星表、红移目录、测光目录），
优先通过各数据库的官方 TAP/ADQL 接口或批量下载脚本获取压缩后的 ECSV/CSV/FITS 表文件。
对于图像级数据（FITS图像、FFI、光谱立方体），仅在特定任务（如弱引力透镜形状测量）时按需下载。
全库原始数据总量约 135 PB，但实际科学分析仅需目录数据约 14 TB，辅以少量图像数据。""", BODY))

    # ========== 3. BOINC/MilkyWay@home 架构 ==========
    story.append(P("3. BOINC 分布式计算架构与 MilkyWay@home", H1))
    story.append(P("""BOINC 的核心是<b>客户端-服务器模型</b>。服务器端负责生成工作单元（Work Unit），
每个工作单元包含输入数据、可执行程序和参数文件。工作单元通过 HTTP 分发给志愿者客户端，
客户端在本地执行计算后将结果上传回服务器。为保证结果可靠性，BOINC 采用<b>冗余计算</b>机制：
同一工作单元被发送给至少 2 个独立客户端，服务器比较多个返回结果的一致性，
若不一致则发起第三次重试，直到达到多数一致。""", BODY))
    story.append(P("""MilkyWay@home 在此基础上引入了<b>进化算法</b>作为核心搜索策略：
(1) <b>N 体模拟</b>：使用 Barnes-Hut 树算法计算引力相互作用，支持异构计算节点上的数值一致性；
(2) <b>遗传算法（GA）</b>：在模型参数空间（演化时间、重子/暗物质质量、尺度半径等）中搜索最优拟合；
(3) <b>粒子群优化（PSO）</b>：在多峰似然曲面上寻找全局最优；
(4) <b>差分进化（DE）</b>：用于优化 N 体模拟的初始条件。
该项目支持超过 15 万个累计贡献者，峰值算力约 0.5 PFLOPs。""", BODY))
    story.append(P("""本项目借鉴 BOINC 的<b>任务分片、冗余验证、结果回收</b>思想，
但将志愿者计算替换为受控局域网内的 HTCondor 高通量集群，以提高可靠性和数据安全性。
HTCondor 的 ClassAd 匹配机制允许精细控制任务与节点的匹配条件，
DAGMan 工作流管理器支持复杂的任务依赖图，
与 BOINC 的松耦合架构形成互补。""", BODY))

    # ========== 4. 千禧年模拟方法论 ==========
    story.append(P("4. 千禧年模拟与宇宙学模拟方法", H1))
    story.append(P("""千禧年模拟（Springel et al. 2005）使用 GADGET-2 代码在 500 h⁻¹ Mpc 立方体内模拟了 2160³ ≈ 10¹⁰ 个暗物质粒子，
质量分辨率约 8.6×10⁸ h⁻¹ M☉，空间分辨率 5 h⁻¹ kpc。其核心算法为 <b>Tree-PM</b>：
长程力通过粒子网格法（PM，FFT 求解泊松方程）计算，复杂度 O(N + M log M)；
短程力通过 Barnes-Hut 八叉树计算，复杂度 O(N log N)。
后续 Millennium-II（2008）将分辨率提高了 100 倍，验证了算法在不同尺度上的自洽性。""", BODY))

    story.append(P("""暗物质晕识别采用 <b>Friends-of-Friends（FoF）</b> + <b>SUBFIND</b> 两步法：
(1) FoF 以 linking length = 0.2 倍平均粒子间距识别过密度区域；
(2) SUBFIND 在 FoF 晕内部通过自适应密度场降低阈值识别子晕，并通过迭代引力解绑移除未束缚粒子。
并合树（Merger Trees）追踪子晕随时间的演化，为半解析星系形成模型（如 GALFORM）提供结构骨架。""", BODY))

    story.append(P("""Illustris/TNG 和 EAGLE 模拟进一步加入了辐射冷却、恒星形成、恒星反馈和 AGN 反馈等完整物理过程。
IllustrisTNG 使用 AREPO 移动网格有限体积法，EAGLE 使用 GADGET-3 TreeSPH 代码。
这些模拟的公开数据库（如 TNG API）提供了暗物质晕目录、星系目录、快照和并合树，
可用于与本项目的观测重建结果进行交叉验证。""", BODY))

    story.append(P("""<b>光锥构造</b>是将模拟结果转换为可观测预测的关键步骤：
利用周期性边界条件平铺多个模拟盒，在中心放置观测者，沿径向提取不同红移壳层的数据，
投影到 HEALPix 球面网格上，构建 Mock 星系目录。Millennium-XXL 已提供全天空光锥目录，
覆盖至 z=2.2，可直接用于本项目 CG2 和 CG5 的模拟对比。""", BODY))

    # ========== 5. 算法设计 ==========
    story.append(P("5. 核心算法设计", H1))
    story.append(P("""本项目将三维宇宙建模分解为 5 个计算组（CG），每个计算组下设子计算组（SG）和天区分片（Tile）：
""", BODY))

    data_alg = [
        ["计算组", "目标", "核心算法", "输入数据", "输出产品"],
        ["CG1 银河系近场", "银盘/银晕三维恒星密度场", "视差->距离->CIC网格化", "Gaia/2MASS/WISE", "stars3d, density_grid"],
        ["CG2 河外大尺度", "z<2 宇宙三维物质分布", "红移->共动坐标->CIC->2PCF->FoF", "DESI/SDSS/Euclid", "galaxies3d, P(k), xi(r), halos"],
        ["CG3 弱引力透镜", "投影物质分布质量图", "Kaiser-Squires (1993)", "Euclid/MaST形状", "shear_catalog, mass_map"],
        ["CG4 瞬变与多信使", "光变周期检测与宿主匹配", "BLS凌日检测+天区匹配", "TESS/LIGO", "lc_features, gw_hosts"],
        ["CG5 模拟对比", "半解析验证与偏差校准", "HOD Mock + 交叉校验", "Millennium/TNG", "mock_catalog, validation"],
    ]
    story.append(tbl(data_alg, colw=[3.0*cm,4.0*cm,4.5*cm,3.5*cm,3.5*cm]))
    story.append(P("表2: 计算组与核心算法", CAP))

    story.append(P("<b>5.1 三维坐标重建</b>", H2))
    story.append(P("""对于河外星系，使用 Planck18 宇宙学模型将红移 z 转换为共动距离 d_C(z)，
再通过球面-直角坐标投影：
<br/>x = d_C · cos δ · cos α, &nbsp;&nbsp; y = d_C · cos δ · sin α, &nbsp;&nbsp; z = d_C · sin δ
<br/>其中 α 为赤经，δ 为赤纬。对于银河系恒星，利用 Gaia 视差 π（mas）反推距离 d = 1/π（kpc），
注意对 π≤0 的样本进行截断处理并标记，避免 Lutz-Kelker 偏差。""", BODY))

    story.append(P("<b>5.2 Cloud-In-Cell (CIC) 密度场网格化</b>", H2))
    story.append(P("""将三维坐标分配到规则网格上，每个粒子对 8 个相邻网格点的贡献按距离加权。
CIC 保证质量守恒且抑制数值噪声。对密度场 ρ 计算过密度 δ = ρ/⟨ρ⟩ - 1，
用于后续的功率谱和成团性分析。""", BODY))

    story.append(P("<b>5.3 功率谱 P(k)</b>", H2))
    story.append(P("""对过密度场 δ 进行三维 FFT，估计各向同性功率谱 P(k)。
算法复杂度 O(M³ log M³)，其中 M 为网格维度（如 64³, 96³, 128³）。
对于大尺度结构研究，k 的范围覆盖 ~0.01–1 h/Mpc。""", BODY))

    story.append(P("<b>5.4 两点相关函数 ξ(r)</b>", H2))
    story.append(P("""使用 Landy-Szalay 估计量：ξ(r) = (DD - 2DR + RR)/RR，
其中 DD、DR、RR 分别为数据-数据、数据-随机、随机-随机对的计数。
本项目使用 scipy cKDTree.count_neighbors 实现 O(N log N) 的计数，可处理百万级天体。""", BODY))

    story.append(P("<b>5.5 Friends-of-Friends 成团识别</b>", H2))
    story.append(P("""基于 scipy.spatial.cKDTree 的 query_pairs 功能，
以 linking length 为阈值构建粒子对的邻接图，通过 Union-Find 算法标记连通分量。
迭代引力解绑（简化版）移除未束缚粒子，得到稳定的暗物质晕/星系团候选。""", BODY))

    story.append(P("<b>5.6 Kaiser-Squires 弱透镜质量图</b>", H2))
    story.append(P("""将剪切场 γ = γ₁ + iγ₂ 投影到二维天空网格，通过 Kaiser & Squires (1993) 的平面近似公式：
<br/>κ = ℱ⁻¹[(k_x² - k_y²)/(k_x² + k_y²) · ℱ(γ₁) + 2k_xk_y/(k_x² + k_y²) · ℱ(γ₂)]
<br/>其中 ℱ 表示二维傅里叶变换，κ 为收敛场（质量面密度相对涨落）。""", BODY))

    # ========== 6. 算力测算 ==========
    story.append(P("6. 算力规模严谨测算", H1))
    story.append(P(f"""基于各数据库天体数量与算法复杂度，对每个计算组进行 FLOPs 估算。
<b>CG1</b>（银河系近场）：Gaia DR4 约 2.8×10⁹ 个源。坐标转换每源约 50 FLOPs，密度网格化约 100 FLOPs，
潮汐流探测需 KDTree 邻域搜索约 20×log₂N 次操作/源。总计约 <b>{F['CG1']['total_flops']:.2e} FLOPs</b>。
<br/><b>CG2</b>（河外大尺度）：DESI DR1 约 1.87×10⁷、SDSS 约 10⁹、Euclid 约 1.5×10⁹，
合计约 1.6×10⁹ 个星系。坐标转换+密度场+两点相关+FoF 总计约 <b>{F['CG2']['total_flops']:.2e} FLOPs</b>。
<br/><b>CG3</b>（弱引力透镜）：Euclid 形状目录约 1.5×10⁹ 个星系。
Kaiser-Squires 重建主要开销为二维 FFT（512² 网格约 1.8×10¹⁰ FLOPs）。总计约 <b>{F['CG3']['total_flops']:.2e} FLOPs</b>。
<br/><b>CG4</b>（瞬变与多信使）：TESS 光变曲线检测（BLS）开销最大，
每源约 2000 点 × 500 周期 × 10 FLOPs = 10⁷ FLOPs，5×10⁷ 个源总计约 <b>{F['CG4']['total_flops']:.2e} FLOPs</b>。
<br/><b>CG5</b>（模拟对比）：Millennium/TNG 晕目录约 10¹⁰ 个粒子/晕，HOD 分配+交叉校验总计约 <b>{F['CG5']['total_flops']:.2e} FLOPs</b>。""", BODY))

    story.append(P(f"""<b>汇总：</b>五个计算组总计约 <b>{F['_TOTAL']['total_petaflops']:.2f} PFLOPs</b>。
以现代多核 CPU 单节点双精度约 50 GFLOPS 计，单节点连续运行约 <b>{F['_TIME']['single_node_hours']:.1f} 小时</b>。
考虑到 I/O 开销（FITS/CSV 解析、网络传输、任务调度延迟），实际运行时间约为纯 FLOPs 时间的 5–10 倍，
即单节点约 15–30 小时。""", BODY))

    story.append(P("<b>6.1 集群规模与时间估算</b>", H2))
    data_hw = [
        ["配置级别", "节点数", "CPU/节点", "内存/节点", "存储", "网络", "预估时间"],
        ["最小可行", "10", "32核", "128 GB", "5 TB", "10 Gbps", "约6个月"],
        ["推荐配置", "100", "64核", "256 GB", "20 TB", "25 Gbps", "约3个月"],
        ["雄心配置", "1000", "64核+2 GPU", "512 GB", "1 PB", "100 Gbps", "约2周"],
    ]
    story.append(tbl(data_hw, colw=[3.0*cm,2.5*cm,2.5*cm,2.5*cm,2.0*cm,2.0*cm,3.0*cm]))
    story.append(P("表3: 硬件配置与时间估算", CAP))

    story.append(P("""<b>备注：</b>上表中的"预估时间"基于目录级数据处理（星表、红移、测光目录）。
若涉及全量图像数据（如 Euclid 100 PB 成像数据），则需要 PB 级存储和 GPU 加速的图像处理流水线，
时间将显著延长。本项目第一阶段仅处理目录数据。""", BODY))

    story.append(P("<b>6.2 存储与网络需求</b>", H2))
    story.append(P(f"""<b>原始数据：</b>全库原始数据约 <b>{F['_STORAGE']['raw_data_tb']:,.0f} TB</b>，
但科学分析仅需目录级数据约 <b>{F['_STORAGE']['catalog_tb']:.1f} TB</b>。
<br/><b>中间结果：</b>按天区分片的 HDF5 结果文件（每任务约 1–10 MB），
648 tiles × 5 计算组 ≈ 3240 个结果文件，总计约 <b>{F['_STORAGE']['intermediate_tb']:.1f} TB</b>。
<br/><b>推荐存储：</b>目录数据 + 中间结果 + 备份 ≈ <b>{F['_STORAGE']['total_recommended_tb']:.1f} TB</b>。
<br/><b>网络：</b>每任务输入约 10 MB，输出约 1 MB。100 节点同时运行时，
峰值带宽需求约 25 Gbps 即可满足。""", BODY))

    # ========== 7. HTCondor 集成 ==========
    story.append(P("7. HTCondor 高通量计算框架集成", H1))
    story.append(P("""本项目采用 HTCondor 作为局域网内的任务调度引擎。HTCondor 的核心组件包括：
(1) <b>Schedd</b>（调度器）：管理作业队列；(2) <b>Collector</b>：收集集群资源信息；
(3) <b>Negotiator</b>：任务与机器的匹配协商；(4) <b>Startd</b>：计算节点端守护进程。

任务通过 ClassAd 描述资源需求：
<pre>
universe = vanilla
executable = compute_worker.py
arguments = $(ProcID)
request_cpus = 2
request_memory = 4GB
request_disk = 10GB
should_transfer_files = YES
when_to_transfer_output = ON_EXIT
</pre>

复杂依赖通过 <b>DAGMan</b> 管理：上游任务（如坐标重建 SG2.1）完成后，
自动触发下游任务（密度场 SG2.2、两点相关 SG2.3、FoF SG2.4）。
Rescue DAG 机制支持失败恢复。""", BODY))

    story.append(P("""<b>与 BOINC 的互补关系：</b>HTCondor 适合受控环境下的批量科学计算，
BOINC 适合面向公众的大规模志愿者计算。本项目以 HTCondor 为主框架处理局域网内的数据密集型任务，
未来可将部分独立子任务（如 CG4 的光变曲线 BLS 检测）通过 BOINC 扩展至志愿者网络。""", BODY))

    # ========== 8. 系统架构 ==========
    story.append(P("8. 系统架构：前处理与后处理平台", H1))
    story.append(P("""<b>8.1 数据前处理平台</b>""", H2))
    story.append(P("""功能：(1) 本地数据库扫描与识别：递归遍历用户指定的数据根目录，
通过文件名模式匹配和 FITS/CSV/HDF5 头部关键字自动识别所属巡天数据库；
(2) 数据-任务匹配：根据计算组定义的需求（所需数据库、语义列），
自动将扫描到的文件匹配到对应的子计算任务；
(3) HTCondor 任务生成：按天区分片自动生成 task.json 规格文件和 .sub 提交文件，
并构建全局 DAGMan 工作流；
(4) 自检自验：校验输入文件存在性与 SHA-256 完整性，自动标记错误任务并支持重新生成；
(5) 缺失数据下载清单：为未扫描到的数据库生成官方下载入口指引；
(6) 热力图进度展示：Web 仪表盘以子计算组 × 天区分片矩阵形式实时显示任务状态。

<b>8.2 数据后处理平台</b>""", H2))
    story.append(P("""功能：(1) 结果回收与校验：扫描计算节点输出目录中的 manifest.json，
校验结果 HDF5 文件的 SHA-256 完整性，更新数据库任务状态；
(2) 科学归并：将各天区分片的中间结果合并为全局科学产品，
包括三维密度场、功率谱 P(k)、两点相关函数 ξ(r)、暗物质晕目录；
(3) 可视化：生成 Mollweide 全天投影图、三维密度场切片图、功率谱曲线、相关函数曲线、弱透镜质量图；
(4) 进度热力图：实时展示各子组的任务完成率。

两个平台均采用 Flask 轻量 Web 框架，纯原生 HTML5 Canvas 绘制热力图，无外部 JS 依赖。""", BODY))

    # ========== 9. 实现路径 ==========
    story.append(P("9. 整体实现路径与里程碑", H1))
    data_mile = [
        ["阶段", "时间", "目标", "交付物"],
        ["Phase 0", "第1–2月", "数据获取与基础设施搭建", "下载 Gaia/DESI/SDSS 目录（~10 TB），部署 HTCondor 池"],
        ["Phase 1", "第3–4月", "前处理平台开发与测试", "扫描/匹配/任务生成/自检功能上线，合成数据 e2e 测试通过"],
        ["Phase 2", "第5–7月", "核心算法计算与验证", "CG1/CG2 坐标重建、密度场、功率谱、FoF 成团识别完成"],
        ["Phase 3", "第8–9月", "高级分析与模拟对比", "CG3 弱透镜、CG4 瞬变/多信使、CG5 模拟交叉校验完成"],
        ["Phase 4", "第10–12月", "全局归并、可视化与论文", "全局密度场、功率谱、晕目录、科学可视化、论文撰写"],
    ]
    story.append(tbl(data_mile, colw=[3.0*cm,3.0*cm,5.0*cm,5.5*cm]))
    story.append(P("表4: 项目里程碑", CAP))

    # ========== 10. 参考文献 ==========
    story.append(P("10. 参考文献", H1))
    refs = [
        "Bauer, M., et al. (2004). BOINC: A Platform for Volunteer Computing. IJGU Computing.",
        "Springel, V., et al. (2005). The Millennium Simulation. Nature, 435, 629.",
        "Springel, V., et al. (2008). Resolving Cosmic Structure Formation with the Millennium-II Simulation. MNRAS, 391, 1685.",
        "Cole, N., et al. (2011). Evolutionary Algorithms on Volunteer Computing Platforms. SCI 269.",
        "Schaye, J., et al. (2015). The EAGLE simulations of galaxy formation. MNRAS, 450, 1937.",
        "Nelson, D., et al. (2015). The Illustris Simulation. MNRAS, 429, 3353.",
        "Kaiser, N., & Squires, G. (1993). Mapping the Dark Matter with Weak Gravitational Lensing. ApJ, 404, 441.",
        "Landy, S. D., & Szalay, A. S. (1993). Bias and Variance of Angular Correlation Functions. ApJ, 412, 64.",
        "Greengard, L., & Rokhlin, V. (1987). A Fast Algorithm for Particle Simulations. J. Comput. Phys., 73, 325.",
        "Barnes, J., & Hut, P. (1986). A Hierarchical O(N log N) Force-Calculation Algorithm. Nature, 324, 446.",
        "Desell, T., et al. (2011). Evolving N-Body Simulations using Volunteer Computing. IPDPSW.",
        "Sarbadhicary, S., et al. (2014). MilkyWay@home: Harnessing volunteer computers to constrain dark matter. arXiv:1411.6003.",
        "Du, W., et al. (2025). AI-Driven Reconstruction of Large-Scale Structure. arXiv:2504.06309.",
        "Manticore Project II (2026). Bayesian digital twins of cosmic structure. arXiv:2606.10020.",
        "Gaia Collaboration (2023). Gaia DR3. A&A, 674, A1.",
        "DESI Collaboration (2025). DESI DR1. Fermilab News, 2025/03.",
        "Euclid Consortium (2025). Euclid Q1 Data Release. ESA ESDC.",
        "SDSS Collaboration (2024). SDSS DR19 Data Volume. sdss.org/dr19.",
        "TNG Project (2024). IllustrisTNG Data Access. tng-project.org.",
        "HTCondor Team (2025). HTCondor Documentation. htcondor.org.",
    ]
    for i, r in enumerate(refs, 1):
        story.append(P(f"[{i}] {r}", BODY))

    doc.build(story)
    print(f"报告已生成: {out_pdf}")


if __name__ == "__main__":
    build_report(Path(r"d:\N体仿真开发\universe\docs\project_plan_report.pdf"),
                 Path(r"d:\N体仿真开发\universe\docs\flops_estimate.json"))
