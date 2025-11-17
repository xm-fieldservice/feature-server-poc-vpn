#!/usr/bin/env python3
"""
临时/测试压测脚本：DeepSeek Reasoning /reasoning 并发与时延基线
- 功能：
  1) 并发发送 POST /reasoning 请求，采集响应时间与状态码
  2) 统计 P50/P95/P99、成功率、429/5xx/超时比例
  3) 输出参数建议：MAX_CONCURRENCY、请求超时
- 依赖：aiohttp
- 示例：
  python scripts/bench-reasoning.py --url http://localhost:2471/reasoning --concurrency 4 --requests 40
"""

import asyncio
import aiohttp
import time
import json
import argparse
from statistics import median


def percentile(sorted_list, p):
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


async def worker(idx, session, url, payload, timeout, results):
    try:
        t0 = time.time()
        async with session.post(url, json=payload, timeout=timeout) as resp:
            elapsed = (time.time() - t0)
            code = resp.status
            if code == 200:
                results["latencies"].append(elapsed)
                results["ok"] += 1
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


async def run_bench(url, concurrency, total_requests, timeout, payload):
    results = {
        "ok": 0,
        "r429": 0,
        "r5xx": 0,
        "rother": 0,
        "timeout": 0,
        "errors": 0,
        "latencies": [],
    }

    conn = aiohttp.TCPConnector(limit=concurrency)
    async with aiohttp.ClientSession(connector=conn) as session:
        sem = asyncio.Semaphore(concurrency)

        async def bound_worker(i):
            async with sem:
                await worker(i, session, url, payload, timeout, results)

        tasks = [asyncio.create_task(bound_worker(i)) for i in range(total_requests)]
        t0 = time.time()
        await asyncio.gather(*tasks)
        total_time = time.time() - t0

    lats = sorted(results["latencies"]) if results["latencies"] else []
    p50 = median(lats) if lats else 0.0
    p95 = percentile(lats, 0.95)
    p99 = percentile(lats, 0.99)
    total = total_requests
    ok = results["ok"]
    err = total - ok
    r429 = results["r429"]
    r5xx = results["r5xx"]
    tout = results["timeout"]

    rps = ok / total_time if total_time > 0 else 0.0
    srate = ok / total if total > 0 else 0.0

    print("\n=== DeepSeek Reasoning 并发压测结果 ===")
    print(f"目标: {url}")
    print(f"并发: {concurrency}, 总请求: {total}")
    print(f"成功: {ok}, 429: {r429}, 5xx: {r5xx}, 超时: {tout}, 其它: {results['rother']+results['errors']}")
    print(f"吞吐: {rps:.2f} rps, 总时长: {total_time:.3f}s, 成功率: {srate*100:.1f}%")
    print(f"延迟 P50: {p50*1000:.1f} ms, P95: {p95*1000:.1f} ms, P99: {p99*1000:.1f} ms")

    # 参数建议（启发式）
    rec_conc = concurrency
    notes = []
    r429_rate = r429 / total if total > 0 else 0.0
    if r429_rate > 0.05:
        rec_conc = max(1, int(concurrency * 0.5))
        notes.append("429 过高，建议下调并发")
    if p95 > 1.5:  # 秒
        notes.append("P95 > 1.5s，建议提高超时或降低并发")

    print("\n=== 参数建议 ===")
    print(f"建议 MAX_CONCURRENCY: {rec_conc}")
    print(f"建议 请求超时: {max(timeout, 3.0):.1f}s (当前 {timeout:.1f}s)")
    if notes:
        print("建议说明:")
        for n in notes:
            print(f"- {n}")


def main():
    parser = argparse.ArgumentParser(description="DeepSeek Reasoning 并发压测")
    parser.add_argument("--url", default="http://localhost:2471/reasoning")
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--requests", type=int, default=40)
    parser.add_argument("--timeout", type=float, default=5.0)
    parser.add_argument("--prompt", default="性能压测请求：解释AI应用的主要领域")
    parser.add_argument("--context", default=None)
    parser.add_argument("--max-tokens", type=int, default=200)
    parser.add_argument("--temperature", type=float, default=0.7)
    args = parser.parse_args()

    payload = {
        "prompt": args.prompt,
        "context": args.context,
        "max_tokens": args.max_tokens,
        "temperature": args.temperature,
    }

    asyncio.run(run_bench(
        url=args.url,
        concurrency=args.concurrency,
        total_requests=args.requests,
        timeout=args.timeout,
        payload=payload,
    ))


if __name__ == "__main__":
    main()
