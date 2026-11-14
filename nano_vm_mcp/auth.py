"""nano_vm_mcp.auth — single source of truth for transport authentication.

One decision point: ``resolve_auth_policy(transport, env)`` is called once at
start-up, before any transport branch and before the server module is imported.
Everything that enforces authentication at request time is built from the
returned ``AuthPolicy`` and never reads the environment itself.

Policy states
-------------
``none``    local transport (stdio): there is no network peer, no middleware.
``key``     network transport with a non-empty API key: Bearer token is checked.
``noauth``  network transport, explicitly opened with NANO_VM_MCP_ALLOW_NOAUTH=1.

A network transport without a key and without the explicit opt-out is not a
state: ``resolve_auth_policy`` raises ``AuthConfigError`` instead of returning.
"""

from __future__ import annotations

import hmac
import logging
from collections.abc import Mapping
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# Every transport the CLI accepts. cli.py builds ``choices`` from this tuple.
TRANSPORTS: tuple[str, ...] = ("stdio", "sse")

# Transports without a network peer. Everything else (including any transport
# added to TRANSPORTS later) is treated as a network transport and needs auth.
LOCAL_TRANSPORTS: frozenset[str] = frozenset({"stdio"})

ENV_KEY = "NANO_VM_MCP_API_KEY"
ENV_NOAUTH = "NANO_VM_MCP_ALLOW_NOAUTH"

_MODES = ("none", "key", "noauth")
_BEARER = "Bearer "


class AuthConfigError(Exception):
    """Authentication is not configured for a network transport."""


@dataclass(frozen=True)
class AuthPolicy:
    mode: str
    key: str = field(default="", repr=False)

    def __post_init__(self) -> None:
        if self.mode not in _MODES:
            raise ValueError(f"unknown auth mode: {self.mode!r}")
        if self.mode == "key" and not self.key.strip():
            raise ValueError("auth mode 'key' requires a non-empty key")

    def verify(self, authorization: str) -> bool:
        """True if the Authorization header carries the configured Bearer token."""
        if self.mode != "key":
            raise ValueError(f"verify() is only defined for mode 'key', not {self.mode!r}")
        if not authorization.startswith(_BEARER):
            return False
        token = authorization[len(_BEARER) :].strip()
        # bytes: compare_digest raises TypeError on non-ASCII str (would be a 500, not a 401)
        return hmac.compare_digest(token.encode("utf-8"), self.key.encode("utf-8"))


def resolve_auth_policy(transport: str, env: Mapping[str, str]) -> AuthPolicy:
    """Decide the auth policy for ``transport`` from ``env``; raise if unsafe."""
    if transport in LOCAL_TRANSPORTS:
        return AuthPolicy(mode="none")

    key = (env.get(ENV_KEY) or "").strip()
    if key:
        return AuthPolicy(mode="key", key=key)

    if (env.get(ENV_NOAUTH) or "").strip() == "1":
        logger.warning(
            "auth_disabled transport=%s: %s=1 and no %s set; the endpoint has NO "
            "authentication. Anyone who can reach it can run programs.",
            transport,
            ENV_NOAUTH,
            ENV_KEY,
        )
        return AuthPolicy(mode="noauth")

    raise AuthConfigError(
        f"transport '{transport}' is a network transport and requires authentication.\n"
        f"  Set {ENV_KEY} to a non-empty token (empty or whitespace-only counts as unset),\n"
        f"  or set {ENV_NOAUTH}=1 to run without authentication (trusted network only)."
    )
