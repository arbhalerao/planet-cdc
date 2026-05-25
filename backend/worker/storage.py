from __future__ import annotations

import sys
from typing import Any

import boto3
from botocore.client import Config
from botocore.exceptions import ClientError

from app.config import settings


_client = None


def s3() -> Any:
    global _client
    if _client is None:
        endpoint = settings.MINIO_ENDPOINT
        if not endpoint.startswith("http"):
            endpoint = f"http://{endpoint}"
        _client = boto3.client(
            "s3",
            endpoint_url=endpoint,
            aws_access_key_id=settings.MINIO_ROOT_USER,
            aws_secret_access_key=settings.MINIO_ROOT_PASSWORD,
            region_name="us-east-1",
            config=Config(signature_version="s3v4"),
        )
    return _client


def band_key(workflow_id, workflow_item_id, name: str) -> str:
    return f"{workflow_id}/{workflow_item_id}/{name}.tif"


def band_exists(key: str) -> bool:
    try:
        s3().head_object(Bucket=settings.MINIO_BUCKET, Key=key)
        return True
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code", "")
        if code in ("404", "NoSuchKey", "NotFound"):
            return False
        raise


def _cog_profile(array, transform, crs) -> dict:
    import numpy as np

    return {
        "driver": "COG",
        "dtype": "float32",
        "count": 1,
        "height": int(array.shape[0]),
        "width": int(array.shape[1]),
        "transform": transform,
        "crs": crs,
        "nodata": float(np.nan),
        "compress": "DEFLATE",
        "predictor": 2,
        "blocksize": 512,
    }


def write_cog_to_disk(path: str, array, transform, crs) -> None:
    """Write a single-band float32 COG to a local file."""
    import numpy as np
    import rasterio

    profile = _cog_profile(array, transform, crs)
    with rasterio.open(path, "w", **profile) as dst:
        dst.write(array.astype(np.float32), 1)


def upload_file(key: str, path: str) -> None:
    s3().upload_file(path, settings.MINIO_BUCKET, key)


def put_band_cog(key: str, array, transform, crs) -> None:
    """Write a COG straight to MinIO (used for derived rasters, no disk staging)."""
    import numpy as np
    import rasterio
    from rasterio.io import MemoryFile

    profile = _cog_profile(array, transform, crs)
    with MemoryFile() as memfile:
        with memfile.open(**profile) as dst:
            dst.write(array.astype(np.float32), 1)
        s3().put_object(
            Bucket=settings.MINIO_BUCKET,
            Key=key,
            Body=memfile.read(),
            ContentType="image/tiff",
        )


def get_band_array(key: str):
    """Read a COG from MinIO. Returns (ndarray float32, transform, crs)."""
    import numpy as np
    import rasterio
    from rasterio.io import MemoryFile

    obj = s3().get_object(Bucket=settings.MINIO_BUCKET, Key=key)
    body = obj["Body"].read()
    with MemoryFile(body) as memfile:
        with memfile.open() as src:
            arr = src.read(1).astype(np.float32)
            return arr, src.transform, src.crs


def get_band_bytes(key: str) -> bytes | None:
    """Return the raw object bytes from MinIO, or None if missing. boto3-only — safe to call from the API container."""
    try:
        obj = s3().get_object(Bucket=settings.MINIO_BUCKET, Key=key)
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code", "")
        if code in ("NoSuchKey", "404", "NotFound"):
            return None
        raise
    return obj["Body"].read()


def delete_workflow_prefix(workflow_id) -> None:
    """Delete every object under {workflow_id}/. No-op if the prefix is empty."""
    client = s3()
    prefix = f"{workflow_id}/"
    paginator = client.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=settings.MINIO_BUCKET, Prefix=prefix):
        contents = page.get("Contents") or []
        if not contents:
            continue
        client.delete_objects(
            Bucket=settings.MINIO_BUCKET,
            Delete={"Objects": [{"Key": o["Key"]} for o in contents]},
        )


def init_bucket() -> None:
    """Create the artifacts bucket if missing. Idempotent."""
    client = s3()
    try:
        client.create_bucket(Bucket=settings.MINIO_BUCKET)
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code", "")
        if code not in ("BucketAlreadyOwnedByYou", "BucketAlreadyExists"):
            raise


def _main(argv: list[str]) -> int:
    if len(argv) < 2 or argv[1] != "init":
        print("usage: python -m worker.storage init", file=sys.stderr)
        return 2
    init_bucket()
    print(f"bucket '{settings.MINIO_BUCKET}' ready")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv))
