#!/usr/bin/env python3
"""
混合算力平台 - 对象存储 SDK 蓝图
基于P0阶段冻结的数据旁路契约v1实现的对象存储SDK设计
支持预签名URL、分片上传、断点续传等功能
"""

import os
import json
import logging
import hashlib
import asyncio
import aiohttp
import aiofiles
from datetime import datetime, timedelta
from typing import Dict, List, Any, Optional, AsyncGenerator
from urllib.parse import urlparse, urlencode
from dataclasses import dataclass
from enum import Enum

import boto3
from botocore.exceptions import ClientError
from pydantic import BaseModel, Field

# 配置日志
logging.basicConfig(
    level=os.getenv("LOGGING_LEVEL", "INFO"),
    format='{"timestamp": "%(asctime)s", "level": "%(levelname)s", "name": "%(name)s", "message": "%(message)s", "trace_id": "%(trace_id)s", "span_id": "%(span_id)s"}',
    datefmt="%Y-%m-%dT%H:%M:%SZ"
)
logger = logging.getLogger("storage-sdk")

# 枚举定义
class StorageClass(str, Enum):
    """存储类别"""
    STANDARD = "STANDARD"
    INFREQUENT_ACCESS = "INFREQUENT_ACCESS"
    GLACIER = "GLACIER"

class UploadStatus(str, Enum):
    """上传状态"""
    INITIALIZED = "initialized"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    ABORTED = "aborted"

# Pydantic 模型 - 使用 snake_case 命名
class PresignedURLResponse(BaseModel):
    """预签名URL响应"""
    url: str = Field(..., description="预签名URL")
    method: str = Field(..., description="HTTP方法")
    expires_at: str = Field(..., description="过期时间")
    headers: Optional[Dict[str, str]] = Field(None, description="请求头")

class MultipartUploadPart(BaseModel):
    """多段上传分片"""
    part_number: int = Field(..., description="分片编号")
    url: str = Field(..., description="预签名上传URL")
    size: int = Field(..., description="分片大小")

class MultipartUploadResponse(BaseModel):
    """多段上传响应"""
    upload_id: str = Field(..., description="上传ID")
    parts: List[MultipartUploadPart] = Field(..., description="分片列表")
    expires_at: str = Field(..., description="过期时间")

class UploadProgress(BaseModel):
    """上传进度"""
    upload_id: str = Field(..., description="上传ID")
    total_size: int = Field(..., description="总大小")
    uploaded_size: int = Field(..., description="已上传大小")
    progress_percentage: float = Field(..., description="进度百分比")
    status: UploadStatus = Field(..., description="上传状态")
    parts_completed: List[int] = Field(..., description="已完成分片")

class ObjectMetadata(BaseModel):
    """对象元数据"""
    key: str = Field(..., description="对象键")
    size: int = Field(..., description="对象大小")
    last_modified: str = Field(..., description="最后修改时间")
    etag: str = Field(..., description="ETag")
    storage_class: StorageClass = Field(..., description="存储类别")
    content_type: Optional[str] = Field(None, description="内容类型")

@dataclass
class StorageConfig:
    """存储配置"""
    endpoint_url: str = "http://localhost:9000"
    access_key: str = "minioadmin"
    secret_key: str = "minioadmin"
    bucket_name: str = "hybrid-compute"
    region: str = "us-east-1"
    secure: bool = False
    
    # 上传配置
    chunk_size: int = 64 * 1024 * 1024  # 64MB
    max_concurrent_uploads: int = 4
    presigned_url_ttl: int = 1800  # 30分钟
    
    # 重试配置
    max_retries: int = 3
    retry_delay: float = 2.0

