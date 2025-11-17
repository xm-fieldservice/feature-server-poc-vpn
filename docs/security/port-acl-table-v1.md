# 混合算力平台 - 端口 ACL 表 v1

## 概述

本文档定义了混合算力平台的端口访问控制列表（ACL）v1版本，作为P0阶段的安全标准冻结文档。

## 1. 网络架构原则

### 1.1 安全边界
- **VPN优先**: 所有服务默认仅VPN内网可达
- **最小权限**: 按需开放端口，默认拒绝所有
- **源IP限制**: 关键服务严格限制源IP范围
- **纵深防御**: 多层安全控制

### 1.2 网络平面
- **控制平面**: 任务调度、队列管理 (NATS/Redis)
- **数据平面**: 对象存储、数据传输 (MinIO)
- **执行平面**: MCP服务、计算节点 (MCP Server)
- **管理平面**: 监控、日志、管理 (Prometheus/Loki/SSH)

## 2. 端口 ACL 详细规则

### 2.1 控制平面

```yaml
# NATS 消息队列
- name: nats-primary
  protocol: tcp
  port: 4222
  direction: Ingress
  source: vpn_cidr_only
  description: "NATS核心消息端口"
  action: allow

# Redis 缓存与状态
- name: redis-primary  
  protocol: tcp
  port: 6379
  direction: Ingress
  source: vpn_cidr_only
  description: "Redis主端口"
  action: allow

- name: redis-replica
  protocol: tcp  
  port: 6380
  direction: Ingress
  source: vpn_cidr_only
  description: "Redis副本端口"
  action: allow
```

### 2.2 数据平面

```yaml
# MinIO 对象存储
- name: minio-api
  protocol: tcp
  port: 9000
  direction: Ingress
  source: vpn_cidr_only
  description: "MinIO API端口"
  action: allow

- name: minio-console
  protocol: tcp
  port: 9001
  direction: Ingress  
  source: vpn_cidr_only
  description: "MinIO控制台端口"
  action: allow
```

### 2.3 执行平面

```yaml
# MCP 服务网关
- name: mcp-gateway-range
  protocol: tcp
  port_range: "2470-2499"
  direction: Ingress
  source: vpn_cidr_only
  description: "MCP服务端口范围"
  action: allow

# 具体MCP服务分配
- name: mcp-llm-inference
  protocol: tcp
  port: 2470
  direction: Ingress
  source: vpn_cidr_only  
  description: "LLM推理服务"
  action: allow

- name: mcp-image-generation
  protocol: tcp
  port: 2471
  direction: Ingress
  source: vpn_cidr_only
  description: "图像生成服务"  
  action: allow

- name: mcp-data-processing
  protocol: tcp
  port: 2472
  direction: Ingress
  source: vpn_cidr_only
  description: "数据处理服务"
  action: allow
```

### 2.4 观测平面

```yaml
# Prometheus 指标
- name: prometheus-metrics
  protocol: tcp
  port: 9090
  direction: Ingress
  source: vpn_cidr_only
  description: "Prometheus指标收集"
  action: allow

# OpenTelemetry 追踪
- name: otel-collector
  protocol: tcp  
  port: 4317
  direction: Ingress
  source: vpn_cidr_only
  description: "OpenTelemetry追踪数据"
  action: allow

# Loki 日志
- name: loki-logging
  protocol: tcp
  port: 3100
  direction: Ingress
  source: vpn_cidr_only
  description: "Loki日志收集"
  action: allow

# Grafana 仪表板 (可选)
- name: grafana-dashboard
  protocol: tcp
  port: 3000
  direction: Ingress
  source: vpn_cidr_only
  description: "Grafana监控仪表板"
  action: allow
```

### 2.5 管理平面

