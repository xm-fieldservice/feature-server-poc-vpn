#!/usr/bin/env python3
"""
DeepSeek Reasoning MCP Server - 最小实现版本
专为混合算力平台设计的轻量级推理服务
"""

import os
import json
import logging
import time
import asyncio
from datetime import datetime
from typing import Dict, List, Any, Optional

from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field
import uvicorn
from prometheus_client import Counter, Histogram, Gauge, generate_latest, CONTENT_TYPE_LATEST

# 配置JSON格式日志
logging.basicConfig(
    level=os.getenv("LOGGING_LEVEL", "INFO"),
    format='{"timestamp": "%(asctime)s", "level": "%(levelname)s", "name": "%(name)s", "message": "%(message)s"}',
    datefmt="%Y-%m-%dT%H:%M:%SZ"
)
logger = logging.getLogger("deepseek-reasoning")

# Prometheus 指标
REQUEST_COUNT = Counter('deepseek_request_total', 'Total requests', ['method', 'endpoint', 'status'])
REQUEST_LATENCY = Histogram('deepseek_request_latency_seconds', 'Request latency (s)', ['endpoint'])
ACTIVE_REQUESTS = Gauge('deepseek_inprogress', 'In-progress requests')
TOOL_INVOCATIONS = Counter('deepseek_tool_invocations_total', 'Tool invocations', ['tool_name', 'status'])
REASONING_REQUESTS = Counter('deepseek_reasoning_requests_total', 'Reasoning requests', ['status'])

# 数据模型定义
class HealthResponse(BaseModel):
    status: str = Field(..., description="服务状态")
    version: str = Field(..., description="服务版本")
    timestamp: str = Field(..., description="时间戳")

class Tool(BaseModel):
    name: str = Field(..., description="工具名称")
    description: str = Field(..., description="工具描述")
    input_schema: Dict[str, Any] = Field(..., description="输入模式")

class ToolInvocationRequest(BaseModel):
    arguments: Dict[str, Any] = Field(..., description="调用参数")

class ContentItem(BaseModel):
    type: str = Field(..., description="内容类型")
    text: str = Field(..., description="内容文本")

class ToolInvocationResponse(BaseModel):
    content: List[ContentItem] = Field(..., description="响应内容")
    is_error: bool = Field(False, description="是否错误")

class ReasoningRequest(BaseModel):
    prompt: str = Field(..., description="推理提示")
    context: Optional[str] = Field(None, description="上下文信息")
    max_tokens: int = Field(1000, description="最大token数")
    temperature: float = Field(0.7, description="温度参数")

class ReasoningResponse(BaseModel):
    reasoning: str = Field(..., description="推理过程")
    answer: str = Field(..., description="最终答案")
    tokens_used: int = Field(..., description="使用的token数")
    processing_time: float = Field(..., description="处理时间(秒)")

