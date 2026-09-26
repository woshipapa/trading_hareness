"""Non-secret build provenance exposed by the operational health endpoint."""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from pathlib import Path


_GIT_SHA_RE = re.compile(r"^[0-9a-f]{7,64}$", re.IGNORECASE)
#: Directory name ``scripts/hotfix-quant-service.sh`` gives each hot release.
_HOTFIX_RELEASE_RE = re.compile(r"^hotfix-\d{8}T\d{6}Z-([0-9a-f]{7,40})$")
_MAX_TEXT_LENGTH = 160


def _text(value: object | None) -> str | None:
    text = str(value or "").strip()
    if not text or text.lower() in {"unknown", "unset", "none"}:
        return None
    return text[:_MAX_TEXT_LENGTH]


def hotfix_release(module_file: str | None = None) -> str | None:
    """The hot-deployed source release this process actually imports, if any.

    A hot deploy swaps the source under ``/app/hotfix/current`` without
    rebuilding the image, so the image's ``APP_*`` build fields keep naming
    the release underneath.  The code's own resolved path names the release
    that is running.
    """
    for parent in Path(module_file or __file__).resolve().parents:
        if _HOTFIX_RELEASE_RE.fullmatch(parent.name):
            return parent.name
    return None


def release_metadata(environment: Mapping[str, str] | None = None,
                     module_file: str | None = None) -> dict[str, str | None]:
    """Return only display-safe release fields; never include runtime secrets.

    ``running_git_sha`` is the commit of the code serving requests: the hot
    release's when one is active, otherwise the image's own.
    """
    env = os.environ if environment is None else environment
    git_sha = _text(env.get("APP_GIT_SHA"))
    image_sha = git_sha.lower() if git_sha and _GIT_SHA_RE.fullmatch(git_sha) else None
    hotfix = hotfix_release(module_file)
    return {
        "git_sha": image_sha,
        "release": _text(env.get("APP_RELEASE")),
        "build_created_at": _text(env.get("APP_BUILD_CREATED_AT")),
        "hotfix_release": hotfix,
        "running_git_sha": _HOTFIX_RELEASE_RE.fullmatch(hotfix).group(1) if hotfix else image_sha,
    }


__all__ = ["hotfix_release", "release_metadata"]
