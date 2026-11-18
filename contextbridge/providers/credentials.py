"""Endpoint-scoped OS credential storage. Secrets never enter configuration files."""

from __future__ import annotations

import hashlib
from typing import Any

from contextbridge.errors import UserError

SERVICE = "ContextBridge model providers"


def _account(endpoint: str) -> str:
    return hashlib.sha256(endpoint.rstrip("/").encode()).hexdigest()


def _backend() -> Any:
    import keyring

    backend = keyring.get_keyring()
    backends = getattr(backend, "backends", [backend])
    allowed = (
        "keyring.backends.macOS",
        "keyring.backends.Windows",
        "keyring.backends.SecretService",
        "keyring.backends.kwallet",
        "keyring.backends.libsecret",
    )
    for candidate in backends:
        if type(candidate).__module__.startswith(allowed) and candidate.priority > 0:
            return candidate
    raise UserError("A secure OS keyring is unavailable on this computer.")


def get_key(endpoint: str) -> str | None:
    try:
        value = _backend().get_password(SERVICE, _account(endpoint))
        return str(value) if value is not None else None
    except Exception:
        return None


def set_key(endpoint: str, value: str) -> None:
    try:
        _backend().set_password(SERVICE, _account(endpoint), value)
    except UserError:
        raise
    except Exception:
        raise UserError(
            "The OS keyring could not save this key. Unlock your keyring and try again."
        ) from None


def delete_key(endpoint: str) -> None:
    try:
        backend = _backend()
        if backend.get_password(SERVICE, _account(endpoint)) is not None:
            backend.delete_password(SERVICE, _account(endpoint))
    except Exception:
        raise UserError("The OS keyring could not remove this key.") from None
