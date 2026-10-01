"""数据后处理平台 Web 仪表盘。

功能：
- 收集计算节点返回的结果(manifest校验)
- 执行科学归并(密度场/功率谱/两点相关函数/晕目录)
- 生成可视化图像
- 热力图展示各子组/分片的计算完成进度
- 全局统计数据

启动: python -m postprocessing.dashboard.app --results results --db state_pre.sqlite --workdir work
"""
import argparse
import json
from pathlib import Path

from flask import Flask, jsonify, render_template, request

from common.db import DB
from common.tiling import all_tiles, load_tiling
from postprocessing.collector import collect_results
from postprocessing.reducer import (global_density_and_power, merge_clustering,
                                    merge_halo_catalog)
from postprocessing.visualize import make_all_plots

ROOT = Path(__file__).resolve().parent.parent.parent

app = Flask(__name__)
STATE = {}


def get_db():
    return DB(STATE["db"])


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/status")
def api_status():
    db = get_db()
    tasks = db.tasks()
    counts = db.status_counts()
    res = db.results()
    metrics = {}
    for r in res:
        if r.get("metrics_json"):
            m = json.loads(r["metrics_json"])
            for kk in ["n_objects", "n_halos", "n_tiles", "n_points"]:
                if kk in m:
                    metrics[kk] = metrics.get(kk, 0) + m[kk]
    return jsonify({
        "n_tasks": len(tasks),
        "counts": counts,
        "n_results": len(res),
        "verified": len([x for x in res if x["status"] == "verified"]),
        "metrics": metrics,
    })


@app.route("/api/collect", methods=["POST"])
def api_collect():
    db = get_db()
    stats = collect_results(db, STATE["results"])
    return jsonify({"ok": True, "stats": stats})


@app.route("/api/merge", methods=["POST"])
def api_merge():
    db = get_db()
    outdir = Path(STATE["results"]) / "merged"
    outdir.mkdir(parents=True, exist_ok=True)
    info = {}
    info["density"] = global_density_and_power(db, outdir / "global_density.h5", ngrid=96)
    info["clustering"] = merge_clustering(db, outdir / "global_xi.h5")
    info["halos"] = merge_halo_catalog(db, outdir / "halos.csv")
    return jsonify({"ok": True, "info": info})


@app.route("/api/visualize", methods=["POST"])
def api_visualize():
    h5 = Path(STATE["results"]) / "merged" / "global_density.h5"
    if not h5.exists():
        return jsonify({"ok": False, "error": "缺少全局密度场结果(global_density.h5)，请先执行科学归并"}), 400
    out = Path(STATE["results"]) / "plots"
    out.mkdir(parents=True, exist_ok=True)
    made = make_all_plots(h5, out)
    return jsonify({"ok": True, "plots": made})


@app.route("/api/heatmap")
def api_heatmap():
    db = get_db()
    ra_bins, dec_bins = load_tiling(ROOT / "configs" / "compute_groups.yaml")
    tiles = all_tiles(ra_bins, dec_bins)
    matrix = db.progress_matrix()
    subgroups = sorted(matrix.keys())
    data = []
    for sg in subgroups:
        for tile in tiles:
            st = matrix[sg].get(tile)
            if st is not None:
                data.append({"subgroup": sg, "tile": tile, "status": st})
    return jsonify({"subgroups": subgroups, "tiles": tiles, "data": data})


@app.route("/api/tasks")
def api_tasks():
    db = get_db()
    tasks = db.tasks()
    return jsonify({"tasks": tasks[:2000], "total": len(tasks)})


@app.route("/metrics")
def metrics():
    """Prometheus 文本格式指标端点：任务状态计数/完成率/僵尸任务数。"""
    import time
    db = get_db()
    counts = db.status_counts()
    lines = ["# HELP universe_tasks 任务状态计数",
             "# TYPE universe_tasks gauge"]
    total = 0
    for st, n in sorted(counts.items()):
        lines.append(f'universe_tasks{{status="{st}"}} {n}')
        total += n
    done = counts.get("done", 0)
    lines += ["# HELP universe_tasks_total 任务总数",
              "# TYPE universe_tasks_total gauge",
              f"universe_tasks_total {total}",
              "# HELP universe_completion_ratio 完成率",
              "# TYPE universe_completion_ratio gauge",
              f"universe_completion_ratio {(done/total if total else 0):.4f}"]
    stale = len(db.stale_tasks())
    lines += ["# HELP universe_stale_tasks 心跳超时任务数",
              "# TYPE universe_stale_tasks gauge",
              f"universe_stale_tasks {stale}"]
    return "\n".join(lines) + "\n", 200, {"Content-Type": "text/plain; charset=utf-8"}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--results", default="results")
    ap.add_argument("--db", default="state_pre.sqlite")
    ap.add_argument("--workdir", default="work")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8602)
    args = ap.parse_args()
    STATE.update(results=args.results, db=args.db, workdir=args.workdir)
    app.run(host=args.host, port=args.port, debug=False)


if __name__ == "__main__":
    main()
