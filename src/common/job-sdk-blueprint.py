#!/usr/bin/env python3
"""
混合算力平台 - Job SDK 蓝图
基于P0阶段冻结的Job契约v1实现的任务SDK设计
"""

import os
import asyncio
import json
import logging
import time
import uuid
from datetime import datetime
from typing import Dict, List, Any, Optional, Callable
from enum import Enum
from dataclasses import dataclass

import aiohttp
import backoff
from pydantic import BaseModel, Field

# 配置日志
logging.basicConfig(
    level=os.getenv("LOGGING_LEVEL", "INFO"),
    format='{"timestamp": "%(asctime)s", "level": "%(levelname)s", "name": "%(name)s", "message": "%(message)s", "trace_id": "%(trace_id)s", "span_id": "%(span_id)s"}',
    datefmt="%Y-%m-%dT%H:%M:%SZ"
)
logger = logging.getLogger("job-sdk")

# 枚举定义
class JobState(str, Enum):
    """任务状态枚举"""
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    DEAD_LETTER = "dead_letter"

class ErrorCode(str, Enum):
    """错误码枚举"""
    RATE_LIMITED = "rate_limited"
    RESOURCE_UNAVAILABLE = "resource_unavailable"
    TIMEOUT = "timeout"
    INVALID_INPUT = "invalid_input"
    PERMISSION_DENIED = "permission_denied"
    UNKNOWN_ERROR = "unknown_error"

# Pydantic 模型 - 使用 snake_case 命名
class JobTimestamps(BaseModel):
    """任务时间戳"""
    created: Optional[str] = Field(None, description="创建时间")
    started: Optional[str] = Field(None, description="开始时间")
    completed: Optional[str] = Field(None, description="完成时间")

class JobMetadata(BaseModel):
    """任务元数据"""
    model: Optional[str] = Field(None, description="模型名称")
    max_tokens: Optional[int] = Field(None, description="最大token数")
    temperature: Optional[float] = Field(None, description="温度参数")
    stream: Optional[bool] = Field(None, description="是否流式输出")

class JobRequest(BaseModel):
    """任务请求"""
    job_id: str = Field(..., description="任务ID")
    idempotency_key: str = Field(..., description="幂等键")
    type: str = Field(..., description="任务类型")
    priority: int = Field(5, description="优先级", ge=0, le=9)
    ttl: int = Field(3600, description="生存时间(秒)")
    attempts: int = Field(0, description="重试次数")
    state: JobState = Field(JobState.QUEUED, description="任务状态")
    timestamps: JobTimestamps = Field(default_factory=JobTimestamps)
    input_ref: str = Field(..., description="输入文件引用")
    output_ref: str = Field(..., description="输出文件引用")
    error_code: Optional[str] = Field(None, description="错误码")
    metadata: Optional[JobMetadata] = Field(None, description="扩展元数据")

class JobResponse(BaseModel):
    """任务响应"""
    job_id: str = Field(..., description="任务ID")
    status: str = Field(..., description="任务状态")
    message: Optional[str] = Field(None, description="状态消息")
    estimated_start: Optional[str] = Field(None, description="预计开始时间")
    queue_position: Optional[int] = Field(None, description="队列位置")

class JobStatus(BaseModel):
    """任务状态查询响应"""
    job_id: str = Field(..., description="任务ID")
    state: JobState = Field(..., description="任务状态")
    progress: Optional[int] = Field(None, description="进度百分比", ge=0, le=100)
    timestamps: JobTimestamps = Field(..., description="时间戳")
    current_step: Optional[str] = Field(None, description="当前步骤")
    metrics: Optional[Dict[str, Any]] = Field(None, description="运行时指标")

@dataclass
class RetryConfig:
    """重试配置"""
    max_attempts: int = 3
    base_delay: float = 2.0
    max_delay: float = 60.0
    jitter: bool = True

