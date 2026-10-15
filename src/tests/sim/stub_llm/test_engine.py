"""Matching, templating and usage estimation."""

import json
from datetime import UTC, date, datetime

import pytest

from criba.channel import router as criba_router
from criba.channel.router import spoken_run_input
from sim.personas.schema import Interpretation
from sim.stub_llm.app import DEFAULT_SCRIPT
from sim.stub_llm.engine import (
    Context,
    get_path,
    guess_lang,
    render_value,
    respond_to,
    strip_markers,
)
from sim.stub_llm.interpret import Interpreter
from sim.stub_llm.script import load_script, parse_script


def user(text):
    return {"role": "user", "content": text}


def tool_msg(name, payload, call_id="call_1"):
    return {
        "role": "tool",
        "name": name,
        "tool_call_id": call_id,
        "content": json.dumps(payload),
    }


def assistant_call(name, call_id="call_1", arguments="{}"):
    return {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {
                "id": call_id,
                "type": "function",
                "function": {"name": name, "arguments": arguments},
            }
        ],
    }


def test_context_counts_turns_and_steps():
    ctx = Context.from_messages(
        [
            {"role": "system", "content": "You are a bot."},
            user("hola"),
            {"role": "assistant", "content": "Hola, ¿tu nombre?"},
            user("Ana García"),
            assistant_call("get_application"),
            tool_msg("get_application", {"next_field": "full_name"}),
        ]
    )
    assert (ctx.user_turn, ctx.step, ctx.last_role) == (2, 1, "tool")
    assert ctx.last_user == "Ana García"
    assert ctx.last_tool == "get_application"
    assert ctx.prev_assistant == "Hola, ¿tu nombre?"
    assert ctx.system.strip() == "You are a bot."


def test_last_tool_is_resolved_from_the_call_id_when_the_message_has_no_name():
    msgs = [
        user("x"),
        assistant_call("record_answer", call_id="abc"),
        {"role": "tool", "tool_call_id": "abc", "content": '{"status": "accepted"}'},
    ]
    assert Context.from_messages(msgs).last_tool == "record_answer"


def test_content_parts_are_flattened():
    ctx = Context.from_messages(
        [user("x"), {"role": "user", "content": [{"type": "text", "text": "hola"}]}]
    )
    assert ctx.last_user == "hola"


def test_get_path_reads_root_then_value_envelope():
    envelope = {"ok": True, "value": {"stage": "zone", "options": [{"id": "z1"}]}}
    assert get_path(envelope, "stage") == "zone"
    assert get_path(envelope, "ok") is True
    assert get_path(envelope, "options.0.id") == "z1"
    assert get_path({"stage": "zone"}, "stage") == "zone"


def test_guess_lang_uses_the_latest_message_that_shows_one():
    assert guess_lang("Yes, I have a driver license") == "en"
    assert guess_lang("sí, tengo licencia") == "es"
    assert guess_lang("Madrid") is None
    ctx = Context.from_messages([user("Yes I can please"), user("Chamberí")])
    assert ctx.user_lang == "en"


def test_the_reply_language_is_the_one_get_application_returned():
    """The Screener skill: reply in the `language` the result returns, so a one-word Spanish
    answer ("Sí", which guess_lang reads as Spanish) does not change an English reply."""
    sent = [user("Yes I can please"), user("Sí")]
    assert guess_lang("Sí") == "es"
    for envelope in (True, False):
        payload = {"language": "en"}
        ctx = Context.from_messages(
            [
                *sent,
                assistant_call("get_application"),
                tool_msg(
                    "get_application",
                    {"ok": True, "value": payload} if envelope else payload,
                ),
            ]
        )
        assert ctx.user_lang == "en"
    ctx = Context.from_messages(
        [
            *sent,
            tool_msg("get_application", {"ok": True, "value": {"language": "es-MX"}}),
        ]
    )
    assert ctx.user_lang == "es"


