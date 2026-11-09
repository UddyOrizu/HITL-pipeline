"""Object storage for original files and evidence packs.

Local filesystem by default; S3-compatible storage (MinIO, S3) when POR_S3_BUCKET is set; Azure Blob Storage
(managed identity) when POR_AZURE_STORAGE_ACCOUNT_URL is set. Downloads use short-lived signed URLs.
"""

from __future__ import annotations

import hashlib
import hmac
import time
from pathlib import Path
from urllib.parse import quote

from services.config import get_settings


class Storage:
    def put(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> str:
        raise NotImplementedError

    def get(self, uri: str) -> bytes:
        raise NotImplementedError

    def signed_url(self, uri: str, ttl_seconds: int = 300) -> str:
        raise NotImplementedError

    def delete(self, uri: str) -> None:
        raise NotImplementedError


class LocalStorage(Storage):
    def __init__(self, root: str):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        path = (self.root / key).resolve()
        if not str(path).startswith(str(self.root)):
            raise ValueError("invalid storage key")
        return path

    def put(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> str:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return f"local://{key}"

    def get(self, uri: str) -> bytes:
        return self._path(uri.removeprefix("local://")).read_bytes()

    def delete(self, uri: str) -> None:
        self._path(uri.removeprefix("local://")).unlink(missing_ok=True)

    def signed_url(self, uri: str, ttl_seconds: int = 300) -> str:
        key = uri.removeprefix("local://")
        exp = int(time.time()) + ttl_seconds
        sig = sign_file_key(key, exp)
        return f"{get_settings().public_base_url}/v1/files/{quote(key)}?exp={exp}&sig={sig}"


class S3Storage(Storage):
    def __init__(self):
        import boto3

        s = get_settings()
        self.bucket = s.s3_bucket
        self.client = boto3.client(
            "s3",
            endpoint_url=s.s3_endpoint,
            aws_access_key_id=s.s3_access_key,
            aws_secret_access_key=s.s3_secret_key,
        )
        try:
            self.client.head_bucket(Bucket=self.bucket)
        except Exception:  # noqa: BLE001
            self.client.create_bucket(Bucket=self.bucket)

    def put(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> str:
        self.client.put_object(
            Bucket=self.bucket, Key=key, Body=data, ContentType=content_type, ServerSideEncryption="AES256"
        )
        return f"s3://{self.bucket}/{key}"

    def get(self, uri: str) -> bytes:
        key = uri.removeprefix(f"s3://{self.bucket}/")
        return self.client.get_object(Bucket=self.bucket, Key=key)["Body"].read()

    def delete(self, uri: str) -> None:
        self.client.delete_object(Bucket=self.bucket, Key=uri.removeprefix(f"s3://{self.bucket}/"))

    def signed_url(self, uri: str, ttl_seconds: int = 300) -> str:
        key = uri.removeprefix(f"s3://{self.bucket}/")
        return self.client.generate_presigned_url(
            "get_object", Params={"Bucket": self.bucket, "Key": key}, ExpiresIn=ttl_seconds
        )


class AzureBlobStorage(Storage):
    """Azure Blob Storage with the app's managed identity (DefaultAzureCredential): no account keys in config.
    Needs the azure extra: pip install -e ".[azure]"."""

    def __init__(self):
        from azure.identity import DefaultAzureCredential
        from azure.storage.blob import BlobServiceClient

        s = get_settings()
        self.account_url = s.azure_storage_account_url.rstrip("/")
        self.container = s.azure_storage_container
        self.service = BlobServiceClient(self.account_url, credential=DefaultAzureCredential())
        self.client = self.service.get_container_client(self.container)
        try:
            self.client.create_container()
        except Exception:  # noqa: BLE001  (already exists, or no permission to create: it must exist then)
            pass

    def _name(self, uri: str) -> str:
        return uri.removeprefix(f"azblob://{self.container}/")

    def put(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> str:
        from azure.storage.blob import ContentSettings

        self.client.upload_blob(key, data, overwrite=True, content_settings=ContentSettings(content_type=content_type))
        return f"azblob://{self.container}/{key}"

    def get(self, uri: str) -> bytes:
        return self.client.download_blob(self._name(uri)).readall()

    def delete(self, uri: str) -> None:
        self.client.delete_blob(self._name(uri), delete_snapshots="include")

    def signed_url(self, uri: str, ttl_seconds: int = 300) -> str:
        from datetime import UTC, datetime, timedelta

        from azure.storage.blob import BlobSasPermissions, generate_blob_sas

        start = datetime.now(UTC) - timedelta(minutes=1)
        expiry = datetime.now(UTC) + timedelta(seconds=ttl_seconds)
        key = self.service.get_user_delegation_key(start, expiry)  # managed identity: user delegation SAS
        name = self._name(uri)
        sas = generate_blob_sas(self.service.account_name, self.container, name, user_delegation_key=key,
                                permission=BlobSasPermissions(read=True), expiry=expiry, start=start)
        return f"{self.account_url}/{self.container}/{quote(name)}?{sas}"


def sign_file_key(key: str, exp: int) -> str:
    msg = f"{key}:{exp}".encode()
    return hmac.new(get_settings().secret_key.encode(), msg, hashlib.sha256).hexdigest()


def verify_file_signature(key: str, exp: int, sig: str) -> bool:
    return exp >= time.time() and hmac.compare_digest(sign_file_key(key, exp), sig)


_storage: Storage | None = None


def get_storage() -> Storage:
    global _storage
    if _storage is None:
        s = get_settings()
        if s.azure_storage_account_url:
            _storage = AzureBlobStorage()
        elif s.s3_bucket:
            _storage = S3Storage()
        else:
            _storage = LocalStorage(s.storage_dir)
    return _storage


def reset_storage() -> None:
    global _storage
    _storage = None
