"""Persona schema, loader and the 10 seed personas (spec 004 task 1)."""

import copy
import json
import re
from pathlib import Path
from typing import get_args

import pytest
import yaml

from sim.personas import (
    FIELD_ORDER,
    PERSONAS_DIR,
    Persona,
    PersonaError,
    duration_seconds,
    load_persona,
    load_personas,
)
from sim.personas import schema as persona_schema

REPO = Path(__file__).resolve().parents[3]
TOOLS = json.loads((REPO / "contracts" / "tools.json").read_text(encoding="utf-8"))["$defs"]
SCHEMA_SQL = (REPO / "contracts" / "schema.sql").read_text(encoding="utf-8")

PERSONAS = load_personas()

EXPECTED_IDS = {
    "qualified_es",
    "no_license_mx",
    "zone_outside_es",
    "zone_ambiguous_mx",
    "invalid_then_corrected_es",
    "stalled_abandoned_es",
    "withdrawn_mx",
    "opted_out_code_switch_es",
    "language_switch_mx",
    "prompt_injection_es",
    # W3-1: the stub screener's several-fields and confirm-correction rules.
    "multi_answer_es",
    "confirm_correction_mx",
    # 006b: a Zone said in free form.
    "zone_freeform_mx",
    # Language-switch P2: one word in another language is not a switch.
    "language_one_word_en",
    # Confirm-step correction stall: a corrected Zone picked from buttons (ADR 0048).
    "confirm_correction_pick_mx",
    # Demo chat 2026-10-10: delivered on their own, no app, recorded on the first answer.
    "platforms_none_mx",
    # Demo chat 2026-10-10: "el próximo domingo" fits two Sundays; the backend asks (ADR 0054).
    "next_weekday_choice_es",
    # Demo chat 2026-10-10: an MX chat living in Chamberí moves to ES; "tomorrow" is recorded.
    "zone_country_tomorrow_en",
    # Demo chat 2026-10-11: "me preguntas en inglés porfi" asks for English; Jev reads the
    # request, not the language it is written in (ADR 0061).
    "language_request_es",
    # Demo chat 2026-10-11: a city plus a place that is not a Zone gets the city's list (ADR 0062).
    "zone_unknown_place_mx",
    # Demo chat 2026-10-11: three unreadable messages hand the chat to the Recruiters (ADR 0063).
    "unreadable_name_mx",
    # Demo chat 2026-10-12: "I live in Texas!" matches no Zone; the cities funnel, then a Knockout
    # (ADR 0068).
    "zone_texas_funnel_en",
}


def _event_kinds_in_schema() -> list[str]:
    """`events.kind` values from the CHECK constraint in contracts/schema.sql, in its order."""
    block = SCHEMA_SQL.split("CREATE TABLE events", 1)[1].split("actor ", 1)[0]
    return [k for k in re.findall(r"'([a-z_]+)'", block) if k != "kind"]


# ---- the seed set ---------------------------------------------------------------------


def test_seed_personas_with_expected_ids():
    assert set(PERSONAS) == EXPECTED_IDS
    assert len(list(PERSONAS_DIR.glob("*.yaml"))) == len(EXPECTED_IDS) == 22


def test_each_persona_states_its_end_state():
    for persona in PERSONAS.values():
        exp = persona.expect
        assert exp.stage and exp.status, persona.id
        assert exp.events, f"{persona.id} lists no key events"
        assert persona.description.strip(), persona.id


def test_phone_numbers_are_unique_and_match_the_country():
    phones = [p.identity.phone for p in PERSONAS.values()]
    assert len(set(phones)) == len(phones)
    for p in PERSONAS.values():
        assert p.identity.phone.startswith("+34" if p.identity.country == "ES" else "+52")


def test_both_countries_are_represented():
    assert {p.identity.country for p in PERSONAS.values()} == {"ES", "MX"}


def by_quirk(quirk: str) -> list[Persona]:
    return [p for p in PERSONAS.values() if quirk in p.quirks]