def test_a_one_word_answer_after_the_first_message_sends_no_language():
    script = script_of(
        {
            "respond": {
                "tool_calls": [
                    {
                        "name": "get_application",
                        "arguments": {"language": "{{language}}"},
                        "omit_empty": ["language"],
                    }
                ]
            }
        }
    )
    interp = Interpreter([Interpretation("start_date", "Mañana", "es", value="2026-10-10")])

    def args(*texts):
        return call_of(respond_to(script, [user(t) for t in texts], interpreter=interp))[1]

    assert args("Hello, I am looking for a job", "Mañana") == {}
    assert args("Mañana") == {"language": "es-ES"}  # a first message still sets it
    assert args("Hello", "Mañana por la tarde, por favor") == {"language": "es-ES"}


def test_render_keeps_the_type_of_a_whole_placeholder():
    ctx = Context.from_messages([user("hola"), tool_msg("t", {"n": 3, "xs": ["a", "b"]})])
    assert render_value("{{tool.n}}", ctx) == 3
    assert render_value("{{tool.xs}}", ctx) == ["a", "b"]
    assert render_value("n={{tool.n}} u={{user}}", ctx) == "n=3 u=hola"
    assert render_value({"k": ["{{user}}"]}, ctx) == {"k": ["hola"]}
    assert render_value("{{tool.missing}}", ctx) == ""


SCRIPT = parse_script(
    {
        "name": "t",
        "defaults": {"cache_ratio": 0.5, "min_prompt_tokens": 100},
        "rules": [
            {"name": "a", "match": {"user_turn": 1}, "respond": {"content": "uno"}},
            {
                "name": "b",
                "match": {"user_turn": [2, 3], "user_contains": ["HOLA"]},
                "respond": {"content": "dos"},
            },
            {
                "name": "c",
                "match": {
                    "last_tool": "get_application",
                    "tool_json": {"stage": ["zone", "name"]},
                },
                "respond": {
                    "content": {"es": "ES {{tool.stage}}", "en": "EN {{tool.stage}}"},
                    "tool_calls": [
                        {
                            "name": "record_answer",
                            "arguments": {"field": "zone", "value": "{{user}}"},
                        }
                    ],
                    "usage": {"prompt_tokens": 1000, "completion_tokens": 7},
                },
            },
            {
                "name": "e",
                "match": {"user_regex": "^boom"},
                "respond": {"error": {"status": 429, "message": "slow down"}},
            },
        ],
        "fallback": {"content": "fb"},
    }
)


def test_first_matching_rule_wins_and_fallback_applies():
    assert respond_to(SCRIPT, [user("x")]).rule == "a"
    assert respond_to(SCRIPT, [user("x"), user("y")]).rule == "fallback"
    assert respond_to(SCRIPT, [user("x"), user("hola")]).rule == "b"


def test_tool_json_any_of_and_templated_tool_call():
    msgs = [
        user("x"),
        user("Centro"),
        assistant_call("get_application"),
        tool_msg("get_application", {"stage": "zone"}),
    ]
    reply = respond_to(SCRIPT, msgs)
    assert reply.rule == "c"
    assert reply.content == "ES zone"
    assert reply.finish_reason == "tool_calls"
    [call] = reply.tool_calls
    assert call.name == "record_answer"
    assert json.loads(call.arguments) == {"field": "zone", "value": "Centro"}
    assert call.id.startswith("call_")


def test_call_ids_are_deterministic_and_distinct_within_a_reply():
    msgs = [
        user("x"),
        user("Centro"),
        assistant_call("get_application"),
        tool_msg("get_application", {"stage": "zone"}),
    ]
    assert respond_to(SCRIPT, msgs).tool_calls[0].id == respond_to(SCRIPT, msgs).tool_calls[0].id
    two = parse_script(
        {"fallback": {"tool_calls": [{"name": "get_application"}, {"name": "get_application"}]}}
    )
    ids = [c.id for c in respond_to(two, [user("x")]).tool_calls]
    assert len(set(ids)) == 2


def test_usage_override_and_estimates():
    msgs = [
        user("x"),
        user("Centro"),
        assistant_call("get_application"),
        tool_msg("get_application", {"stage": "zone"}),
    ]
    usage = respond_to(SCRIPT, msgs).usage
    assert usage["prompt_tokens"] == 1000
    assert usage["completion_tokens"] == 7
    assert usage["total_tokens"] == 1007
    assert usage["prompt_tokens_details"]["cached_tokens"] == 500  # history present, ratio 0.5
    first = respond_to(SCRIPT, [user("x")]).usage
    assert first["prompt_tokens"] == 100  # floor
    assert first["prompt_tokens_details"]["cached_tokens"] == 0  # nothing to cache on turn 1
    assert first["completion_tokens"] >= 1


