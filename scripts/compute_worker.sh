#!/bin/bash
# HTCondor 计算节点工作程序包装脚本（Linux/macOS）
# HTCondor submit 文件中 executable 指向此脚本
# $1 = task_id

set -e

VENV="$(cd "$(dirname "$0")/../../.." && pwd)/.venv"
PYTHON="$VENV/bin/python"
PROJECT="$(cd "$(dirname "$0")/../.." && pwd)"

if [ ! -f "$PYTHON" ]; then
    echo "[ERROR] 虚拟环境不存在: $PYTHON"
    exit 1
fi

cd "$PROJECT"
"$PYTHON" -m compute.worker "$1" --workdir work --results results
