# 混合算力融合平台

基于服务器和本地GPU资源的分布式计算平台，实现算力共享和任务调度。

## 项目概述

本项目是一个混合算力融合平台，结合云端服务器和本地高性能桌面资源，为AI推理、模型训练等计算密集型任务提供弹性算力支持。

## 技术架构

### 五大平面架构
- **网络平面**: VPN (Tailscale/Headscale 或 WireGuard)
- **控制平面**: NATS JetStream 或 Redis Streams
- **数据平面**: MinIO/S3 对象存储
- **执行平面**: MCP Server 微服务
- **观测安全平面**: 统一监控和安全治理

### 核心特性
- 🚀 分布式任务调度和队列管理
- 🔒 安全的VPN网络连接
- 📊 统一的观测和监控
- 💾 智能数据分层管理
- 🔧 标准化的MCP工具协议

## 项目结构

```
混合算力平台/
├── src/                    # 源代码
│   ├── common/            # 共享代码
│   ├── cloud/             # 云端服务
│   ├── local/             # 本地节点
│   └── shared/            # 共享组件
├── configs/               # 配置管理
│   ├── templates/         # 配置模板
│   ├── cloud/             # 云端配置 (.gitignore)
│   └── local/             # 本地配置 (.gitignore)
├── docs/                  # 项目文档
├── scripts/               # 部署脚本
└── .gitignore            # Git忽略规则
```

## 快速开始

### 环境要求
- Docker & Docker Compose
- Python 3.8+
- GPU支持 (本地节点)

### 部署步骤

1. **本地节点部署**
   ```bash
   # 复制配置模板
   cp configs/templates/*.template.yaml configs/local/
   
   # 设置环境变量
   cp .env.template .env.local
   # 编辑 .env.local 文件
   
   # 启动服务
   docker-compose -f docker-compose.local.yml up -d
   ```

2. **云端服务部署**
   ```bash
   # 复制配置模板  
   cp configs/templates/*.template.yaml configs/cloud/
   
   # 设置环境变量
   cp .env.template .env.cloud
   # 编辑 .env.cloud 文件
   
   # 启动服务
   docker-compose -f docker-compose.cloud.yml up -d
   ```

## 开发指南

### 分支策略
- `main` - 生产就绪代码
- `develop` - 开发集成
- `feature/*` - 功能开发
- `env/cloud` - 云端环境配置
- `env/local` - 本地环境配置

### 代码规范
- 共享代码放在 `src/common/`
- 环境特定代码放在 `src/cloud/` 或 `src/local/`
- 配置模板化，环境变量注入

## 文档

- [技术底座设计](./docs/requirements/2025-11-15-13-混合算力融合平台-技术底座-初稿v1.1-补齐版.md)
- [Git管理方案](./混合算力平台-Git管理方案.md)
- [架构设计](./混合算力融合平台-技术底座设计融合版.md)

## 许可证

MIT License