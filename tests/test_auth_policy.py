"""tests/test_auth_policy.py — G-23: fail-closed auth for every network transport.

Single decision point (nano_vm_mcp.auth.resolve_auth_policy); the middleware and the CLI
are built from the same AuthPolicy.
"""

from __future__ import annotations

import logging
import os

import pytest
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from nano_vm_mcp import cli
from nano_vm_mcp.auth import (
    ENV_KEY,
    ENV_NOAUTH,
    LOCAL_TRANSPORTS,
    TRANSPORTS,
    AuthConfigError,
    AuthPolicy,
    resolve_auth_policy,
)
from nano_vm_mcp.server import BearerAuthMiddleware, build_sse_app

# Independent of auth.LOCAL_TRANSPORTS on purpose: only stdio is exempt, by literal.
NETWORK_TRANSPORTS = [t for t in TRANSPORTS if t != "stdio"]


# ---------------------------------------------------------------------------
# resolve_auth_policy
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("transport", NETWORK_TRANSPORTS)
def test_ap01_network_transport_without_key_is_refused(transport):
    with pytest.raises(AuthConfigError) as exc:
        resolve_auth_policy(transport, {})
    assert ENV_KEY in str(exc.value) and ENV_NOAUTH in str(exc.value)


@pytest.mark.parametrize("transport", NETWORK_TRANSPORTS)
def test_ap02_network_transport_with_key_gives_key_policy(transport):
    policy = resolve_auth_policy(transport, {ENV_KEY: "s3cret"})
    assert policy.mode == "key"
    assert policy.verify("Bearer s3cret") is True
    assert policy.verify("Bearer nope") is False
    assert policy.verify("") is False


@pytest.mark.parametrize("transport", NETWORK_TRANSPORTS)
def test_ap03_noauth_opt_out_starts_and_warns(transport, caplog):
    with caplog.at_level(logging.WARNING, logger="nano_vm_mcp.auth"):
        policy = resolve_auth_policy(transport, {ENV_NOAUTH: "1"})
    assert policy.mode == "noauth"
    assert any("auth_disabled" in r.getMessage() for r in caplog.records)


@pytest.mark.parametrize("blank", ["", " ", "   ", "\t\n"])
@pytest.mark.parametrize("transport", NETWORK_TRANSPORTS)
def test_ap04_blank_key_equals_no_key(transport, blank):
    with pytest.raises(AuthConfigError):
        resolve_auth_policy(transport, {ENV_KEY: blank})
    # and a blank key does not shadow the explicit opt-out
    assert resolve_auth_policy(transport, {ENV_KEY: blank, ENV_NOAUTH: "1"}).mode == "noauth"


@pytest.mark.parametrize("value", ["0", "true", "yes", "", "11"])
def test_ap05_only_exact_1_enables_noauth(value):
    with pytest.raises(AuthConfigError):
        resolve_auth_policy("sse", {ENV_NOAUTH: value})


def test_ap06_key_wins_over_noauth():
    assert resolve_auth_policy("sse", {ENV_KEY: "k", ENV_NOAUTH: "1"}).mode == "key"


def test_ap07_key_is_stripped():
    policy = resolve_auth_policy("sse", {ENV_KEY: "  k  "})
    assert policy.verify("Bearer k") is True


def test_ap08_stdio_needs_no_key():
    assert resolve_auth_policy("stdio", {}).mode == "none"
    assert resolve_auth_policy("stdio", {ENV_KEY: "ignored"}).mode == "none"


def test_ap09_policy_validates_itself():
    with pytest.raises(ValueError):
        AuthPolicy(mode="key", key="")
    with pytest.raises(ValueError):
        AuthPolicy(mode="key", key="   ")
    with pytest.raises(ValueError):
        AuthPolicy(mode="bogus")
    assert "s3cret" not in repr(AuthPolicy(mode="key", key="s3cret"))


# ---------------------------------------------------------------------------
# Contract: transports come from the same source as the CLI
# ---------------------------------------------------------------------------


def _cli_choices() -> list[str]:
    parser = cli.build_parser()
    action = next(a for a in parser._actions if a.dest == "transport")
    return list(action.choices)


def test_ct01_cli_choices_are_the_transport_registry():
    assert _cli_choices() == list(TRANSPORTS)
    assert NETWORK_TRANSPORTS, "at least one network transport must be registered"


def test_ct05_only_stdio_is_exempt():
    """Allow-list contract: a transport added to TRANSPORTS is a network transport unless
    this literal says otherwise. Excluding one from the check must fail this test."""
    assert LOCAL_TRANSPORTS == frozenset({"stdio"})
    for transport in TRANSPORTS:
        if transport == "stdio":
            continue
        with pytest.raises(AuthConfigError):
            resolve_auth_policy(transport, {})


