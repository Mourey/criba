"""The criba-tools history middleware: what finished turns left in a request is shrunk, and the
turn in progress is not (turn-size.md, ADR 0045).

The samples are the shapes the real runs of 2026-10-10 sent (tool results from the backend,
``reasoning_details`` from OpenRouter on Haiku 5.5).
"""

from __future__ import annotations

import copy
import json
import sys
from types import ModuleType
from typing import Any

import pytest

GET_APPLICATION = (
    '{"ok":true,"value":{"stage":"zone","status":"open","control":"agent","language":"es-MX",'
    '"country":"MX","first_name":"Valeria","next_field":"zone","today":"2026-10-10",'
    '"weekday":"saturday"}}'
)
ACCEPTED = (
    '{"ok":true,"value":{"status":"accepted","field":"driver_license",'
    '"next_field":"availability","stage":"availability","flags":[]}}'
)
AMBIGUOUS_ZONE = (
    '{"ok":false,"error":"invalid_value","hint":"City recognised (Ciudad de México); '
    'neighbourhood not recognised or not given; offer the list of its areas.",'
    '"reason":"ambiguous_zone","options":[{"id":"zone_mx_cdmx_centro","label":"Centro"},'
    '{"id":"zone_mx_cdmx_condesa","label":"Condesa"}]}'
)
ZONES = (
    '{"ok":true,"value":{"zones":[{"zone_id":"zone_mx_cdmx_centro","label":"Centro",'
    '"city":"Ciudad de México"},{"zone_id":"zone_mx_cdmx_condesa","label":"Condesa",'
    '"city":"Ciudad de México"}],"more":false}}'
)
REASONING = [
    {"type": "reasoning.text", "signature": "CAQS" + "x" * 1380, "format": "x", "index": 1}
]


@pytest.fixture(scope="module")
def trim(plugin: ModuleType) -> ModuleType:
    return sys.modules[f"{plugin.__name__}.history_trim"]


def call(call_id: str, name: str, arguments: str = "{}") -> dict[str, Any]:
    return {"id": call_id, "type": "function", "function": {"name": name, "arguments": arguments}}


def two_turns() -> list[dict[str, Any]]:
    """A finished turn (get_application, record_answer, a reply) and a turn in progress."""
    return [
        {"role": "system", "content": "SOUL and skill"},
        {"role": "user", "content": "Valeria Núñez"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [call("c1", "get_application", '{"language":"es-ES"}')],
            "reasoning_details": REASONING,
        },
        {"role": "tool", "tool_call_id": "c1", "content": GET_APPLICATION},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [call("c2", "record_answer", '{"field":"full_name","value":"V"}')],
        },
        {"role": "tool", "tool_call_id": "c2", "content": ACCEPTED},
        {"role": "assistant", "content": "Gracias. ¿Dónde vives?", "reasoning_details": REASONING},
        {"role": "user", "content": "Ciudad de México"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [call("c3", "get_application", '{"language":"es-ES"}')],
            "reasoning_details": REASONING,
        },
        {"role": "tool", "tool_call_id": "c3", "content": GET_APPLICATION},
    ]


def test_a_finished_turns_tool_results_keep_only_what_they_were(trim: ModuleType) -> None:
    out = trim.trim_history(two_turns())
    assert json.loads(out[3]["content"]) == {"ok": True, "value": {"status": "open"}}
    assert json.loads(out[5]["content"]) == {
        "ok": True,
        "value": {"status": "accepted", "field": "driver_license"},
    }
    assert len(out[3]["content"]) < len(GET_APPLICATION) / 3


def test_the_turn_in_progress_is_left_whole(trim: ModuleType) -> None:
    messages = two_turns()
    out = trim.trim_history(messages)
    assert out[7:] == messages[7:], "from the latest user message on, nothing changes"
    assert out[9]["content"] == GET_APPLICATION
    assert out[8]["reasoning_details"] == REASONING, "the provider needs this one back"


def test_reasoning_of_finished_turns_is_dropped_and_the_rest_of_the_message_stays(
    trim: ModuleType,
) -> None:
    messages = two_turns()
    out = trim.trim_history(messages)
    for index in (2, 6):
        assert "reasoning_details" not in out[index]
        assert {k: v for k, v in messages[index].items() if k != "reasoning_details"} == out[index]
    assert out[2]["tool_calls"] == messages[2]["tool_calls"], (
        "the calls stay as the model made them"
    )
    assert out[6]["content"] == "Gracias. ¿Dónde vives?"


def test_user_and_assistant_text_and_the_system_message_are_never_changed(trim: ModuleType) -> None:
    messages = two_turns()
    out = trim.trim_history(messages)
    for index in (0, 1, 4, 7):
        assert out[index] == messages[index]