def test_cost_is_reported_when_prices_are_set():
    script = parse_script(
        {
            "defaults": {
                "price_per_mtok": {"prompt": 1.0, "completion": 5.0},
                "cache_ratio": 0,
            },
            "fallback": {
                "content": "x",
                "usage": {"prompt_tokens": 1_000_000, "completion_tokens": 1_000_000},
            },
        }
    )
    assert respond_to(script, [user("x")]).usage["cost"] == 6.0


def test_error_rule():
    reply = respond_to(SCRIPT, [user("x"), user("boom")])
    assert reply.error["status"] == 429
    assert reply.error["message"] == "slow down"


def test_history_tool_matches_and_templates_read_earlier_tool_results():
    script = parse_script(
        {
            "rules": [
                {
                    "name": "link",
                    "match": {"last_tool": "get_application", "history_tool": "offer"},
                    "respond": {"content": "{{history.offer.url}}|{{history.offer.a.0}}"},
                },
            ],
            "fallback": {"content": "fb"},
        }
    )
    call = {
        "id": "c1",
        "type": "function",
        "function": {"name": "offer", "arguments": "{}"},
    }
    offer = [
        {"role": "assistant", "content": None, "tool_calls": [call]},
        {
            "role": "tool",
            "tool_call_id": "c1",
            "content": '{"ok": true, "value": {"url": "U", "a": ["z"]}}',
        },
    ]
    tail = [
        {"role": "user", "content": "y"},
        {
            "role": "tool",
            "name": "get_application",
            "tool_call_id": "c2",
            "content": "{}",
        },
    ]
    first = [{"role": "user", "content": "x"}]
    assert respond_to(script, first + offer + tail).content == "U|z"
    assert respond_to(script, first + tail).content == "fb"


def test_tool_json_contains_matches_text_case_insensitively_and_needs_the_path():
    script = parse_script(
        {
            "rules": [
                {
                    "name": "city",
                    "match": {
                        "last_tool": "record_answer",
                        "tool_json_contains": {"hint": ["city recognised"]},
                    },
                    "respond": {"content": "city"},
                },
                {
                    "name": "zone-option",
                    "match": {
                        "last_tool": "record_answer",
                        "tool_json_contains": {"options.0.id": ["zone_"]},
                    },
                    "respond": {"content": "zone"},
                },
            ],
            "fallback": {"content": "fb"},
        }
    )

    def reply_to(payload):
        msgs = [
            user("x"),
            assistant_call("record_answer"),
            tool_msg("record_answer", payload),
        ]
        return respond_to(script, msgs).rule

    assert reply_to({"ok": False, "hint": "City recognised (X); offer the list"}) == "city"
    assert reply_to({"ok": False, "options": [{"id": "zone_a"}]}) == "zone-option"
    assert reply_to({"ok": False, "options": [{"id": "full_time"}]}) == "fallback"
    assert reply_to({"ok": False, "hint": "Something else"}) == "fallback"


def test_regex_groups_are_templated_trimmed_and_scoped_to_the_matching_rule():
    script = parse_script(
        {
            "rules": [
                {
                    "name": "zone",
                    "match": {"user_regex": r"(?i)mi zona es (?P<value>.+)"},
                    "respond": {
                        "tool_calls": [
                            {
                                "name": "record_answer",
                                "arguments": {
                                    "field": "zone",
                                    "value": "{{match.value}}",
                                },
                            }
                        ]
                    },
                },
                {"name": "other", "respond": {"content": "[{{match.value}}]"}},
            ]
        }
    )
    reply = respond_to(script, [user("No, mi zona es La Paz.")])
    assert json.loads(reply.tool_calls[0].arguments) == {
        "field": "zone",
        "value": "La Paz",
    }
    # A rule without user_regex sees no groups.
    assert respond_to(script, [user("hola")]).content == "[]"


# ---- the interpreter's placeholders (spec 006b) ---------------------------------------------

