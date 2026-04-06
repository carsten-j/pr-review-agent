from __future__ import annotations

import hashlib
import hmac
import logging
from typing import Annotated

from fastapi import Depends, Header, HTTPException, Request

logger = logging.getLogger(__name__)


def verify_github_signature(payload_body: bytes, signature: str, secret: str) -> bool:
    expected = (
        "sha256=" + hmac.new(secret.encode(), payload_body, hashlib.sha256).hexdigest()
    )
    return hmac.compare_digest(expected, signature)


def verify_bitbucket_signature(
    payload_body: bytes, signature: str, secret: str
) -> bool:
    """Verify Bitbucket webhook HMAC-SHA256 signature (X-Hub-Signature header)."""
    expected = (
        "sha256=" + hmac.new(secret.encode(), payload_body, hashlib.sha256).hexdigest()
    )
    return hmac.compare_digest(expected, signature)


async def _get_raw_body(request: Request) -> bytes:
    return await request.body()


async def verify_webhook_signature(
    body: Annotated[bytes, Depends(_get_raw_body)],
    x_hub_signature_256: Annotated[str | None, Header()] = None,
) -> bytes:
    from pr_review_agent.main import get_settings

    settings = get_settings()

    if x_hub_signature_256 is None:
        logger.warning("Missing X-Hub-Signature-256 header")
        raise HTTPException(status_code=401, detail="Missing signature header")

    if not verify_github_signature(
        body, x_hub_signature_256, settings.github_webhook_secret
    ):
        logger.warning("Invalid webhook signature")
        raise HTTPException(status_code=401, detail="Invalid signature")

    return body


async def verify_bitbucket_webhook_signature(
    body: Annotated[bytes, Depends(_get_raw_body)],
    x_hub_signature: Annotated[str | None, Header()] = None,
) -> bytes:
    """FastAPI dependency for verifying Bitbucket webhook signatures."""
    from pr_review_agent.main import get_settings

    settings = get_settings()

    if x_hub_signature is None:
        logger.warning("Missing X-Hub-Signature header")
        raise HTTPException(status_code=401, detail="Missing signature header")

    if not verify_bitbucket_signature(
        body, x_hub_signature, settings.bitbucket_webhook_secret
    ):
        logger.warning("Invalid Bitbucket webhook signature")
        raise HTTPException(status_code=401, detail="Invalid signature")

    return body