class TestCoverageOfPhase1EdgeCases:
    def test_qualified(self):
        done = [p for p in PERSONAS.values() if p.expect.stage == "qualified"]
        assert len(done) >= 4
        assert any(p.expect.fields and len(p.expect.field_order) == 8 for p in done)

    def test_no_license_knockout(self):
        [p] = [p for p in PERSONAS.values() if p.expect.knockout_criterion == "driver_license"]
        assert p.expect.outcome == "knockout" and p.expect.knockout_template == "es-MX"
        assert "review_requested" in p.expect.events  # disputes it

    def test_zone_outside_every_service_area(self):
        p = PERSONAS["zone_outside_es"]
        assert p.expect.status == "open"  # Knockout, then Reopened by a corrected Zone
        assert "knockout" in p.expect.events
        assert "reopened" in p.expect.events  # writes again within 30 days
        assert any("outside every" in a for a in p.seed_assumptions)

    def test_ambiguous_zone(self):
        p = PERSONAS["zone_ambiguous_mx"]
        assert "answer_ambiguous" in p.expect.events
        assert any(r.pick_option is not None for r in p.answers["zone"])

    def test_no_license_persona_resolves_an_ambiguous_zone_by_hint(self):
        p = PERSONAS["no_license_mx"]
        assert "ambiguous_zone" in p.quirks
        assert p.zone_choice == "Centro"
        assert all(r.pick_option is None for r in p.answers["zone"])
        assert "answer_ambiguous" in p.expect.events

    def test_no_other_persona_names_a_bare_city_that_matches_several_zones(self):
        # "Puebla" alone matches three seeded Zones: only personas that resolve it may say it.
        for p in PERSONAS.values():
            replies = p.answers.get("zone", [])
            bare = [r for r in replies if r.text == "Puebla"]
            resolves = p.zone_choice is not None or any(r.pick_option is not None for r in replies)
            assert not bare or resolves, p.id

    def test_invalid_then_corrected(self):
        [p] = by_quirk("invalid_answers")
        assert len(p.answers["full_name"]) >= 3 and p.confirm
        assert p.expect.stage == "qualified"
        assert "escalation_raised" in p.expect.forbidden_events

    def test_unreadable_messages_end_with_the_recruiters(self):
        [p] = by_quirk("unreadable")
        assert len(p.answers["full_name"]) == 3 and all(
            r.value is None for r in p.answers["full_name"]
        )
        assert p.expect.status == "open" and "escalation_raised" in p.expect.events
        assert "answer_recorded" in p.expect.forbidden_events

    def test_stalled_then_abandoned(self):
        [p] = by_quirk("silence")
        assert p.expect.outcome == "abandoned" and p.expect.nudges == 3
        assert p.expect.events.count("nudge_sent") == 3
        [stall] = [i for i in p.interrupts if i.advance]
        seconds = [duration_seconds(d) for d in stall.advance]
        cumulative = [sum(seconds[: i + 1]) for i in range(len(seconds))]
        # The Phase 1 timings: 1h, 20h, 48h, then 7 days.
        assert cumulative[:3] == [3600, 20 * 3600, 48 * 3600]
        assert cumulative[-1] > 7 * 86400

    def test_stalled_nudges_avoid_quiet_hours(self):
        """Quiet hours are 22:00 to 09:00 local (Phase 1 4.1)."""
        for persona in (
            PERSONAS["stalled_abandoned_es"],
            PERSONAS["opted_out_code_switch_es"],
        ):
            hour, minute = map(int, persona.timing.start_local_time.split(":"))
            for offset_h in (1, 20, 48):
                local = (hour * 60 + minute + offset_h * 60) % (24 * 60) / 60
                assert 9 <= local < 22, f"{persona.id}: nudge at +{offset_h}h lands at {local:.1f}h"

    def test_withdrawn(self):
        [p] = by_quirk("withdraws")
        assert p.expect.outcome == "withdrawn" and "record_backout" in p.expect.tools
        assert "nudge_sent" in p.expect.forbidden_events

    def test_opted_out(self):
        [p] = by_quirk("opts_out")
        assert p.expect.candidate_opted_out and p.expect.status == "open"
        assert p.expect.outcome is None  # the Application stays as it is
        assert "opted_out" in p.expect.events and p.expect.nudges == 0

    def test_language_switching_covers_both_spanish_variants_and_english(self):
        switchers = by_quirk("code_switching")
        assert {p.identity.locale for p in switchers} == {"es-ES", "es-MX"}
        for p in switchers:
            langs = {r.lang for rs in p.answers.values() for r in rs}
            langs |= {i.say.lang for i in p.interrupts if i.say}
            assert "en" in langs, p.id
            assert p.expect.reply_languages_follow_latest, p.id

    def test_one_word_in_another_language_is_not_a_switch(self):
        [p] = by_quirk("foreign_word")
        assert p.identity.locale == "en"
        word = p.answers["start_date"][0]
        assert word.lang == "es" and len(word.text.split()) == 1
        assert p.expect.final_language == "en" and p.expect.reply_language == "en"
        assert not p.expect.reply_languages_follow_latest  # the reply is English to "Mañana"

    def test_prompt_injection(self):
        [p] = by_quirk("injection")
        assert len([i for i in p.interrupts if i.say]) >= 2
        assert {"offer_interview_slots", "book_interview"} <= set(p.expect.forbidden_tools)
        assert {"approved", "interview_booked"} <= set(p.expect.forbidden_events)
        assert "Criba" in p.expect.forbidden_agent_text

    def test_every_persona_forbids_naming_criba(self):
        for p in PERSONAS.values():
            assert "Criba" in p.expect.forbidden_agent_text, p.id

    def test_every_spain_spanish_persona_forbids_the_mexican_colonia(self):
        """Spain asks for the barrio, never the colonia (the Screener skill's Zone wording)."""
        spain = [p for p in PERSONAS.values() if p.identity.locale == "es-ES"]
        assert spain
        for p in spain:
            assert "colonia" in p.expect.forbidden_agent_text, p.id


