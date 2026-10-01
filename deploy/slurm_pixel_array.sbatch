#!/bin/bash
#SBATCH --job-name=universe-pixel
#SBATCH --array=0-3071%200        # 每个数组元素一个 HEALPix 像素任务，限200并发
#SBATCH --cpus-per-task=4
#SBATCH --mem=8G
#SBATCH --time=01:00:00
#SBATCH --output=logs/slurm_%A_%a.out

# 像素任务批量提交模板：
# 1) 先用 preprocessing.taskgen 生成任务列表文件 pixel_tasks.txt（每行一个 task_id）
# 2) sbatch deploy/slurm_pixel_array.sbatch

TASK_ID=$(sed -n "$((SLURM_ARRAY_TASK_ID+1))p" work/pixel_tasks.txt)
echo "[slurm] node=$(hostname) task=$TASK_ID"

singularity run --bind "$PWD/data:/data,$PWD/work:/work,$PWD/results:/results" \
    universe.sif "$TASK_ID" \
    --workdir /work --results /results \
    --db /work/state.sqlite --owner "slurm-${SLURM_JOB_ID}-${SLURM_ARRAY_TASK_ID}"
