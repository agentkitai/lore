"""Named redaction policy packs + level config (#80).

Tests the env → pipeline config mapping in get_write_redactor; the default
(no env) must stay L1-only + secrets-masked (prior behavior).
"""

import pytest

from lore.redact.write import _SECRET_TYPES, get_write_redactor

_ENV = (
    "LORE_REDACT_POLICY",
    "LORE_REDACT_LEVELS",
    "LORE_REDACT_BLOCK",
    "LORE_REDACT_DISABLED",
    "LORE_REDACT_DENYLIST",
)


def _redactor(monkeypatch, **env):
    for k in _ENV:
        monkeypatch.delenv(k, raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    get_write_redactor.cache_clear()
    return get_write_redactor()


@pytest.fixture(autouse=True)
def _clear_cache():
    get_write_redactor.cache_clear()
    yield
    get_write_redactor.cache_clear()


def test_default_is_l1_and_masks_secrets(monkeypatch):
    r = _redactor(monkeypatch)
    assert r is not None
    assert r._levels == {1}  # zero-dep default, unchanged
    assert all(r._action_overrides.get(t) == "mask" for t in _SECRET_TYPES)


def test_policy_off_disables(monkeypatch):
    assert _redactor(monkeypatch, LORE_REDACT_POLICY="off") is None


def test_policy_strict_enables_all_levels_and_blocks(monkeypatch):
    r = _redactor(monkeypatch, LORE_REDACT_POLICY="strict")
    assert r._levels == {1, 2, 3}
    assert not r._action_overrides  # block is the pipeline default for secrets


def test_policy_pii_levels_1_3_mask(monkeypatch):
    r = _redactor(monkeypatch, LORE_REDACT_POLICY="pii")
    assert r._levels == {1, 3}
    assert all(r._action_overrides.get(t) == "mask" for t in _SECRET_TYPES)


def test_policy_secrets_levels_1_2_block(monkeypatch):
    r = _redactor(monkeypatch, LORE_REDACT_POLICY="secrets")
    assert r._levels == {1, 2}
    assert not r._action_overrides


def test_explicit_levels_override_the_pack(monkeypatch):
    r = _redactor(monkeypatch, LORE_REDACT_POLICY="pii", LORE_REDACT_LEVELS="1,2")
    assert r._levels == {1, 2}


def test_unknown_policy_falls_back_to_l1(monkeypatch):
    r = _redactor(monkeypatch, LORE_REDACT_POLICY="bogus")
    assert r._levels == {1}


def test_block_env_forces_block_backcompat(monkeypatch):
    r = _redactor(monkeypatch, LORE_REDACT_BLOCK="1")
    assert not r._action_overrides


def test_malformed_levels_csv_falls_back(monkeypatch):
    r = _redactor(monkeypatch, LORE_REDACT_LEVELS="abc")
    assert r._levels == {1}