# ---- the schema stays in step with the contracts --------------------------------------


def test_fields_stages_and_outcomes_match_tools_json():
    assert list(get_args(persona_schema.FieldName)) == TOOLS["field"]["enum"] == list(FIELD_ORDER)
    assert list(get_args(persona_schema.Stage)) == TOOLS["stage"]["enum"]
    outcomes = [o for o in TOOLS["outcome"]["enum"] if o is not None]
    assert list(get_args(persona_schema.Outcome)) == outcomes
    assert list(get_args(persona_schema.Locale)) == TOOLS["language"]["enum"]
    assert list(get_args(persona_schema.Country)) == TOOLS["country"]["enum"]
    assert list(get_args(persona_schema.ToolName)) == TOOLS["tool_name"]["enum"]


def test_event_kinds_are_exactly_the_events_kind_list_in_schema_sql():
    in_schema = _event_kinds_in_schema()
    assert {"nudge_sent", "system_turn"} <= set(in_schema)  # the parser found the list
    kinds = list(get_args(persona_schema.EventKind))
    assert set(kinds) == set(in_schema), (
        f"missing from EventKind: {sorted(set(in_schema) - set(kinds))}; "
        f"not in schema.sql: {sorted(set(kinds) - set(in_schema))}"
    )
    assert kinds == in_schema  # same order, so the two read side by side


def test_expected_values_use_the_contract_enums():
    availability = set(TOOLS["availability_value"]["enum"])
    schedule = set(TOOLS["schedule_value"]["enum"])
    platforms = set(TOOLS["platform_value"]["enum"])
    for p in PERSONAS.values():
        for field, allowed in (
            ("availability", availability),
            ("preferred_schedule", schedule),
            ("experience_platforms", platforms),
        ):
            check = p.expect.fields.get(field)
            if check is not None:
                assert set(check.value) <= allowed, (p.id, field)


def test_field_order_follows_the_collection_order():
    for p in PERSONAS.values():
        idx = [FIELD_ORDER.index(f) for f in p.expect.field_order]
        assert idx == sorted(idx), p.id


def test_knockout_checks_run_before_details_in_every_expected_order():
    for p in PERSONAS.values():
        order = p.expect.field_order
        if "driver_license" in order:
            assert order.index("zone") < order.index("driver_license"), p.id
        for detail in (
            "availability",
            "preferred_schedule",
            "experience_years",
            "start_date",
        ):
            if detail in order:
                assert "driver_license" in order and "zone" in order, p.id


def test_qualified_personas_can_answer_every_field():
    for p in PERSONAS.values():
        if p.expect.stage != "qualified":
            continue
        answerable = set(p.answers) | set(p.opening.covers)
        needed = set(FIELD_ORDER)
        years = p.expect.fields.get("experience_years")
        if years is not None and years.value == 0:
            needed.discard("experience_platforms")
        assert needed <= answerable, p.id


def test_personas_are_data_only():
    """No persona encodes agent prose expectations (spec 004: never assert wording)."""
    for path in PERSONAS_DIR.glob("*.yaml"):
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        assert set(raw["expect"]) <= set(persona_schema.Expect.model_fields), path.name


# ---- loader and validation errors -----------------------------------------------------


def raw(persona_id: str = "qualified_es") -> dict:
    return yaml.safe_load((PERSONAS_DIR / f"{persona_id}.yaml").read_text(encoding="utf-8"))


