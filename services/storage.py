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
        # is_relative_to, not a string prefix: /data/storage-other must not pass for root /data/storage.
        if path == self.root or not path.is_relative_to(self.root):
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


def _error_code(exc: Exception) -> str:
    """The error code of a botocore ClientError (e.g. "404", "NoSuchBucket", "403"), or "" for anything else."""
    response = getattr(exc, "response", None) or {}
    code = str((response.get("Error") or {}).get("Code") or "")
    status = (response.get("ResponseMetadata") or {}).get("HTTPStatusCode")
    return code or (str(status) if status else "")


class S3Storage(Storage):
    def __init__(self, client=None):
        s = get_settings()
        self.bucket = s.s3_bucket
        # Empty POR_S3_SERVER_SIDE_ENCRYPTION sends no header (MinIO without a KMS rejects it).
        self.sse = (s.s3_server_side_encryption or "").strip() or None
        if client is None:
            import boto3

            client = boto3.client(
                "s3",
                endpoint_url=s.s3_endpoint,
                aws_access_key_id=s.s3_access_key,
                aws_secret_access_key=s.s3_secret_key,
            )
        self.client = client
        try:
            self.client.head_bucket(Bucket=self.bucket)
        except Exception as exc:
            # Create the bucket only when it does not exist. Anything else (wrong credentials, no permission,
            # endpoint down) is a configuration error that must surface, not be papered over.
            if _error_code(exc) not in ("404", "NoSuchBucket", "NotFound"):
                raise
            self.client.create_bucket(Bucket=self.bucket)

    def put(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> str:
        extra = {"ServerSideEncryption": self.sse} if self.sse else {}
        self.client.put_object(Bucket=self.bucket, Key=key, Body=data, ContentType=content_type, **extra)
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

    def __init__(self, service=None):
        s = get_settings()
        self.account_url = s.azure_storage_account_url.rstrip("/")
        self.container = s.azure_storage_container
        if service is None:
            from azure.identity import DefaultAzureCredential
            from azure.storage.blob import BlobServiceClient

            service = BlobServiceClient(self.account_url, credential=DefaultAzureCredential())
        self.service = service
        self.client = self.service.get_container_client(self.container)
        # Create the container only when it is missing. Wrong credentials or a missing role assignment must
        # surface here, not on the first upload.
        if not self.client.exists():
            try:
                self.client.create_container()
            except Exception as exc:
                if _azure_status(exc) != 409:  # 409: created meanwhile by another replica
                    raise

    def _name(self, uri: str) -> str:
        return uri.removeprefix(f"azblob://{self.container}/")

    def put(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> str:
        try:
            from azure.storage.blob import ContentSettings

            settings = ContentSettings(content_type=content_type)
        except ImportError:  # tests with a fake client
            settings = None
        self.client.upload_blob(key, data, overwrite=True, content_settings=settings)
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


def _azure_status(exc: Exception) -> int | None:
    return getattr(exc, "status_code", None)


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
