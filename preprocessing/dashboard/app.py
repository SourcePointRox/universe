"""数据前处理平台 Web 仪表盘。

功能：
- 设定数据库本地根目录并触发扫描
- 查看扫描文件统计与数据库覆盖情况
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
from common.tiling import all_tiles, load_tiling
from preprocessing.scanner import scan_directory
from preprocessing.selfcheck import (redownload_manifest, regenerate_errors,
                                     validate_tasks)
from preprocessing.taskgen import generate_tasks, write_htcondor_files

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
    files = db.files()
    by_survey = {}
    for f in files:
        by_survey[f["survey"]] = by_survey.get(f["survey"], 0) + 1
    return jsonify({
        "n_files": len(files),
        "by_survey": by_survey,
        "task_counts": db.status_counts(),
        "data_root": STATE.get("data_root", ""),
    })


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
