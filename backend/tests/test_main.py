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
