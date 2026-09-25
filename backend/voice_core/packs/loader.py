from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from voice_core.speech.units import SpokenUnit, parse_units


class PackLoadError(Exception):
    def __init__(self, pack_id: str, path: Path | str, reason: str) -> None:
        self.pack_id = pack_id
        self.path = path
        self.reason = reason
        super().__init__(f"pack '{pack_id}' at {path}: {reason}")


@dataclass(frozen=True)
class LoadedPack:
    id: str
    languages: tuple[str, ...]
    default_language: str
    timezone: str
    app_name: str
    persona_template: str
    tools: tuple[dict[str, Any], ...]
    workflows: tuple[dict[str, Any], ...]
    fillers: dict[str, str]
    confirmation_lexicon_extra: dict[str, list[str]]
    client_actions: tuple[str, ...]
    pack_dir: Path
    speech_units: tuple[SpokenUnit, ...] = ()
    tts_speaker: str | None = None
    speech_pace: float = 1.0


def _load_yaml(pack_id: str, path: Path) -> Any:
    if not path.exists():
        raise PackLoadError(pack_id, path, "file not found")
    try:
        with path.open(encoding="utf-8") as f:
            return yaml.safe_load(f)
    except yaml.YAMLError as exc:
        raise PackLoadError(pack_id, path, f"invalid YAML: {exc}") from exc


def _substitute_persona(template: str, app_name: str) -> str:
    return template.replace("{app_name}", app_name)


def load_pack(pack_id: str, packs_root: Path) -> LoadedPack:
    pack_dir = packs_root / pack_id
    pack_yaml_path = pack_dir / "pack.yaml"
    config = _load_yaml(pack_id, pack_yaml_path)
    if not isinstance(config, dict):
        raise PackLoadError(pack_id, pack_yaml_path, "pack.yaml must be a mapping")

    try:
        languages = tuple(config["languages"])
        default_language = config["default_language"]
        timezone = config["timezone"]
        app_name = config["app_name"]
        persona_file = config["persona_file"]
        tools_file = config["tools_file"]
        workflows_dir = config["workflows_dir"]
    except KeyError as exc:
        raise PackLoadError(pack_id, pack_yaml_path, f"missing required key: {exc}") from exc

    persona_path = pack_dir / persona_file
    if not persona_path.exists():
        raise PackLoadError(pack_id, persona_path, "persona file not found")
    persona_template = _substitute_persona(persona_path.read_text(encoding="utf-8"), app_name)

    tools_raw = _load_yaml(pack_id, pack_dir / tools_file)
    if not isinstance(tools_raw, list):
        raise PackLoadError(pack_id, pack_dir / tools_file, "tools file must be a list")

    workflows_path = pack_dir / workflows_dir
    workflows: list[dict[str, Any]] = []
    if workflows_path.is_dir():
        for wf_file in sorted(workflows_path.glob("*.yaml")):
            wf = _load_yaml(pack_id, wf_file)
            if not isinstance(wf, dict):
                raise PackLoadError(pack_id, wf_file, "workflow file must be a mapping")
            workflows.append(wf)

    try:
        speech_units = parse_units(config.get("speech_units") or [])
    except ValueError as exc:
        raise PackLoadError(pack_id, pack_yaml_path, str(exc)) from exc
    voice = config.get("voice") or {}
    speaker = voice.get("tts_speaker")
    # A placeholder like "<audition … and set one>" means "not chosen yet".
    tts_speaker = speaker if isinstance(speaker, str) and speaker and "<" not in speaker else None

    return LoadedPack(
        id=pack_id,
        languages=languages,
        default_language=default_language,
        timezone=timezone,
        app_name=app_name,
        persona_template=persona_template,
        tools=tuple(tools_raw),
        workflows=tuple(workflows),
        fillers=dict(config.get("fillers") or {}),
        confirmation_lexicon_extra={
            k: list(v) for k, v in (config.get("confirmation_lexicon_extra") or {}).items()
        },
        client_actions=tuple(config.get("client_actions") or ()),
        pack_dir=pack_dir,
        speech_units=speech_units,
        tts_speaker=tts_speaker,
        speech_pace=float(voice.get("speech_pace", 1.0)),
    )
