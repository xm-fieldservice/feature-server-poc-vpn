# 混合算力平台 - 最小告警阈值 v1

## 概述

本文档定义了混合算力平台的最小告警阈值v1版本，作为P0阶段的监控标准冻结文档。这些阈值旨在提供基本的系统健康监控，避免过度告警。

## 1. 告警设计原则

### 1.1 最小化原则
- **关键指标**: 只监控影响业务连续性的核心指标
- **避免噪音**: 减少非关键告警，防止告警疲劳
- **分级响应**: 根据严重程度分级，匹配响应流程

### 1.2 阈值设定原则
- **基于SLO**: 阈值与服务水平目标对齐
- **渐进恶化**: 检测趋势性恶化而非瞬时波动
- **环境感知**: 考虑测试/生产环境差异

## 2. 核心告警阈值

### 2.1 可用性告警

#### 2.1.1 节点失联
```yaml
alert: NodeDown
expr: up == 0
for: 5m
labels:
  severity: critical
  component: infrastructure
annotations:
  summary: "节点失联"
  description: "节点 {{ $labels.instance }} 已失联超过5分钟"
```

**阈值**:
- 失联持续时间: ≥ 5分钟
- 采样次数: ≥ 3次 (默认1分钟间隔)
- 严重程度: Critical

#### 2.1.2 服务不可用
```yaml
alert: ServiceDown
expr: rate(requests_total{status=~"5.."}[5m]) > 0.01
for: 2m
labels:
  severity: critical
  component: application
annotations:
  summary: "服务不可用"
  description: "服务 {{ $labels.service }} 5xx错误率超过1%"
```

**阈值**:
- 5xx错误率: > 1% (5分钟窗口)
- 持续时间: ≥ 2分钟
- 严重程度: Critical

### 2.2 性能告警

#### 2.2.1 响应时间恶化
```yaml
alert: HighLatency
expr: histogram_quantile(0.95, rate(request_duration_seconds_bucket[5m])) > 2.5
for: 5m
labels:
  severity: warning
  component: performance
annotations:
  summary: "高延迟告警"
  description: "服务 {{ $labels.service }} P95延迟超过2.5秒"
```

**阈值**:
- P95延迟: > 2.5秒
- 持续时间: ≥ 5分钟
- 严重程度: Warning

#### 2.2.2 队列积压
```yaml
alert: QueueBacklog
expr: queue_depth > 1000
for: 5m
labels:
  severity: warning
  component: queue
annotations:
  summary: "队列积压"
  description: "队列 {{ $labels.queue }} 积压超过1000个任务"
```

**阈值**:
- 队列深度: > 1000
- 持续时间: ≥ 5分钟
- 严重程度: Warning

### 2.3 资源告警

#### 2.3.1 CPU使用率
```yaml
alert: HighCPUUsage
expr: rate(node_cpu_seconds_total{mode="idle"}[5m]) < 0.2
for: 5m
labels:
  severity: warning
  component: resource
annotations:
  summary: "高CPU使用率"
  description: "节点 {{ $labels.instance }} CPU空闲率低于20%"
```

**阈值**:
- CPU空闲率: < 20%
- 持续时间: ≥ 5分钟
- 严重程度: Warning

#### 2.3.2 内存使用率
```yaml
alert: HighMemoryUsage
expr: node_memory_MemAvailable_bytes / node_memory_MemTotal_bytes < 0.1
for: 5m
labels:
  severity: warning
  component: resource
annotations:
  summary: "高内存使用率"
  description: "节点 {{ $labels.instance }} 可用内存低于10%"
```

**阈值**:
- 可用内存比例: < 10%
- 持续时间: ≥ 5分钟
- 严重程度: Warning

#### 2.3.3 磁盘空间
```yaml
alert: DiskSpaceLow
expr: node_filesystem_avail_bytes / node_filesystem_size_bytes < 0.15
for: 2m
labels:
  severity: critical
  component: resource
annotations:
  summary: "磁盘空间不足"
  description: "磁盘 {{ $labels.device }} 可用空间低于15%"
```

**阈值**:
- 可用磁盘空间: < 15%
- 持续时间: ≥ 2分钟
- 严重程度: Critical

### 2.4 业务告警

#### 2.4.1 任务失败率
```yaml
alert: HighJobFailureRate
expr: rate(job_status_total{status="failed"}[5m]) / rate(job_status_total[5m]) > 0.01
for: 5m
labels:
  severity: warning
  component: business
annotations:
  summary: "高任务失败率"
  description: "任务类型 {{ $labels.job_type }} 失败率超过1%"
```

