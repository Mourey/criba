"""The ``criba.db.repo`` package keeps the surface its callers use as one module.

The names below are the ones callers reach as ``repo.<name>`` or import with
``from criba.db.repo import <name>`` (found by grepping ``src/``), so splitting the
package further cannot silently drop one.
"""

from __future__ import annotations

import importlib
import subprocess
import sys

import pytest

from criba.db import repo

# Every ``repo.<name>`` used across src/ (grep ``\brepo\.[A-Za-z_]+``).
CALLER_NAMES = (
    "ANSWER_FIELDS",
    "Actor",
    "CLAIM_IDLE_AFTER",
    "HOLD_REASON",
    "NotFound",
    "SCREENING_STAGES",
    "STAGES",
    "_follow_zone_country",
    "add_event",
    "add_handback",
    "add_message",
    "agent_on_hold",
    "application_location_ids",
    "claim_application",
    "close_application",
    "country_from_phone",
    "covering_location_ids",
    "export_application",
    "find_candidate_by_identity",
    "get_application",
    "get_candidate",
    "get_handback",
    "get_open_application",
    "get_or_create_candidate",
    "hold_for_recruiters",
    "last_event_id",
    "last_funnel_offer",
    "last_inbound_is_spoken",
    "last_inbound_message",
    "last_typed_inbound_text",
    "last_inbound_message_id",
    "lift_opt_out",
    "link_knockout_message",
    "link_message_to_application",
    "list_candidate_applications",
    "list_events",
    "list_messages",
    "local_label",
    "mark_approved",
    "mark_opted_out",
    "mark_qualified",
    "open_queue_item",
    "record_answer",
    "record_invalid_answer",
    "release_claim",
    "reopen_application",
    "reopenable_application",
    "require_application",
    "resolve_application",
    "resolve_hold",
    "set_candidate_language",
    "set_raw_input",
    "set_stage",
    "set_summary",
    "start_application",
    "touch_claim",
    "user_can_access",
    "within_reopen_window",
    # Moved out of api/queue.py, tools/application.py and scheduler/jobs.py.
    "agent_wrote_after",
    "claimed_idle_due",
    "clear_control",
    "clear_stale_flags",
    "counted_unreadable",
    "direct_rejections_after",
    "find_scoped_queue_item",
    "get_queue_item_row",
    "has_open_queue_item",
    "last_agent_message_at",
    "latest_event",
    "latest_tone_hint",
    "list_queue_items",
    "live_zone_choice",
    "max_event_id",
    "max_queue_item_rowid",
    "qualified_waiting_due",
    "queue_item_status",
    "queue_items_created_after",
    "queue_items_resolved_since",
    "reset_stalled",
    "resolve_queue_item",
    "screening_application_ids",
    "session_is_alive",
    "set_claimed_idle",
    "set_nudges_sent",
    "set_qualified_waiting",
    "set_stalled_since",
    "stamp_erasure_request",
    "takeover_messages",
    "takeover_started_before",
    "zone_timezone",
    # Moved out of tools/booking.py and scheduler/deferral.py.
    "booking_for_ics",
    "booking_is_standing",
    "claim_held_action",
    "current_booking",
    "current_booking_id",
    "expire_slot_offers",
    "finish_held_action",
    "first_agent_message_after",
    "held_action_history",
    "hold_action",
    "insert_booking",
    "insert_slot_offer",
    "latest_invite_booking_event",
    "max_message_id",
    "offered_slot",
    "open_slot_rows",
    "pending_held_actions",
    "record_held_action_failure",
    "release_booking",
    "reschedule_held_action",
    "slot_is_booked",
    "slot_row",
    "waiting_held_actions",
    # Moved out of channel/voice_session.py and channel/webchat.py.
    "add_candidate_identity",
    "candidate_application_ids",
    "candidate_country",
    "candidate_language",
    "candidate_messages_after",
    "client_message_seen",
    "conversation_identity",
    "first_candidate_by_phone",
    "identity_candidate_row",
    "identity_exists",
    "insert_voice_session",
    "live_voice_session_count",
    "mark_voice_session_ended",
    "max_candidate_message_id",
    "open_application_under_takeover",
    "recent_interactive_messages",
    "recent_voice_session_starts",
    "set_voice_session_conversation_id",
    "stored_message_body",
    "voice_session_exists",
    "voice_session_row",
    "voice_session_speaker",
    "voice_sessions_started_since",
    # Moved out of api/slots.py and api/metrics.py.
    "active_location_exists",
    "cancel_interview_slot",
    "cohort_applications",
    "cohort_by_location",
    "cohort_event_counts",
    "insert_interview_slot",
    "latest_replay_dataset",
    "list_scoped_slots",
    "move_interview_slot",
    "public_open_slots",
    "qualified_waiting_count",
    "replay_cost_rows",
    "results_where",
    "slot_detail",
    "slot_start_taken",
    "usage_cost_by_environment",
    "usage_cost_rows",
    "usage_reach",
    "usage_where",
)

# Every ``from criba.db.repo import <name>`` across src/.
IMPORTED_NAMES = ("Conflict", "Forbidden", "NotFound", "RepoError", "covering_location_ids")

SUBMODULES = (
    "base",
    "events",
    "handbacks",
    "applications",
    "candidates",
    "queue",
    "lifecycle",
    "outcomes",
    "messages",
    "claims",
    "export",
    "scheduling",
    "zones",
    "bookings",
    "held_actions",
    "voice_sessions",
    "slots",
    "metrics",
)


@pytest.mark.parametrize("name", CALLER_NAMES)
def test_facade_exposes_each_name_callers_use(name: str) -> None:
    assert hasattr(repo, name)


@pytest.mark.parametrize("name", IMPORTED_NAMES)
def test_names_imported_from_the_package_resolve(name: str) -> None:
    module = importlib.import_module("criba.db.repo")
    assert getattr(module, name) is getattr(repo, name)


def test_all_lists_every_caller_name() -> None:
    assert set(CALLER_NAMES) | set(IMPORTED_NAMES) <= set(repo.__all__)
    assert all(hasattr(repo, name) for name in repo.__all__)


def test_error_hierarchy_is_unchanged() -> None:
    for error in (repo.NotFound, repo.Conflict, repo.Forbidden):
        assert issubclass(error, repo.RepoError)
    assert repo.RepoError("x").code == "repo_error"
    assert repo.NotFound("x").code == "not_found"
    assert repo.Conflict("x", "application_open").code == "application_open"


@pytest.mark.parametrize("submodule", SUBMODULES)
def test_each_submodule_imports_on_its_own(submodule: str) -> None:
    """A fresh interpreter per submodule: an import cycle would fail here."""
    result = subprocess.run(
        [sys.executable, "-c", f"import criba.db.repo.{submodule}"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