def write(tmp_path: Path, data: dict, name: str | None = None) -> Path:
    path = tmp_path / f"{name or data['id']}.yaml"
    path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    return path


def test_load_persona_roundtrip(tmp_path: Path):
    path = write(tmp_path, raw())
    assert load_persona(path) == PERSONAS["qualified_es"]


def mutate(fn):
    data = copy.deepcopy(raw())
    fn(data)
    return data


@pytest.mark.parametrize(
    "change, message",
    [
        (lambda d: d["identity"].update(phone="+44700000001"), "phone"),
        (lambda d: d["identity"].update(phone="+525500000001"), "does not match ES"),
        (lambda d: d["identity"].update(locale="es-MX"), "does not match"),
        (
            lambda d: d["expect"].update(outcome="knockout"),
            "open Application has no Outcome",
        ),
        (lambda d: d["expect"].update(status="closed"), "needs an Outcome"),
        (lambda d: d["expect"].update(stage="somewhere"), "stage"),
        (lambda d: d.update(surprise=1), "surprise"),
        (
            lambda d: d["answers"].update(favourite_colour=[{"text": "azul"}]),
            "favourite_colour",
        ),
        (lambda d: d["answers"].pop("start_date"), "no replies for"),
        (
            lambda d: d["expect"].update(field_order=["zone", "full_name"]),
            "field_order",
        ),
        (lambda d: d["expect"].update(nudges=4), "nudges"),
        (lambda d: d["expect"]["events"].append("teleported"), "teleported"),
        (lambda d: d["expect"]["tools"].append("drop_database"), "drop_database"),
        (lambda d: d["expect"]["fields"].update(zone={}), "value or non_null"),
        (
            lambda d: d["expect"]["fields"].update(zone={"value": "x", "non_null": True}),
            "not both",
        ),
        (lambda d: d.update(policy="model"), "needs a style"),
        (lambda d: d.update(zone_choice=-1), "zone_choice index"),
        (lambda d: d.update(zone_choice="  "), "zone_choice label"),
        (
            lambda d: d.update(interrupts=[{"at": "start", "advance": ["2 weeks"]}]),
            "advance",
        ),
        (lambda d: d["opening"].update(text=""), "text"),
        (
            lambda d: d["expect"].update(
                fields_unanswered=["full_name"],
            ),
            "both answered and unanswered",
        ),
    ],
)
def test_invalid_personas_are_rejected_with_the_file_name(tmp_path: Path, change, message):
    data = mutate(change)
    with pytest.raises(PersonaError, match=message) as err:
        load_persona(write(tmp_path, data))
    assert "qualified_es.yaml" in str(err.value)


def test_missing_platform_reply_is_fine_when_years_is_zero(tmp_path: Path):
    data = raw("prompt_injection_es")
    assert "experience_platforms" not in data["answers"]
    assert load_persona(write(tmp_path, data)).expect.stage == "qualified"


def test_id_must_match_the_file_name(tmp_path: Path):
    with pytest.raises(PersonaError, match="must equal the file name"):
        load_persona(write(tmp_path, raw(), name="other_name"))


def test_invalid_yaml_and_non_mapping_are_reported(tmp_path: Path):
    bad = tmp_path / "bad.yaml"
    bad.write_text("id: [unclosed", encoding="utf-8")
    with pytest.raises(PersonaError, match="invalid YAML"):
        load_persona(bad)
    listy = tmp_path / "listy.yaml"
    listy.write_text("- a\n- b\n", encoding="utf-8")
    with pytest.raises(PersonaError, match="mapping"):
        load_persona(listy)


def test_duplicate_phone_numbers_are_rejected(tmp_path: Path):
    write(tmp_path, raw())
    twin = raw()
    twin["id"] = "qualified_twin"
    write(tmp_path, twin)
    with pytest.raises(PersonaError, match="phone shared"):
        load_personas(tmp_path)


@pytest.mark.parametrize(
    "text, seconds", [("90s", 90), ("15m", 900), ("20h", 72000), ("7d", 604800)]
)
def test_duration_seconds(text, seconds):
    assert duration_seconds(text) == seconds


@pytest.mark.parametrize("text", ["7", "d7", "1.5h", "2 weeks", ""])
def test_bad_durations(text):
    with pytest.raises(ValueError, match="bad duration"):
        duration_seconds(text)


# ---- canonical values and zone queries (spec 006b) -------------------------------------------