**阈值**:
- 失败率: > 1% (5分钟窗口)
- 持续时间: ≥ 5分钟
- 严重程度: Warning

#### 2.4.2 数据处理延迟
```yaml
alert: DataProcessingDelay
expr: data_processing_lag_seconds > 300
for: 5m
labels:
  severity: warning
  component: data
annotations:
  summary: "数据处理延迟"
  description: "数据管道 {{ $labels.pipeline }} 处理延迟超过5分钟"
```

**阈值**:
- 处理延迟: > 300秒 (5分钟)
- 持续时间: ≥ 5分钟
- 严重程度: Warning

## 3. 告警分级与响应

### 3.1 严重程度定义

| 级别 | 颜色 | 响应时间 | 影响范围 | 示例 |
|------|------|----------|----------|------|
| Critical | 红色 | 15分钟 | 业务中断 | 服务不可用、节点失联 |
| Warning | 黄色 | 1小时 | 性能下降 | 高延迟、资源紧张 |
| Info | 蓝色 | 4小时 | 观察事项 | 配置变更、容量趋势 |

### 3.2 响应流程

#### Critical 告警
```yaml
响应流程:
  - 立即通知: 值班工程师
  - 自动操作: 尝试重启/转移负载
  - 人工介入: 必须立即处理
  - 升级策略: 30分钟无响应升级主管
```

#### Warning 告警
```yaml
响应流程:
  - 定时通知: 下一个工作日
  - 自动操作: 记录日志，无自动操作
  - 人工介入: 计划内处理
  - 升级策略: 24小时无响应升级
```

## 4. 告警路由与通知

### 4.1 通知渠道

```yaml
通知配置:
  critical:
    - sms: "+86-13800138000"
    - phone: "+86-13800138000"
    - slack: "#critical-alerts"
    - email: "oncall@company.com"
  
  warning:
    - slack: "#warning-alerts"
    - email: "team@company.com"
  
  info:
    - slack: "#info-alerts"
```

### 4.2 静默规则

```yaml
静默策略:
  - 维护窗口: 提前配置静默
  - 已知问题: 手动静默，必须设置过期时间
  - 测试环境: 默认静默非Critical告警
  - 自动恢复: 告警解决后自动取消静默
```

## 5. 环境特定配置

### 5.1 生产环境

```yaml
生产环境:
  告警阈值:
    - 节点失联: 3分钟
    - 服务不可用: 1分钟
    - 磁盘空间: 10%
  通知频率: 立即
```

### 5.2 测试环境

```yaml
测试环境:
  告警阈值:
    - 节点失联: 10分钟
    - 服务不可用: 5分钟
    - 磁盘空间: 5%
  通知频率: 每日汇总
```

### 5.3 开发环境

```yaml
开发环境:
  告警阈值:
    - 仅Critical告警
    - 节点失联: 30分钟
  通知频率: 不通知，仅记录
```

## 6. 监控数据保留

### 6.1 指标保留策略

```yaml
数据保留:
  - 原始数据: 15天
  - 5分钟聚合: 30天
  - 1小时聚合: 90天
  - 1天聚合: 1年
```

### 6.2 告警历史保留

```yaml
告警历史:
  - 活跃告警: 实时
  - 历史告警: 90天
  - 告警统计: 1年
```

## 7. 实施检查清单

### 7.1 部署前验证
- [ ] 所有告警规则语法正确
- [ ] 阈值与业务SLO对齐
- [ ] 通知渠道配置正确
- [ ] 静默规则设置合理

### 7.2 运行时验证
- [ ] 告警能够正常触发
- [ ] 通知能够正确送达
- [ ] 告警恢复机制工作正常
- [ ] 误报率在可接受范围

### 7.3 持续优化
- [ ] 定期评审告警有效性
- [ ] 根据业务变化调整阈值
- [ ] 优化告警分组和路由
- [ ] 减少噪音和误报

## 8. 版本管理

- **版本**: v1.0
- **状态**: 冻结 (P0)
- **生效日期**: 2024-01-01
- **评审记录**: 技术底座v1.1审核通过

## 9. 变更日志

| 版本 | 日期 | 变更说明 |
|------|------|----------|
| v1.0 | 2024-01-01 | 初始版本，基于技术底座v1.1 |

---
**注意**: 这些是最小告警阈值，实际部署时应根据具体业务需求调整，但核心可用性告警不应被禁用。