@dataclass
class SDKConfig:
    """SDK配置"""
    nats_url: str = "nats://localhost:4222"
    api_gateway_url: str = "http://localhost:8080"
    timeout: int = 30
    retry_config: RetryConfig = RetryConfig()

class JobSDK:
    """
    Job SDK 主类
    提供任务提交、状态查询、幂等控制等功能
    """
    
    def __init__(self, config: SDKConfig):
        self.config = config
        self.session: Optional[aiohttp.ClientSession] = None
        self._setup_session()
    
    def _setup_session(self):
        """设置HTTP会话"""
        timeout = aiohttp.ClientTimeout(total=self.config.timeout)
        self.session = aiohttp.ClientSession(timeout=timeout)
    
    async def close(self):
        """关闭SDK"""
        if self.session:
            await self.session.close()
    
    def generate_idempotency_key(self, input_data: Dict[str, Any]) -> str:
        """
        生成幂等键
        基于输入数据生成SHA256哈希
        """
        import hashlib
        input_str = json.dumps(input_data, sort_keys=True)
        return hashlib.sha256(input_str.encode()).hexdigest()
    
    def generate_job_id(self, prefix: str = "job") -> str:
        """生成任务ID"""
        return f"{prefix}-{uuid.uuid4().hex[:8]}"
    
    @backoff.on_exception(
        backoff.expo,
        (aiohttp.ClientError, asyncio.TimeoutError),
        max_tries=3
    )
    async def submit_job(self, job_request: JobRequest) -> JobResponse:
        """
        提交任务
        实现ACK、幂等、指数退避等语义保证
        """
        if not self.session:
            raise RuntimeError("SDK not initialized")
        
        url = f"{self.config.api_gateway_url}/jobs"
        
        try:
            async with self.session.post(url, json=job_request.dict()) as response:
                if response.status == 200:
                    data = await response.json()
                    return JobResponse(**data)
                elif response.status == 409:
                    # 幂等冲突，任务已存在
                    error_data = await response.json()
                    return JobResponse(
                        job_id=job_request.job_id,
                        status="conflict",
                        message=error_data.get("message", "Job already exists")
                    )
                else:
                    error_data = await response.json()
                    raise JobSDKError(
                        f"Failed to submit job: {error_data}",
                        response.status
                    )
        except asyncio.TimeoutError:
            raise JobSDKError("Request timeout", 408)
    
    @backoff.on_exception(
        backoff.expo,
        (aiohttp.ClientError, asyncio.TimeoutError),
        max_tries=3
    )
    async def get_job_status(self, job_id: str) -> JobStatus:
        """查询任务状态"""
        if not self.session:
            raise RuntimeError("SDK not initialized")
        
        url = f"{self.config.api_gateway_url}/jobs/{job_id}"
        
        try:
            async with self.session.get(url) as response:
                if response.status == 200:
                    data = await response.json()
                    return JobStatus(**data)
                elif response.status == 404:
                    raise JobNotFoundError(f"Job {job_id} not found")
                else:
                    error_data = await response.json()
                    raise JobSDKError(
                        f"Failed to get job status: {error_data}",
                        response.status
                    )
        except asyncio.TimeoutError:
            raise JobSDKError("Request timeout", 408)
    
    async def wait_for_completion(
        self, 
        job_id: str, 
        poll_interval: int = 5,
        timeout: int = 3600
    ) -> JobStatus:
        """
        等待任务完成
        支持轮询和超时控制
        """
        start_time = time.time()
        
        while True:
            status = await self.get_job_status(job_id)
            
            if status.state in [JobState.SUCCEEDED, JobState.FAILED, JobState.DEAD_LETTER]:
                return status
            
            if time.time() - start_time > timeout:
                raise JobSDKError(f"Job {job_id} timeout after {timeout} seconds", 408)
            
            await asyncio.sleep(poll_interval)
    
    async def cancel_job(self, job_id: str) -> bool:
        """取消任务"""
        if not self.session:
            raise RuntimeError("SDK not initialized")
        
        url = f"{self.config.api_gateway_url}/jobs/{job_id}/cancel"
        
        try:
            async with self.session.post(url) as response:
                return response.status == 200
        except asyncio.TimeoutError:
            raise JobSDKError("Request timeout", 408)
    
    def create_llm_inference_job(
        self,
        prompt: str,
        model: str = "llama2-7b",
        max_tokens: int = 1000,
        temperature: float = 0.7,
        priority: int = 5
    ) -> JobRequest:
        """创建LLM推理任务"""
        input_data = {
            "prompt": prompt,
            "model": model,
            "max_tokens": max_tokens,
            "temperature": temperature
        }
        
        job_id = self.generate_job_id("llm-inf")
        idempotency_key = self.generate_idempotency_key(input_data)
        
        return JobRequest(
            job_id=job_id,
            idempotency_key=idempotency_key,
            type="llm.inference.v1",
            priority=priority,
            ttl=3600,
            input_ref=f"minio://hybrid-compute/inputs/{job_id}/input.json",
            output_ref=f"minio://hybrid-compute/outputs/{job_id}/result.json",
            metadata=JobMetadata(
                model=model,
                max_tokens=max_tokens,
                temperature=temperature,
                stream=False
            ),
            timestamps=JobTimestamps(
                created=datetime.utcnow().isoformat() + "Z"
            )
        )
    
    def create_image_generation_job(
        self,
        prompt: str,
        model: str = "stable-diffusion",
        width: int = 512,
        height: int = 512,
        priority: int = 5
    ) -> JobRequest:
        """创建图像生成任务"""
        input_data = {
            "prompt": prompt,
            "model": model,
            "width": width,
            "height": height
        }
        
        job_id = self.generate_job_id("img-gen")
        idempotency_key = self.generate_idempotency_key(input_data)
        
        return JobRequest(
            job_id=job_id,
            idempotency_key=idempotency_key,
            type="image.generation.v1",
            priority=priority,
            ttl=7200,
            input_ref=f"minio://hybrid-compute/inputs/{job_id}/input.json",
            output_ref=f"minio://hybrid-compute/outputs/{job_id}/image.png",
            metadata=JobMetadata(
                model=model
            ),
            timestamps=JobTimestamps(
                created=datetime.utcnow().isoformat() + "Z"
            )
        )

