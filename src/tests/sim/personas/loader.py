"""Load and validate persona YAML files."""

from __future__ import annotations

from pathlib import Path

import yaml

from sim.personas.schema import Persona

PERSONAS_DIR = Path(__file__).resolve().parent


class PersonaError(ValueError):
    """A persona file is invalid. The message names the file."""


def load_persona(path: str | Path) -> Persona:
    path = Path(path)
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise PersonaError(f"{path.name}: invalid YAML: {exc}") from exc
    if not isinstance(raw, dict):
        raise PersonaError(f"{path.name}: expected a mapping at the top level")
    try:
        persona = Persona.model_validate(raw)
    except ValueError as exc:
        raise PersonaError(f"{path.name}: {exc}") from exc
    if persona.id != path.stem:
        raise PersonaError(f"{path.name}: id {persona.id!r} must equal the file name")
    return persona


def load_personas(directory: str | Path = PERSONAS_DIR) -> dict[str, Persona]:
    """Load every `*.yaml` in `directory`, keyed by persona id.

    Raises PersonaError on the first invalid file, or when two personas share a phone number
    (each persona gets its own simulated number, ADR 0017).
    """
    personas: dict[str, Persona] = {}
    phones: dict[str, str] = {}
    for path in sorted(Path(directory).glob("*.yaml")):
        persona = load_persona(path)
        other = phones.get(persona.identity.phone)
        if other:
            raise PersonaError(f"{path.name}: phone shared with {other}")
        phones[persona.identity.phone] = persona.id
        personas[persona.id] = persona
    return personas
