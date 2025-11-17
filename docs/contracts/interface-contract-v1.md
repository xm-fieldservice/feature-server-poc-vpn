# 混合算力平台 - 接口契约 v1

## 概述

本文档定义了混合算力平台的接口契约v1版本，作为P0阶段的标准冻结文档。所有实现必须遵循此契约。

## 1. 命名约定

- **JSON字段**: 统一使用 snake_case 命名规范
- **枚举值**: 使用小写字母和下划线
- **时间格式**: 使用 ISO8601 格式
- **标识符**: UUID 或符合业务语义的字符串

## 2. Job 契约 v1

### 2.1 字段定义

```json
{
  "job_id": "string (UUID)",
  "idempotency_key": "string (SHA256)",
  "type": "string (任务类型)",
  "priority": "integer (0-9)",
  "ttl": "integer (秒)",
  "attempts": "integer (当前重试次数)",
  "state": "string (queued|running|succeeded|failed|dead_letter)",
  "timestamps": {
    "created": "string (ISO8601)",
    "started": "string (ISO8601)",
    "completed": "string (ISO8601)"
  },
  "input_ref": "string (预签名URL)",
  "output_ref": "string (预签名URL)",
  "error_code": "string (错误码)",
  "metadata": "object (扩展元数据)"
}
```

### 2.2 主题命名规范

```
<domain>.<task>.<version>
```

示例：
- `llm.inference.v1`
- `image.generation.v1`
- `data.preprocessing.v1`

### 2.3 语义保证

- **ACK机制**: 所有消息必须确认
- **指数退避**: 重试间隔 = min(2^attempts * base_delay, max_delay)
- **最大重试**: max_attempts = 3 (可配置)
- **死信队列**: 失败任务进入DLQ并记录审计日志

## 3. MCP 工具契约 v1

### 3.1 端点定义

```
GET  /tools                    # 工具发现
POST /tools/{name}/invoke      # 工具调用
GET  /health                   # 健康检查
GET  /metrics                  # Prometheus指标
```

### 3.2 输入/输出 Schema

```json
// 工具调用请求
{
  "arguments": {
    "input": "string",
    "model": "string",
    "parameters": {}
  }
}

// 工具调用响应
{
  "content": [
    {
      "type": "text",
      "text": "string"
    }
  ],
  "is_error": false
}

// 错误响应
{
  "error": {
    "code": "RETRYABLE_ERROR|NON_RETRYABLE_ERROR",
    "message": "string",
    "details": {}
  }
}
```

### 3.3 错误分类

| 错误码 | 类型 | 描述 |
|--------|------|------|
| RATE_LIMITED | RETRYABLE | 速率限制 |
| RESOURCE_UNAVAILABLE | RETRYABLE | 资源不可用 |
| TIMEOUT | RETRYABLE | 超时 |
| INVALID_INPUT | NON_RETRYABLE | 无效输入 |
| PERMISSION_DENIED | NON_RETRYABLE | 权限拒绝 |

### 3.4 目标 SLO

- p95延迟: < 2.5秒
- 成功率: ≥ 99%
- 可用性: ≥ 99.5%

## 4. 数据旁路契约 v1

### 4.1 预签名 URL

```yaml
# URL参数
ttl: 1800  # 30分钟默认TTL
method: GET|PUT|POST
content_type: application/octet-stream
```

### 4.2 路径命名策略

```
/{bucket}/{dataset}/{version}/{shard}/{filename}
```

示例：
- `/hybrid-compute/models/llama2/v1/part-00000/model.bin`
- `/hybrid-compute/datasets/coco/v2/train/images/001.jpg`

### 4.3 传输规范

- **分片大小**: 16-64MB
- **并发上限**: 可配置 (默认4)
- **校验机制**: MD5/ETag
- **断点续传**: 支持
- **幂等键**: 基于文件内容SHA256

## 5. 观测契约 v1

### 5.1 指标最小集

```prometheus
# 节点指标
node_cpu_usage{instance="", job=""}
node_memory_usage{instance="", job=""}
node_gpu_utilization{instance="", gpu_id=""}

# 队列指标
queue_depth{queue="", priority=""}
job_latency_p95{type=""}
job_error_rate{type=""}

# 缓存指标
cache_hit_rate{type=""}
egress_bytes_total{direction=""}
```

### 5.2 日志规范

```json
{
  "timestamp": "2024-01-01T00:00:00Z",
  "level": "INFO|WARN|ERROR",
  "trace_id": "string",
  "span_id": "string", 
  "job_id": "string",
  "err_code": "string",
  "message": "string",
  "component": "string"
}
```

### 5.3 追踪链路命名

```
queue.receive → mcp.invoke → storage.upload → queue.ack
```

## 6. 安全与端口 ACL v1

### 6.1 端口白名单

| 服务 | 协议 | 端口 | 方向 | 源 | 动作 |
|------|------|------|------|----|------|
| VPN | UDP | 51820 | Ingress | VPN peers | Allow |
| MCP | TCP | 2470-2499 | Ingress | VPN CIDR | Allow |
| NATS | TCP | 4222 | Ingress | VPN CIDR | Allow |
| Redis | TCP | 6379,6380 | Ingress | VPN CIDR | Allow |
| MinIO | TCP | 9000,9001 | Ingress | VPN CIDR | Allow |
| 监控 | TCP | 9090,4317,3100 | Ingress | VPN CIDR | Allow |
| SSH | TCP | 22,2222 | Ingress | Admin IPs | Allow |

### 6.2 鉴权机制

- **JWT TTL**: ≤ 15分钟
- **mTLS**: 可选
- **权限**: 最小权限原则
- **密钥轮换**: ≤ 90天
- **审计留存**: ≥ 180天

## 7. 试点用例验证

### 用例1: 提交LLM推理任务
```json
{
  "job_id": "llm-inf-001",
  "idempotency_key": "sha256(input)",
  "type": "llm.inference.v1",
  "priority": 5,
  "input_ref": "https://minio/.../input.json?signature=...",
  "metadata": {
    "model": "llama2-7b",
    "max_tokens": 1000
  }
}
```

### 用例2: 查询任务状态
```json
{
  "job_id": "llm-inf-001",
  "state": "running",
  "timestamps": {
    "created": "2024-01-01T00:00:00Z",
    "started": "2024-01-01T00:00:05Z"
  }
}
```

### 用例3: 预签名多段上传
```json
{
  "upload_id": "multipart-001",
  "parts": [
    {
      "part_number": 1,
      "url": "https://minio/.../part1?signature=...",
      "size": 16777216
    }
  ]
}
```

### 用例4: 监控指标
```prometheus
job_latency_p95{type="llm.inference"} 2.1
job_error_rate{type="llm.inference"} 0.005
```

### 用例5: 安全审计事件
```json
{
  "timestamp": "2024-01-01T00:00:00Z",
  "event_type": "auth_success",
  "user": "api-client",
  "resource": "mcp-server",
  "source_ip": "10.0.1.100"
}
```

## 8. 版本管理

- **版本策略**: SemVer
- **向后兼容**: minor版本必须兼容
- **迁移指引**: major版本断裂需提供迁移文档
- **弃用策略**: 提前30天通知

---
**文档状态**: 冻结 (P0)
**生效日期**: 2024-01-01
**评审记录**: 技术底座v1.1审核通过