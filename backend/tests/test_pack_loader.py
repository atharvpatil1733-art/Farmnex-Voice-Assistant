from __future__ import annotations

from pathlib import Path

import pytest

from voice_core.packs.loader import PackLoadError, load_pack

PACKS_ROOT = Path(__file__).resolve().parents[2] / "domain_packs"


def test_loads_the_real_farm_marketplace_pack() -> None:
    pack = load_pack("farm_marketplace", PACKS_ROOT)

    assert pack.id == "farm_marketplace"
    assert pack.default_language in pack.languages
    assert "{app_name}" not in pack.persona_template
    assert len(pack.tools) == 8
    assert len(pack.workflows) == 1
    assert pack.pack_dir == PACKS_ROOT / "farm_marketplace"


def test_missing_pack_yaml_raises_pack_load_error(tmp_path: Path) -> None:
    with pytest.raises(PackLoadError):
        load_pack("does_not_exist", tmp_path)


def test_malformed_pack_yaml_raises_pack_load_error(tmp_path: Path) -> None:
    pack_dir = tmp_path / "broken"
    pack_dir.mkdir()
    (pack_dir / "pack.yaml").write_text("id: [unclosed", encoding="utf-8")

    with pytest.raises(PackLoadError):
        load_pack("broken", tmp_path)


def test_missing_required_key_raises_pack_load_error(tmp_path: Path) -> None:
    pack_dir = tmp_path / "incomplete"
    pack_dir.mkdir()
    (pack_dir / "pack.yaml").write_text("id: incomplete\n", encoding="utf-8")

    with pytest.raises(PackLoadError):
        load_pack("incomplete", tmp_path)
