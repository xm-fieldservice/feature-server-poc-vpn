#!/usr/bin/env python3
"""
DeepSeek Reasoning 端到端(E2E) 验证脚本（临时/测试脚本）
- 功能：
  1) 在 MinIO/hybrid-compute 桶下写入输入对象
  2) 调用 deepseek-reasoning /reasoning 接口执行推理
  3) 将推理结果写回输出对象，并打印端到端统计
- 依赖：boto3, httpx
- 运行示例：
  python scripts/e2e-deepseek-job.py --prompt "解释量子计算的基本原理"
  python scripts/e2e-deepseek-job.py --base-url http://localhost:2471 --bucket hybrid-compute \
    --endpoint http://localhost:9000 --access-key admin --secret-key changeme123
"""

import os
import json
import time
import uuid
import hashlib
import argparse
from datetime import datetime

import httpx
import boto3
from botocore.exceptions import ClientError


def sha256_hex(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def ensure_bucket(s3, bucket: str):
    try:
        s3.head_bucket(Bucket=bucket)
    except ClientError as e:
        code = int(e.response.get("ResponseMetadata", {}).get("HTTPStatusCode", 0))
        if code == 404:
            s3.create_bucket(Bucket=bucket)
        else:
            raise


def put_json(s3, bucket: str, key: str, data: dict):
    body = json.dumps(data, ensure_ascii=False).encode("utf-8")
    s3.put_object(Bucket=bucket, Key=key, Body=body, ContentType="application/json; charset=utf-8")


def main():
    parser = argparse.ArgumentParser(description="DeepSeek Reasoning E2E 验证脚本")
    parser.add_argument("--base-url", default=os.getenv("DEESEEK_BASE_URL", "http://localhost:2471"))
    parser.add_argument("--bucket", default=os.getenv("MINIO_BUCKET", "hybrid-compute"))
    parser.add_argument("--endpoint", default=os.getenv("MINIO_ENDPOINT", "http://localhost:9000"))
    parser.add_argument("--access-key", default=os.getenv("MINIO_ROOT_USER", os.getenv("MINIO_ACCESS_KEY", "admin")))
    parser.add_argument("--secret-key", default=os.getenv("MINIO_ROOT_PASSWORD", os.getenv("MINIO_SECRET_KEY", "changeme123")))
    parser.add_argument("--region", default=os.getenv("AWS_REGION", "us-east-1"))
    parser.add_argument("--prompt", default="解释人工智能的基本概念和应用领域")
    parser.add_argument("--context", default=None)
    parser.add_argument("--max-tokens", type=int, default=1000)
    parser.add_argument("--temperature", type=float, default=0.7)
    parser.add_argument("--input-key", default=None)
    parser.add_argument("--output-key", default=None)
    args = parser.parse_args()

    job_id = f"e2e-{uuid.uuid4().hex[:8]}"
    input_key = args.input_key or f"inputs/{job_id}/input.json"
    output_key = args.output_key or f"outputs/{job_id}/result.json"

    # 初始化 S3 客户端（MinIO）
    s3 = boto3.client(
        "s3",
        endpoint_url=args.endpoint,
        aws_access_key_id=args.access_key,
        aws_secret_access_key=args.secret_key,
        region_name=args.region,
        verify=False,
    )

    # 确保桶存在
    ensure_bucket(s3, args.bucket)

    # 1) 写入输入对象
    input_payload = {
        "job_id": job_id,
        "prompt": args.prompt,
        "context": args.context,
        "max_tokens": args.max_tokens,
        "temperature": args.temperature,
        "created": datetime.utcnow().isoformat() + "Z",
    }
    put_json(s3, args.bucket, input_key, input_payload)

    # 2) 调用推理服务
    t0 = time.time()
    try:
        with httpx.Client(timeout=30.0) as client:
            r = client.post(
                f"{args.base_url}/reasoning",
                json={
                    "prompt": args.prompt,
                    "context": args.context,
                    "max_tokens": args.max_tokens,
                    "temperature": args.temperature,
                },
            )
            r.raise_for_status()
            reasoning_resp = r.json()
    except Exception as e:
        print(f"❌ 推理请求失败: {e}")
        return 1
    t1 = time.time()

    # 3) 写回输出对象
    result_doc = {
        "job_id": job_id,
        "input_ref": f"s3://{args.bucket}/{input_key}",
        "output_ref": f"s3://{args.bucket}/{output_key}",
        "reasoning": reasoning_resp.get("reasoning"),
        "answer": reasoning_resp.get("answer"),
        "tokens_used": reasoning_resp.get("tokens_used"),
        "processing_time": reasoning_resp.get("processing_time"),
        "e2e_latency": round(t1 - t0, 3),
        "created": datetime.utcnow().isoformat() + "Z",
        "checksums": {
            "input": sha256_hex(json.dumps(input_payload, ensure_ascii=False)),
            "output": sha256_hex(json.dumps(reasoning_resp, ensure_ascii=False)),
        },
    }

    try:
        put_json(s3, args.bucket, output_key, result_doc)
    except Exception as e:
        print(f"❌ 输出对象写入失败: {e}")
        return 1

    # 打印摘要
    print("✅ E2E 成功")
    print(f"  job_id        : {job_id}")
    print(f"  input_ref     : s3://{args.bucket}/{input_key}")
    print(f"  output_ref    : s3://{args.bucket}/{output_key}")
    print(f"  e2e_latency   : {result_doc['e2e_latency']}s")
    print(f"  tokens_used   : {reasoning_resp.get('tokens_used')}")
    print(f"  processing_time: {reasoning_resp.get('processing_time')}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
