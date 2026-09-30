"""结果回收器：扫描结果目录中的 manifest.json，
校验sha256完整性，登记到SQLite并更新任务状态。"""
import json
from pathlib import Path

from common.db import DB
from common.utils import sha256_file


def collect_results(db: DB, results_root):
    results_root = Path(results_root)
    stats = {"received": 0, "verified": 0, "corrupt": 0, "orphan": 0}
    for mp in sorted(results_root.rglob("*.manifest.json")):
        man = json.loads(mp.read_text(encoding="utf-8"))
        task_id = man["task_id"]
        stats["received"] += 1
        rp = Path(man["result"])
        ok = rp.exists() and sha256_file(rp) == man.get("sha256")
        task = db.task(task_id)
        if task is None:
            stats["orphan"] += 1
            continue
        parts = task_id.split("__")
        sg = parts[0]
        tile = parts[1] if len(parts) > 1 else ""
        cg = next((k for k, v in {"SG1": "CG1", "SG2": "CG2", "SG3": "CG3",
                                  "SG4": "CG4", "SG5": "CG5"}.items() if sg.startswith(k)), "")
        if ok:
            stats["verified"] += 1
            db.add_result({"task_id": task_id, "cgroup": cg, "subgroup": sg,
                           "tile_id": tile, "path": str(rp),
                           "checksum": man.get("sha256"), "metrics": man.get("metrics", {}),
                           "status": "verified"})
            db.set_task_status(task_id, "done", result_path=str(rp), checksum=man.get("sha256"))
        else:
            stats["corrupt"] += 1
            db.add_result({"task_id": task_id, "cgroup": cg, "subgroup": sg,
                           "tile_id": tile, "path": str(rp),
                           "checksum": man.get("sha256"), "metrics": man.get("metrics", {}),
                           "status": "corrupt"})
            db.set_task_status(task_id, "error", error="结果校验失败(sha256不匹配或文件丢失)")
    return stats