def test_option_codes_match_the_contract_enums():
    answer = TOOLS
    assert (
        list(persona_schema.FIELD_CODES["driver_license"])
        == answer["answer_value_driver_license"]["enum"]
    )
    assert list(persona_schema.FIELD_CODES["availability"]) == answer["availability_value"]["enum"]
    assert (
        list(persona_schema.FIELD_CODES["preferred_schedule"]) == answer["schedule_value"]["enum"]
    )
    assert (
        list(persona_schema.FIELD_CODES["experience_platforms"]) == answer["platform_value"]["enum"]
    )


def test_list_fields_are_the_ones_the_contract_makes_arrays():
    for name, ref in json.loads((REPO / "contracts" / "tools.json").read_text(encoding="utf-8"))[
        "x-answer-values"
    ].items():
        schema = TOOLS[ref.rsplit("/", 1)[-1]]
        assert (schema.get("type") == "array") == (name in persona_schema.LIST_FIELDS), name


def test_weekdays_are_the_three_letter_codes_in_order():
    assert persona_schema.WEEKDAYS == ("mon", "tue", "wed", "thu", "fri", "sat", "sun")


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("full_name", "Ana García"),
        ("full_name", None),
        ("driver_license", "expired"),
        ("driver_license", None),
        ("availability", ["full_time", "weekends"]),
        ("preferred_schedule", ["flexible"]),
        ("experience_platforms", []),
        ("experience_platforms", ["rappi", "other"]),
        ("experience_years", 0),
        ("experience_years", 1.5),
        ("start_date", "2026-10-12"),
        ("start_date", "+2d"),
        ("start_date", "-1d"),
        ("start_date", "next:sun"),
        ("start_date", "next:sun+7d"),
        ("start_date", ["next:sun", "next:sun+7d"]),  # the two readings (ADR 0054)
        ("start_date", ["2026-10-11", "2026-10-18"]),
        ("start_date", None),
    ],
)
def test_canonical_values_that_fit_their_field(field, value):
    assert persona_schema.value_problem(field, value) is None


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("full_name", "Al"),
        ("full_name", "x" * 81),
        ("full_name", 7),
        ("driver_license", "si"),
        ("driver_license", True),
        ("driver_license", ["yes"]),
        ("availability", "full_time"),
        ("availability", []),
        ("availability", ["full_time", "full_time"]),
        ("availability", ["sometimes"]),
        ("preferred_schedule", ["full_time"]),
        ("experience_platforms", ["tiktok"]),
        ("experience_years", -1),
        ("experience_years", "2"),
        ("experience_years", True),
        ("start_date", "el lunes"),
        ("start_date", "2026-13-40"),
        ("start_date", "next:funday"),
        ("start_date", "+2"),
        ("start_date", 5),
        ("start_date", ["next:sun"]),
        ("start_date", ["next:sun", "next:sun"]),
        ("start_date", ["next:sun", "el domingo"]),
        ("start_date", ["+1d", "+8d", "+15d"]),
        ("zone", ["zone_x"]),
        ("shoe_size", 9),
    ],
)
def test_values_that_do_not_fit_their_field_are_refused(field, value):
    assert persona_schema.value_problem(field, value)


def test_every_scripted_answer_of_the_seed_personas_has_a_value_or_is_an_option_id():
    for persona in PERSONAS.values():
        for field, replies in persona.answers.items():
            for reply in replies:
                if reply.covers or field == "zone" or reply.pick_option is not None:
                    continue
                assert "value" in reply.model_fields_set, (
                    persona.id,
                    field,
                    reply.text,
                )


def test_the_zone_replies_say_what_the_agent_searches():
    outside = PERSONAS["zone_outside_es"]
    assert outside.answers["zone"][0].query == "Toledo"
    tap = outside.answers["zone"][1]  # the Sí tap on the confirmation
    assert tap.pick_option == 0 and tap.query is None and "query" in tap.model_fields_set
    freeform = PERSONAS["zone_freeform_mx"]
    assert freeform.answers["zone"][0].query == "Toluca"  # the town the road names
    no_tap = freeform.answers["zone"][1]  # the No tap on its confirmation
    assert no_tap.pick_option == 1
    assert freeform.answers["zone"][1].query is None  # nothing to search for a tap
    assert freeform.answers["zone"][2].query == "Ciudad de México"


