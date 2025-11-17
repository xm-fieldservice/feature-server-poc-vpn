#!/usr/bin/env python3
"""
DeepSeek Reasoning 服务测试脚本
用于验证推理服务的功能性和性能
"""

import argparse
import asyncio
import aiohttp
import time
import json
import logging
from typing import Dict, List, Any

# 配置日志
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("deepseek-test")

class DeepSeekReasoningTester:
    """DeepSeek Reasoning 服务测试器"""
    
    def __init__(self, base_url: str = "http://localhost:2471"):
        self.base_url = base_url
        self.session = None
    
    async def __aenter__(self):
        self.session = aiohttp.ClientSession()
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self.session:
            await self.session.close()
    
    async def health_check(self) -> bool:
        """健康检查"""
        try:
            async with self.session.get(f"{self.base_url}/health") as response:
                if response.status == 200:
                    data = await response.json()
                    logger.info(f"健康检查: {data}")
                    return data.get("status") == "healthy"
                return False
        except Exception as e:
            logger.error(f"健康检查失败: {e}")
            return False
    
    async def list_tools(self) -> List[Dict[str, Any]]:
        """列出可用工具"""
        try:
            async with self.session.get(f"{self.base_url}/tools") as response:
                if response.status == 200:
                    tools = await response.json()
                    logger.info(f"可用工具: {[tool['name'] for tool in tools]}")
                    return tools
                return []
        except Exception as e:
            logger.error(f"获取工具列表失败: {e}")
            return []
    
    async def test_reasoning_endpoint(self, prompt: str, context: str = None) -> Dict[str, Any]:
        """测试推理端点"""
        payload = {
            "prompt": prompt,
            "context": context,
            "max_tokens": 1000,
            "temperature": 0.7
        }
        
        try:
            start_time = time.time()
            async with self.session.post(
                f"{self.base_url}/reasoning",
                json=payload,
                timeout=30
            ) as response:
                processing_time = time.time() - start_time
                
                if response.status == 200:
                    result = await response.json()
                    logger.info(f"推理成功 - 处理时间: {processing_time:.2f}s")
                    return {
                        "success": True,
                        "data": result,
                        "processing_time": processing_time
                    }
                else:
                    error_text = await response.text()
                    logger.error(f"推理失败 - 状态码: {response.status}, 错误: {error_text}")
                    return {
                        "success": False,
                        "error": f"HTTP {response.status}: {error_text}",
                        "processing_time": processing_time
                    }
        except asyncio.TimeoutError:
            logger.error("推理请求超时")
            return {
                "success": False,
                "error": "请求超时",
                "processing_time": 30.0
            }
        except Exception as e:
            logger.error(f"推理请求异常: {e}")
            return {
                "success": False,
                "error": str(e),
                "processing_time": 0.0
            }
    
    async def test_tool_invocation(self, tool_name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        """测试工具调用"""
        payload = {
            "arguments": arguments
        }
        
        try:
            start_time = time.time()
            async with self.session.post(
                f"{self.base_url}/tools/{tool_name}/invoke",
                json=payload,
                timeout=30
            ) as response:
                processing_time = time.time() - start_time
                
                if response.status == 200:
                    result = await response.json()
                    logger.info(f"工具调用成功 - 处理时间: {processing_time:.2f}s")
                    return {
                        "success": True,
                        "data": result,
                        "processing_time": processing_time
                    }
                else:
                    error_text = await response.text()
                    logger.error(f"工具调用失败 - 状态码: {response.status}, 错误: {error_text}")
                    return {
                        "success": False,
                        "error": f"HTTP {response.status}: {error_text}",
                        "processing_time": processing_time
                    }
        except asyncio.TimeoutError:
            logger.error("工具调用请求超时")
            return {
                "success": False,
                "error": "请求超时",
                "processing_time": 30.0
            }
        except Exception as e:
            logger.error(f"工具调用请求异常: {e}")
            return {
                "success": False,
                "error": str(e),
                "processing_time": 0.0
            }

async def run_comprehensive_test():
    """运行综合测试"""
    test_cases = [
        {
            "name": "简单推理测试",
            "prompt": "如果我有3个苹果，吃了1个，又买了2个，现在有多少个苹果？",
            "context": None
        },
        {
            "name": "复杂逻辑测试", 
            "prompt": "分析这个商业决策的利弊：公司是否应该投资人工智能技术？",
            "context": "公司目前年收入1000万，AI投资需要200万，预计能提升效率15%"
        },
        {
            "name": "技术问题分析",
            "prompt": "如何优化数据库查询性能？",
            "context": "当前数据库有1000万条记录，查询响应时间超过5秒"
        }
    ]
    
    tool_test_cases = [
        {
            "tool_name": "deepseek_reasoning",
            "arguments": {
                "prompt": "解释量子计算的基本原理",
                "max_tokens": 800,
                "temperature": 0.5
            }
        },
        {
            "tool_name": "complex_analysis", 
            "arguments": {
                "problem": "如何设计一个可扩展的微服务架构？",
                "constraints": "团队规模10人，预算有限，需要快速上线",
                "analysis_depth": "deep"
            }
        }
    ]
    
    async with DeepSeekReasoningTester() as tester:
        # 1. 健康检查
        logger.info("=== 健康检查 ===")
        health_ok = await tester.health_check()
        if not health_ok:
            logger.error("服务不健康，停止测试")
            return
        
        # 2. 工具发现
        logger.info("=== 工具发现 ===")
        tools = await tester.list_tools()
        if not tools:
            logger.error("未发现可用工具")
            return
        
        # 3. 推理端点测试
        logger.info("=== 推理端点测试 ===")
        reasoning_results = []
        for test_case in test_cases:
            logger.info(f"测试: {test_case['name']}")
            result = await tester.test_reasoning_endpoint(
                test_case["prompt"],
                test_case["context"]
            )
            reasoning_results.append({
                "test_case": test_case["name"],
                **result
            })
            
            if result["success"]:
                data = result["data"]
                logger.info(f"  推理过程: {data.get('reasoning', '')[:100]}...")
                logger.info(f"  最终答案: {data.get('answer', '')[:100]}...")
            else:
                logger.error(f"  测试失败: {result['error']}")
            
            # 短暂等待避免服务器过载
            await asyncio.sleep(1)
        
        # 4. 工具调用测试
        logger.info("=== 工具调用测试 ===")
        tool_results = []
        for test_case in tool_test_cases:
            logger.info(f"测试工具: {test_case['tool_name']}")
            result = await tester.test_tool_invocation(
                test_case["tool_name"],
                test_case["arguments"]
            )
            tool_results.append({
                "tool_name": test_case["tool_name"],
                **result
            })
            
            if result["success"]:
                data = result["data"]
                content_texts = [item.get("text", "") for item in data.get("content", [])]
                logger.info(f"  工具响应: {content_texts[0][:100]}..." if content_texts else "无响应内容")
            else:
                logger.error(f"  工具调用失败: {result['error']}")
            
            await asyncio.sleep(1)
        
        # 5. 性能统计
        logger.info("=== 性能统计 ===")
        successful_reasoning = [r for r in reasoning_results if r["success"]]
        successful_tools = [r for r in tool_results if r["success"]]
        
        if successful_reasoning:
            avg_reasoning_time = sum(r["processing_time"] for r in successful_reasoning) / len(successful_reasoning)
            logger.info(f"推理测试平均处理时间: {avg_reasoning_time:.2f}s")
        
        if successful_tools:
            avg_tool_time = sum(r["processing_time"] for r in successful_tools) / len(successful_tools)
            logger.info(f"工具调用平均处理时间: {avg_tool_time:.2f}s")
        
        # 6. 测试总结
        logger.info("=== 测试总结 ===")
        total_tests = len(reasoning_results) + len(tool_results)
        successful_tests = len(successful_reasoning) + len(successful_tools)
        success_rate = (successful_tests / total_tests) * 100 if total_tests > 0 else 0
        
        logger.info(f"总测试数: {total_tests}")
        logger.info(f"成功测试: {successful_tests}")
        logger.info(f"成功率: {success_rate:.1f}%")
        
        return {
            "reasoning_results": reasoning_results,
            "tool_results": tool_results,
            "success_rate": success_rate
        }

async def quick_test():
    """快速测试 - 用于开发调试"""
    async with DeepSeekReasoningTester() as tester:
        # 健康检查
        if not await tester.health_check():
            logger.error("服务不可用")
            return False
        
        # 简单推理测试
        result = await tester.test_reasoning_endpoint(
            "解释人工智能的基本概念和应用领域"
        )
        
        if result["success"]:
            data = result["data"]
            logger.info("快速测试成功!")
            logger.info(f"推理过程: {data.get('reasoning', '')}")
            logger.info(f"最终答案: {data.get('answer', '')}")
            logger.info(f"处理时间: {result['processing_time']:.2f}s")
            return True
        else:
            logger.error(f"快速测试失败: {result['error']}")
            return False

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="DeepSeek Reasoning 服务测试脚本")
    parser.add_argument("mode", nargs="?", default="", help="兼容模式参数: quick 或 full")
    parser.add_argument("--quick", action="store_true", help="运行快速测试模式")
    parser.add_argument("--full", action="store_true", help="运行综合测试模式")
    args = parser.parse_args()

    run_quick = args.quick or args.mode == "quick"
    run_full = args.full or args.mode == "full" or not run_quick

    if run_quick:
        result = asyncio.run(quick_test())
        exit(0 if result else 1)
    else:
        logger.info("开始DeepSeek Reasoning服务综合测试...")
        result = asyncio.run(run_comprehensive_test())
        if result and result.get("success_rate", 0) >= 80:
            logger.info("测试完成 - 服务运行正常")
            exit(0)
        else:
            logger.error("测试完成 - 服务存在问题")
            exit(1)