def test_a_failure_keeps_its_error_and_reason_and_loses_the_hint_and_a_long_list(
    trim: ModuleType,
) -> None:
    long_list = [{"id": f"zone_mx_cdmx_z{n}", "label": f"Colonia {n}"} for n in range(9)]
    text = json.dumps(
        {"ok": False, "error": "invalid_value", "reason": "ambiguous_zone", "options": long_list}
    )
    assert json.loads(trim.compact_result(text)) == {
        "ok": False,
        "error": "invalid_value",
        "reason": "ambiguous_zone",
    }
    compact = trim.compact_result(AMBIGUOUS_ZONE)
    assert json.loads(compact)["reason"] == "ambiguous_zone"
    retry = json.dumps(
        {
            "ok": False,
            "error": "invalid_value",
            "hint": "x" * 100,
            "reason": "unresolved",
            "attempt": 2,
        }
    )
    assert json.loads(trim.compact_result(retry)) == {
        "ok": False,
        "error": "invalid_value",
        "reason": "unresolved",
        "attempt": 2,
    }


def test_a_short_choice_and_its_confirm_id_stay_for_a_typed_answer_next_turn(
    trim: ModuleType,
) -> None:
    options = [{"id": "zone_mx_toluca_toluca", "label": "Sí"}, {"id": "no", "label": "No"}]
    confirmation = json.dumps(
        {
            "ok": True,
            "value": {
                "status": "needs_choice",
                "field": "zone",
                "options": options,
                "confirm": "zone_mx_toluca_toluca",
                "pad": "p" * 100,
            },
        }
    )
    assert json.loads(trim.compact_result(confirmation)) == {
        "ok": True,
        "value": {
            "status": "needs_choice",
            "field": "zone",
            "options": options,
            "confirm": "zone_mx_toluca_toluca",
        },
    }
    miss = json.dumps(
        {
            "ok": False,
            "error": "invalid_value",
            "hint": "h" * 100,
            "reason": "invalid_option",
            "attempt": 2,
            "options": [{"id": "morning", "label": "Mañana"}],
        }
    )
    assert json.loads(trim.compact_result(miss)) == {
        "ok": False,
        "error": "invalid_value",
        "reason": "invalid_option",
        "attempt": 2,
        "options": [{"id": "morning", "label": "Mañana"}],
    }


def test_a_zone_list_is_dropped_to_its_outcome(trim: ModuleType) -> None:
    assert json.loads(trim.compact_result(ZONES)) == {"ok": True, "value": {}}


def test_a_stage_is_kept_when_no_status_names_the_outcome(trim: ModuleType) -> None:
    text = json.dumps({"ok": True, "value": {"stage": "qualified", "note": "y" * 100}})
    assert json.loads(trim.compact_result(text)) == {"ok": True, "value": {"stage": "qualified"}}


@pytest.mark.parametrize(
    "text",
    [
        '{"ok":true,"value":{"stage":"qualified"}}',  # already short
        "not json at all, and long enough to be worth a look: " + "z" * 80,
        json.dumps(["a list", "y" * 100]),
        "",
    ],
)
def test_a_short_or_unrecognised_result_comes_back_unchanged(trim: ModuleType, text: str) -> None:
    assert trim.compact_result(text) == text


# ADR 0050: what the agent passed on to the Candidate keeps a trace. The samples are the
# results of the live run of 2026-10-10 (booking and Knockout flows on Haiku 5.5).
KNOCKOUT = json.dumps(
    {
        "ok": True,
        "value": {
            "status": "knockout",
            "field": "driver_license",
            "message": "Gracias, Luis. Por ahora necesitamos licencia de manejo vigente: es un "
            "requisito legal y del seguro para repartir. Aquí están los requisitos: "
            "https://empleo.gruposazon.example/requisitos. Cuando la tengas, escríbenos y "
            'seguimos donde lo dejamos. Si crees que hay un error, responde "revisión" y una '
            "persona lo verá.",
        },
    },
    ensure_ascii=False,
)


def _slot(ref: str, label: str, place: str) -> dict[str, Any]:
    interview = {
        "starts_at": "2026-10-12T08:00:00Z",
        "ends_at": "2026-10-12T08:30:00Z",
        "location_name": place,
        "address": "(synthetic) Calle Ejemplo 4, Centro, Madrid",
        "local_label": label,
    }
    return {"slot_ref": ref, "label": label, "interview": interview}


OFFER = json.dumps(
    {
        "ok": True,
        "value": {
            "slots": [
                _slot("so_Fsz6ZmE_jl0-", "lun 12 · 10:00", "Sazón Madrid Centro"),
                _slot("so_bAWhoDumDx5p", "lun 12 · 12:00", "Sazón Madrid Centro"),
                _slot("so_sGjuyVKuKub2", "lun 12 · 16:00", "Sazón Madrid Retiro"),
            ],
            "booking_url": "http://localhost:18170/book/" + "t" * 120,
            "other_label": "Otro horario",
        },
    },
    ensure_ascii=False,
)
BOOKED = json.dumps(
    {
        "ok": True,
        "value": {
            "stage": "interview_booked",
            "interview": _slot("so_x", "lun 12 · 10:00", "Sazón Madrid Centro")["interview"],
            "ics_sent": True,
            "released_previous": False,
        },
    },
    ensure_ascii=False,
)


