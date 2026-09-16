"""Enforces CLAUDE.md golden rule 1: the core never knows the domain."""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

CORE_DIRS = [
    REPO_ROOT / "backend" / "voice_core",
    REPO_ROOT / "flutter_voice" / "lib",
]

DOMAIN_WORDS = [
    "crop",
    "farmer",
    "bid",
    "listing",
    "buyer",
    "pickup",
    "prebid",
    "pre-bid",
    "rescue",
    "quintal",
    "quintals",
    "kg",
    "mandi",
    "harvest",
]

WORD_PATTERN = re.compile(
    r"\b(" + "|".join(re.escape(w) for w in DOMAIN_WORDS) + r")\b", re.IGNORECASE
)


def _source_files(root: Path) -> list[Path]:
    if not root.exists():
        return []
    return [p for p in root.rglob("*") if p.suffix in {".py", ".dart"} and p.is_file()]


def test_core_never_mentions_the_domain() -> None:
    violations: list[str] = []
    for core_dir in CORE_DIRS:
        for path in _source_files(core_dir):
            text = path.read_text(encoding="utf-8")
            for lineno, line in enumerate(text.splitlines(), start=1):
                if WORD_PATTERN.search(line):
                    violations.append(f"{path.relative_to(REPO_ROOT)}:{lineno}: {line.strip()}")

    assert not violations, "Domain words leaked into core:\n" + "\n".join(violations)


def test_vendor_sdks_are_isolated_to_their_adapter_directory() -> None:
    """Golden rule 2: vendor SDK imports are allowed only inside adapters/<vendor>/."""
    vendor_import_pattern = re.compile(r"^\s*(from|import)\s+(google|openai|anthropic|sarvamai)\b")
    allowed_dirs = {
        REPO_ROOT / "backend" / "voice_core" / "adapters" / "gemini",
        REPO_ROOT / "backend" / "voice_core" / "adapters" / "openai_compat",
        REPO_ROOT / "backend" / "voice_core" / "adapters" / "anthropic",
        REPO_ROOT / "backend" / "voice_core" / "adapters" / "sarvam",
    }
    violations: list[str] = []
    for path in _source_files(REPO_ROOT / "backend" / "voice_core"):
        if any(str(path).startswith(str(d)) for d in allowed_dirs):
            continue
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if vendor_import_pattern.match(line):
                violations.append(f"{path.relative_to(REPO_ROOT)}:{lineno}: {line.strip()}")

    assert not violations, "Vendor SDK imported outside its adapter directory:\n" + "\n".join(
        violations
    )
