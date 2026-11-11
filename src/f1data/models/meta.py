from __future__ import annotations

from f1data.models.base import Model, OutputFile, Utc


class SessionLag(Model):
    session_end: Utc  # last chequered flag, else OpenF1's scheduled end
    first_published_at: Utc  # when a build first wrote the session's summary.json


class PublishLag(OutputFile):
    # "{year}/{round}/{session}" -> lag entry; only sessions first published after tracking started
    sessions: dict[str, SessionLag]