class StorageSDK:
    """
    对象存储 SDK 主类
    提供预签名URL、分片上传、断点续传等功能
    """
    
    def __init__(self, config: StorageConfig):
        self.config = config
        self._setup_s3_client()
        self._setup_async_session()
    
    def _setup_s3_client(self):
        """设置S3客户端"""
        self.s3_client = boto3.client(
            's3',
            endpoint_url=self.config.endpoint_url,
            aws_access_key_id=self.config.access_key,
            aws_secret_access_key=self.config.secret_key,
            region_name=self.config.region,
            verify=self.config.secure
        )
    
    def _setup_async_session(self):
        """设置异步会话"""
        self.session = aiohttp.ClientSession()
    
    async def close(self):
        """关闭SDK"""
        await self.session.close()
    
    def generate_presigned_url(
        self,
        key: str,
        method: str = "get_object",
        expires_in: int = None,
        content_type: str = None
    ) -> PresignedURLResponse:
        """
        生成预签名URL
        支持get_object, put_object, delete_object等方法
        """
        expires_in = expires_in or self.config.presigned_url_ttl
        
        try:
            url = self.s3_client.generate_presigned_url(
                ClientMethod=method,
                Params={
                    'Bucket': self.config.bucket_name,
                    'Key': key
                },
                ExpiresIn=expires_in,
                HttpMethod="GET" if method == "get_object" else "PUT"
            )
            
            expires_at = (datetime.utcnow() + timedelta(seconds=expires_in)).isoformat() + "Z"
            
            headers = {}
            if content_type and method == "put_object":
                headers["Content-Type"] = content_type
            
            return PresignedURLResponse(
                url=url,
                method="GET" if method == "get_object" else "PUT",
                expires_at=expires_at,
                headers=headers
            )
            
        except ClientError as e:
            logger.error(f"Failed to generate presigned URL: {e}")
            raise StorageSDKError(f"Failed to generate presigned URL: {e}")
    
    def initiate_multipart_upload(
        self,
        key: str,
        file_size: int,
        content_type: str = "application/octet-stream"
    ) -> MultipartUploadResponse:
        """
        初始化多段上传
        自动计算分片数量和大小
        """
        try:
            # 计算分片数量
            chunk_size = self.config.chunk_size
            num_parts = (file_size + chunk_size - 1) // chunk_size
            
            # 初始化多段上传
            response = self.s3_client.create_multipart_upload(
                Bucket=self.config.bucket_name,
                Key=key,
                ContentType=content_type
            )
            upload_id = response['UploadId']
            
            # 生成分片预签名URL
            parts = []
            expires_in = self.config.presigned_url_ttl
            
            for part_number in range(1, num_parts + 1):
                part_size = min(chunk_size, file_size - (part_number - 1) * chunk_size)
                
                url = self.s3_client.generate_presigned_url(
                    ClientMethod='upload_part',
                    Params={
                        'Bucket': self.config.bucket_name,
                        'Key': key,
                        'UploadId': upload_id,
                        'PartNumber': part_number
                    },
                    ExpiresIn=expires_in
                )
                
                parts.append(MultipartUploadPart(
                    part_number=part_number,
                    url=url,
                    size=part_size
                ))
            
            expires_at = (datetime.utcnow() + timedelta(seconds=expires_in)).isoformat() + "Z"
            
            return MultipartUploadResponse(
                upload_id=upload_id,
                parts=parts,
                expires_at=expires_at
            )
            
        except ClientError as e:
            logger.error(f"Failed to initiate multipart upload: {e}")
            raise StorageSDKError(f"Failed to initiate multipart upload: {e}")
    
    async def upload_file(
        self,
        file_path: str,
        key: str,
        content_type: str = "application/octet-stream"
    ) -> ObjectMetadata:
        """
        上传文件
        自动选择单段或多段上传
        """
        file_size = os.path.getsize(file_path)
        
        # 小文件使用单段上传，大文件使用多段上传
        if file_size <= self.config.chunk_size:
            return await self._upload_single_part(file_path, key, content_type)
        else:
            return await self._upload_multipart(file_path, key, content_type)
    
    async def _upload_single_part(
        self,
        file_path: str,
        key: str,
        content_type: str
    ) -> ObjectMetadata:
        """单段上传"""
        presigned_url = self.generate_presigned_url(
            key=key,
            method="put_object",
            content_type=content_type
        )
        
        try:
            async with aiofiles.open(file_path, 'rb') as file:
                file_data = await file.read()
                
            async with self.session.put(
                presigned_url.url,
                data=file_data,
                headers=presigned_url.headers or {}
            ) as response:
                if response.status == 200:
                    # 获取对象元数据
                    return await self.get_object_metadata(key)
                else:
                    raise StorageSDKError(f"Upload failed with status {response.status}")
                    
        except Exception as e:
            logger.error(f"Single part upload failed: {e}")
            raise StorageSDKError(f"Single part upload failed: {e}")
    
    async def _upload_multipart(
        self,
        file_path: str,
        key: str,
        content_type: str
    ) -> ObjectMetadata:
        """多段上传"""
        file_size = os.path.getsize(file_path)
        upload_info = self.initiate_multipart_upload(key, file_size, content_type)
        
        parts_completed = []
        semaphore = asyncio.Semaphore(self.config.max_concurrent_uploads)
        
        async def upload_part(part: MultipartUploadPart):
            async with semaphore:
                try:
                    # 读取分片数据
                    start = (part.part_number - 1) * self.config.chunk_size
                    async with aiofiles.open(file_path, 'rb') as file:
                        await file.seek(start)
                        part_data = await file.read(part.size)
                    
                    # 上传分片
                    async with self.session.put(part.url, data=part_data) as response:
                        if response.status == 200:
                            etag = response.headers.get('ETag', '').strip('"')
                            parts_completed.append({
                                'PartNumber': part.part_number,
                                'ETag': etag
                            })
                            return True
                        else:
                            return False
                            
                except Exception as e:
                    logger.error(f"Part {part.part_number} upload failed: {e}")
                    return False
        
        # 并发上传所有分片
        tasks = [upload_part(part) for part in upload_info.parts]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        # 检查所有分片是否上传成功
        if not all(results):
            # 上传失败，中止上传
            self.s3_client.abort_multipart_upload(
                Bucket=self.config.bucket_name,
                Key=key,
                UploadId=upload_info.upload_id
            )
            raise StorageSDKError("Multipart upload failed")
        
        # 完成上传
        self.s3_client.complete_multipart_upload(
            Bucket=self.config.bucket_name,
            Key=key,
            UploadId=upload_info.upload_id,
            MultipartUpload={'Parts': parts_completed}
        )
        
        return await self.get_object_metadata(key)
    
    async def download_file(
        self,
        key: str,
        file_path: str,
        chunk_size: int = 8 * 1024 * 1024  # 8MB
    ) -> None:
        """下载文件"""
        presigned_url = self.generate_presigned_url(key=key, method="get_object")
        
        try:
            async with self.session.get(presigned_url.url) as response:
                if response.status == 200:
                    async with aiofiles.open(file_path, 'wb') as file:
                        async for chunk in response.content.iter_chunked(chunk_size):
                            await file.write(chunk)
                else:
                    raise StorageSDKError(f"Download failed with status {response.status}")
                    
        except Exception as e:
            logger.error(f"Download failed: {e}")
            raise StorageSDKError(f"Download failed: {e}")
    
    async def get_object_metadata(self, key: str) -> ObjectMetadata:
        """获取对象元数据"""
        try:
            response = self.s3_client.head_object(
                Bucket=self.config.bucket_name,
                Key=key
            )
            
            return ObjectMetadata(
                key=key,
                size=response['ContentLength'],
                last_modified=response['LastModified'].isoformat() + "Z",
                etag=response['ETag'].strip('"'),
                storage_class=StorageClass(response.get('StorageClass', 'STANDARD')),
                content_type=response.get('ContentType')
            )
            
        except ClientError as e:
            logger.error(f"Failed to get object metadata: {e}")
            raise StorageSDKError(f"Failed to get object metadata: {e}")
    
    async def list_objects(
        self,
        prefix: str = "",
        max_keys: int = 1000
    ) -> List[ObjectMetadata]:
        """列出对象"""
        try:
            response = self.s3_client.list_objects_v2(
                Bucket=self.config.bucket_name,
                Prefix=prefix,
                MaxKeys=max_keys
            )
            
            objects = []
            for obj in response.get('Contents', []):
                objects.append(ObjectMetadata(
                    key=obj['Key'],
                    size=obj['Size'],
                    last_modified=obj['LastModified'].isoformat() + "Z",
                    etag=obj['ETag'].strip('"'),
                    storage_class=StorageClass.STANDARD
                ))
            
            return objects
            
        except ClientError as e:
            logger.error(f"Failed to list objects: {e}")
            raise StorageSDKError(f"Failed to list objects: {e}")
    
    def delete_object(self, key: str) -> bool:
        """删除对象"""
        try:
            self.s3_client.delete_object(
                Bucket=self.config.bucket_name,
                Key=key
            )
            return True
        except ClientError as e:
            logger.error(f"Failed to delete object: {e}")
            return False
    
    def generate_key(
        self,
        dataset: str,
        version: str,
        filename: str,
        shard: str = None
    ) -> str:
        """
        生成对象键
        符合路径命名策略: /{dataset}/{version}/{shard}/{filename}
        """
        parts = [dataset, version]
        if shard:
            parts.append(shard)
        parts.append(filename)
        
        return "/".join(parts)
    
    def calculate_checksum(self, file_path: str) -> str:
        """计算文件校验和"""
        sha256_hash = hashlib.sha256()
        
        with open(file_path, "rb") as f:
            for chunk in iter(lambda: f.read(4096), b""):
                sha256_hash.update(chunk)
        
        return sha256_hash.hexdigest()

