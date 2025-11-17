# 混合算力融合平台

本仓库是混合算力融合平台在 **DeepSeek Reasoning + 技术底座文档** 方向的精简实现，用于本地 / 服务器侧 PoC 以及 VPN 对向开发。

## 仓库定位

- 面向 **DeepSeek Reasoning 服务 + MinIO/S3 + 基础监控** 的最小可运行环境。
- 不包含完整的云端控制平面实现，只保留与技术底座 v1.1 直接相关的代码与文档：
  - `src/local/deepseek-reasoning-server.py`：FastAPI 推理服务（含 Prometheus 指标、并发控制）。
  - `scripts/*.py`：本地 E2E、基准压测、调度轮询等脚本。
  - `docs/requirements/*.md`：技术底座、P1P2 纪要、最终需求文档。
  - `docs/reports/*.md`：DeepSeek P0 对齐与运行记录（含断点现场保护与 Checklist）。

推荐将本仓库视为 **feature/server-poc-vpn 对向开发分支** 的代码源，在本地和服务器上通过 Git 进行双向协作。

## 目录结构（精简版）

```text
projects_share_structure_vpn/
├── src/
│   ├── common/                # 合同/SDK 蓝图（Job、Storage、MCP 等）
│   ├── local/                 # 本地 DeepSeek 推理服务及相关代码
│   ├── cloud/                 # 预留：控制平面（当前为空，实现待定）
│   └── shared/                # 预留：共享组件（当前为空）
├── scripts/                   # 本地 E2E / Bench / Scheduler 等脚本
├── docs/
│   ├── requirements/          # 技术底座、P1P2 纪要、最终需求
│   └── reports/               # DeepSeek P0 对齐与运行记录
├── .github/workflows/ci.yml   # CI 门禁（当前主要针对主干分支）
├── docker-compose.local.yml   # 本地环境 Blueprint（后续可收敛到 P0 版本）
├── docker-compose.production.yml
├── .gitignore
└── README.md
```

## 快速开始（本地环境）

### 1. 基本环境要求

- 操作系统：Windows / Linux / macOS 均可（示例命令以 Windows PowerShell 为主）。
- Python：3.10+（建议使用虚拟环境）。
- 可选：Docker（用于快速拉起本地 MinIO）。

### 2. 创建虚拟环境并安装依赖

```powershell
cd D:\AI-Projects\projects_share_structure_vpn

python -m venv venv
./venv/Scripts/Activate.ps1   # PowerShell 中激活虚拟环境

pip install -r src/local/requirements.txt
```

> 如果你在 Linux / macOS：
> ```bash
> python -m venv venv
> source venv/bin/activate
> pip install -r src/local/requirements.txt
> ```

### 3. 启动 DeepSeek Reasoning 服务（本地）

```powershell
cd D:\AI-Projects\projects_share_structure_vpn

$env:SERVER_HOST = "0.0.0.0"
$env:SERVER_PORT = "2470"
$env:MAX_CONCURRENCY = "1"      # P0 基线：单并发，避免 429

python src/local/deepseek-reasoning-server.py
```

服务启动后可访问：

- 健康检查：`http://localhost:2470/health`
- 推理接口：`POST http://localhost:2470/reasoning`
- Prometheus 指标：`http://localhost:2470/metrics`

### 4. 本地 MinIO（可选，推荐）

如果你需要完成 MinIO 端到端验证（例如使用 `e2e-deepseek-job.py` 和 `scheduler-poller.py`），可以用 Docker 快速启动一个本地 MinIO：

```powershell
docker run -d --name hybrid-minio `
  -p 9000:9000 -p 9001:9001 `
  -e MINIO_ROOT_USER=minioadmin `
  -e MINIO_ROOT_PASSWORD=minioadmin `
  -v D:\minio-data:/data `
  minio/minio server /data --console-address ":9001"
```

默认：
- API 地址：`http://localhost:9000`
- 控制台：`http://localhost:9001`
- 账号：`minioadmin` / `minioadmin`

你可以根据 `docs/reports/2025-11-16-03-DeepSeek-P0对齐与下一步工作记录.md` 中的说明创建 Bucket（如 `hybrid-compute`）并准备测试数据。

### 5. 使用脚本进行验证

在虚拟环境激活的前提下：

```powershell
cd D:\AI-Projects\projects_share_structure_vpn

# 1）接口连通性与基本功能
python scripts/test-deepseek-reasoning.py

# 2）并发基准测试与 P0 阈值验证（MAX_CONCURRENCY=1）
python scripts/bench-reasoning.py

# 3）MinIO 端到端验证（上传输入 → 调用推理 → 回写输出）
python scripts/e2e-deepseek-job.py

# 4）调度轮询（scheduler-poller）模拟 Job 合同写回 result.json
python scripts/scheduler-poller.py
```

每个脚本的详细行为和关键参数说明，可参考对应脚本头部注释以及 DeepSeek P0 对齐文档中的描述。

## 服务器侧使用（VPN 对向开发）

在服务器侧（例如内网 GPU 服务器）上：

```bash
git clone git@github.com:xm-fieldservice/feature-server-poc-vpn.git
cd feature-server-poc-vpn
git checkout feature/server-poc-vpn

python -m venv venv
source venv/bin/activate
pip install -r src/local/requirements.txt

export SERVER_HOST="0.0.0.0"
export SERVER_PORT="2470"
export MAX_CONCURRENCY="1"

python src/local/deepseek-reasoning-server.py
```

通过 VPN 将服务器侧推理服务与本地资源（MinIO / Windows 桌面等）连接起来时请注意：

- 端口与网段规划要符合技术底座中 **网络平面 + 端口 ACL** 的约束。
- 建议只开放必要端口（如 2470、9000/9001、9090/3000 等），并在 VPN / 防火墙中限制访问源。
- 详细的 VPN 规划与联调 Checklist，可参考：
  - `docs/reports/2025-11-16-03-DeepSeek-P0对齐与下一步工作记录.md` 中的 *服务器侧 PoC + VPN 联调 Checklist（离线版）* 小节。

## 文档导航

- 技术底座设计（补齐版 v1.1）：
  - `docs/requirements/2025-11-15-13-混合算力融合平台-技术底座-初稿v1.1-补齐版.md`
- 最终需求文档（GPT-5 版）：
  - `docs/requirements/2025-11-15-12-混合算力融合平台-最终需求文档-gpt5版.md`
- 服务器共享算力 P1/P2 阶段性纪要：
  - `docs/requirements/2025-11-16-01-服务器共享算力-技术底座-P1P2阶段性工作纪要.md`
- DeepSeek P0 对齐与运行记录（含断点现场保护、Checklist）：
  - `docs/reports/2025-11-16-03-DeepSeek-P0对齐与下一步工作记录.md`

## 许可证

MIT License