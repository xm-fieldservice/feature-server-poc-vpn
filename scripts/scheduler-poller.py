#!/usr/bin/env python3
"""
临时/测试调度脚本：MinIO 轮询 → DeepSeek Reasoning → 回写结果
- 功能：
  1) 轮询 MinIO 指定桶与输入前缀（默认 inputs/），查找形如 inputs/<job_id>/input.json 的对象
  2) 若 outputs/<job_id>/result.json 已存在则幂等跳过；否则调用 reasoning 接口并写回结果
  3) 将已处理的输入移动到 processed/<job_id>/input.json（copy+delete）
- 依赖：boto3, httpx
- 示例：
  python scripts/scheduler-poller.py --base-url http://localhost:2471 --endpoint http://localhost:9000 \
    --access-key admin --secret-key changeme123 --bucket hybrid-compute --interval 2 --once
"""

import os
import re
import json
import time
import hashlib
import argparse
from datetime import datetime
from typing import Optional, Dict, Any, List

import httpx
import boto3
from botocore.exceptions import ClientError


def sha256_hex_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def ensure_bucket(s3, bucket: str):
    try:
        s3.head_bucket(Bucket=bucket)
    except ClientError as e:
        code = int(e.response.get("ResponseMetadata", {}).get("HTTPStatusCode", 0))
        if code == 404:
            s3.create_bucket(Bucket=bucket)
        else:
            raise


def s3_key_exists(s3, bucket: str, key: str) -> bool:
    try:
        s3.head_object(Bucket=bucket, Key=key)
        return True
    except ClientError as e:
        code = int(e.response.get("ResponseMetadata", {}).get("HTTPStatusCode", 0))
        if code in (403, 404):
            return False
        raise


def s3_get_json(s3, bucket: str, key: str) -> Dict[str, Any]:
    obj = s3.get_object(Bucket=bucket, Key=key)
    data = obj["Body"].read()
    return json.loads(data.decode("utf-8"))


def s3_put_json(s3, bucket: str, key: str, data: Dict[str, Any]):
    body = json.dumps(data, ensure_ascii=False).encode("utf-8")
    s3.put_object(Bucket=bucket, Key=key, Body=body, ContentType="application/json; charset=utf-8")


def s3_copy(s3, bucket: str, src_key: str, dst_key: str):
    s3.copy_object(Bucket=bucket, CopySource={"Bucket": bucket, "Key": src_key}, Key=dst_key)


def s3_delete(s3, bucket: str, key: str):
    s3.delete_object(Bucket=bucket, Key=key)


def list_input_objects(s3, bucket: str, prefix: str) -> List[str]:
    keys: List[str] = []
    continuation_token: Optional[str] = None
    while True:
        kwargs = {
            "Bucket": bucket,
            "Prefix": prefix,
            "MaxKeys": 1000,
        }
        if continuation_token:
            kwargs["ContinuationToken"] = continuation_token
        resp = s3.list_objects_v2(**kwargs)
        for item in resp.get("Contents", []) :
            keys.append(item["Key"])
        if resp.get("IsTruncated"):
            continuation_token = resp.get("NextContinuationToken")
        else:
            break
    return keys


def extract_job_id_from_key(key: str, input_prefix: str) -> Optional[str]:
    # 匹配: inputs/<job_id>/input.json
    pattern = re.compile(rf"^{re.escape(input_prefix.rstrip('/') + '/')}(?P<job>[A-Za-z0-9_-]+)/input\.json$")
    m = pattern.match(key)
    if not m:
        return None
    return m.group("job")


