#!/usr/bin/env python3
"""
临时/测试脚本：DeepSeek Reasoning Canary 干跑与回滚验证
- 功能：
  1) 启动候选实例（指定端口与并发参数）
  2) 进行压力探测（并发、请求量、超时），计算429比例与P95
  3) 基于阈值判定通过/失败；失败则回滚（终止候选实例）
  4) 输出JSON化报告
- 依赖：aiohttp
示例：
  python scripts/canary-dryrun.py --candidate-port 2473 --max-concurrency 2 \
    --concurrency 6 --requests 60 --timeout 5 --threshold-429 0.05 --threshold-p95 0.5
"""

import asyncio
import os
import sys
import time
import json
import signal
import argparse
import subprocess
from statistics import median
import aiohttp


def percentile(sorted_list, p: float) -> float:
    if not sorted_list:
        return 0.0
    k = (len(sorted_list) - 1) * p
    f = int(k)
    c = min(f + 1, len(sorted_list) - 1)
    if f == c:
        return float(sorted_list[int(k)])
    d0 = sorted_list[f] * (c - k)
    d1 = sorted_list[c] * (k - f)
    return float(d0 + d1)


async def probe(url: str, concurrency: int, total_requests: int, timeout: float, payload: dict):
    results = {"ok": 0, "r429": 0, "r5xx": 0, "rother": 0, "timeout": 0, "errors": 0, "latencies": []}
    conn = aiohttp.TCPConnector(limit=concurrency)
    async with aiohttp.ClientSession(connector=conn) as session:
        sem = asyncio.Semaphore(concurrency)

        async def one():
            try:
                t0 = time.time()
                async with session.post(url, json=payload, timeout=timeout) as resp:
                    el = time.time() - t0
                    code = resp.status
                    if code == 200:
                        results["ok"] += 1
                        results["latencies"].append(el)
                    elif code == 429:
                        results["r429"] += 1
                    elif 500 <= code < 600:
                        results["r5xx"] += 1
                    else:
                        results["rother"] += 1
            except asyncio.TimeoutError:
                results["timeout"] += 1
            except Exception:
                results["errors"] += 1

        async def bound():
            async with sem:
                await one()

        tasks = [asyncio.create_task(bound()) for _ in range(total_requests)]
        t0 = time.time()
        await asyncio.gather(*tasks)
        total_time = time.time() - t0

    lats = sorted(results["latencies"]) if results["latencies"] else []
    p50 = median(lats) if lats else 0.0
    p95 = percentile(lats, 0.95)
    p99 = percentile(lats, 0.99)
    total = total_requests
    r429 = results["r429"]
    ok = results["ok"]
    rps = ok / total_time if total_time > 0 else 0.0

    return {
        "summary": results,
        "p50": p50,
        "p95": p95,
        "p99": p99,
        "r429_ratio": (r429 / total) if total > 0 else 0.0,
        "success_rate": (ok / total) if total > 0 else 0.0,
        "rps": rps,
        "total_time": total_time,
    }


def wait_health(port: int, retries: int = 40, sleep_s: float = 0.25) -> bool:
    import http.client
    conn = http.client.HTTPConnection("localhost", port, timeout=2)
    for _ in range(retries):
        try:
            conn.request("GET", "/health")
            resp = conn.getresponse()
            if resp.status == 200:
                return True
        except Exception:
            pass
        time.sleep(sleep_s)
    return False


def start_candidate(port: int, max_concurrency: int):
    env = os.environ.copy()
    env["SERVER_PORT"] = str(port)
    env["MAX_CONCURRENCY"] = str(max_concurrency)
    # 启动子进程
    p = subprocess.Popen([sys.executable, "src/local/deepseek-reasoning-server.py"], env=env)
    return p


def stop_proc(p: subprocess.Popen):
    try:
        if os.name == "nt":
            p.terminate()
        else:
            p.send_signal(signal.SIGTERM)
        try:
            p.wait(timeout=5)
        except Exception:
            p.kill()
    except Exception:
        pass


def main():
    ap = argparse.ArgumentParser(description="DeepSeek Canary 干跑与回滚")
    ap.add_argument("--candidate-port", type=int, default=2473)
    ap.add_argument("--max-concurrency", type=int, default=2)
    ap.add_argument("--concurrency", type=int, default=6)
    ap.add_argument("--requests", type=int, default=60)
    ap.add_argument("--timeout", type=float, default=5.0)
    ap.add_argument("--threshold-429", type=float, default=0.05)
    ap.add_argument("--threshold-p95", type=float, default=0.5)
    ap.add_argument("--prompt", default="Canary干跑：解释AI应用的主要领域")
    ap.add_argument("--max-tokens", type=int, default=200)
    ap.add_argument("--temperature", type=float, default=0.7)
    args = ap.parse_args()

    payload = {
        "prompt": args.prompt,
        "context": None,
        "max_tokens": args.max_tokens,
        "temperature": args.temperature,
    }

    report = {
        "candidate_port": args.candidate_port,
        "max_concurrency": args.max_concurrency,
        "probe": {
            "concurrency": args.concurrency,
            "requests": args.requests,
            "timeout": args.timeout,
        },
        "thresholds": {
            "r429": args.threshold_429,
            "p95": args.threshold_p95,
        },
        "result": None,
        "passed": None,
        "rollback": None,
    }

    p = start_candidate(args.candidate_port, args.max_concurrency)
    try:
        if not wait_health(args.candidate_port):
            report["result"] = {"error": "candidate health check failed"}
            report["passed"] = False
            print(json.dumps(report, ensure_ascii=False, indent=2))
            return 2

        url = f"http://localhost:{args.candidate_port}/reasoning"
        bench = asyncio.run(probe(url, args.concurrency, args.requests, args.timeout, payload))
        report["result"] = bench

        passed = (bench["r429_ratio"] <= args.threshold_429) and (bench["p95"] <= args.threshold_p95)
        report["passed"] = bool(passed)

        if not passed:
            # 回滚：终止候选
            stop_proc(p)
            report["rollback"] = {"action": "terminate_candidate", "status": "done"}
            print(json.dumps(report, ensure_ascii=False, indent=2))
            return 1

        # 通过：也终止候选（干跑不保留）
        stop_proc(p)
        report["rollback"] = {"action": "terminate_candidate", "status": "done"}
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0

    finally:
        try:
            if p.poll() is None:
                stop_proc(p)
        except Exception:
            pass


if __name__ == "__main__":
    sys.exit(main())