INTERP = Interpreter(
    [
        Interpretation("full_name", "me llamo ana garcía", "es", value="Ana García"),
        Interpretation("start_date", "el lunes", "es", value="next:mon"),
        Interpretation("availability", "full time", "en", value=["full_time"]),
        Interpretation("zone", "near Chamberí", "en", query="Chamberí"),
        Interpretation("zone", "por la salida a Toluca", "es", query=None),
    ]
)


def call_of(reply):
    assert len(reply.tool_calls) == 1
    return reply.tool_calls[0].name, json.loads(reply.tool_calls[0].arguments)


def script_of(*rules):
    return parse_script({"rules": list(rules)})


def after_tool(name, payload, text="x"):
    """The history of a fresh turn in which `name` has just answered."""
    return [
        user(text),
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": name, "arguments": "{}"},
                }
            ],
        },
        tool_msg(name, payload),
    ]


def get_application_result(text, **payload):
    base = {"next_field": "full_name", "today": "2026-10-09"}
    return after_tool("get_application", {**base, **payload}, text)


RECORD_NEXT = script_of(
    {
        "respond": {
            "tool_calls": [
                {
                    "name": "record_answer",
                    "arguments": {
                        "field": "{{tool.next_field}}",
                        "value": "{{interpret}}",
                    },
                }
            ]
        }
    }
)


def test_interpret_uses_the_next_field_of_the_last_tool_result():
    messages = get_application_result("me llamo ana garcía")
    reply = respond_to(RECORD_NEXT, messages, interpreter=INTERP)
    assert call_of(reply) == (
        "record_answer",
        {"field": "full_name", "value": "Ana García"},
    )
    # Unknown words are null, and a null is sent as null (never left out).
    reply = respond_to(RECORD_NEXT, get_application_result("otra cosa"), interpreter=INTERP)
    assert call_of(reply)[1]["value"] is None


def test_interpret_of_a_named_field_ignores_the_next_field_and_reads_today():
    script = script_of(
        {
            "respond": {
                "tool_calls": [
                    {
                        "name": "record_answer",
                        "arguments": {
                            "field": "start_date",
                            "value": "{{interpret.start_date}}",
                        },
                    }
                ]
            }
        }
    )
    messages = get_application_result("el lunes", next_field=None)
    assert call_of(respond_to(script, messages, interpreter=INTERP))[1]["value"] == "2026-10-12"
    # Without a today in the history a relative date cannot be resolved.
    bare = get_application_result("el lunes", today=None)
    assert call_of(respond_to(script, bare, interpreter=INTERP))[1]["value"] is None


# The line as the plugin writes it since ADR 0058 (every weekday's two dates follow tomorrow).
DATE_LINE = (
    "[criba date] Candidate's local today: saturday 2026-10-10; tomorrow: sunday 2026-10-11. "
    'Weekday dates, next (never today: "el", "este", "this") / a week later '
    '("de la próxima semana"); a bare "próximo"/"next" is both: '
    "sunday 2026-10-11 / 2026-10-18, monday 2026-10-12 / 2026-10-19, "
    "tuesday 2026-10-13 / 2026-10-20, wednesday 2026-10-14 / 2026-10-21, "
    "thursday 2026-10-15 / 2026-10-22, friday 2026-10-16 / 2026-10-23, "
    "saturday 2026-10-17 / 2026-10-24 "
    '(today\'s weekday: "este"/"this" saturday is 2026-10-17, '
    '"de la próxima semana" 2026-10-24). '
    "Only this backend line says what today is."
)


