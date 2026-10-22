"""受控对象存储：不接收远程 URL，所有对象键由服务端生成。"""

import io
from pathlib import Path

try:
    import boto3  # type: ignore[import-untyped]  # 官方包未携带类型标记，边界在本适配器内。
    from botocore.config import Config  # type: ignore[import-untyped]
    from PIL import Image, UnidentifiedImageError
except ImportError:
    boto3 = None
    Image = None  # type: ignore[assignment]
    UnidentifiedImageError = ValueError  # type: ignore[misc, assignment]

from a_commerce.conf.settings import Settings


def sanitize_image(body: bytes) -> tuple[bytes, str, str]:
    if Image is None:
        raise ValueError("未安装 storage 可选依赖")
    # 解码后重新编码可移除元数据和附加内容；大小上限同时限制压缩包和解码像素。
    Image.MAX_IMAGE_PIXELS = 20_000_000
    try:
        with Image.open(io.BytesIO(body), formats=["PNG", "JPEG", "WEBP"]) as source:
            if (
                source.format not in {"PNG", "JPEG", "WEBP"}
                or source.width * source.height > 20_000_000
            ):
                raise ValueError("仅允许不超过 2000 万像素的 PNG/JPEG/WebP")
            source.load()
            converted = source.convert("RGB")
            output = io.BytesIO()
            converted.save(output, format="JPEG", quality=90)
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
        raise ValueError("图片无法解码或超过安全像素限制") from exc
    result = output.getvalue()
    if len(result) > 8 * 1024 * 1024:
        raise ValueError("重新编码后的图片超过 8 MiB")
    return result, "image/jpeg", "jpg"


def put(settings: Settings, key: str, data: bytes, content_type: str) -> None:
    if settings.storage_backend == "s3":
        if boto3 is None or not settings.s3_bucket:
            raise ValueError("未配置 S3 存储")
        client = boto3.client(
            "s3",
            endpoint_url=settings.s3_endpoint,
            config=Config(connect_timeout=2, read_timeout=5, retries={"max_attempts": 1}),
        )
        client.put_object(Bucket=settings.s3_bucket, Key=key, Body=data, ContentType=content_type)
    else:
        path = settings.files_directory / key
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_bytes(data)
        temporary.replace(path)


def local_path(settings: Settings, key: str) -> Path:
    return settings.files_directory / key


def delete_object(settings: Settings, key: str, backend: str) -> None:
    """只接受数据库台账中由服务端生成的媒体键；删除不存在对象也是成功。"""
    if not key.startswith("media/") or "/" in key[6:] or ".." in key:
        raise ValueError("非法媒体对象键")
    if backend == "s3":
        if boto3 is None or not settings.s3_bucket:
            raise ValueError("未配置 S3 存储")
        try:
            client = boto3.client(
                "s3",
                endpoint_url=settings.s3_endpoint,
                config=Config(connect_timeout=2, read_timeout=5, retries={"max_attempts": 1}),
            )
            client.delete_object(Bucket=settings.s3_bucket, Key=key)
        except Exception as exc:
            # SDK 的网络/权限异常统一交给持久重试，避免 worker 反复退出而不增加尝试次数。
            raise OSError("对象存储删除失败") from exc
    elif backend == "local":
        path = local_path(settings, key)
        path.unlink(missing_ok=True)
        path.with_suffix(".tmp").unlink(missing_ok=True)
    else:
        raise ValueError("未知存储后端")


def signed_url(settings: Settings, key: str) -> str:
    if boto3 is None or not settings.s3_bucket:
        raise ValueError("未配置 S3 存储")
    client = boto3.client("s3", endpoint_url=settings.s3_endpoint)
    result: str = client.generate_presigned_url(
        "get_object", Params={"Bucket": settings.s3_bucket, "Key": key}, ExpiresIn=300
    )
    return result