```yaml
# VPN 连接
- name: wireguard-vpn
  protocol: udp
  port: 51820
  direction: Ingress
  source: vpn_peers_cidr
  description: "WireGuard VPN连接"
  action: allow

# SSH 管理 (严格限制)
- name: ssh-primary
  protocol: tcp
  port: 22
  direction: Ingress
  source: admin_whitelist_ip
  description: "SSH主端口 - 仅管理员IP"
  action: allow

- name: ssh-alternative
  protocol: tcp  
  port: 2222
  direction: Ingress
  source: admin_whitelist_ip
  description: "SSH备用端口 - 仅管理员IP"
  action: allow
```

### 2.6 出站规则

```yaml
# 默认出站规则
- name: egress-default
  direction: Egress
  destination: vpn_cidr_only
  description: "默认出站到VPN网络"
  action: allow

# 必要的公网出站 (如需要)
- name: egress-dns
  protocol: udp
  port: 53
  direction: Egress  
  destination: 0.0.0.0/0
  description: "DNS解析"
  action: allow

- name: egress-ntp
  protocol: udp
  port: 123
  direction: Egress
  destination: 0.0.0.0/0
  description: "时间同步"
  action: allow
```

## 3. 环境特定配置

### 3.1 云端环境

```yaml
# 云端可能需要额外暴露的端口
- name: cloud-loadbalancer
  protocol: tcp
  port: 443
  direction: Ingress
  source: 0.0.0.0/0
  description: "HTTPS负载均衡器 (如需要公网访问)"
  action: allow
  condition: "仅在配置公网访问时启用"
```

### 3.2 本地环境

```yaml
# 本地环境可能需要的额外规则
- name: local-dev-access
  protocol: tcp
  port_range: "3000-4000"
  direction: Ingress
  source: local_network_cidr
  description: "开发测试端口范围"
  action: allow
  condition: "仅在开发环境启用"
```

## 4. 安全加固措施

### 4.1 端口扫描防护

```bash
# 使用iptables/nftables默认策略
iptables -P INPUT DROP
iptables -P FORWARD DROP
iptables -P OUTPUT ACCEPT

# 允许已建立的连接
iptables -A INPUT -m state --state ESTABLISHED,RELATED -j ACCEPT
```

### 4.2 速率限制

```yaml
# SSH连接速率限制
- rule: "限制SSH暴力破解"
  protocol: tcp
  port: 22
  limit: "3/minute"
  burst: 5
  action: reject

# API端点速率限制  
- rule: "限制API调用频率"
  protocol: tcp
  port: 2470-2499
  limit: "1000/minute"
  burst: 2000
  action: reject
```

### 4.3 网络隔离

```yaml
# 网络分段建议
- segment: "management"
  cidr: "10.0.1.0/24"
  services: ["ssh", "monitoring"]

- segment: "control"
  cidr: "10.0.2.0/24"  
  services: ["nats", "redis"]

- segment: "data"
  cidr: "10.0.3.0/24"
  services: ["minio"]

- segment: "execution" 
  cidr: "10.0.4.0/24"
  services: ["mcp-servers"]
```

## 5. 实施检查清单

### 5.1 部署前验证
- [ ] 所有服务端口已在ACL表中定义
- [ ] 源IP范围已正确配置
- [ ] 出站规则满足业务需求
- [ ] 速率限制策略已设置
- [ ] 网络分段方案已规划

### 5.2 运行时监控
- [ ] 端口扫描告警已配置
- [ ] 异常连接日志已启用
- [ ] 安全事件审计已设置
- [ ] 规则变更审计已记录

### 5.3 应急响应
- [ ] 端口封锁流程已定义
- [ ] 安全事件上报机制已建立
- [ ] 回滚方案已准备
- [ ] 联系人清单已更新

## 6. 版本管理

- **版本**: v1.0
- **状态**: 冻结 (P0)
- **生效日期**: 2024-01-01
- **评审记录**: 技术底座v1.1审核通过

## 7. 变更日志

| 版本 | 日期 | 变更说明 |
|------|------|----------|
| v1.0 | 2024-01-01 | 初始版本，基于技术底座v1.1 |

---
**注意**: 此ACL表为最小安全基线，实际部署时应根据具体环境调整，但不得降低安全标准。