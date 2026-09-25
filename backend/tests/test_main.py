from __future__ import annotations

import pytest

from app.main import _build_auth_verifier
from voice_core.adapters.fakes.auth import FakeAuthVerifier
from voice_core.config import Settings


def test_dev_env_gets_the_dev_token_fake_verifier() -> None:
    settings = Settings(app_env="dev")
    verifier = _build_auth_verifier(settings)
    assert isinstance(verifier, FakeAuthVerifier)


def test_non_dev_env_refuses_to_start_without_a_real_verifier() -> None:
    settings = Settings(app_env="prod")
    with pytest.raises(RuntimeError, match="no real AuthVerifier"):
        _build_auth_verifier(settings)


def test_host_api_must_be_https_outside_dev_when_tools_call_it() -> None:
    from app.main import _check_host_api_transport

    prod_http = Settings(app_env="prod", host_api_base_url="http://host.example")
    with pytest.raises(RuntimeError, match="https"):
        _check_host_api_transport(prod_http, uses_host_api=True)
    _check_host_api_transport(prod_http, uses_host_api=False)  # all-mock pack: fine
    _check_host_api_transport(
        Settings(app_env="prod", host_api_base_url="https://host.example"), uses_host_api=True
    )
    _check_host_api_transport(Settings(app_env="dev"), uses_host_api=True)