class JobSDKError(Exception):
    """Job SDK 异常基类"""
    
    def __init__(self, message: str, status_code: int = 500):
        super().__init__(message)
        self.status_code = status_code
        self.message = message

class JobNotFoundError(JobSDKError):
    """任务未找到异常"""
    pass

class JobTimeoutError(JobSDKError):
    """任务超时异常"""
    pass

# 使用示例
async def example_usage():
    """SDK使用示例"""
    config = SDKConfig(
        api_gateway_url="http://localhost:8080",
        retry_config=RetryConfig(max_attempts=3, base_delay=2.0)
    )
    
    sdk = JobSDK(config)
    
    try:
        # 创建LLM推理任务
        job_request = sdk.create_llm_inference_job(
            prompt="Explain quantum computing in simple terms",
            model="llama2-7b",
            max_tokens=500
        )
        
        # 提交任务
        response = await sdk.submit_job(job_request)
        print(f"Job submitted: {response.job_id}")
        
        # 等待任务完成
        status = await sdk.wait_for_completion(response.job_id)
        print(f"Job completed with state: {status.state}")
        
        if status.state == JobState.SUCCEEDED:
            print("Job succeeded!")
        else:
            print(f"Job failed: {status.metrics}")
            
    except JobSDKError as e:
        print(f"Job SDK error: {e}")
    finally:
        await sdk.close()

if __name__ == "__main__":
    # 运行示例
    asyncio.run(example_usage())