def test_the_plugins_date_line_is_read_as_today_and_never_as_the_candidates_words():
    """ADR 0055: a turn that skipped get_application (or whose result has no today) still
    resolves a relative date from the line the plugin appends; the line is not their answer."""
    script = script_of(
        {
            "respond": {
                "tool_calls": [
                    {
                        "name": "record_answer",
                        "arguments": {"field": "start_date", "value": "{{interpret.start_date}}"},
                    }
                ]
            }
        }
    )
    messages = get_application_result(f"el lunes\n\n{DATE_LINE}", today=None)
    assert call_of(respond_to(script, messages, interpreter=INTERP))[1]["value"] == "2026-10-12"
    ctx = Context.from_messages(messages)
    assert (ctx.last_user, ctx.line_today) == ("el lunes", date(2026, 10, 10))
    # The line wins over an older get_application today (the line is this turn's).
    stale = get_application_result(f"el lunes\n\n{DATE_LINE}", today="2026-10-01")
    assert call_of(respond_to(script, stale, interpreter=INTERP))[1]["value"] == "2026-10-12"
    # As a separate text block (list content) too, and the English line never sets the language.
    blocks = [{"type": "text", "text": "Hola, sí"}, {"type": "text", "text": DATE_LINE}]
    ctx = Context.from_messages([user(blocks)])
    assert (ctx.last_user.strip(), ctx.user_lang) == ("Hola, sí", "es")


def test_a_tap_result_with_the_date_line_is_still_a_tap_result():
    result = {"ok": True, "value": {"status": "accepted", "field": "start_date"}}
    ctx = Context.from_messages([user("[tap result] " + json.dumps(result) + "\n\n" + DATE_LINE)])
    assert (ctx.last_role, ctx.last_tool, ctx.tool_json) == ("tool", "record_answer", result)


def test_a_tapped_id_keeps_its_shape_through_interpret():
    availability = get_application_result("part_time", next_field="availability")
    got = call_of(respond_to(RECORD_NEXT, availability, interpreter=INTERP))
    assert got[1]["value"] == ["part_time"]
    zone = get_application_result("zone_mx_puebla_centro", next_field="zone")
    got = call_of(respond_to(RECORD_NEXT, zone, interpreter=INTERP))
    assert got[1]["value"] == ["zone_mx_puebla_centro"]


def test_zone_query_and_the_interprets_match():
    script = script_of(
        {
            "name": "search",
            "match": {"interprets": "zone"},
            "respond": {
                "tool_calls": [{"name": "search_zones", "arguments": {"query": "{{zone_query}}"}}]
            },
        },
        {"name": "other", "respond": {"content": "no search"}},
    )
    found = respond_to(script, [user("near Chamberí")], interpreter=INTERP)
    assert call_of(found) == ("search_zones", {"query": "Chamberí"})
    # A query of null means nothing to search, so the rule does not apply.
    nothing = respond_to(script, [user("por la salida a Toluca")], interpreter=INTERP)
    assert nothing.rule == "other"
    assert respond_to(script, [user("nadie lo dice")], interpreter=INTERP).rule == "other"


def zone_ids_of(messages):
    script = script_of(
        {
            "respond": {
                "tool_calls": [
                    {
                        "name": "record_answer",
                        "arguments": {"field": "zone", "value": "{{zone_ids}}"},
                    }
                ]
            }
        }
    )
    return call_of(respond_to(script, messages, interpreter=INTERP))[1]["value"]


def zone_row(i):
    return {
        "zone_id": f"zone_mx_x_{i}",
        "label": "x",
        "city": "X",
        "country": "MX",
        "covered": True,
    }


def test_zone_ids_are_every_id_the_search_returned():
    rows = [zone_row(i) for i in range(3)]
    found = after_tool("search_zones", {"ok": True, "value": {"zones": rows, "more": False}})
    assert zone_ids_of(found) == ["zone_mx_x_0", "zone_mx_x_1", "zone_mx_x_2"]
    bare = after_tool("search_zones", {"zones": rows[:1], "more": False})  # no envelope
    assert zone_ids_of(bare) == ["zone_mx_x_0"]


def test_zone_ids_are_null_when_nothing_was_found_or_there_is_more():
    empty = after_tool("search_zones", {"ok": True, "value": {"zones": [], "more": False}})
    assert zone_ids_of(empty) is None
    more = after_tool("search_zones", {"ok": True, "value": {"zones": [zone_row(0)], "more": True}})
    assert zone_ids_of(more) is None


def test_zone_ids_are_null_after_any_other_tool():
    assert zone_ids_of(get_application_result("x")) is None


GET_APPLICATION = script_of(
    {
        "respond": {
            "tool_calls": [
                {
                    "name": "get_application",
                    "arguments": {"language": "{{language}}"},
                    "omit_empty": ["language"],
                }
            ]
        }
    }
)


