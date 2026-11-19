"""Validate the generated Vdbench data preparation configuration."""
from __future__ import annotations

import re


MAX_EXPLICIT_FWDS = 256


class ConfigValidationError(ValueError):
    """Raised when a generated Vdbench parameter file violates its contract."""


def _named_lines(text: str, prefix: str) -> dict[str, str]:
    definitions: dict[str, str] = {}
    for line in text.splitlines():
        if not line.startswith(prefix):
            continue
        name = line.split(",", 1)[0].split("=", 1)[1]
        if name in definitions:
            raise ConfigValidationError(f"duplicate {prefix[:-1].upper()} name: {name}")
        definitions[name] = line
    return definitions


def _explicit_fwd_names(line: str) -> list[str]:
    match = re.search(r",fwd=\(([^)]*)\)", line)
    if not match:
        raise ConfigValidationError(f"RD must use an explicit FWD list: {line}")
    names = [name for name in match.group(1).split(",") if name]
    if not names:
        raise ConfigValidationError(f"RD has an empty FWD list: {line}")
    return names


def validate_prepare_text(text: str) -> None:
    """Validate bounded explicit FWD lists used only for data creation."""
    fwds = _named_lines(text, "fwd=")
    rds = _named_lines(text, "rd=")
    if not fwds or not rds:
        raise ConfigValidationError("prepare config must declare FWDs and RDs")
    for rd_name, line in rds.items():
        names = _explicit_fwd_names(line)
        if len(names) > MAX_EXPLICIT_FWDS:
            raise ConfigValidationError(
                f"prepare RD {rd_name} has {len(names)} explicit FWDs; maximum is {MAX_EXPLICIT_FWDS}"
            )
        missing = [name for name in names if name not in fwds]
        if missing:
            raise ConfigValidationError(
                f"prepare RD {rd_name} references undefined FWD: {missing[0]}"
            )
