#!/usr/bin/env python3
"""Migrate legacy PowerPoint outputs into canonical session Workspaces."""

from __future__ import annotations

import argparse
import hashlib
import json

import boto3
from botocore.exceptions import ClientError


def workspace_id(user_id: str, session_id: str) -> str:
    source = f"{user_id}\0{session_id}".encode("utf-8")
    return hashlib.sha256(source).hexdigest()[:48]


def object_exists(s3, bucket: str, key: str) -> bool:
    try:
        s3.head_object(Bucket=bucket, Key=key)
        return True
    except ClientError as error:
        if error.response.get("Error", {}).get("Code") in {
            "404",
            "NoSuchKey",
            "NotFound",
        }:
            return False
        raise


def migrate(bucket: str, region: str, apply: bool) -> dict[str, int]:
    s3 = boto3.client("s3", region_name=region)
    paginator = s3.get_paginator("list_objects_v2")
    counts = {
        "eligible": 0,
        "copied": 0,
        "already_migrated": 0,
        "input_name_collision": 0,
        "ignored": 0,
    }

    for page in paginator.paginate(Bucket=bucket, Prefix="documents/"):
        for item in page.get("Contents", []):
            key = item["Key"]
            parts = key.split("/", 4)
            if (
                len(parts) != 5
                or parts[0] != "documents"
                or parts[3] != "powerpoint"
                or not parts[4].lower().endswith(".pptx")
            ):
                counts["ignored"] += 1
                continue

            _, user_id, session_id, _, filename = parts
            root = f"code-interpreter-workspace/{workspace_id(user_id, session_id)}"
            destination = f"{root}/artifacts/powerpoint/{filename}"
            input_key = f"{root}/inputs/{filename}"
            counts["eligible"] += 1

            if object_exists(s3, bucket, destination):
                counts["already_migrated"] += 1
                continue
            if object_exists(s3, bucket, input_key):
                counts["input_name_collision"] += 1
                continue
            if apply:
                s3.copy_object(
                    Bucket=bucket,
                    Key=destination,
                    CopySource={"Bucket": bucket, "Key": key},
                    MetadataDirective="COPY",
                )
            counts["copied"] += 1

    return counts


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bucket", required=True)
    parser.add_argument("--region", default="us-west-2")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Copy objects. Without this flag, only report the dry-run counts.",
    )
    args = parser.parse_args()
    result = migrate(args.bucket, args.region, args.apply)
    result["dry_run"] = int(not args.apply)
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
