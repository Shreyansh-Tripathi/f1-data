"""When is a session's data final?

A session is final once it ended at least `FINAL_AFTER` ago *and* Jolpica has
published its round. Final data is cached forever and written without the
`provisional` flag; anything else is re-fetched after the provisional TTL.
"""

from __future__ import annotations

from datetime import datetime, timedelta

FINAL_AFTER = timedelta(hours=72)


def is_final(session_end: datetime, jolpica_has_round: bool, now: datetime) -> bool:
    return jolpica_has_round and now - session_end >= FINAL_AFTER
