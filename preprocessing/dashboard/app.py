"""数据前处理平台 Web 仪表盘。

功能：
- 查看 HEALPix 预分片（Parquet 分区）覆盖情况（/api/shards）
- 设定数据库本地根目录并触发原始目录扫描（v1 兼容流程，新管线以分片为准）
- 生成计算任务与 HTCondor submit/DAG 文件
- 热力图展示任务生成/执行进度 (subgroup × tile)
- 自检自验：错误任务重新生成；缺失数据库生成补全下载清单

启动: python -m preprocessing.dashboard.app --workdir work --db state.sqlite
"""
import argparse
import json
from pathlib import Path

from flask import Flask, jsonify, render_template, request

from common.db import DB
from preprocessing.scanner import scan_directory
from preprocessing.selfcheck import (redownload_manifest, regenerate_errors,
                                     validate_tasks)
from preprocessing.taskgen import generate_tasks, write_htcondor_files

ROOT = Path(__file__).resolve().parent.parent.parent

app = Flask(__name__)
STATE = {}


def get_db():
    return DB(STATE["db"])


def _shard_summaries():
    """读取 data/shards 下各巡天的 _metadata.json，返回分片覆盖摘要。"""
    root = Path(STATE.get("shards_root") or (ROOT / "data" / "shards"))
    out = []
    if not root.exists():
        return out
    for meta_path in sorted(root.glob("*/_metadata.json")):
        try:
            m = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        out.append({
            "survey": m.get("survey", meta_path.parent.name),
            "nside": m.get("nside"),
            "n_partitions": m.get("n_partitions"),
            "n_rows": m.get("n_rows"),
            "columns": m.get("columns", []),
        })
    return out


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/status")
def api_status():
    db = get_db()
    files = db.files()
    by_survey = {}
    for f in files:
        by_survey[f["survey"]] = by_survey.get(f["survey"], 0) + 1
    return jsonify({
        "n_files": len(files),
        "by_survey": by_survey,
        "task_counts": db.status_counts(),
        "data_root": STATE.get("data_root", ""),
        "shards": _shard_summaries(),
    })


@app.route("/api/shards")
def api_shards():
    """HEALPix 预分片覆盖情况（新管线的数据源真相，替代旧目录扫描）。"""
    return jsonify({"shards": _shard_summaries()})


@app.route("/api/scan", methods=["POST"])
def api_scan():
    root = request.json.get("root") or STATE.get("data_root")
    if not root or not Path(root).exists():
        return jsonify({"ok": False, "error": f"目录不存在: {root}"}), 400
    STATE["data_root"] = root
    db = get_db()
    stats = scan_directory(root, db, ROOT / "configs" / "surveys.yaml")
    return jsonify({"ok": True, "stats": stats})


@app.route("/api/generate", methods=["POST"])
def api_generate():
    db = get_db()
    created = generate_tasks(db, ROOT / "configs" / "compute_groups.yaml", STATE["workdir"])
    dag = write_htcondor_files(db, STATE["workdir"])
    return jsonify({"ok": True, "n_tasks": len(created), "dag": str(dag)})


@app.route("/api/heatmap")
def api_heatmap():
    """热力图数据：tile 轴直接取任务表中的 HEALPix tile_id（hpXXXXXX/global），
    与 v2.0 分片管线一致，不再依赖旧柱面 tile 配置。"""
    db = get_db()
    matrix = db.progress_matrix()
    subgroups = sorted(matrix.keys())
    tiles = sorted({tile for sg in matrix.values() for tile in sg})
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
    status = request.args.get("status")
    tasks = db.tasks(status=status)
    for t in tasks:
        t.pop("input_files", None)
    return jsonify({"tasks": tasks[:2000], "total": len(tasks)})


@app.route("/api/selfcheck", methods=["POST"])
def api_selfcheck():
    db = get_db()
    bad = validate_tasks(db)
    return jsonify({"ok": True, "n_bad": len(bad), "bad": bad[:50]})


@app.route("/api/regenerate", methods=["POST"])
def api_regenerate():
    db = get_db()
    info = regenerate_errors(db, ROOT / "configs" / "compute_groups.yaml", STATE["workdir"])
    return jsonify({"ok": True, **info})


@app.route("/api/redownload", methods=["POST"])
def api_redownload():
    db = get_db()
    out = Path(STATE["workdir"]) / "redownload_manifest.md"
    missing = redownload_manifest(db, ROOT / "configs" / "surveys.yaml", out)
    return jsonify({"ok": True, "missing": missing, "manifest": str(out)})


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
    ap.add_argument("--workdir", default="work")
    ap.add_argument("--db", default="state_pre.sqlite")
    ap.add_argument("--data-root", default="")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8601)
    args = ap.parse_args()
    STATE.update(workdir=args.workdir, db=args.db, data_root=args.data_root)
    app.run(host=args.host, port=args.port, debug=False)


if __name__ == "__main__":
    main()