class DeepSeekReasoningServer:
    """DeepSeek Reasoning MCP Server 核心类"""
    
    def __init__(self):
        self.config = self._load_config()
        self.app = FastAPI(
            title="DeepSeek Reasoning MCP Server",
            description="专为混合算力平台设计的轻量级推理服务",
            version=self.config["version"],
            docs_url="/docs",
            redoc_url=None
        )
        
        # 工具注册表
        self.tools: Dict[str, Tool] = {}
        
        # 并发控制与指标
        self._semaphore = asyncio.Semaphore(self.config["max_concurrency"])
        self.errors_total = Counter("deepseek_errors_total", "Total errors", ["endpoint"])
        
        # 设置路由和中间件
        self._setup_routes()
        self._setup_middleware()
        self._register_tools()
    
    def _setup_middleware(self):
        """设置中间件"""
        
        @self.app.middleware("http")
        async def add_process_time_header(request, call_next):
            start_time = time.time()
            ACTIVE_REQUESTS.inc()
            
            try:
                response = await call_next(request)
                process_time = time.time() - start_time
                response.headers["X-Process-Time"] = str(process_time)
                
                # 记录访问日志
                REQUEST_COUNT.labels(
                    method=request.method,
                    endpoint=request.url.path,
                    status=response.status_code
                ).inc()
                REQUEST_LATENCY.labels(
                    endpoint=request.url.path
                ).observe(process_time)
                
                return response
            except Exception as e:
                process_time = time.time() - start_time
                status_code = e.status_code if isinstance(e, HTTPException) else 500
                REQUEST_COUNT.labels(
                    method=request.method,
                    endpoint=request.url.path,
                    status=status_code
                ).inc()
                REQUEST_LATENCY.labels(
                    endpoint=request.url.path
                ).observe(process_time)
                raise
            finally:
                ACTIVE_REQUESTS.dec()
        
    def _load_config(self) -> Dict[str, Any]:
        """加载配置"""
        return {
            "server_host": os.getenv("SERVER_HOST", "0.0.0.0"),
            "server_port": int(os.getenv("SERVER_PORT", "2471")),
            "server_name": "deepseek-reasoning",
            "version": os.getenv("VERSION", "1.0.0"),
            "max_workers": int(os.getenv("SERVER_WORKERS", "2")),
            "request_timeout": int(os.getenv("TIMEOUT_GLOBAL", "30")),
            "model_name": os.getenv("MODEL_NAME", "deepseek-reasoner"),
            "max_reasoning_tokens": int(os.getenv("MAX_REASONING_TOKENS", "2000")),
            "max_concurrency": int(os.getenv("MAX_CONCURRENCY", "1"))
        }
    
    def _setup_routes(self):
        """设置API路由"""
        
        @self.app.get("/health", response_model=HealthResponse)
        async def health_check():
            """健康检查端点"""
            status = "healthy"
            return HealthResponse(
                status=status,
                version=self.config["version"],
                timestamp=datetime.utcnow().isoformat() + "Z"
            )
        
        @self.app.get("/tools", response_model=List[Tool])
        async def list_tools():
            """工具发现端点"""
            return list(self.tools.values())
        
        @self.app.post("/tools/{tool_name}/invoke", response_model=ToolInvocationResponse)
        async def invoke_tool(
            tool_name: str,
            request: ToolInvocationRequest,
            background_tasks: BackgroundTasks
        ):
            """工具调用端点"""
            try:
                await asyncio.wait_for(self._semaphore.acquire(), timeout=0.3)
            except asyncio.TimeoutError:
                self.errors_total.labels(endpoint="tool_invoke").inc()
                raise HTTPException(status_code=429, detail="Server is busy")

            try:
                if tool_name not in self.tools:
                    self.errors_total.labels(endpoint="tool_invoke").inc()
                    raise HTTPException(status_code=404, detail=f"Tool {tool_name} not found")

                # 执行工具逻辑并计时
                with REQUEST_LATENCY.labels(endpoint="tool_invoke").time():
                    result = await asyncio.wait_for(
                        self._execute_tool(tool_name, request.arguments),
                        timeout=self.config["request_timeout"]
                    )
                return result
            except HTTPException:
                raise
            except Exception as e:
                self.errors_total.labels(endpoint="tool_invoke").inc()
                logger.error(f"Tool invocation error: {str(e)}")
                raise HTTPException(status_code=500, detail=str(e))
            finally:
                self._semaphore.release()
        
        @self.app.post("/reasoning", response_model=ReasoningResponse)
        async def reasoning_endpoint(request: ReasoningRequest):
            """专用推理端点"""
            try:
                await asyncio.wait_for(self._semaphore.acquire(), timeout=0.3)
            except asyncio.TimeoutError:
                self.errors_total.labels(endpoint="reasoning").inc()
                raise HTTPException(status_code=429, detail="Server is busy")

            try:
                with REQUEST_LATENCY.labels(endpoint="reasoning").time():
                    result = await asyncio.wait_for(
                        self._perform_reasoning(request),
                        timeout=self.config["request_timeout"]
                    )
                return result
            except HTTPException:
                raise
            except Exception as e:
                self.errors_total.labels(endpoint="reasoning").inc()
                logger.error(f"Reasoning error: {str(e)}")
                raise HTTPException(status_code=500, detail=str(e))
            finally:
                self._semaphore.release()

        @self.app.get("/metrics")
        async def metrics_endpoint():
            """Prometheus 指标端点"""
            return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)
    
    def _register_tools(self):
        """注册可用工具"""
        
        # 推理工具
        reasoning_tool = Tool(
            name="deepseek_reasoning",
            description="执行深度推理分析",
            input_schema={
                "type": "object",
                "properties": {
                    "prompt": {
                        "type": "string",
                        "description": "推理提示词"
                    },
                    "context": {
                        "type": "string",
                        "description": "上下文信息"
                    },
                    "max_tokens": {
                        "type": "integer",
                        "description": "最大token数",
                        "default": 1000
                    },
                    "temperature": {
                        "type": "number",
                        "description": "温度参数",
                        "default": 0.7
                    }
                },
                "required": ["prompt"]
            }
        )
        self.tools[reasoning_tool.name] = reasoning_tool
        
        # 分析工具
        analysis_tool = Tool(
            name="complex_analysis",
            description="执行复杂问题分析",
            input_schema={
                "type": "object",
                "properties": {
                    "problem": {
                        "type": "string",
                        "description": "待分析的问题"
                    },
                    "constraints": {
                        "type": "string",
                        "description": "约束条件"
                    },
                    "analysis_depth": {
                        "type": "string",
                        "description": "分析深度",
                        "enum": ["shallow", "medium", "deep"],
                        "default": "medium"
                    }
                },
                "required": ["problem"]
            }
        )
        self.tools[analysis_tool.name] = analysis_tool

    def _validate_parameters(self, tool_name: str, arguments: dict) -> dict:
        """参数校验方法"""
        if tool_name not in self.tools:
            raise HTTPException(status_code=404, detail=f"Tool {tool_name} not found")
        
        tool = self.tools[tool_name]
        schema = tool.input_schema
        
        # 检查必需参数
        required_fields = schema.get("required", [])
        for field in required_fields:
            if field not in arguments:
                raise HTTPException(
                    status_code=400,
                    detail=f"Missing required parameter: {field}"
                )
        
        # 校验参数类型和范围
        validated_args = {}
        properties = schema.get("properties", {})
        
        for field, value in arguments.items():
            if field not in properties:
                continue  # 忽略未知参数
                
            prop_schema = properties[field]
            expected_type = prop_schema.get("type")
            
            # 类型校验
            if expected_type == "string" and not isinstance(value, str):
                raise HTTPException(
                    status_code=400,
                    detail=f"Parameter {field} must be string"
                )
            elif expected_type == "integer" and not isinstance(value, int):
                raise HTTPException(
                    status_code=400,
                    detail=f"Parameter {field} must be integer"
                )
            elif expected_type == "number" and not isinstance(value, (int, float)):
                raise HTTPException(
                    status_code=400,
                    detail=f"Parameter {field} must be number"
                )
            
            # 范围校验
            if expected_type == "integer":
                min_val = prop_schema.get("minimum")
                max_val = prop_schema.get("maximum")
                if min_val is not None and value < min_val:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Parameter {field} must be >= {min_val}"
                    )
                if max_val is not None and value > max_val:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Parameter {field} must be <= {max_val}"
                    )
            
            validated_args[field] = value
        
        # 设置默认值
        for field, prop_schema in properties.items():
            if field not in validated_args and "default" in prop_schema:
                validated_args[field] = prop_schema["default"]
        
        return validated_args
    async def _execute_tool(self, tool_name: str, arguments: Dict[str, Any]) -> ToolInvocationResponse:
        """执行工具逻辑"""
        
        if tool_name == "deepseek_reasoning":
            # 转换为推理请求
            request = ReasoningRequest(
                prompt=arguments.get("prompt", ""),
                context=arguments.get("context"),
                max_tokens=arguments.get("max_tokens", 1000),
                temperature=arguments.get("temperature", 0.7)
            )
            result = await self._perform_reasoning(request)
            
            return ToolInvocationResponse(
                content=[
                    ContentItem(type="text", text=f"推理过程: {result.reasoning}"),
                    ContentItem(type="text", text=f"最终答案: {result.answer}"),
                    ContentItem(type="text", text=f"处理统计: {result.tokens_used} tokens, {result.processing_time:.2f}s")
                ],
                is_error=False
            )
        
        elif tool_name == "complex_analysis":
            problem = arguments.get("problem", "")
            constraints = arguments.get("constraints", "")
            depth = arguments.get("analysis_depth", "medium")
            
            # 执行分析
            analysis_result = await self._perform_complex_analysis(problem, constraints, depth)
            
            return ToolInvocationResponse(
                content=[
                    ContentItem(type="text", text=analysis_result)
                ],
                is_error=False
            )
        
        else:
            raise HTTPException(status_code=400, detail=f"Tool {tool_name} not implemented")
    
    async def _perform_reasoning(self, request: ReasoningRequest) -> ReasoningResponse:
        """执行推理任务"""
        start_time = time.time()
        
        # 模拟推理过程 - 在实际部署中这里会调用真实的DeepSeek模型
        reasoning_steps = self._generate_reasoning_steps(request.prompt, request.context)
        final_answer = self._synthesize_answer(reasoning_steps)
        
        # 计算token使用量（模拟）
        tokens_used = len(request.prompt) // 4 + len(final_answer) // 4
        processing_time = time.time() - start_time
        
        # 模拟处理时间
        await asyncio.sleep(0.1)  # 模拟网络延迟
        
        return ReasoningResponse(
            reasoning=reasoning_steps,
            answer=final_answer,
            tokens_used=min(tokens_used, request.max_tokens),
            processing_time=processing_time
        )
    
    def _generate_reasoning_steps(self, prompt: str, context: Optional[str] = None) -> str:
        """生成推理步骤（模拟实现）"""
        steps = []
        
        # 步骤1: 理解问题
        steps.append(f"1. 问题理解: 分析用户提出的问题 '{prompt}'")
        
        # 步骤2: 上下文分析
        if context:
            steps.append(f"2. 上下文分析: 考虑提供的上下文信息 '{context}'")
        else:
            steps.append("2. 上下文分析: 无额外上下文信息")
        
        # 步骤3: 逻辑推理
        steps.append("3. 逻辑推理: 基于问题特征进行逐步推理")
        
        # 步骤4: 验证检查
        steps.append("4. 验证检查: 确认推理结果的合理性和一致性")
        
        return "\n".join(steps)
    
    def _synthesize_answer(self, reasoning_steps: str) -> str:
        """合成最终答案（模拟实现）"""
        # 在实际实现中，这里会根据推理步骤生成具体的答案
        # 目前返回一个通用的回答
        return f"基于逐步推理分析，我得出了结论。推理过程如下:\n{reasoning_steps}"
    
    async def _perform_complex_analysis(self, problem: str, constraints: str, depth: str) -> str:
        """执行复杂问题分析"""
        analysis_depth_map = {
            "shallow": "初步分析",
            "medium": "中等深度分析", 
            "deep": "深度分析"
        }
        
        analysis = f"""
执行{analysis_depth_map.get(depth, '中等深度分析')}:

问题: {problem}
约束条件: {constraints if constraints else "无特定约束"}

分析步骤:
1. 问题分解: 将复杂问题拆解为可管理的子问题
2. 约束评估: 分析约束条件对解决方案的影响
3. 方案探索: 考虑多种可能的解决路径
4. 最优选择: 基于约束和效率选择最佳方案

结论: 这是一个需要{analysis_depth_map.get(depth, '中等深度分析')}的复杂问题，建议采用系统化的方法逐步解决。
"""
        return analysis.strip()
    
    def run(self):
        """启动服务器"""
        logger.info(f"Starting DeepSeek Reasoning Server on {self.config['server_host']}:{self.config['server_port']}")
        logger.info(f"Available tools: {list(self.tools.keys())}")
        
        # 修复uvicorn启动配置 - 使用单worker避免reload问题
        uvicorn.run(
            self.app,
            host=self.config["server_host"],
            port=self.config["server_port"],
            workers=1,
            log_level="info"
        )

def main():
    """主函数"""
    server = DeepSeekReasoningServer()
    server.run()

if __name__ == "__main__":
    main()