@pytest.mark.parametrize("transport", NETWORK_TRANSPORTS)
def test_ct02_cli_refuses_every_network_transport_without_key(
    transport, monkeypatch, tmp_path, capsys
):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv(ENV_KEY, raising=False)
    monkeypatch.delenv(ENV_NOAUTH, raising=False)
    import nano_vm_mcp.server as server

    def must_not_start(*args, **kwargs):
        # If the refusal is skipped, fail the test instead of binding a real port.
        raise AssertionError("server started without authentication")

    monkeypatch.setattr(server, "run_sse", must_not_start)
    monkeypatch.setattr(server, "run_stdio", must_not_start)
    with pytest.raises(SystemExit) as exc:
        cli.main(["--transport", transport, "--env-file", str(tmp_path / "absent.env")])
    assert exc.value.code == 2
    assert ENV_KEY in capsys.readouterr().err


def test_ct03_cli_reads_key_from_env_file_before_deciding(monkeypatch, tmp_path):
    """The decision runs after .env is loaded: a key in .env counts."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv(ENV_KEY, raising=False)
    monkeypatch.delenv(ENV_NOAUTH, raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text(f"{ENV_KEY}=from-dotenv\n")
    seen = {}

    def fake_run_sse(policy, host, port):
        seen["policy"] = policy

    import nano_vm_mcp.server as server

    monkeypatch.setattr(server, "run_sse", fake_run_sse)
    try:
        cli.main(["--transport", "sse", "--env-file", str(env_file)])
    finally:
        os.environ.pop(ENV_KEY, None)  # load_dotenv wrote it into the real environment
    assert seen["policy"].mode == "key"


def test_ct04_run_sse_receives_the_resolved_policy(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv(ENV_KEY, "k")
    import nano_vm_mcp.server as server

    seen = {}
    monkeypatch.setattr(server, "run_sse", lambda policy, host, port: seen.update(p=policy))
    cli.main(["--transport", "sse", "--env-file", str(tmp_path / "absent.env")])
    assert seen["p"] == AuthPolicy(mode="key", key="k")


# ---------------------------------------------------------------------------
# Binding: the middleware exists only through AuthPolicy
# ---------------------------------------------------------------------------


def _stub_app(policy: AuthPolicy) -> Starlette:
    async def stub(request):
        return JSONResponse({"ok": True})

    return Starlette(
        routes=[Route("/sse", endpoint=stub)],
        middleware=[Middleware(BearerAuthMiddleware, policy=policy)],
    )


def test_bd01_middleware_cannot_be_built_without_a_policy():
    with pytest.raises(TypeError):
        BearerAuthMiddleware(object())  # type: ignore[call-arg]
    with pytest.raises(TypeError):
        BearerAuthMiddleware(object(), policy="key")  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        TestClient(
            Starlette(
                routes=[Route("/sse", endpoint=lambda r: None)],
                middleware=[Middleware(BearerAuthMiddleware)],
            )
        ).get("/sse")


def test_bd02_none_policy_is_not_a_network_policy():
    with pytest.raises(ValueError):
        BearerAuthMiddleware(object(), policy=AuthPolicy(mode="none"))
    with pytest.raises(ValueError):
        build_sse_app(AuthPolicy(mode="none"))


def test_bd03_key_and_noauth_differ_on_the_same_request(monkeypatch):
    """Same request, same (empty) environment; only the policy differs."""
    monkeypatch.delenv(ENV_KEY, raising=False)
    monkeypatch.delenv(ENV_NOAUTH, raising=False)
    assert TestClient(_stub_app(AuthPolicy(mode="noauth"))).get("/sse").status_code == 200
    assert TestClient(_stub_app(AuthPolicy(mode="key", key="k"))).get("/sse").status_code == 401


def test_bd04_middleware_ignores_the_environment(monkeypatch):
    """A key in the environment must not change a 'noauth' policy, and vice versa."""
    monkeypatch.setenv(ENV_KEY, "env-key")
    assert TestClient(_stub_app(AuthPolicy(mode="noauth"))).get("/sse").status_code == 200
    monkeypatch.delenv(ENV_KEY)
    key_app = _stub_app(AuthPolicy(mode="key", key="k"))
    assert TestClient(key_app).get("/sse").status_code == 401
    assert TestClient(key_app).get("/sse", headers={"Authorization": "Bearer k"}).status_code == 200


# ---------------------------------------------------------------------------
# Real SSE app topology (build_sse_app)
# ---------------------------------------------------------------------------


def test_bd05_real_sse_app_key_policy_protects_routes_but_not_health():
    # No GET /sse here on purpose: if authentication were broken, that request would reach
    # the real SSE handler, which streams forever and would hang the test. The /sse gate
    # itself is covered by BD03/BD04 and tests/test_auth.py (same middleware, stub route).
    client = TestClient(build_sse_app(AuthPolicy(mode="key", key="k")))
    assert client.get("/health").status_code == 200
    assert client.post("/messages/").status_code == 401
    assert client.post("/messages/", headers={"Authorization": "Bearer x"}).status_code == 401


def test_bd06_real_sse_app_noauth_policy_does_not_block_messages():
    client = TestClient(build_sse_app(AuthPolicy(mode="noauth")))
    assert client.post("/messages/").status_code != 401
