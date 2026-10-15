"""The Jev router (ADR 0051): which language the agent replies in, and tone from sentiment.

- :mod:`criba.jev.config`: settings and the on/off rule (:func:`jev_enabled`).
- :mod:`criba.jev.battery`: the typed questions and how to read the answers.
- :mod:`criba.jev.client`: the port (:class:`JevPort`) and the OpenRouter client.
- :mod:`criba.jev.policy`: thresholds and the pure decisions made from a reading.
- :mod:`criba.jev.turn`: the router's step, which applies the decisions to the record.
"""

from criba.jev.battery import JevInput, Reading, Turn
from criba.jev.client import JevFailed, JevPort, JevResult, OpenRouterJev
from criba.jev.config import JevSettings, jev_enabled

__all__ = [
    "JevFailed",
    "JevInput",
    "JevPort",
    "JevResult",
    "JevSettings",
    "OpenRouterJev",
    "Reading",
    "Turn",
    "jev_enabled",
]