class StorageSDKError(Exception):
    """存储SDK异常基类"""
    pass

# 使用示例
async def example_usage():
    """SDK使用示例"""
    config = StorageConfig(
        endpoint_url="http://localhost:9000",
        access_key="minioadmin",
        secret_key="minioadmin",
        bucket_name="hybrid-compute"
    )
    
    sdk = StorageSDK(config)
    
    try:
        # 示例1: 生成预签名URL
        presigned_url = sdk.generate_presigned_url(
            key="models/llama2/v1/model.bin",
            method="get_object"
        )
        print(f"Presigned URL: {presigned_url.url}")
        
        # 示例2: 上传文件
        metadata = await sdk.upload_file(
            file_path="/path/to/local/file.bin",
            key=sdk.generate_key("models", "v1", "model.bin", "shard-001")
        )
        print(f"Upload completed: {metadata.key}")
        
        # 示例3: 下载文件
        await sdk.download_file(
            key="models/llama2/v1/model.bin",
            file_path="/path/to/download/model.bin"
        )
        print("Download completed")
        
        # 示例4: 列出对象
        objects = await sdk.list_objects(prefix="models/")
        for obj in objects:
            print(f"Object: {obj.key} ({obj.size} bytes)")
            
    except StorageSDKError as e:
        print(f"Storage SDK error: {e}")
    finally:
        await sdk.close()

if __name__ == "__main__":
    # 运行示例
    asyncio.run(example_usage())