def test_language_is_left_out_after_a_tap_and_sent_for_typed_text():
    def args(text):
        return call_of(respond_to(GET_APPLICATION, [user(text)], interpreter=INTERP))[1]

    assert args("full time") == {"language": "en"}  # a persona message: its own lang
    assert args("hola buenas") == {"language": "es-ES"}  # a guess, Spanish by country later
    assert args("expired") == {}  # a tapped option says nothing about the language
    assert args("[system] approved") == {}  # and neither does a text with no language


def test_omit_empty_only_drops_the_named_arguments():
    script = script_of(
        {
            "respond": {
                "tool_calls": [
                    {
                        "name": "record_answer",
                        "arguments": {"field": "zone", "value": "{{zone_ids}}"},
                        "omit_empty": ["note"],
                    }
                ]
            }
        }
    )
    assert call_of(respond_to(script, [user("x")], interpreter=INTERP))[1] == {
        "field": "zone",
        "value": None,
    }


def test_tool_json_absent_needs_the_key_to_be_missing():
    script = parse_script(
        {
            "rules": [
                {
                    "name": "no-booking",
                    "match": {
                        "last_tool": "get_application",
                        "tool_json": {"approved": True},
                        "tool_json_absent": ["interview"],
                    },
                    "respond": {"content": "offer"},
                },
            ],
            "fallback": {"content": "fb"},
        }
    )

    def reply_to(payload):
        msgs = [
            user("x"),
            assistant_call("get_application"),
            tool_msg("get_application", payload),
        ]
        return respond_to(script, msgs).rule

    assert reply_to({"ok": True, "value": {"approved": True}}) == "no-booking"
    assert reply_to({"ok": True, "value": {"approved": True, "interview": {"x": 1}}}) == "fallback"
    assert reply_to({"ok": True, "value": {"approved": True, "interview": None}}) == "fallback"


def test_a_tap_result_reads_as_a_record_answer_result():
    """The backend records a tap itself (ADR 0043); the agent gets the result as its input."""
    result = {
        "ok": True,
        "value": {
            "status": "accepted",
            "field": "zone",
            "next_field": "driver_license",
        },
    }
    ctx = Context.from_messages(
        [
            user("Puebla"),
            {"role": "assistant", "content": "¿Cuál?"},
            user("[tap result] " + json.dumps(result)),
        ]
    )
    assert (ctx.last_role, ctx.last_tool, ctx.tool_json) == (
        "tool",
        "record_answer",
        result,
    )
    assert ctx.tool_results["record_answer"] == result
    assert ctx.user_turn == 2


@pytest.mark.parametrize(
    "result",
    [
        {
            "ok": True,
            "value": {
                "stage": "interview_booked",
                "interview": {"local_label": "lun 12 · 10:00", "location_name": "Sazón Centro"},
                "ics_sent": True,
            },
        },
        {"ok": False, "error": "slot_unavailable", "hint": "taken"},
    ],
)
def test_a_slot_tap_result_reads_as_a_book_interview_result(result):
    """The backend books a tapped slot itself (ADR 0050)."""
    ctx = Context.from_messages(
        [
            user("[system] approved"),
            {"role": "assistant", "content": "Elige."},
            user("[tap result] " + json.dumps(result)),
        ]
    )
    assert (ctx.last_role, ctx.last_tool, ctx.tool_json) == ("tool", "book_interview", result)


def test_an_other_time_tap_result_reads_as_a_booking_link_answer():
    """The backend answers "other time" with the booking link itself (ADR 0064)."""
    result = {
        "ok": True,
        "value": {"status": "booking_link", "booking_url": "https://x/book/t", "message": "m"},
    }
    ctx = Context.from_messages(
        [
            user("[system] approved"),
            {"role": "assistant", "content": "Elige."},
            user("[tap result] " + json.dumps(result)),
        ]
    )
    assert (ctx.last_role, ctx.last_tool, ctx.tool_json) == ("tool", "booking_link", result)


def test_text_that_only_looks_like_a_tap_result_is_a_message():
    ctx = Context.from_messages([user("[tap result] not json")])
    assert (ctx.last_role, ctx.last_tool, ctx.tool_json) == ("user", None, None)


