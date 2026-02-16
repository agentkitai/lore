"""Docker secrets and AWS Secrets Manager resolution (LO-E8)."""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Environment variables that support _FILE suffix
FILE_SUFFIX_VARS = ("DATABASE_URL", "REDIS_URL", "LORE_ROOT_KEY")


def resolve_file_env(name: str) -> Optional[str]:
    """Resolve an env var, checking for _FILE suffix first.

    If ``<name>_FILE`` is set, read the file and return its contents.
    ``_FILE`` takes precedence over the plain env var.
    Falls back to the plain env var if ``_FILE`` is not set.
    Returns None if neither is set.
    """
    file_path = os.environ.get(f"{name}_FILE")
    if file_path:
        try:
            value = Path(file_path).read_text().strip()
            logger.info("Resolved %s from file %s", name, file_path)
            return value
        except OSError as exc:
            logger.error("Failed to read %s_FILE=%s: %s", name, file_path, exc)
            raise
    return os.environ.get(name)


def resolve_aws_secrets(arn: str) -> dict[str, str]:
    """Fetch a JSON secret from AWS Secrets Manager and return as dict.

    Requires ``boto3`` to be installed. Returns an empty dict on import error.
    """
    try:
        import json

        import boto3  # type: ignore[import-untyped]
    except ImportError:
        logger.warning("boto3 not installed — skipping AWS Secrets Manager resolution")
        return {}

    try:
        client = boto3.client("secretsmanager")
        response = client.get_secret_value(SecretId=arn)
        secret_str = response.get("SecretString", "{}")
        secrets: dict[str, str] = json.loads(secret_str)
        logger.info("Resolved %d keys from AWS secret %s", len(secrets), arn)
        return secrets
    except Exception as exc:
        logger.error("Failed to fetch AWS secret %s: %s", arn, exc)
        return {}


def apply_secrets_to_env() -> None:
    """Resolve all supported _FILE vars and optional AWS secrets into env.

    Call this early at startup, before Settings.from_env().
    """
    # AWS Secrets Manager (lowest priority — set env vars that aren't already set)
    aws_arn = os.environ.get("AWS_SECRET_ARN")
    if aws_arn:
        aws_secrets = resolve_aws_secrets(aws_arn)
        for key, value in aws_secrets.items():
            key_upper = key.upper()
            if key_upper not in os.environ:
                os.environ[key_upper] = value

    # _FILE suffix resolution (highest priority)
    for var_name in FILE_SUFFIX_VARS:
        resolved = resolve_file_env(var_name)
        if resolved is not None:
            os.environ[var_name] = resolved
