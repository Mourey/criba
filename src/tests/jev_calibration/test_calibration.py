"""The calibration module's own tests: the labelled set, the scoring and the report. No network."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

import pytest
import yaml

from criba.jev import JevFailed, JevInput, JevResult, Reading
from criba.jev import battery as jev_battery
from criba.jev.battery import SIGNALS
from criba.jev.policy import THRESHOLDS
from jev_calibration import collect as collecting
from jev_calibration import fixtures as fixture_files
from jev_calibration.analyze import (
    BUDGET_MEAN_COST_USD,
    BUDGET_P95_MS,
    BUDGET_P99_MS,
    MISSED,
    SWEEP,
    ask_confusion_at,
    ask_values,
    budget_check,
    choice_accuracy,
    confusion_at,
    cost_stats,
    expected_family,
    intent_accuracy,
    intent_confusion,
    intent_stability,
    language_score,
    latency_stats,
    merge,
    noul_values,
    percentile,
    tone_score,
    unreadable_score,
)
from jev_calibration.collect import Row
from jev_calibration.fixtures import Fixture, load_fixtures
from jev_calibration.report import render
from sim.spend import SpendGuard

FIXTURES = load_fixtures()


def row_for(fixture: Fixture, **overrides: object) -> Row:
    """What a perfect Jev would answer for a fixture."""
    signals = {name: 0.95 if name in fixture.signals else 0.02 for name in SIGNALS}
    base: dict[str, Any] = {
        "fixture_id": fixture.id,
        "rep": 0,
        "ok": True,
        "latency_ms": 300,
        "model": "typesafe/jev-1.13-20260917",
        "cost_usd": 3e-05,
        "input_tokens": 400,
        "output_tokens": 60,
        "lang": fixture.lang,
        "lang_confidence": 0.99,
        "switch": 0.95 if fixture.switch else 0.03,
        "signals": signals,
        "asks": fixture.asks,
        "asks_confidence": 0.97,
        "unreadable": 0.95 if fixture.unreadable else 0.02,
    }
    return Row(**{**base, **overrides})


def test_the_labelled_set_covers_what_the_router_must_get_right() -> None:
    ids = [f.id for f in FIXTURES]
    assert len(ids) == len(set(ids)) >= 160
    splits = Counter(f.split for f in FIXTURES)
    assert splits["dev"] >= 100 and splits["holdout"] >= 40
    for split in ("dev", "holdout"):
        mine = [f for f in FIXTURES if f.split == split]
        assert {f.lang for f in mine} == {"es", "en", "other", "unclear"}
        assert {s for f in mine for s in f.signals} == set(SIGNALS)
        assert any(f.first for f in mine) and any(f.switch for f in mine)
        assert any(not f.signals for f in mine)
    assert {f.current_language for f in FIXTURES} == {"es-ES", "es-MX", "en"}
    assert {f.country for f in FIXTURES} == {"ES", "MX"}


def test_the_labelled_set_covers_requests_and_mentions_in_both_directions() -> None:
    """Explicit requests (ADR 0061) against mentions of a language that are not requests."""
    for split, minimum in (("dev", 12), ("holdout", 6)):
        mine = [f for f in FIXTURES if f.split == split]
        requests = [f for f in mine if f.asks in ("es", "en")]
        assert len(requests) >= minimum, split
        assert {f.asks for f in requests} == {"es", "en"}
        # both Spanish variants ask for English, and an English chat asks for Spanish in both
        # countries
        assert {f.current_language for f in requests if f.asks == "en"} == {"es-ES", "es-MX"}
        assert {f.country for f in requests if f.asks == "es"} >= {"ES"}
        mentions = [
            f
            for f in mine
            if f.asks == "none"
            and any(w in f.text.lower() for w in ("inglés", "english", "spanish", "español"))
        ]
        assert len(mentions) >= 4, split
    assert sum(1 for f in FIXTURES if f.asks != "none") >= 20


def test_the_labelled_set_has_unreadable_messages_and_hard_readable_ones() -> None:
    for split in ("dev", "holdout"):
        mine = [f for f in FIXTURES if f.split == split]
        assert sum(f.unreadable for f in mine) >= 5
    by_id = {f.id: f for f in FIXTURES}
    for readable in ("n_repertidor", "n_simon", "n_inaki", "n_emoji", "h_n_polish"):
        assert not by_id[readable].unreadable
    assert by_id["u_blok"].unreadable and by_id["u_mixed_es"].unreadable


def test_unreadable_is_scored_with_the_policy() -> None:
    rows = [row_for(f) for f in FIXTURES]
    merged = merge(rows, FIXTURES)
    for split in ("dev", "holdout"):
        c = unreadable_score(merged, FIXTURES, split, THRESHOLDS)
        assert (c.fp, c.fn) == (0, 0) and c.tp >= 5
        values = noul_values(merged, FIXTURES, "unreadable", split)
        assert confusion_at(values, THRESHOLDS.unreadable).fn == 0
    # An emoji-only message Jev wrongly calls unreadable is readable in code.
    emoji = next(f for f in FIXTURES if f.id == "n_emoji")
    rows = [row_for(f) for f in FIXTURES if f.id != emoji.id] + [row_for(emoji, unreadable=0.99)]
    assert unreadable_score(merge(rows, FIXTURES), FIXTURES, "dev", THRESHOLDS).fp == 0


def test_fixtures_hold_synthetic_text_only() -> None:
    for fixture in FIXTURES:
        assert 0 < len(fixture.text) <= 160
        for turn in fixture.recent:
            assert turn.sender in ("assistant", "candidate")


def test_a_perfect_jev_scores_perfectly_on_every_question() -> None:
    rows = [row_for(f) for f in FIXTURES]
    merged = merge(rows, FIXTURES)
    for split in ("dev", "holdout"):
        score = language_score(merged, FIXTURES, split, THRESHOLDS)
        assert score.correct == score.cases and not score.wrong_ids
        acc = choice_accuracy(merged, FIXTURES, split)
        assert acc["right"] == acc["total"]
        for name in ("switch", *SIGNALS):
            c = confusion_at(noul_values(merged, FIXTURES, name, split), 0.85)
            assert (c.fp, c.fn) == (0, 0) and c.precision == c.recall == 1.0
        tone = tone_score(merged, FIXTURES, split, THRESHOLDS)
        assert all(c.fp == 0 and c.fn == 0 for c in tone.values())
        asked = ask_confusion_at(ask_values(merged, FIXTURES, split), THRESHOLDS.ask_confidence)
        assert (asked.fp, asked.fn) == (0, 0) and asked.tp > 0 and asked.tn > 0


def test_a_failed_call_stays_in_the_denominator_as_a_miss() -> None:
    switch = next(f for f in FIXTURES if f.switch and not f.first and f.split == "dev")
    rows = [row_for(f) for f in FIXTURES if f.id != switch.id]
    rows.append(Row(switch.id, 0, False, error="timeout", latency_ms=1200))
    merged = merge(rows, FIXTURES)
    score = language_score(merged, FIXTURES, "dev", THRESHOLDS)
    assert score.missed_switch == 1 and switch.id in score.wrong_ids
    c = confusion_at(noul_values(merged, FIXTURES, "switch", "dev"), 0.85)
    assert c.fn == 1
    assert latency_stats(rows)["failed"] == 1


def test_a_failed_call_or_the_wrong_language_on_a_request_is_a_miss() -> None:
    request = next(f for f in FIXTURES if f.id == "req_es_en_porfi")
    mention = next(f for f in FIXTURES if f.id == "ment_worked_en")
    rows = [row_for(f) for f in FIXTURES if f.id not in (request.id, mention.id)]
    rows += [Row(request.id, 0, False, error="timeout", latency_ms=1200)]
    rows += [row_for(mention, asks="en")]  # a mention read as a request
    values = ask_values(merge(rows, FIXTURES), FIXTURES, "dev")
    c = ask_confusion_at(values, THRESHOLDS.ask_confidence)
    assert (c.fn, c.fp) == (1, 1)
    # the right request in the wrong direction is both a miss and a false alarm
    flipped = [
        row_for(f, asks="es" if f.asks == "en" else "en") if f.id == request.id else row_for(f)
        for f in FIXTURES
    ]
    c = ask_confusion_at(ask_values(merge(flipped, FIXTURES), FIXTURES, "dev"), 0.8)
    assert (c.fn, c.fp) == (1, 1)


def test_a_wrong_switch_is_counted_apart_from_a_missed_one() -> None:
    word = next(f for f in FIXTURES if f.id == "word_manana_en")
    rows = [row_for(f) for f in FIXTURES if f.id != word.id]
    rows.append(row_for(word, lang="es", switch=0.99))  # Jev wrongly sure of a switch
    score = language_score(merge(rows, FIXTURES), FIXTURES, "dev", THRESHOLDS)
    assert score.false_switch == 1 and score.missed_switch == 0


def test_expected_language_follows_the_labels() -> None:
    by_id = {f.id: f for f in FIXTURES}
    assert expected_family(by_id["open_en_job"]) == "en"  # a first message sets it
    assert expected_family(by_id["word_manana_en"]) == "en"  # one word keeps it
    assert expected_family(by_id["sw_en_to_es_pay"]) == "es"  # a real switch
    assert expected_family(by_id["open_pt"]) == "es"  # another language keeps it
    assert expected_family(by_id["req_es_en_porfi"]) == "en"  # a request wins over the writing
    assert expected_family(by_id["ment_worked_en"]) == "es"  # a mention changes nothing


def test_percentiles_and_costs() -> None:
    assert percentile([1, 2, 3, 4, 5], 0.5) == 3 and percentile([7], 0.95) == 7
    assert percentile([], 0.5) != percentile([], 0.5)  # nan
    rows = [row_for(FIXTURES[0]), row_for(FIXTURES[1], cost_usd=None)]
    stats = cost_stats(rows)
    assert stats["total"] == pytest.approx(3e-05) and stats["unknown"] == 1


class FakeJev:
    model = "typesafe/jev-1.13"

    def __init__(self) -> None:
        self.calls = 0

    def read(self, request: JevInput) -> JevResult:
        self.calls += 1
        if request.message == "OK":
            raise JevFailed("http 400", latency_ms=50)
        return JevResult(Reading("es", 1.0, 0.0, dict.fromkeys(SIGNALS, 0.0)), "m", cost_usd=3e-05)


def test_collect_records_failures_and_stops_at_the_cap() -> None:
    fixtures = [f for f in FIXTURES if f.text in ("OK", "Hola")][:2]
    guard = SpendGuard(cap_usd=1.0, real=True)
    rows = collecting.collect(FakeJev(), fixtures, repeats=2, workers=2, guard=guard)
    assert len(rows) == 4 and sum(not r.ok for r in rows) == 2
    assert guard.spent_usd > 0
    stingy = SpendGuard(cap_usd=1e-9, real=True)
    assert collecting.collect(FakeJev(), fixtures, repeats=1, workers=2, guard=stingy) == []


def test_results_round_trip_through_json() -> None:
    rows = [row_for(f) for f in FIXTURES[:3]]
    assert collecting.load(collecting.dump(rows)) == rows


def test_the_report_renders_with_results_and_with_only_failures() -> None:
    text = render([row_for(f) for f in FIXTURES], FIXTURES, note="a note")
    for heading in ("## Method", "## Accuracy per question", "## Thresholds and why", "p95"):
        assert heading in text
    assert "a note" in text and "typesafe/jev-1.13-20260917" in text
    assert "asks (Choice, right language)" in text and "explicit language request" in text
    failed = [Row(f.id, 0, False, error="timeout", latency_ms=1200) for f in FIXTURES]
    assert "## Latency and cost" in render(failed, FIXTURES)


# The intent and Manipulation set (JI6, ADR 0071): labelled by hand from the battery's option
# definitions before any harness code, never from a Jev or stub answer.

PERSONA = Path(__file__).parents[1] / "sim" / "personas" / "prompt_injection_es.yaml"
HARD_NEGATIVES = ("hn_job_", "hn_bot_", "hn_complaint_", "hn_person_")


def intent_set() -> list[Fixture]:
    return load_fixtures(fixture_files.INTENT)


def test_the_intent_set_loads_last_with_both_labels_on_every_message() -> None:
    mine = intent_set()
    assert len(mine) >= 6 * (6 + 3)
    assert [f.id for f in FIXTURES][-len(mine) :] == [f.id for f in mine]  # in the default set
    for fixture in mine:
        assert fixture.intent in jev_battery.INTENTS, fixture.id
        assert isinstance(fixture.manipulation, bool), fixture.id
        assert not fixture.unreadable and fixture.asks == "none", fixture.id
    for raw in json.loads(fixture_files.INTENT.read_text(encoding="utf-8")):
        assert raw["intent"] in jev_battery.INTENTS, raw["id"]
        assert isinstance(raw["manipulation"], bool), raw["id"]


def test_the_intent_set_has_every_intent_in_both_splits() -> None:
    mine = intent_set()
    for split, minimum in (("dev", 6), ("holdout", 3)):
        counts = Counter(f.intent for f in mine if f.split == split)
        for intent in jev_battery.INTENTS:
            assert counts[intent] >= minimum, (split, intent)


def test_the_intent_set_has_enough_manipulation_in_both_splits() -> None:
    mine = intent_set()
    for split, minimum in (("dev", 10), ("holdout", 5)):
        assert sum(f.manipulation for f in mine if f.split == split) >= minimum, split
        assert any(not f.manipulation for f in mine if f.split == split), split


def test_both_prompt_injection_persona_lines_are_labelled_manipulation() -> None:
    persona = yaml.safe_load(PERSONA.read_text(encoding="utf-8"))
    lines = [interrupt["say"]["text"] for interrupt in persona["interrupts"]]
    assert len(lines) == 2
    by_text = {f.text: f for f in intent_set()}
    for line in lines:
        assert line in by_text, line
        assert by_text[line].manipulation and by_text[line].intent == "steering", line


def test_lines_aimed_at_jev_itself_are_labelled_manipulation_in_both_splits() -> None:
    aimed = [f for f in intent_set() if f.id.startswith("m_jev_")]
    assert {f.split for f in aimed} == {"dev", "holdout"}
    assert all(f.manipulation for f in aimed)


def test_the_hard_negatives_are_not_manipulation() -> None:
    mine = intent_set()
    for prefix in HARD_NEGATIVES:
        found = [f for f in mine if f.id.startswith(prefix)]
        assert {f.split for f in found} == {"dev", "holdout"}, prefix
        assert not any(f.manipulation for f in found), prefix
    bots = {f.split: f.text for f in mine if f.id.startswith("hn_bot_")}
    assert bots["dev"] == "¿eres un bot?" and bots["holdout"] != "¿eres un bot?"
    assert all("frustrated" in f.signals for f in mine if f.id.startswith("hn_complaint_"))
    assert all("abusive" not in f.signals for f in mine if f.id.startswith("hn_complaint_"))
    assert all("wants_human" in f.signals for f in mine if f.id.startswith("hn_person_"))
    assert all(f.intent == "asking" for f in mine if f.id.startswith("hn_job_"))


def test_the_older_fixtures_are_manipulation_negatives_with_no_intent() -> None:
    older = load_fixtures(fixture_files.MESSAGES, fixture_files.UNREADABLE)
    assert older
    for fixture in older:
        assert fixture.manipulation is False and fixture.intent is None, fixture.id


def test_an_unknown_intent_label_is_refused(tmp_path: Path) -> None:
    raw = {"id": "x_bad", "split": "dev", "text": "Hola", "cur": "es-ES", "lang": "es"}
    path = tmp_path / "bad.json"
    path.write_text(json.dumps([{**raw, "intent": "chatting", "manipulation": False}]))
    with pytest.raises(ValueError, match="intent"):
        load_fixtures(path)
    path.write_text(json.dumps([{**raw, "intent": "other", "manipulation": False}]))
    assert load_fixtures(path)[0].intent == "other"


def test_no_intent_text_is_a_battery_example_or_an_older_fixture_text() -> None:
    examples = {
        example
        for question in jev_battery.QUESTIONS.values()
        for option in question["criteria"].values()
        for example in option.get("examples", [])
    }
    older: set[str] = set()
    for path in (fixture_files.MESSAGES, fixture_files.UNREADABLE):
        for raw in json.loads(path.read_text(encoding="utf-8")):
            older.add(raw["text"])
            older.update(text for _, text in raw.get("recent", []))
    for fixture in intent_set():
        for text in (fixture.text, *(turn.text for turn in fixture.recent)):
            assert text not in examples, (fixture.id, text)
            assert text not in older, (fixture.id, text)


# Scoring intent, Manipulation, stability and the budget (JI6, ADR 0071). No threshold for either
# reading: the sweep only shows precision and recall at each cut-off.


def perfect_row(fixture: Fixture, **overrides: object) -> Row:
    """What a perfect Jev would answer, the two display-only readings included."""
    return row_for(
        fixture,
        **{
            "intent": fixture.intent,
            "intent_confidence": 0.95 if fixture.intent else None,
            "malice": 0.95 if fixture.manipulation else 0.02,
            **overrides,
        },
    )


class IntentJev:
    model = "typesafe/jev-1.13"

    def read(self, request: JevInput) -> JevResult:
        if request.message == "OK":
            raise JevFailed("timeout", latency_ms=1200)
        reading = Reading(
            "es",
            0.9,
            0.0,
            dict.fromkeys(SIGNALS, 0.0),
            intent="asking",
            intent_confidence=0.88,
            malice=0.07,
        )
        return JevResult(reading, "m", cost_usd=3e-05)


def test_collect_keeps_intent_and_malice_and_none_on_a_failure() -> None:
    asking = next(f for f in intent_set() if f.intent == "asking")
    row = collecting._one(IntentJev(), asking, 0)
    assert row.ok and (row.intent, row.intent_confidence, row.malice) == ("asking", 0.88, 0.07)
    ok_text = next(f for f in FIXTURES if f.text == "OK")
    failed = collecting._one(IntentJev(), ok_text, 0)
    assert not failed.ok and (failed.intent, failed.intent_confidence, failed.malice) == (
        None,
        None,
        None,
    )


def test_an_old_results_file_without_the_new_keys_still_loads() -> None:
    old = json.loads(collecting.dump([row_for(FIXTURES[0])]))
    for raw in old:
        for key in ("intent", "intent_confidence", "malice"):
            raw.pop(key, None)
    (row,) = collecting.load(json.dumps(old))
    assert row.ok and row.intent is None and row.intent_confidence is None and row.malice is None
    assert collecting.load(collecting.dump([perfect_row(intent_set()[0])]))[0].intent


def test_perfect_rows_score_full_intent_accuracy_and_manipulation_recall() -> None:
    merged = merge([perfect_row(f) for f in FIXTURES], FIXTURES)
    for split in ("dev", "holdout"):
        acc = intent_accuracy(merged, FIXTURES, split)
        labelled = sum(1 for f in FIXTURES if f.split == split and f.intent)
        assert acc["total"] == acc["right"] == labelled > 0 and acc["missed"] == 0
        assert not acc["wrong"]
        values = noul_values(merged, FIXTURES, "malice", split)
        assert len(values) == sum(1 for f in FIXTURES if f.split == split)  # every message
        for cut in SWEEP:
            assert confusion_at(values, cut).recall == 1.0, (split, cut)
        c = confusion_at(values, 0.85)
        assert c.fp == 0 and c.tp > 0 and c.tn > 0


def test_a_failed_call_or_a_missing_answer_is_a_miss_for_intent_and_manipulation() -> None:
    mine = [f for f in intent_set() if f.split == "dev" and f.manipulation]
    failed, no_intent, no_malice = mine[:3]
    rows = [perfect_row(f) for f in FIXTURES if f.id not in (failed.id, no_intent.id, no_malice.id)]
    rows.append(Row(failed.id, 0, False, error="timeout", latency_ms=1200))
    rows.append(perfect_row(no_intent, intent=None, intent_confidence=None))
    rows.append(perfect_row(no_malice, malice=None))
    merged = merge(rows, FIXTURES)
    acc = intent_accuracy(merged, FIXTURES, "dev")
    labelled = sum(1 for f in FIXTURES if f.split == "dev" and f.intent)
    assert acc["total"] == labelled and acc["right"] == labelled - 2 and acc["missed"] == 2
    assert {failed.id, no_intent.id} <= set(acc["wrong"])
    c = confusion_at(noul_values(merged, FIXTURES, "malice", "dev"), 0.5)
    assert c.fn == 2 and c.fp == 0  # the failure and the missing malice; no_intent kept malice


def test_the_intent_confusion_table_puts_each_answer_in_its_cell() -> None:
    by_intent = {f.intent: f for f in intent_set() if f.split == "holdout"}
    asking, other = by_intent["asking"], by_intent["other"]
    rows = [perfect_row(f) for f in FIXTURES if f.id not in (asking.id, other.id)]
    rows.append(perfect_row(asking, intent="off_topic"))
    rows.append(Row(other.id, 0, False, error="timeout", latency_ms=1200))
    table = intent_confusion(merge(rows, FIXTURES), FIXTURES, "holdout")
    assert set(table) == set(jev_battery.INTENTS)
    holdout = [f for f in FIXTURES if f.split == "holdout" and f.intent]
    assert table["asking"]["off_topic"] == 1
    assert table["asking"]["asking"] == sum(f.intent == "asking" for f in holdout) - 1
    assert table["other"][MISSED] == 1
    assert sum(sum(row.values()) for row in table.values()) == len(holdout)


def test_repeats_that_disagree_on_intent_show_in_the_stability_figures() -> None:
    mine = [f for f in intent_set() if f.split == "dev"]
    moved = mine[0]
    rows = [perfect_row(f, rep=rep) for f in mine for rep in (0, 1)]
    steady = intent_stability(rows, mine)
    assert steady["messages"] == len(mine) and steady["agree"] == len(mine)
    assert steady["malice_spread"] == 0.0 and not steady["disagree"]
    rows = [r for r in rows if not (r.fixture_id == moved.id and r.rep == 1)]
    rows.append(perfect_row(moved, rep=1, intent="other", malice=0.5))
    shaky = intent_stability(rows, mine)
    assert shaky["agree"] == len(mine) - 1 and shaky["disagree"] == [moved.id]
    assert shaky["malice_spread"] == pytest.approx(
        abs(0.5 - (0.95 if moved.manipulation else 0.02))
    )


def test_the_new_readings_leave_the_existing_spread_alone() -> None:
    mine = intent_set()[:4]
    rows = [perfect_row(f, rep=0) for f in mine]
    rows += [perfect_row(f, rep=1, intent="other", intent_confidence=0.1, malice=0.5) for f in mine]
    merged = merge(rows, mine)
    assert all(m.spread == 0.0 for m in merged.values())
    for f in mine:
        reading = merged[f.id].reading
        assert reading is not None and reading.malice is not None
        assert reading.malice == pytest.approx((0.5 + (0.95 if f.manipulation else 0.02)) / 2)


def test_merge_votes_on_intent_among_rows_that_have_one() -> None:
    fixture = intent_set()[0]
    rows = [
        perfect_row(fixture, rep=0, intent="asking", intent_confidence=0.8),
        perfect_row(fixture, rep=1, intent="asking", intent_confidence=0.6),
        perfect_row(fixture, rep=2, intent=None, intent_confidence=None, malice=None),
    ]
    reading = merge(rows, [fixture])[fixture.id].reading
    assert reading is not None and reading.intent == "asking"
    assert reading.intent_confidence == pytest.approx(0.7)
    assert reading.malice == pytest.approx(0.95 if fixture.manipulation else 0.02)
    none = merge([perfect_row(fixture, intent=None, malice=None)], [fixture])[fixture.id].reading
    assert none is not None and none.intent is None and none.malice is None


def budget_rows(latencies: list[int], cost: float | None = 3e-05, failed: int = 0) -> list[Row]:
    fixture = FIXTURES[0]
    rows = [
        perfect_row(fixture, rep=i, latency_ms=ms, cost_usd=cost) for i, ms in enumerate(latencies)
    ]
    rows += [
        Row(fixture.id, 1000 + i, False, error="timeout", latency_ms=1200) for i in range(failed)
    ]
    return rows


def test_the_budget_bounds_pass_at_the_bound_and_fail_past_it() -> None:
    assert (BUDGET_P95_MS, BUDGET_P99_MS, BUDGET_MEAN_COST_USD) == (600, 1200, 0.00015)
    assert budget_check(budget_rows([600] * 100))["p95_pass"]
    assert not budget_check(budget_rows([601] * 100))["p95_pass"]
    assert budget_check(budget_rows([1199] * 100))["p99_pass"]
    assert not budget_check(budget_rows([1200] * 100))["p99_pass"]
    assert budget_check(budget_rows([300] * 10, cost=0.00015))["cost_pass"]
    assert not budget_check(budget_rows([300] * 10, cost=0.000151))["cost_pass"]
    # the unrounded mean decides, not cost_stats' 7 decimals
    near = budget_rows([300] * 10, cost=0.00015004)
    assert cost_stats(near)["mean"] == 0.00015 and not budget_check(near)["cost_pass"]
    fine = budget_check(budget_rows([300] * 100))
    assert fine["p95_pass"] and fine["p99_pass"] and fine["cost_pass"] and fine["pass"]


def test_no_data_fails_every_bound() -> None:
    for rows in ([], budget_rows([], failed=5)):
        check = budget_check(rows)
        assert not (check["p95_pass"] or check["p99_pass"] or check["cost_pass"] or check["pass"])
    unknown = budget_check(budget_rows([300] * 10, cost=None))
    assert unknown["p95_pass"] and not unknown["cost_pass"] and not unknown["pass"]


def test_failed_calls_count_against_the_latency_bounds() -> None:
    six = budget_check(budget_rows([300] * 94, failed=6))
    assert six["p95"] == float("inf") and not six["p95_pass"] and six["failed"] == 6
    assert latency_stats(budget_rows([300] * 94, failed=6))["p95"] == 300  # unchanged
    two = budget_check(budget_rows([300] * 98, failed=2))
    assert two["p95"] == 300 and two["p95_pass"]
    assert two["p99"] == float("inf") and not two["p99_pass"] and not two["pass"]


# The report's intent, Manipulation, stability and budget sections (JI6, JI8). No threshold is
# chosen or marked for either reading (display only, ADR 0071).


def section(text: str, heading: str) -> str:
    """From ``heading`` to the next heading of the same or a higher level."""
    start = text.index(heading)
    level = heading.split(" ", 1)[0]
    rest = text[start + len(heading) :]
    ends = [
        i
        for i in range(len(rest))
        if rest.startswith("\n#", i) and rest[i + 1 :].split(" ", 1)[0] in {level, level[:-1]}
    ]
    return rest[: ends[0]] if ends else rest


def budget_lines(text: str) -> list[str]:
    budget = section(text, "### Budget (owner decision 2026-10-12)")
    return [line for line in budget.splitlines() if line.startswith(("| p95", "| p99", "| mean"))]


def test_the_report_shows_intent_manipulation_stability_and_the_budget() -> None:
    rows = [perfect_row(f, rep=rep) for f in FIXTURES for rep in (0, 1)]
    text = render(rows, FIXTURES)
    assert "ten questions" in text and "seven questions" not in text
    readings = section(text, "## Intent and Manipulation readings (ADR 0071)")
    assert "No threshold is chosen for either reading" in readings and "ADR 0071" in readings
    intent = section(readings, "### Intent (Choice, top option)")
    for split in ("dev", "holdout"):
        labelled = sum(1 for f in FIXTURES if f.split == split and f.intent)
        assert f"| {split} | 100% ({labelled}/{labelled}) |" in intent
    assert "| Label | " + " | ".join((*jev_battery.INTENTS, MISSED)) + " |" in intent
    manipulation = section(readings, "### Manipulation (`malice` Noul)")
    for cut in SWEEP:
        assert f"| {cut} " in manipulation or f"| {cut} |" in manipulation
    assert "| dev |" in manipulation and "| holdout |" in manipulation
    for fixture in intent_set():
        if fixture.id.startswith(("m_jev_", "hn_")):
            score = "0.95" if fixture.manipulation else "0.02"
            assert f"`{fixture.id}`" in manipulation and score in manipulation, fixture.id
    stability = section(readings, "### Stability of the two readings")
    assert "agreed across repeats" in stability
    lines = budget_lines(text)
    assert len(lines) == 3 and all(line.endswith("| pass |") for line in lines)


def test_the_new_readings_get_no_threshold_row_and_no_chosen_cut_off() -> None:
    text = render([perfect_row(f) for f in FIXTURES], FIXTURES)
    for heading in ("## Thresholds and why", "## Decisions with the chosen thresholds"):
        part = section(text, heading).lower()
        assert "intent" not in part and "manipulation" not in part and "malice" not in part
    readings = section(text, "## Intent and Manipulation readings (ADR 0071)")
    lowered = readings.lower()
    assert "recommended" not in lowered and "chosen cut-off" not in lowered
    # the question id only in backticks; the product label is Manipulation
    outside = "".join(part for i, part in enumerate(text.split("`")) if i % 2 == 0).lower()
    assert "malice" not in outside and "malicious" not in outside


def test_rows_over_each_bound_render_every_bound_as_fail() -> None:
    rows = [perfect_row(f, latency_ms=700, cost_usd=0.0002) for f in FIXTURES]
    rows += [perfect_row(f, rep=1, latency_ms=1300, cost_usd=0.0002) for f in FIXTURES[:10]]
    lines = budget_lines(render(rows, FIXTURES))
    assert len(lines) == 3 and all(line.endswith("| fail |") for line in lines)


def test_only_failed_rows_still_render_and_every_bound_fails() -> None:
    failed = [Row(f.id, 0, False, error="timeout", latency_ms=1200) for f in FIXTURES]
    text = render(failed, FIXTURES)
    assert "## Intent and Manipulation readings (ADR 0071)" in text
    lines = budget_lines(text)
    assert len(lines) == 3 and all(line.endswith("| fail |") for line in lines)
    budget = section(text, "### Budget (owner decision 2026-10-12)")
    assert f"Failed calls: {len(failed)} of {len(failed)}" in budget


def test_the_budget_table_counts_failed_calls_unlike_the_latency_table() -> None:
    rows = [perfect_row(f) for f in FIXTURES]
    rows[0] = Row(rows[0].fixture_id, 0, False, error="timeout", latency_ms=1200)
    budget = section(render(rows, FIXTURES), "### Budget (owner decision 2026-10-12)")
    assert f"Failed calls: 1 of {len(rows)}" in budget
    assert "count each failed call as over the bound" in budget
    assert "latency table above covers successful calls only" in budget