# ---- spoken turns: the backend's run-input blocks (sim-stub SM1, SM2, R24.3) ----------------

SENTINEL = "sentinelturninput"
NOW = datetime(2026, 10, 9, 12, 0, tzinfo=UTC)
APP = {"id": "app_spoken"}
SPOKEN_RECORD = {
    "status": "open",
    "control": "agent",
    "stage": "name",
    "next_field": "full_name",
    "today": "2026-10-09",
}
PACKAGED = load_script(DEFAULT_SCRIPT)
SPOKEN_INTERP = Interpreter(
    [Interpretation("full_name", "Luis Pérez", "es", value="Luis Pérez")], {"Hola": "es"}
)


@pytest.fixture
def spoken(monkeypatch):
    """A spoken run input built by the merged ``spoken_run_input``: its blocks, then the raw
    ``text`` in place of the turn input (the builder would strip a leading marker from it)."""
    monkeypatch.setattr(
        criba_router, "get_application", lambda conn, app, args, now: dict(SPOKEN_RECORD)
    )

    def build(text, *, application=True, voice=True, optout_request=False):
        built = spoken_run_input(None, APP, SENTINEL, optout_request=optout_request, now=NOW)
        lines = built.split("\n")
        assert lines[-1] == SENTINEL
        lines = lines[:-1]
        if not application:
            assert lines[0].startswith("[application]")
            assert lines[1].startswith("This [application] block")
            lines = lines[2:]
        if not voice:
            start = lines.index("[voice]")
            end = start + 1
            while end < len(lines) and lines[end].startswith("- "):
                end += 1
            lines = lines[:start] + lines[end:]
        return "\n".join([*lines, text])

    return build


MARKER_COMBINATIONS = [
    pytest.param(application, voice, optout, id=f"{name}{'-optout' if optout else ''}")
    for name, application, voice in (
        ("both", True, True),
        ("application", True, False),
        ("voice", False, True),
    )
    for optout in (False, True)
]
TAP = {"ok": True, "value": {"status": "accepted", "field": "zone", "next_field": "driver_license"}}
SPOKEN_TEXTS = [
    pytest.param("Me llamo Luis Pérez y vivo en Madrid", id="spanish"),
    pytest.param("Hello, I want to apply for the job", id="english"),
    pytest.param(f"sí, tengo carnet\n\n{DATE_LINE}", id="date-line"),
    pytest.param("[tap result] " + json.dumps(TAP), id="tap"),
]


def _reading(ctx):
    return (
        ctx.last_user,
        ctx.user_lang,
        ctx.user_turn,
        ctx.line_today,
        ctx.last_role,
        ctx.last_tool,
        ctx.tool_json,
    )


@pytest.mark.parametrize("text", SPOKEN_TEXTS)
@pytest.mark.parametrize(("application", "voice", "optout"), MARKER_COMBINATIONS)
def test_spoken_turns_read_as_their_typed_text(spoken, text, application, voice, optout):
    marked = spoken(text, application=application, voice=voice, optout_request=optout)
    assert marked != text
    typed = Context.from_messages([user(text)])
    heard = Context.from_messages([user(marked)])
    assert _reading(heard) == _reading(typed)


def _replied(messages, interpreter=SPOKEN_INTERP):
    reply = respond_to(PACKAGED, messages, interpreter=interpreter)
    calls = [(c.id, c.name, json.loads(c.arguments)) for c in reply.tool_calls]
    return reply.rule, reply.content, calls


def test_a_spoken_system_approved_still_offers_slots(spoken):
    record = {"ok": True, "value": {"status": "open", "control": "agent", "approved": True}}
    rule, _, calls = _replied(after_tool("get_application", record, spoken("[system] approved")))
    assert rule == "approved-offer"
    assert [name for _, name, _ in calls] == ["offer_interview_slots"]