def test_interpretations_list_every_field_a_message_answers():
    found = PERSONAS["qualified_es"].interpretations()
    opening = [
        (i.field, i.value, i.query) for i in found if i.text.startswith("Hola, soy Ana García")
    ]
    assert sorted(opening, key=str) == sorted(
        [
            ("full_name", "Ana García", None),
            ("zone", None, "Madrid, Chamberí"),
            ("driver_license", "yes", None),
        ],
        key=str,
    )
    corrections = [
        (i.field, i.value, i.query)
        for i in PERSONAS["confirm_correction_mx"].interpretations()
        if i.text.startswith("no, mi zona")
    ]
    assert corrections == [("zone", None, "La Paz, Puebla")]


def test_a_reply_defaults_its_zone_query_to_its_text():
    found = {
        i.text: i for i in PERSONAS["zone_ambiguous_mx"].interpretations() if i.field == "zone"
    }
    assert found["Puebla"].query == "Puebla"
    assert found["Centro, Puebla"].query == "Centro, Puebla"


def test_the_language_of_a_reply_is_its_lang_or_its_locales():
    switch = PERSONAS["language_switch_mx"]
    langs = {i.text: i.lang for i in switch.interpretations()}
    assert langs["Full time, please"] == "en"
    assert langs["el lunes"] == "es"
    assert {i.lang for i in PERSONAS["qualified_es"].interpretations()} == {"es"}


def reply_changes(change):
    data = raw()
    change(data)
    return data


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (  # a typed answer with no canonical value
            lambda d: d["answers"].update(availability=[{"text": "tiempo completo"}]),
            "needs a canonical `value`",
        ),
        (  # a value of the wrong shape
            lambda d: d["answers"].update(
                availability=[{"text": "tiempo completo", "value": "full_time"}]
            ),
            "availability must be a list",
        ),
        (
            lambda d: d["answers"].update(driver_license=[{"text": "sí", "value": "maybe"}]),
            "driver_license must be one of",
        ),
        (
            lambda d: d["answers"].update(start_date=[{"text": "ayer", "value": "yesterday"}]),
            "start_date must be",
        ),
        (  # a zone answer takes a query
            lambda d: d["answers"].update(
                zone=[{"text": "Madrid, Chamberí", "value": ["zone_es_madrid_chamberi"]}]
            ),
            "takes `query`, not `value`",
        ),
        (  # a query is for the zone
            lambda d: d["answers"].update(
                availability=[{"text": "x", "value": ["full_time"], "query": "Madrid"}]
            ),
            "`query` is for the zone only",
        ),
        (  # covers needs a mapping for the fields but the zone
            lambda d: d["opening"].update(value="Ana García"),
            "`value` maps exactly",
        ),
        (
            lambda d: d["opening"].update(value={"full_name": "Ana García"}),
            "`value` maps exactly",
        ),
        (  # covers holding the zone needs a query
            lambda d: d["opening"].pop("query"),
            "needs a `query`",
        ),
        (
            lambda d: d["answers"]["full_name"][0].update(field="zone"),
            "only for replies outside `answers`",
        ),
        (  # a value outside answers needs to say which field
            lambda d: d.update(
                interrupts=[{"at": "start", "say": {"text": "hola", "value": "Ana García"}}]
            ),
            "needs `field` or `covers`",
        ),
        (
            lambda d: d.update(
                interrupts=[
                    {
                        "at": "start",
                        "say": {"text": "hola", "field": "full_name", "value": "Al"},
                    }
                ]
            ),
            "3 to 80",
        ),
    ],
)
def test_replies_with_misfit_values_are_rejected(tmp_path: Path, change, message):
    with pytest.raises(PersonaError, match=message):
        load_persona(write(tmp_path, reply_changes(change)))


def test_a_tap_needs_no_value(tmp_path: Path):
    data = raw()
    data["answers"]["availability"] = [{"text": "Tiempo completo", "pick_option": 0}]
    load_persona(write(tmp_path, data))


def test_a_null_value_is_a_valid_answer(tmp_path: Path):
    data = raw()
    data["answers"]["start_date"] = [{"text": "en cuanto me den la moto", "value": None}]
    persona = load_persona(write(tmp_path, data))
    entry = next(i for i in persona.interpretations() if i.field == "start_date")
    assert entry.value is None


def test_the_set_roundtrips_through_a_dump_without_changing_what_is_unset():
    seed = PERSONAS["zone_ambiguous_mx"]
    again = Persona.model_validate(seed.model_dump(exclude_unset=True))
    assert again.interpretations() == seed.interpretations()
