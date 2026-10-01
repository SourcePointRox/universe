# Universe 3D Modeling — 本地宇宙 z<=0.15 三维建模平台
# 单容器包含全部依赖；HTCondor/Slurm 集群中作为执行节点镜像使用
FROM python:3.11-slim

LABEL org.opencontainers.image.title="universe" \
      org.opencontainers.image.description="Local Universe (z<=0.15, ~1 Gpc^3) 3D modeling pipeline"

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# 默认数据/工作目录（运行时挂载）
VOLUME ["/data", "/work", "/results"]
ENV UNIVERSE_LOG_DIR=/work/logs

# 默认入口：worker（task_id 由调度器传入）
ENTRYPOINT ["python", "-m", "compute.worker"]
CMD ["--help"]
