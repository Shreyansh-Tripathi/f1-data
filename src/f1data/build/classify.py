"""Result classification from Jolpica `positionText`, cross-checked with OpenF1 flags. Never from status strings."""

from __future__ import annotations

import logging
from typing import Any

from f1data.models.session import Classification

log = logging.getLogger(__name__)

# Ergast positionText codes: R retired, N not classified, W withdrawn, F failed to qualify,
# D disqualified, E excluded. Numbers are classified finishers.
POSITION_TEXT = {"R": "dnf", "N": "dnf", "W": "dns", "F": "dns", "D": "dsq", "E": "dsq"}


def classify(position_text: str, openf1_result: dict[str, Any] | None = None, *, who: str = "") -> Classification:
    result: Classification = "finished" if position_text.isdigit() else POSITION_TEXT.get(position_text, "dnf")
    if openf1_result is not None:
        flags = [k for k in ("dnf", "dns", "dsq") if openf1_result.get(k)]
        other = flags[0] if flags else "finished"
        if other != result:
            log.warning(
                "classification mismatch for %s: Jolpica %r -> %s, OpenF1 %s", who, position_text, result, other
            )
    return result