@pytest.mark.parametrize("answer", ["Luis Pérez", "sí"])
def test_block_text_trips_no_rule(spoken, answer):
    record = {"ok": True, "value": dict(SPOKEN_RECORD)}

    def history(text):
        return [
            user("Hola"),
            {"role": "assistant", "content": "¡Hola! ¿Cómo te llamas?"},
            *after_tool("get_application", record, text),
        ]

    typed = _replied(history(answer))
    heard = _replied(history(spoken(answer)))
    assert heard == typed
    assert heard[0] != "injection-redirect"

    first = {"ok": True, "value": {**SPOKEN_RECORD, "stage": "greeting"}}
    assert _replied(after_tool("get_application", first, spoken("Hola")))[0] == "greeting"
    assert _replied(after_tool("get_application", first, "Hola"))[0] == "greeting"


def test_the_zone_note_is_a_leading_marker_and_never_the_candidates_words():
    """The router puts one `[zone]` line ahead of a typed message read while the Zone is the
    question (ADR 0068); the stub reads the message under it."""
    text = "[zone] If they name a place, look it up with search_zones.\nI live in Texas!"
    assert strip_markers(text) == ("I live in Texas!", frozenset({"zone"}))
    ctx = Context.from_messages([user(text)])
    assert (ctx.last_user, ctx.markers) == ("I live in Texas!", frozenset({"zone"}))
    assert strip_markers("I live in\n[zone] Texas") == ("I live in\n[zone] Texas", frozenset())


def test_a_marker_in_mid_text_is_kept():
    for text in ("hola [voice] adiós", "hola\n[optout_request]"):
        assert strip_markers(text) == (text, frozenset())
        ctx = Context.from_messages([user(text)])
        assert (ctx.last_user, ctx.markers) == (text, frozenset())


@pytest.mark.parametrize(
    ("first", "lang"), [("Hola", "es"), ("Hello, I want to apply for the job", "en")]
)
def test_every_user_message_is_stripped(spoken, first, lang):
    def history(opening, answer):
        return [user(opening), {"role": "assistant", "content": "¿Tu nombre?"}, user(answer)]

    typed = Context.from_messages(history(first, "Luis Pérez"))
    heard = Context.from_messages(history(spoken(first), spoken("Luis Pérez")))
    assert (heard.user_lang, heard.user_turn) == (typed.user_lang, typed.user_turn) == (lang, 2)


def test_a_fresh_spoken_turn_still_calls_get_application(spoken):
    rule, _, calls = _replied([{"role": "system", "content": "x"}, user(spoken("Hola"))])
    assert rule == "fresh-turn"
    assert [name for _, name, _ in calls] == ["get_application"]


def test_markers_name_what_the_latest_message_carried(spoken):
    assert Context.from_messages([user(spoken("Hola"))]).markers == {"application", "voice"}
    asked = Context.from_messages([user(spoken("Hola", optout_request=True))])
    assert asked.markers == {"application", "voice", "optout_request"}
    assert Context.from_messages([user("Hola")]).markers == frozenset()
    earlier = [
        user(spoken("No me llaméis más", optout_request=True)),
        {"role": "assistant", "content": "¿Confirmas?"},
        user("sí"),
    ]
    assert Context.from_messages(earlier).markers == frozenset()


def test_an_optout_request_turn_is_asked_to_confirm(spoken):
    record = {"ok": True, "value": dict(SPOKEN_RECORD)}
    marked = spoken("No me llaméis más", optout_request=True)
    rule, content, calls = _replied(after_tool("get_application", record, marked))
    assert rule == "optout-spoken-ask"
    assert content == "Claro. ¿Confirmas que quieres que dejemos de escribirte?"
    assert calls == []


def test_without_the_marker_the_words_read_as_typed(spoken):
    record = {"ok": True, "value": dict(SPOKEN_RECORD)}
    typed = _replied(after_tool("get_application", record, "No me llaméis más"))
    heard = _replied(after_tool("get_application", record, spoken("No me llaméis más")))
    assert heard == typed
    assert heard[0] != "optout-spoken-ask"


def test_the_spoken_ask_repeats_optout_ask():
    rules = {rule.name: rule for rule in PACKAGED.rules}
    spoken_ask, typed_ask = rules["optout-spoken-ask"], rules["optout-ask"]
    assert spoken_ask.respond.content == typed_ask.respond.content
    assert spoken_ask.respond.tool_calls == []
    names = [rule.name for rule in PACKAGED.rules]
    assert names.index("optout-spoken-ask") == names.index("optout-ask") + 1