def test_a_text_sent_word_for_word_leaves_a_marker_not_nothing(trim: ModuleType) -> None:
    """Without it, the next turn showed the model its own requirements link after a result
    that said nothing of it, and it told the Candidate the link was not official."""
    compact = json.loads(trim.compact_result(KNOCKOUT))
    assert compact == {
        "ok": True,
        "value": {"status": "knockout", "field": "driver_license", "message": trim.SENT_MARKER},
    }


def test_an_offer_keeps_its_slot_labels_and_locations(trim: ModuleType) -> None:
    compact = json.loads(trim.compact_result(OFFER))
    assert compact == {
        "ok": True,
        "value": {
            "slots": [
                {"slot_ref": "so_Fsz6ZmE_jl0-", "label": "lun 12 · 10:00"},
                {"slot_ref": "so_bAWhoDumDx5p", "label": "lun 12 · 12:00"},
                {"slot_ref": "so_sGjuyVKuKub2", "label": "lun 12 · 16:00"},
            ],
            "locations": ["Sazón Madrid Centro", "Sazón Madrid Retiro"],
        },
    }


def test_a_booking_keeps_the_interview_the_agent_confirmed(trim: ModuleType) -> None:
    compact = json.loads(trim.compact_result(BOOKED))
    assert compact == {
        "ok": True,
        "value": {
            "stage": "interview_booked",
            "interview": {
                "local_label": "lun 12 · 10:00",
                "location_name": "Sazón Madrid Centro",
                "address": "(synthetic) Calle Ejemplo 4, Centro, Madrid",
            },
            "ics_sent": True,
        },
    }


@pytest.mark.parametrize("text", [KNOCKOUT, OFFER, BOOKED])
def test_a_relayed_trace_is_stable_and_short(trim: ModuleType, text: str) -> None:
    once = trim.compact_result(text)
    assert trim.compact_result(once) == once  # the cache prefix holds from turn to turn
    # The scenario suite fails on a finished turn's result over 300 characters (_history_leaks).
    assert len(once) <= 300


def test_a_booked_slot_tap_in_an_earlier_turn_keeps_the_interview(trim: ModuleType) -> None:
    messages = [
        {"role": "system", "content": "s"},
        {"role": "user", "content": "[tap result] " + BOOKED},
        {"role": "assistant", "content": "Entrevista reservada: lunes 12 a las 10:00."},
        {"role": "user", "content": "gracias"},
    ]
    out = trim.trim_history(messages)
    assert out[1]["content"] == "[tap result] " + trim.compact_result(BOOKED)
    assert "Sazón Madrid Centro" in out[1]["content"]


def test_a_tapped_answer_in_an_earlier_turn_is_compacted_and_the_latest_is_not(
    trim: ModuleType,
) -> None:
    messages = [
        {"role": "system", "content": "s"},
        {"role": "user", "content": "[tap result] " + ACCEPTED},
        {"role": "assistant", "content": "¿Tienes licencia?"},
        {"role": "user", "content": "[tap result] " + ACCEPTED},
    ]
    out = trim.trim_history(messages)
    assert out[1]["content"] == (
        '[tap result] {"ok":true,"value":{"status":"accepted","field":"driver_license"}}'
    )
    assert out[3] == messages[3]


def test_cache_marked_text_blocks_are_compacted_and_keep_their_markers(trim: ModuleType) -> None:
    marker = {"type": "ephemeral"}
    messages = two_turns()
    messages[3] = {
        "role": "tool",
        "tool_call_id": "c1",
        "content": [{"type": "text", "text": GET_APPLICATION, "cache_control": marker}],
    }
    out = trim.trim_history(messages)
    block = out[3]["content"][0]
    assert block["cache_control"] == marker
    assert json.loads(block["text"]) == {"ok": True, "value": {"status": "open"}}


def test_it_is_idempotent_and_does_not_edit_its_input(trim: ModuleType) -> None:
    messages = two_turns()
    before = copy.deepcopy(messages)
    once = trim.trim_history(messages)
    assert messages == before
    assert trim.trim_history(once) == once


def test_the_first_turn_has_nothing_to_shrink(trim: ModuleType) -> None:
    messages = two_turns()[:4]
    assert trim.trim_history(messages) == messages
    assert trim.trim_history([]) == []


def test_the_middleware_returns_the_request_with_only_its_messages_changed(
    trim: ModuleType,
) -> None:
    request = {"model": "m", "messages": two_turns(), "tools": [{"type": "function"}]}
    before = copy.deepcopy(request)
    result = trim.llm_request(request=request, task_id="t")
    assert result is not None and result["source"] == "criba-tools"
    out = result["request"]
    assert out["tools"] == before["tools"] and out["model"] == "m"
    assert out["messages"] == trim.trim_history(before["messages"])
    assert request == before, "the callback must not edit the request it was given"
    # Run again on its own output: nothing left to do.
    assert trim.llm_request(request=out) is None


def test_garbage_in_is_ignored(trim: ModuleType) -> None:
    assert trim.llm_request(request=None) is None
    assert trim.llm_request(request={"messages": "x"}) is None
    assert trim.llm_request(request={"messages": [None, 3, "x"]}) is None
    assert trim.llm_request() is None
