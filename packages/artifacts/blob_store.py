from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass
from typing import Protocol


class ArtifactBlobStore(Protocol):
    kind: str

    async def put(self, key: str, content: bytes, *, content_type: str) -> None: ...
    async def get(self, key: str) -> bytes: ...
    async def delete(self, key: str) -> None: ...


@dataclass(slots=True)
class S3ArtifactBlobStore:
    """S3-compatible durable artifact storage (AWS S3, Cloudflare R2, MinIO)."""

    bucket: str
    endpoint_url: str | None = None
    region_name: str | None = None
    access_key_id: str | None = None
    secret_access_key: str | None = None
    session_token: str | None = None
    kind: str = "s3"

    def _client(self):
        try:
            import boto3
        except ImportError as error:  # pragma: no cover - deployment dependency guard
            raise RuntimeError("boto3 is required for S3 artifact storage") from error
        kwargs = {}
        if self.endpoint_url:
            kwargs["endpoint_url"] = self.endpoint_url
        if self.region_name:
            kwargs["region_name"] = self.region_name
        if self.access_key_id:
            kwargs["aws_access_key_id"] = self.access_key_id
        if self.secret_access_key:
            kwargs["aws_secret_access_key"] = self.secret_access_key
        if self.session_token:
            kwargs["aws_session_token"] = self.session_token
        return boto3.client("s3", **kwargs)

    async def put(self, key: str, content: bytes, *, content_type: str) -> None:
        raw = bytes(content)

        def _put():
            self._client().put_object(
                Bucket=self.bucket,
                Key=key,
                Body=raw,
                ContentType=content_type or "application/octet-stream",
                Metadata={"operly-artifact": "1"},
            )

        await asyncio.to_thread(_put)

    async def get(self, key: str) -> bytes:
        def _get():
            response = self._client().get_object(Bucket=self.bucket, Key=key)
            body = response["Body"]
            try:
                return body.read()
            finally:
                try:
                    body.close()
                except Exception:
                    pass

        return bytes(await asyncio.to_thread(_get))

    async def delete(self, key: str) -> None:
        await asyncio.to_thread(
            self._client().delete_object,
            Bucket=self.bucket,
            Key=key,
        )


def configured_blob_store() -> ArtifactBlobStore | None:
    """Return the production object backend when configured, else DB fallback.

    Cloudflare R2 example:
      OPERLY_ARTIFACT_S3_BUCKET=operly-artifacts
      OPERLY_ARTIFACT_S3_ENDPOINT=https://<account>.r2.cloudflarestorage.com
      OPERLY_ARTIFACT_S3_REGION=auto
      OPERLY_ARTIFACT_S3_ACCESS_KEY_ID=...
      OPERLY_ARTIFACT_S3_SECRET_ACCESS_KEY=...
    """

    bucket = os.getenv("OPERLY_ARTIFACT_S3_BUCKET", "").strip()
    if not bucket:
        return None
    return S3ArtifactBlobStore(
        bucket=bucket,
        endpoint_url=os.getenv("OPERLY_ARTIFACT_S3_ENDPOINT", "").strip() or None,
        region_name=os.getenv("OPERLY_ARTIFACT_S3_REGION", "").strip() or None,
        access_key_id=os.getenv("OPERLY_ARTIFACT_S3_ACCESS_KEY_ID", "").strip() or None,
        secret_access_key=os.getenv("OPERLY_ARTIFACT_S3_SECRET_ACCESS_KEY", "").strip() or None,
        session_token=os.getenv("OPERLY_ARTIFACT_S3_SESSION_TOKEN", "").strip() or None,
    )
