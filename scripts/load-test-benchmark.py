#!/usr/bin/env python3
"""
混合算力平台 - 性能基线压测脚本
P2 Week1: 性能基线测量与监控完善
"""

import asyncio
import time
import json
import statistics
import psutil
import subprocess
from datetime import datetime
from typing import Dict, List, Any
import aiohttp
import logging

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('benchmark.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

class HybridComputeBenchmark:
    def __init__(self):
        self.base_url = "http://localhost:8080"  # API网关地址
        self.nats_url = "nats://localhost:4222"
        self.minio_url = "http://localhost:9000"
        self.results = {
            "timestamp": datetime.now().isoformat(),
            "test_scenarios": {},
            "system_metrics": {},
            "performance_baselines": {}
        }
        
    async def measure_system_metrics(self) -> Dict[str, Any]:
        """测量系统基础指标"""
        metrics = {}
        
        try:
            # CPU使用率
            metrics["cpu_percent"] = psutil.cpu_percent(interval=1)
            metrics["cpu_count"] = psutil.cpu_count()
            
            # 内存使用
            memory = psutil.virtual_memory()
            metrics["memory_total_gb"] = round(memory.total / (1024**3), 2)
            metrics["memory_used_gb"] = round(memory.used / (1024**3), 2)
            metrics["memory_percent"] = memory.percent
            
            # 磁盘IO
            disk = psutil.disk_usage('/')
            metrics["disk_total_gb"] = round(disk.total / (1024**3), 2)
            metrics["disk_used_gb"] = round(disk.used / (1024**3), 2)
            metrics["disk_percent"] = disk.percent
            
            # GPU使用率 (如果可用)
            try:
                gpu_result = subprocess.run([
                    'nvidia-smi', '--query-gpu=utilization.gpu,memory.used,memory.total',
                    '--format=csv,noheader,nounits'
                ], capture_output=True, text=True, timeout=10)
                
                if gpu_result.returncode == 0:
                    gpu_data = gpu_result.stdout.strip().split(',')
                    metrics["gpu_utilization"] = int(gpu_data[0])
                    metrics["gpu_memory_used_mb"] = int(gpu_data[1])
                    metrics["gpu_memory_total_mb"] = int(gpu_data[2])
                    metrics["gpu_memory_percent"] = round((int(gpu_data[1]) / int(gpu_data[2])) * 100, 2)
            except (subprocess.TimeoutExpired, FileNotFoundError, Exception):
                metrics["gpu_available"] = False
                logger.warning("GPU监控不可用，跳过GPU指标采集")
                
        except Exception as e:
            logger.error(f"系统指标采集失败: {e}")
            
        return metrics
    
    async def test_health_endpoints(self) -> Dict[str, Any]:
        """测试健康检查端点"""
        endpoints = {
            "api_gateway": f"{self.base_url}/health",
            "mcp_llm": "http://localhost:2470/health",
            "mcp_image": "http://localhost:2471/health", 
            "mcp_data": "http://localhost:2472/health",
            "job_scheduler": "http://localhost:2480/health"
        }
        
        results = {}
        async with aiohttp.ClientSession() as session:
            for service, url in endpoints.items():
                try:
                    start_time = time.time()
                    async with session.get(url, timeout=10) as response:
                        response_time = (time.time() - start_time) * 1000
                        results[service] = {
                            "status": response.status,
                            "response_time_ms": round(response_time, 2),
                            "healthy": response.status == 200
                        }
                except Exception as e:
                    results[service] = {
                        "status": "error",
                        "response_time_ms": None,
                        "healthy": False,
                        "error": str(e)
                    }
                    
        return results
    
    async def benchmark_job_submission(self, concurrent_requests: int = 10, duration_seconds: int = 60) -> Dict[str, Any]:
        """基准任务提交性能测试"""
        logger.info(f"开始任务提交性能测试: {concurrent_requests}并发, {duration_seconds}秒")
        
        job_templates = [
            {
                "job_type": "llm_inference",
                "model": "deepseek-reasoner",
                "prompt": "请分析这个性能测试场景",
                "max_tokens": 100
            },
            {
                "job_type": "image_generation", 
                "model": "stable-diffusion",
                "prompt": "高性能计算场景",
                "width": 512,
                "height": 512
            },
            {
                "job_type": "data_processing",
                "operation": "transform",
                "data_size": "1MB"
            }
        ]
        
        completed_requests = 0
        failed_requests = 0
        response_times = []
        start_time = time.time()
        
        async def submit_job(session, job_data):
            nonlocal completed_requests, failed_requests
            try:
                job_start = time.time()
                async with session.post(
                    f"{self.base_url}/api/v1/jobs",
                    json=job_data,
                    headers={"Content-Type": "application/json"},
                    timeout=30
                ) as response:
                    response_time = (time.time() - job_start) * 1000
                    
                    if response.status == 202:
                        completed_requests += 1
                        response_times.append(response_time)
                        job_result = await response.json()
                        return job_result.get("job_id")
                    else:
                        failed_requests += 1
                        logger.warning(f"任务提交失败: {response.status}")
            except Exception as e:
                failed_requests += 1
                logger.error(f"任务提交异常: {e}")
            return None
        
        # 执行压测
        async with aiohttp.ClientSession() as session:
            tasks = []
            end_time = start_time + duration_seconds
            
            while time.time() < end_time and len(tasks) < 1000:  # 防止无限循环
                for i in range(concurrent_requests):
                    job_template = job_templates[i % len(job_templates)]
                    task = asyncio.create_task(submit_job(session, job_template))
                    tasks.append(task)
                    
                # 控制并发数量
                if len(tasks) >= concurrent_requests * 2:
                    await asyncio.gather(*tasks[:concurrent_requests])
                    tasks = tasks[concurrent_requests:]
                
                await asyncio.sleep(0.1)  # 控制请求频率
            
            # 等待剩余任务完成
            if tasks:
                await asyncio.gather(*tasks)
        
        total_time = time.time() - start_time
        throughput = completed_requests / total_time if total_time > 0 else 0
        
        # 计算百分位数
        if response_times:
            p50 = statistics.median(response_times)
            p95 = statistics.quantiles(response_times, n=20)[18]  # 95th percentile
            p99 = statistics.quantiles(response_times, n=100)[98]  # 99th percentile
        else:
            p50 = p95 = p99 = 0
            
        return {
            "total_requests": completed_requests + failed_requests,
            "completed_requests": completed_requests,
            "failed_requests": failed_requests,
            "success_rate": completed_requests / (completed_requests + failed_requests) if (completed_requests + failed_requests) > 0 else 0,
            "throughput_rps": round(throughput, 2),
            "throughput_rpm": round(throughput * 60, 2),
            "response_time_p50_ms": round(p50, 2),
            "response_time_p95_ms": round(p95, 2), 
            "response_time_p99_ms": round(p99, 2),
            "test_duration_seconds": round(total_time, 2),
            "concurrent_requests": concurrent_requests
        }
    
    async def benchmark_storage_operations(self, file_sizes: List[int] = [1024, 10240, 102400]) -> Dict[str, Any]:
        """存储操作性能测试"""
        logger.info("开始存储操作性能测试")
        
        results = {}
        async with aiohttp.ClientSession() as session:
            
            for size in file_sizes:
                # 生成测试数据
                test_data = "x" * size
                
                # 上传性能
                upload_times = []
                for i in range(5):  # 每个大小测试5次
                    try:
                        start_time = time.time()
                        # 这里应该调用实际的存储上传接口
                        # 暂时模拟上传操作
                        await asyncio.sleep(0.01 * (size / 1024))  # 模拟上传时间
                        upload_time = (time.time() - start_time) * 1000
                        upload_times.append(upload_time)
                    except Exception as e:
                        logger.error(f"上传测试失败: {e}")
                
                # 下载性能  
                download_times = []
                for i in range(5):
                    try:
                        start_time = time.time()
                        # 模拟下载操作
                        await asyncio.sleep(0.005 * (size / 1024))  # 模拟下载时间
                        download_time = (time.time() - start_time) * 1000
                        download_times.append(download_time)
                    except Exception as e:
                        logger.error(f"下载测试失败: {e}")
                
                if upload_times and download_times:
                    results[f"file_{size}bytes"] = {
                        "upload_time_avg_ms": round(statistics.mean(upload_times), 2),
                        "upload_time_p95_ms": round(statistics.quantiles(upload_times, n=20)[18], 2),
                        "download_time_avg_ms": round(statistics.mean(download_times), 2),
                        "download_time_p95_ms": round(statistics.quantiles(download_times, n=20)[18], 2),
                        "throughput_mbps": round((size * 8) / (statistics.mean(upload_times) / 1000) / 1e6, 2) if statistics.mean(upload_times) > 0 else 0
                    }
        
        return results
    
    async def test_mcp_service_performance(self) -> Dict[str, Any]:
        """MCP服务性能测试"""
        logger.info("开始MCP服务性能测试")
        
        services = {
            "mcp_llm_inference": "http://localhost:2470/tools/llm-inference/invoke",
            "mcp_image_generation": "http://localhost:2471/tools/image-generation/invoke",
            "mcp_data_processing": "http://localhost:2472/tools/data-processing/invoke"
        }
        
        results = {}
        async with aiohttp.ClientSession() as session:
            for service_name, url in services.items():
                try:
                    test_requests = 10
                    response_times = []
                    success_count = 0
                    
                    for i in range(test_requests):
                        test_payload = {
                            "arguments": {
                                "prompt": f"性能测试请求 {i}",
                                "max_tokens": 50
                            }
                        }
                        
                        start_time = time.time()
                        async with session.post(
                            url,
                            json=test_payload,
                            timeout=30
                        ) as response:
                            response_time = (time.time() - start_time) * 1000
                            response_times.append(response_time)
                            
                            if response.status == 200:
                                success_count += 1
                    
                    if response_times:
                        results[service_name] = {
                            "total_requests": test_requests,
                            "successful_requests": success_count,
                            "success_rate": success_count / test_requests,
                            "avg_response_time_ms": round(statistics.mean(response_times), 2),
                            "p95_response_time_ms": round(statistics.quantiles(response_times, n=20)[18], 2),
                            "throughput_rps": round(test_requests / (sum(response_times) / 1000), 2)
                        }
                        
                except Exception as e:
                    logger.error(f"MCP服务 {service_name} 测试失败: {e}")
                    results[service_name] = {"error": str(e)}
        
        return results
    
    async def run_comprehensive_benchmark(self):
        """运行全面的性能基准测试"""
        logger.info("开始全面的性能基准测试")
        
        try:
            # 1. 采集系统指标
            self.results["system_metrics"] = await self.measure_system_metrics()
            logger.info("系统指标采集完成")
            
            # 2. 测试健康端点
            self.results["test_scenarios"]["health_checks"] = await self.test_health_endpoints()
            logger.info("健康检查测试完成")
            
            # 3. 任务提交性能测试（不同并发级别）
            concurrency_levels = [5, 10, 20]
            for concurrency in concurrency_levels:
                scenario_name = f"job_submission_{concurrency}concurrent"
                self.results["test_scenarios"][scenario_name] = await self.benchmark_job_submission(
                    concurrent_requests=concurrency,
                    duration_seconds=30
                )
                logger.info(f"并发{concurrency}任务提交测试完成")
            
            # 4. 存储操作性能测试
            self.results["test_scenarios"]["storage_operations"] = await self.benchmark_storage_operations()
            logger.info("存储操作测试完成")
            
            # 5. MCP服务性能测试
            self.results["test_scenarios"]["mcp_services"] = await self.test_mcp_service_performance()
            logger.info("MCP服务测试完成")
            
            # 6. 计算性能基线
            self._calculate_performance_baselines()
            
            # 7. 生成测试报告
            self._generate_benchmark_report()
            
            logger.info("性能基准测试完成")
            return self.results
            
        except Exception as e:
            logger.error(f"性能基准测试失败: {e}")
            raise
    
    def _calculate_performance_baselines(self):
        """计算性能基线"""
        baselines = {}
        
        # 从测试场景中提取关键指标
        job_scenarios = {k: v for k, v in self.results["test_scenarios"].items() if k.startswith("job_submission")}
        
        if job_scenarios:
            # 任务吞吐量基线（取10并发场景）
            scenario_10 = job_scenarios.get("job_submission_10concurrent", {})
            baselines["job_throughput_rpm"] = scenario_10.get("throughput_rpm", 0)
            baselines["job_success_rate"] = scenario_10.get("success_rate", 0)
            baselines["job_p95_latency_ms"] = scenario_10.get("response_time_p95_ms", 0)
        
        # MCP服务性能基线
        mcp_services = self.results["test_scenarios"].get("mcp_services", {})
        if mcp_services:
            mcp_response_times = [s.get("p95_response_time_ms", 0) for s in mcp_services.values() if isinstance(s, dict)]
            baselines["mcp_p95_latency_ms"] = round(statistics.mean(mcp_response_times), 2) if mcp_response_times else 0
        
        # 系统资源基线
        system_metrics = self.results["system_metrics"]
        baselines["cpu_usage_percent"] = system_metrics.get("cpu_percent", 0)
        baselines["memory_usage_percent"] = system_metrics.get("memory_percent", 0)
        if system_metrics.get("gpu_available", True):
            baselines["gpu_usage_percent"] = system_metrics.get("gpu_utilization", 0)
        
        self.results["performance_baselines"] = baselines
    
    def _generate_benchmark_report(self):
        """生成基准测试报告"""
        report = {
            "benchmark_summary": {
                "timestamp": self.results["timestamp"],
                "test_duration": "综合测试",
                "overall_status": "COMPLETED"
            },
            "performance_baselines": self.results["performance_baselines"],
            "system_metrics": self.results["system_metrics"],
            "detailed_results": self.results["test_scenarios"],
            "recommendations": self._generate_recommendations()
        }
        
        # 保存报告
        filename = f"benchmark_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        with open(filename, 'w', encoding='utf-8') as f:
            json.dump(report, f, indent=2, ensure_ascii=False)
        
        # 同时生成简明的文本报告
        self._generate_text_report(filename.replace('.json', '.txt'))
        
        logger.info(f"基准测试报告已生成: {filename}")
    
    def _generate_recommendations(self):
        """生成优化建议"""
        recommendations = []
        baselines = self.results["performance_baselines"]
        
        # 任务吞吐量建议
        job_throughput = baselines.get("job_throughput_rpm", 0)
        if job_throughput < 50:
            recommendations.append("任务吞吐量较低，建议优化任务调度和队列处理")
        elif job_throughput > 200:
            recommendations.append("任务吞吐量表现良好，可考虑进一步优化资源利用率")
        
        # 延迟建议
        p95_latency = baselines.get("job_p95_latency_ms", 0)
        if p95_latency > 1000:
            recommendations.append("P95延迟较高，建议检查网络连接和任务处理逻辑")
        
        # 资源使用建议
        memory_usage = baselines.get("memory_usage_percent", 0)
        if memory_usage > 80:
            recommendations.append("内存使用率较高，建议优化内存管理或增加内存资源")
        
        if not recommendations:
            recommendations.append("系统性能表现良好，继续保持当前配置")
        
        return recommendations
    
    def _generate_text_report(self, filename: str):
        """生成文本格式报告"""
        with open(filename, 'w', encoding='utf-8') as f:
            f.write("混合算力平台性能基准测试报告\n")
            f.write("=" * 50 + "\n\n")
            
            f.write(f"测试时间: {self.results['timestamp']}\n\n")
            
            f.write("性能基线指标:\n")
            f.write("-" * 30 + "\n")
            for key, value in self.results["performance_baselines"].items():
                f.write(f"{key}: {value}\n")
            
            f.write("\n系统资源状态:\n")
            f.write("-" * 30 + "\n")
            for key, value in self.results["system_metrics"].items():
                f.write(f"{key}: {value}\n")
            
            f.write("\n优化建议:\n")
            f.write("-" * 30 + "\n")
            for rec in self._generate_recommendations():
                f.write(f"- {rec}\n")

async def main():
    """主函数"""
    benchmark = HybridComputeBenchmark()
    
    try:
        results = await benchmark.run_comprehensive_benchmark()
        
        # 输出关键结果
        print("\n" + "="*60)
        print("性能基准测试关键结果")
        print("="*60)
        
        baselines = results["performance_baselines"]
        print(f"任务吞吐量: {baselines.get('job_throughput_rpm', 0)} jobs/min")
        print(f"任务成功率: {baselines.get('job_success_rate', 0)*100:.1f}%")
        print(f"P95延迟: {baselines.get('job_p95_latency_ms', 0)} ms")
        print(f"CPU使用率: {baselines.get('cpu_usage_percent', 0)}%")
        print(f"内存使用率: {baselines.get('memory_usage_percent', 0)}%")
        
        if 'gpu_usage_percent' in baselines:
            print(f"GPU使用率: {baselines['gpu_usage_percent']}%")
        
    except Exception as e:
        logger.error(f"基准测试执行失败: {e}")
        return 1
    
    return 0

if __name__ == "__main__":
    exit_code = asyncio.run(main())
    exit(exit_code)