def process_one(s3, bucket: str, base_url: str, input_key: str, input_prefix: str,
                output_prefix: str, processed_prefix: str, timeout: float = 30.0) -> bool:
    job_id = extract_job_id_from_key(input_key, input_prefix)
    if not job_id:
        print(f"[skip] 非预期输入对象: {input_key}")
        return False

    output_key = f"{output_prefix.rstrip('/')}/{job_id}/result.json"
    processed_key = f"{processed_prefix.rstrip('/')}/{job_id}/input.json"

    # 幂等：输出已存在则跳过，并将输入移至 processed
    if s3_key_exists(s3, bucket, output_key):
        try:
            s3_copy(s3, bucket, input_key, processed_key)
            s3_delete(s3, bucket, input_key)
            print(f"[idempotent-skip] 已存在输出，移动输入至 {processed_key}")
        except Exception as e:
            print(f"[warn] 移动输入失败但允许跳过: {e}")
        return False

    # 取输入
    try:
        payload = s3_get_json(s3, bucket, input_key)
    except Exception as e:
        print(f"[error] 读取输入失败: {input_key}, {e}")
        return False

    prompt = payload.get("prompt")
    context = payload.get("context")
    max_tokens = int(payload.get("max_tokens", 1000))
    temperature = float(payload.get("temperature", 0.7))

    # 调用 reasoning
    created_ts = datetime.utcnow().isoformat() + "Z"
    t0 = time.time()
    started_ts = datetime.utcnow().isoformat() + "Z"
    try:
        with httpx.Client(timeout=timeout) as client:
            r = client.post(
                f"{base_url.rstrip('/')}/reasoning",
                json={
                    "prompt": prompt,
                    "context": context,
                    "max_tokens": max_tokens,
                    "temperature": temperature,
                },
            )
            r.raise_for_status()
            resp = r.json()
    except Exception as e:
        print(f"[error] 推理失败: job_id={job_id}, {e}")
        return False
    t1 = time.time()
    completed_ts = datetime.utcnow().isoformat() + "Z"

    # 生成结果文档
    try:
        input_bytes = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        result_doc = {
            "job_id": job_id,
            "type": "deepseek.reasoning.v1",
            "input_ref": f"s3://{bucket}/{input_key}",
            "output_ref": f"s3://{bucket}/{output_key}",
            "reasoning": resp.get("reasoning"),
            "answer": resp.get("answer"),
            "tokens_used": resp.get("tokens_used"),
            "processing_time": resp.get("processing_time"),
            "e2e_latency": round(t1 - t0, 3),
            "created": created_ts,
            "state": "succeeded",
            "attempts": 1,
            "error_code": None,
            "timestamps": {
                "created": created_ts,
                "started": started_ts,
                "completed": completed_ts,
            },
            "checksums": {
                "input": sha256_hex_bytes(input_bytes),
                "output": sha256_hex_bytes(json.dumps(resp, ensure_ascii=False).encode("utf-8")),
            },
        }
        s3_put_json(s3, bucket, output_key, result_doc)
    except Exception as e:
        print(f"[error] 写出结果失败: {output_key}, {e}")
        return False

    # 移动输入到 processed
    try:
        s3_copy(s3, bucket, input_key, processed_key)
        s3_delete(s3, bucket, input_key)
    except Exception as e:
        print(f"[warn] 移动输入失败: {input_key} -> {processed_key}, {e}")

    print(f"[ok] 完成: job_id={job_id}, output={output_key}")
    return True


def main():
    parser = argparse.ArgumentParser(description="临时调度脚本：MinIO轮询→Reasoning→回写结果")
    parser.add_argument("--base-url", default=os.getenv("DEESEEK_BASE_URL", "http://localhost:2471"))
    parser.add_argument("--endpoint", default=os.getenv("MINIO_ENDPOINT", "http://localhost:9000"))
    parser.add_argument("--access-key", default=os.getenv("MINIO_ROOT_USER", os.getenv("MINIO_ACCESS_KEY", "admin")))
    parser.add_argument("--secret-key", default=os.getenv("MINIO_ROOT_PASSWORD", os.getenv("MINIO_SECRET_KEY", "changeme123")))
    parser.add_argument("--region", default=os.getenv("AWS_REGION", "us-east-1"))
    parser.add_argument("--bucket", default=os.getenv("MINIO_BUCKET", "hybrid-compute"))
    parser.add_argument("--input-prefix", default=os.getenv("SCHED_INPUT_PREFIX", "inputs"))
    parser.add_argument("--output-prefix", default=os.getenv("SCHED_OUTPUT_PREFIX", "outputs"))
    parser.add_argument("--processed-prefix", default=os.getenv("SCHED_PROCESSED_PREFIX", "processed"))
    parser.add_argument("--interval", type=float, default=float(os.getenv("SCHED_INTERVAL", "2")))
    parser.add_argument("--timeout", type=float, default=float(os.getenv("SCHED_TIMEOUT", "30")))
    parser.add_argument("--once", action="store_true", help="仅扫描一轮后退出")
    parser.add_argument("--stop-after", type=int, default=0, help="处理N个对象后退出，0表示不限制")
    args = parser.parse_args()

    s3 = boto3.client(
        "s3",
        endpoint_url=args.endpoint,
        aws_access_key_id=args.access_key,
        aws_secret_access_key=args.secret_key,
        region_name=args.region,
        verify=False,
    )

    ensure_bucket(s3, args.bucket)

    processed_count = 0
    while True:
        keys = list_input_objects(s3, args.bucket, args.input_prefix.rstrip('/') + '/')
        # 仅处理 input.json 文件
        keys = [k for k in keys if k.endswith("/input.json")]
        if not keys and args.once:
            print("[info] 无待处理输入，退出（once 模式）")
            break

        for key in keys:
            ok = process_one(
                s3=s3,
                bucket=args.bucket,
                base_url=args.base_url,
                input_key=key,
                input_prefix=args.input_prefix,
                output_prefix=args.output_prefix,
                processed_prefix=args.processed_prefix,
                timeout=args.timeout,
            )
            if ok:
                processed_count += 1
                if args.stop_after and processed_count >= args.stop_after:
                    print(f"[info] 达到 stop-after={args.stop_after}，退出")
                    return 0
        if args.once:
            break
        time.sleep(max(0.1, args.interval))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
