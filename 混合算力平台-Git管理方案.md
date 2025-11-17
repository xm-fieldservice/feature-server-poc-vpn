# 混合算力平台 Git 管理方案

## 方案概述

针对服务器和本地都有代码的混合算力平台，推荐采用 **单仓库多环境配置** 的 Git 管理策略，保持代码统一管理的同时支持环境差异。

## 目录结构设计

```
混合算力平台/
├── .git/                          # Git 仓库
├── src/
│   ├── common/                    # 共享代码
│   │   ├── contracts/            # 接口契约
│   │   ├── utils/                # 通用工具
│   │   └── types/                # 类型定义
│   ├── cloud/                    # 云端服务代码
│   │   ├── control-plane/        # 控制平面
│   │   ├── data-plane/          # 数据平面
│   │   └── deployment/          # 云端部署配置
│   ├── local/                    # 本地节点代码
│   │   ├── mcp-servers/         # MCP 服务
│   │   ├── gpu-manager/         # GPU 管理
│   │   └── deployment/          # 本地部署配置
│   └── shared/                   # 共享组件
│       ├── monitoring/          # 监控组件
│       └── security/            # 安全组件
├── configs/
│   ├── cloud/                   # 云端配置文件
│   ├── local/                   # 本地配置文件
│   └── templates/               # 配置模板
├── docs/                        # 文档
├── scripts/                     # 部署脚本
├── .gitignore                   # Git 忽略规则
├── README.md                    # 项目说明
└── docker-compose.yml           # 容器编排
```

## Git 分支策略

### 主分支
- `main` - 生产就绪代码，对应技术底座冻结版本
- `develop` - 开发集成分支

### 支持分支
- `feature/*` - 功能开发分支
- `release/*` - 发布准备分支  
- `hotfix/*` - 紧急修复分支
- `env/cloud` - 云端环境特定配置
- `env/local` - 本地环境特定配置

## 环境配置管理

### 1. 配置分离策略
```bash
# 通用配置（提交到Git）
configs/templates/
├── mcp-server.template.yaml
├── nats.template.yaml
├── minio.template.yaml
└── monitoring.template.yaml

# 环境特定配置（.gitignore）
configs/local/
configs/cloud/
```

### 2. 环境变量管理
```bash
# .env.template （提交到Git）
CLOUD_NATS_URL=nats://nats:4222
LOCAL_NATS_URL=nats://localhost:4222
MINIO_ENDPOINT=minio:9000
OBJECT_STORAGE_BUCKET=hybrid-compute

# 实际环境文件（.gitignore）
.env.cloud    # 云端环境变量
.env.local    # 本地环境变量
```

## Git 工作流程

### 开发流程
1. **功能开发**
   ```bash
   git checkout develop
   git checkout -b feature/mcp-llm-inference
   # 开发完成后
   git push origin feature/mcp-llm-inference
   # 创建 Pull Request 到 develop
   ```

2. **环境特定修改**
   ```bash
   # 云端特定修改
   git checkout env/cloud
   # 本地特定修改  
   git checkout env/local
   ```

### 部署流程
1. **云端部署**
   ```bash
   git checkout main
   git merge develop
   # 使用 configs/cloud/ 配置部署
   ```

2. **本地节点部署**
   ```bash
   git checkout main  
   # 使用 configs/local/ 配置部署
   ```

## Git 忽略规则

```gitignore
# 环境特定配置
configs/cloud/
configs/local/
.env.cloud
.env.local

# 敏感信息
*.key
*.pem
*.cert
secrets/

# 运行时文件
logs/
tmp/
cache/

# 大文件
models/
datasets/
*.bin
*.safetensors
```

## 多环境同步策略

### 1. 配置模板化
```yaml
# configs/templates/mcp-server.template.yaml
server:
  port: ${MCP_PORT:-2470}
  health_check: /health
  metrics: /metrics
  
gpu:
  enabled: ${GPU_ENABLED:-false}
  device_ids: ${GPU_DEVICES:-0}
```

### 2. 部署脚本
```bash
#!/bin/bash
# scripts/deploy-local.sh
cp configs/templates/*.template.yaml configs/local/
envsubst < configs/local/mcp-server.template.yaml > configs/local/mcp-server.yaml
docker-compose -f docker-compose.local.yml up -d
```

## 最佳实践

### 1. 代码共享原则
- 业务逻辑放在 `src/common/`
- 环境特定代码放在 `src/cloud/` 或 `src/local/`
- 接口契约统一在 `src/common/contracts/`

### 2. 配置管理
- 模板文件提交到 Git
- 实际配置通过环境变量生成
- 敏感信息使用 secrets 管理

### 3. 版本对齐
- 使用语义化版本控制
- 保持云端和本地代码版本同步
- 通过 Git tags 标记重要版本

## 实施建议

### 阶段1：基础设置（1天）
1. 创建仓库并设置目录结构
2. 配置 .gitignore 和分支策略
3. 建立基础配置模板

### 阶段2：环境配置（2天）  
1. 设置云端和本地环境配置
2. 编写部署脚本
3. 测试多环境部署

### 阶段3：自动化（3天）
1. 设置 CI/CD 流水线
2. 配置自动化测试
3. 建立监控和回滚机制

这个方案确保了代码的统一管理，同时支持云端和本地环境的差异配置，符合混合算力平台的架构需求。