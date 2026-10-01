#!/bin/bash
#SBATCH --job-name=universe-global
#SBATCH --cpus-per-task=8
#SBATCH --mem=32G
#SBATCH --time=12:00:00
#SBATCH --output=logs/slurm_global_%j.out

# 全局任务（Wiener 重建 / 2PCF / FoF / N-body）在全部 pixel 任务完成后提交：
#   sbatch --dependency=afterok:<pixel_array_jobid> deploy/slurm_global.sbatch SG2.1__global
#   sbatch --dependency=afterok:<pixel_array_jobid> deploy/slurm_global.sbatch SG5.1__global

TASK_ID=${1:?用法: sbatch deploy/slurm_global.sbatch <task_id>}

singularity run --bind "$PWD/data:/data,$PWD/work:/work,$PWD/results:/results" \
    universe.sif "$TASK_ID" \
    --workdir /work --results /results \
    --db /work/state.sqlite --owner "slurm-${SLURM_JOB_ID}"
