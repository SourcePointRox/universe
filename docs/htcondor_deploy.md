# HTCondor 部署指南

## 1. 安装 HTCondor

在提交节点（运行仪表盘的服务器）和所有计算节点上安装 HTCondor：
- Windows: https://research.cs.wisc.edu/htcondor/downloads/
- Linux: `sudo apt install htcondor` 或 `sudo yum install condor`

## 2. 配置集群

### 提交节点（Central Manager）

```bash
# /etc/condor/condor_config.local
CONDOR_HOST = $(FULL_HOSTNAME)
COLLECTOR_NAME = Universe3D
ALLOW_WRITE = *
ALLOW_READ = *
```

### 计算节点

```bash
# /etc/condor/condor_config.local
CONDOR_HOST = <提交节点IP>
ALLOW_WRITE = *
ALLOW_READ = *
# 可选: 限制资源
NUM_SLOTS = 4
MEMORY = 65536  # MB
```

## 3. 启动服务

```bash
# 提交节点
sudo systemctl start condor
condor_status  # 验证节点可见

# 计算节点
sudo systemctl start condor
```

## 4. 提交工作流

```bash
# 在前处理平台生成任务后
cd universe
python -m preprocessing.dashboard.app --data-root /path/to/astro_data

# 平台自动生成 workflow.dag
condor_submit_dag work/workflow.dag

# 监控
condor_q -dag
condor_status
```

## 5. 任务规格

每个任务的 submit 文件（`work/tasks/<CG>/<SG>/<tile>/task.sub`）包含：
- `executable` → `scripts/compute_worker.sh` (Linux) 或 `compute_worker.bat` (Windows)
- `arguments` → 任务 ID（如 `SG1.1__r00_d00`）
- `request_cpus/memory/disk` → 资源请求
- `should_transfer_files` → 输入文件传输

## 6. 故障恢复

- DAGMan Rescue: `condor_submit_dag -Rescue work/workflow.dag.rescue001`
- 单任务重跑: 删除结果文件 → 前处理平台"重新生成错误任务"
