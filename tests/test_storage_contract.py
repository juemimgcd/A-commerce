"""S3 SDK 契约验证使用 Stubber，不把它记为真实桶或生产权限验收。"""

from pathlib import Path

import boto3
import pytest
from botocore.stub import Stubber

from a_commerce.conf.settings import Settings
from a_commerce.integrations import storage


def test_s3_put_delete_and_presigned_read(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    # 显式临时凭证阻止 SDK 搜索本机真实 AWS profile 或实例凭证。
    client = boto3.client(
        "s3",
        region_name="us-east-1",
        aws_access_key_id="testing",
        aws_secret_access_key="testing",
        endpoint_url="http://127.0.0.1:19000",
    )
    monkeypatch.setattr(storage.boto3, "client", lambda *args, **kwargs: client)
    settings = Settings(
        _env_file=None,
        database_url="postgresql+asyncpg://unused/unused",
        redis_url="redis://127.0.0.1/0",
        amqp_url="amqp://unused/",
        provider_key="testing-" * 8,
        callback_secret="testing-" * 8,
        storage_backend="s3",
        s3_bucket="commerce-test",
        files_directory=tmp_path,
    )
    with Stubber(client) as stub:
        stub.add_response(
            "put_object",
            {},
            {
                "Bucket": "commerce-test",
                "Key": "media/test.jpg",
                "Body": b"image",
                "ContentType": "image/jpeg",
            },
        )
        stub.add_response("delete_object", {}, {"Bucket": "commerce-test", "Key": "media/test.jpg"})
        storage.put(settings, "media/test.jpg", b"image", "image/jpeg")
        storage.delete_object(settings, "media/test.jpg", "s3")
        stub.assert_no_pending_responses()
    url = storage.signed_url(settings, "media/test.jpg")
    assert url.startswith("http://127.0.0.1:19000/commerce-test/media/test.jpg?")
    assert "Expires=" in url or "X-Amz-Expires=" in url
    client.close()


@pytest.mark.parametrize(
    "key", ["../secret", "media/../../secret", "media/nested/a.jpg", "exports/a.csv"]
)
def test_media_delete_rejects_uncontrolled_keys(key: str, tmp_path: Path) -> None:
    settings = Settings(
        _env_file=None,
        database_url="postgresql+asyncpg://unused/unused",
        redis_url="redis://127.0.0.1/0",
        amqp_url="amqp://unused/",
        provider_key="testing-" * 8,
        callback_secret="testing-" * 8,
        files_directory=tmp_path,
    )
    with pytest.raises(ValueError, match="非法媒体对象键"):
        storage.delete_object(settings, key, "local")
