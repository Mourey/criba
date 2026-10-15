"""The OpenRouter client, its questions and its answer parsing (ADR 0051). No network."""

from __future__ import annotations

import json
import pathlib
import time
from collections.abc import Iterator
from typing import Any

import httpx
import pytest

import criba.jev.battery as battery
from criba.jev import JevFailed, JevInput, OpenRouterJev, Turn
from criba.jev.battery import QUESTIONS, SIGNALS, build_state, parse_answers
from criba.jev.config import OPENROUTER_DECISIONS_URL

ANSWERS: dict[str, Any] = {
    "lang": {"type": "choice", "choice": "en", "probabilities": {"en": 0.99}, "confidence": 1},
    "asks": {
        "type": "choice",
        "choice": "none",
        "probabilities": {"none": 0.97},
        "confidence": 0.97,
    },
    "switch": {"type": "noul", "noul": 0.66},
    "frustrated": {"type": "noul", "noul": 0.02},
    "confused": {"type": "noul", "noul": 0.03},
    "wants_human": {"type": "noul", "noul": 0.01},
    "abusive": {"type": "noul", "noul": 0.0},
    "unreadable": {"type": "noul", "noul": 0.04},
}
REQUEST = JevInput(
    "Hey, how are you?", "es-MX", "MX", (Turn("assistant", "Hola"), Turn("candidate", "hi"))
)


def response(answers: Any = None, **extra: Any) -> dict[str, Any]:
    return {
        "model": "typesafe/jev-1.13-20260917",
        "answers": ANSWERS if answers is None else answers,
        "usage": {"input_tokens": 404, "output_tokens": 69, "cost": 1.7e-05},
        "id": "gen-dec-1",
        "provider": "TypeSafe",
        **extra,
    }


def client_for(handler, **kwargs: Any) -> tuple[OpenRouterJev, list[httpx.Request]]:
    seen: list[httpx.Request] = []

    def record(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    http = httpx.Client(transport=httpx.MockTransport(record))
    return OpenRouterJev(api_key="k-test", http=http, **kwargs), seen


def test_a_call_posts_the_decisions_request_and_reads_the_answers() -> None:
    client, seen = client_for(lambda request: httpx.Response(200, json=response()))
    result = client.read(REQUEST)
    [request] = seen
    assert str(request.url) == OPENROUTER_DECISIONS_URL
    assert request.headers["authorization"] == "Bearer k-test"
    body = json.loads(request.content)
    assert body["model"] == "typesafe/jev-1.13" and set(body["questions"]) == set(QUESTIONS)
    assert body["state"]["latest_candidate_message"] == "Hey, how are you?"
    assert body["state"]["conversation"]["current_language"] == "es-MX"
    reading = result.reading
    assert (reading.lang, reading.lang_confidence, reading.switch) == ("en", 1.0, 0.66)
    assert reading.signals["frustrated"] == 0.02 and reading.unreadable == 0.04
    assert (reading.asks, reading.asks_confidence) == ("none", 0.97)
    assert result.model == "typesafe/jev-1.13-20260917" and result.request_id == "gen-dec-1"
    assert (result.input_tokens, result.output_tokens, result.cost_usd) == (404, 69, 1.7e-05)
    assert result.provider == "typesafe"


def test_a_missing_or_bad_cost_is_unknown() -> None:
    client, _ = client_for(
        lambda request: httpx.Response(200, json={**response(), "usage": {"cost": "free"}})
    )
    assert client.read(REQUEST).cost_usd is None


@pytest.mark.parametrize(
    ("handler", "kind", "detail"),
    [
        (
            lambda request: httpx.Response(400, json={"error": "echoes the Candidate's text"}),
            "error",
            "http 400",
        ),
        (lambda request: httpx.Response(503), "error", "http 503"),
        (lambda request: httpx.Response(200, text="not json"), "error", "unreadable answer"),
        (lambda request: httpx.Response(200, json={"id": "x"}), "error", "unreadable answer"),
        (
            lambda request: httpx.Response(
                200, json=response({**ANSWERS, "lang": {"choice": "fr", "confidence": 1}})
            ),
            "error",
            "unreadable answer",
        ),
        (
            lambda request: httpx.Response(200, json=response({**ANSWERS, "switch": {"noul": 3}})),
            "error",
            "unreadable answer",
        ),
        (  # an answer to `asks` that is missing, or not one of its options, is never calm
            lambda request: httpx.Response(
                200, json=response({k: v for k, v in ANSWERS.items() if k != "asks"})
            ),
            "error",
            "unreadable answer",
        ),
        (
            lambda request: httpx.Response(
                200, json=response({**ANSWERS, "asks": {"choice": "fr", "confidence": 1}})
            ),
            "error",
            "unreadable answer",
        ),
    ],
)
def test_a_failed_or_unreadable_call_is_a_failure_that_never_echoes_the_body(
    handler, kind, detail
) -> None:
    client, _ = client_for(handler)
    with pytest.raises(JevFailed) as caught:
        client.read(REQUEST)
    assert caught.value.kind == kind and str(caught.value) == detail
    assert "Candidate" not in str(caught.value)


def test_a_timeout_and_a_connection_error_are_failures() -> None:
    def slow(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    client, _ = client_for(slow)
    with pytest.raises(JevFailed) as caught:
        client.read(REQUEST)
    assert caught.value.kind == "timeout"

    def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    client, _ = client_for(down)
    with pytest.raises(JevFailed) as caught:
        client.read(REQUEST)
    assert caught.value.kind == "error" and str(caught.value) == "unreachable: ConnectError"


def test_probabilities_up_to_the_rounding_are_clamped_and_further_out_is_invalid() -> None:
    answers = {**ANSWERS, "switch": {"noul": 1.01}, "abusive": {"noul": -0.01}}
    reading = parse_answers(answers)
    assert reading.switch == 1.0 and reading.signals["abusive"] == 0.0
    with pytest.raises(ValueError, match="outside"):
        parse_answers({**ANSWERS, "switch": {"noul": 1.2}})
    with pytest.raises(TypeError):
        parse_answers({**ANSWERS, "switch": {"noul": True}})


def test_the_state_is_trimmed() -> None:
    long_turns = tuple(Turn("candidate", "x" * 500) for _ in range(6))
    state = build_state(JevInput("y" * 900, "en", None, long_turns))
    assert len(state["latest_candidate_message"]) == 600
    turns = state["conversation"]["recent_turns"]
    assert len(turns) == 3 and all(len(t["text"]) == 200 for t in turns)
    assert "country" not in state["conversation"]


def test_every_question_is_complete_on_its_own() -> None:
    assert set(QUESTIONS) == {"lang", "asks", "switch", *SIGNALS, "unreadable", "intent", "malice"}
    for question in QUESTIONS.values():
        assert question["instructions"]["question"].count("`") >= 2  # points at a state path
    assert QUESTIONS["lang"]["type"] == QUESTIONS["asks"]["type"] == "choice"
    # every question but `lang` is a Noul, except the `asks` Choice
    assert {q["type"] for name, q in QUESTIONS.items() if name != "lang"} == {"noul", "choice"}
    assert {name for name, q in QUESTIONS.items() if q["type"] == "choice"} == {
        "lang",
        "asks",
        "intent",
    }
    assert {q["type"] for name, q in QUESTIONS.items() if name not in ("lang", "asks")} == {
        "noul",
        "choice",
    }
    # `asks` is about what the message asks for, never the language it is written in
    assert set(QUESTIONS["asks"]["criteria"]) == {"es", "en", "none"}
    for option in QUESTIONS["asks"]["criteria"].values():
        assert {"what", "not_for", "examples"} <= set(option)


def test_a_missing_unreadable_answer_is_a_failed_call_never_a_readable_one() -> None:
    answers = {k: v for k, v in ANSWERS.items() if k != "unreadable"}
    client, _ = client_for(lambda request: httpx.Response(200, json=response(answers)))
    with pytest.raises(JevFailed):
        client.read(REQUEST)


# ---- intent and malice: two more questions in the same request (ADR 0071) ----

INTENT_OPTIONS = ("answering", "asking", "off_topic", "wants_to_stop", "steering", "other")
CALIBRATION_DIR = pathlib.Path(__file__).resolve().parents[3] / "tests" / "jev_calibration"
TEN_ANSWERS: dict[str, Any] = {
    **ANSWERS,
    "intent": {
        "type": "choice",
        "choice": "steering",
        "probabilities": {"steering": 0.93},
        "confidence": 0.93,
    },
    "malice": {"type": "noul", "noul": 0.97},
}
# The reading of ANSWERS and nothing else: the eight values a bad new answer must not disturb.
EIGHT_VALUES = (
    "lang",
    "lang_confidence",
    "switch",
    "signals",
    "asks",
    "asks_confidence",
    "unreadable",
)

# The eight questions as they stand on main (a07bfae), copied verbatim: JI1 adds two questions
# and must not change a word of these (every stored reading keeps its threshold markers).


def calibration_texts() -> set[str]:
    """Every text in src/tests/jev_calibration/*.json: the messages and their recent turns."""
    texts: set[str] = set()
    for path in sorted(CALIBRATION_DIR.glob("*.json")):
        for raw in json.loads(path.read_text(encoding="utf-8")):
            texts.add(raw["text"])
            texts.update(text for _, text in raw.get("recent", []))
    return texts


def test_the_intent_choice_has_six_options_each_complete_on_its_own() -> None:
    intent = QUESTIONS["intent"]
    assert intent["type"] == "choice"
    assert tuple(intent["criteria"]) == INTENT_OPTIONS
    for name, option in intent["criteria"].items():
        assert {"what", "not_for", "examples"} <= set(option), name
        assert option["what"].strip() and option["not_for"].strip(), name
        assert len(option["examples"]) >= 2 and all(e.strip() for e in option["examples"]), name
    # a Choice is for which, a Noul for whether: the only Choices are the three that pick one
    assert {name for name, q in QUESTIONS.items() if q["type"] == "choice"} == {
        "lang",
        "asks",
        "intent",
    }
    assert {
        q["type"] for name, q in QUESTIONS.items() if name not in ("lang", "asks", "intent")
    } == {"noul"}


def test_the_confusable_intent_options_are_mirrored_in_their_not_for() -> None:
    options = QUESTIONS["intent"]["criteria"]

    def not_for(name: str) -> str:
        return options[name]["not_for"].lower()

    # each pair names the other side in each option's `not_for`
    assert "question" in not_for("answering") and "answer" in not_for("asking")  # answering/asking
    assert "unrelated" in not_for("asking") and "question about the job" in not_for("off_topic")
    assert "question about the job" in not_for("steering")  # steering/asking
    assert "order" in not_for("asking") or "instruction" in not_for("asking")
    assert "answer" in not_for("other") and "greeting" in not_for("answering")  # other/answering
    # and the `what` of an option is never the `not_for` of the same option
    for name, option in options.items():
        assert option["what"] != option["not_for"], name


def test_both_new_questions_read_the_message_and_malice_asks_about_presence() -> None:
    for name in ("intent", "malice"):
        question = QUESTIONS[name]["instructions"]["question"]
        assert "`latest_candidate_message`" in question, name
        assert "`conversation.recent_turns`" in QUESTIONS[name]["instructions"]["focus"], name
    malice = QUESTIONS["malice"]
    assert malice["type"] == "noul"
    question = malice["instructions"]["question"].lower()
    for phrase in (
        "try to manipulate the assistant",
        "override or reveal its instructions",
        "pose as the system or the team",
        "claim an approval it does not have",
    ):
        assert phrase in question
    assert set(malice["criteria"]) == {"true", "false"}
    for side in malice["criteria"].values():
        assert side["what"].strip() and len(side["examples"]) >= 2


def test_no_example_of_the_new_questions_is_a_calibration_text() -> None:
    texts = calibration_texts()
    assert len(texts) > 100  # the glob found the labelled messages
    examples = [
        example
        for option in QUESTIONS["intent"]["criteria"].values()
        for example in option["examples"]
    ]
    examples += [
        example for side in QUESTIONS["malice"]["criteria"].values() for example in side["examples"]
    ]
    assert examples and not [e for e in examples if e in texts]


EIGHT_QUESTIONS_ON_MAIN: dict[str, dict[str, Any]] = {
    "lang": {
        "type": "choice",
        "instructions": {
            "question": "Which language is `latest_candidate_message` written in?",
            "focus": "Judge the words of `latest_candidate_message` "
            "only. `conversation.recent_turns` is context, not "
            "evidence of the language.",
        },
        "criteria": {
            "es": {
                "what": "Spanish, from Spain or from Latin America",
                "not_for": "English, or a message with no word that belongs to a language",
                "examples": [
                    "Hola, busco trabajo de repartidor",
                    "Vivo cerca del centro, vale",
                    "Tengo licencia de manejo",
                ],
            },
            "en": {
                "what": "English",
                "not_for": "Spanish, or a message with no word that belongs to a language",
                "examples": [
                    "Hi, I'm looking for a delivery job",
                    "I live near downtown",
                    "I have my own bike",
                ],
            },
            "other": {
                "what": "A language that is neither Spanish nor "
                "English, such as Portuguese, French or "
                "Arabic",
                "not_for": "Spanish or English",
                "examples": [
                    "Olá, procuro emprego de entregador",
                    "Bonjour, je cherche du travail",
                ],
            },
            "unclear": {
                "what": "Nothing in the message belongs to one "
                "language: a name, a place, a number, a "
                "date, a lone 'OK' or an emoji",
                "not_for": "A word, phrase or sentence that is clearly Spanish or English",
                "examples": ["Polanco", "25", "12/10/2026", "OK", "👍"],
            },
        },
    },
    "asks": {
        "type": "choice",
        "instructions": {
            "question": "Does `latest_candidate_message` ask for the "
            "conversation to continue in a particular "
            "language from now on, and in which?",
            "focus": "Only an explicit request about the language of "
            "the conversation counts: asking the assistant to "
            "write, ask, answer or talk in Spanish or in "
            "English, or asking whether that is possible. The "
            "language the message is written in does not "
            "matter. Writing in a language, mentioning a "
            "language the Candidate speaks, studied or used at "
            "work, asking what languages the job needs, or a "
            "language name on its own are not requests.",
        },
        "criteria": {
            "es": {
                "what": "Asks the assistant to write, ask, answer or continue in Spanish",
                "not_for": "A message that is merely written in Spanish, "
                "mentions Spanish as something the Candidate "
                "speaks or used, or asks for English",
                "examples": [
                    "Can we please talk in Spanish?",
                    "mejor háblame en español",
                    "I'd rather do this in Spanish, if you don't mind",
                ],
            },
            "en": {
                "what": "Asks the assistant to write, ask, answer or continue in English",
                "not_for": "A message that is merely written in English, "
                "mentions English as something the Candidate "
                "speaks or used, or asks for Spanish",
                "examples": [
                    "Could we continue in English?",
                    "háblame en inglés por favor",
                    "prefiero que me escribas en inglés",
                ],
            },
            "none": {
                "what": "No request about the language of the "
                "conversation: an ordinary answer or question "
                "in any language, or a mention of a language "
                "that is not a request",
                "not_for": "Any message that asks to change or keep the language of the chat",
                "examples": [
                    "Trabajé en Londres y hablaba inglés todos los días",
                    "I speak Spanish at home and English at work",
                    "Vivo en Polanco, cerca del metro",
                ],
            },
        },
    },
    "switch": {
        "type": "noul",
        "instructions": {
            "question": "Does `latest_candidate_message` clearly "
            "continue the conversation in a different "
            "language from "
            "`conversation.current_language`?",
            "focus": "Spanish from Spain and Spanish from Latin "
            "America are the same language. A single word, a "
            "name, a place, a date, a number or a lone "
            "greeting never counts as a different language: "
            "it takes a full sentence or more.",
        },
        "criteria": {
            "true": {
                "what": "A full sentence or more in English while "
                "the current language is Spanish, or in "
                "Spanish while the current language is "
                "English",
                "examples": [
                    "Sorry, can we continue in English? I have a license",
                    "Perfecto, ¿y cuánto pagan?",
                ],
            },
            "false": {
                "what": "The same language as the current one, or "
                "only a word, name, place, date or number "
                "from another language",
                "examples": ["Mañana", "Polanco", "OK", "sí", "Madrid centro"],
            },
        },
    },
    "frustrated": {
        "type": "noul",
        "instructions": {
            "question": "Does `latest_candidate_message` show "
            "that the Candidate is frustrated, "
            "annoyed or impatient with the "
            "conversation?",
            "focus": "Look for complaints about repeating "
            "themselves, about not being understood or "
            "about the process, or an irritated or "
            "sarcastic tone. `conversation.recent_turns` "
            "shows what they were answering.",
        },
        "criteria": {
            "true": {
                "what": "Impatience, irritation or a complaint about the conversation itself",
                "examples": [
                    "Ya te lo dije dos veces",
                    "This is taking forever, you keep asking the same thing",
                ],
            },
            "false": {
                "what": "A neutral or friendly message, "
                "including short casual ones, or a "
                "plain request for a person that "
                "carries no irritation",
                "examples": ["vale", "Sí, tengo moto propia", "Could I talk to someone, please?"],
            },
        },
    },
    "confused": {
        "type": "noul",
        "instructions": {
            "question": "Does `latest_candidate_message` show that "
            "the Candidate does not understand what is "
            "being asked or what is going on?",
            "focus": "Look for a question about what the last "
            "assistant message meant, or an answer that "
            "misreads it. A question about the job itself "
            "(pay, schedule, bike) is not confusion.",
        },
        "criteria": {
            "true": {
                "what": "Says they do not understand, asks what a "
                "question means, or answers something else "
                "because they misread the question",
                "examples": ["No entiendo qué me preguntas", "What do you mean by platform?"],
            },
            "false": {
                "what": "A clear answer, or a question about the job",
                "examples": ["Trabajo por las tardes", "How much does it pay?"],
            },
        },
    },
    "wants_human": {
        "type": "noul",
        "instructions": {
            "question": "Does `latest_candidate_message` ask to "
            "speak with a person instead of the "
            "assistant?",
            "focus": "Asking whether the assistant is a bot or "
            "an AI is not a request for a person.",
        },
        "criteria": {
            "true": {
                "what": "Asks for a human, a recruiter, a "
                "manager or someone from the team, or "
                "asks to be called",
                "examples": ["Quiero hablar con una persona", "Can I speak to a real recruiter?"],
            },
            "false": {
                "what": "No request for a person",
                "examples": ["¿Eres un bot?", "Are you an AI?", "Sí, tengo carnet"],
            },
        },
    },
    "abusive": {
        "type": "noul",
        "instructions": {
            "question": "Does `latest_candidate_message` insult, "
            "swear at or threaten the assistant or the "
            "company?",
            "focus": "Frustration or a complaint without insults or threats is not abuse.",
        },
        "criteria": {
            "true": {
                "what": "Insults, slurs or threats aimed at the assistant, the team or the company",
                "examples": ["Eres un inútil de mierda", "You stupid bot, I'll find you"],
            },
            "false": {
                "what": "Anything else, including a sharp complaint",
                "examples": ["Esto no funciona, qué desastre", "This is useless"],
            },
        },
    },
    "unreadable": {
        "type": "noul",
        "instructions": {
            "question": "Is `latest_candidate_message` "
            "unreadable: keyboard mashing, random "
            "letters or made-up words that are not a "
            "word, an answer or a sentence in any "
            "language?",
            "focus": "Judge whether the words can be read, not "
            "whether they answer the question in "
            "`conversation.recent_turns`. A misspelt "
            "word, slang, an abbreviation, a name, a "
            "place, a number, a short answer or a "
            "sentence in any language is readable, even "
            "when it is wrong or off topic.",
        },
        "criteria": {
            "true": {
                "what": "Strings of letters that form no word in "
                "any language, typed at random or by "
                "mashing the keyboard, with at most a "
                "stray real word among them",
                "examples": ["asdkj qwe lkjh", "aksda asdas asassjqw", "Blok loka ksks aaa"],
            },
            "false": {
                "what": "Readable text, however short, misspelt, informal or off topic",
                "examples": ["repertidor", "simón", "Ana López", "ok", "Olá, tudo bem?"],
            },
        },
    },
}


def test_the_eight_existing_questions_equal_the_ones_on_main() -> None:
    assert set(EIGHT_QUESTIONS_ON_MAIN) == {"lang", "asks", "switch", *SIGNALS, "unreadable"}
    assert {name: QUESTIONS[name] for name in EIGHT_QUESTIONS_ON_MAIN} == EIGHT_QUESTIONS_ON_MAIN


def test_ten_questions_go_in_one_request_with_the_one_point_two_second_timeout(
    monkeypatch,
) -> None:
    client, seen = client_for(lambda request: httpx.Response(200, json=response(TEN_ANSWERS)))
    assert client.timeout_s == 1.2
    result = client.read(REQUEST)
    [request] = seen  # one request, not one per question
    body = json.loads(request.content)
    assert len(body["questions"]) == 10
    assert set(body["questions"]) == {*EIGHT_QUESTIONS_ON_MAIN, "intent", "malice"}
    assert request.extensions["timeout"] == {"connect": 1.2, "read": 1.2, "write": 1.2, "pool": 1.2}
    reading = result.reading
    assert (reading.intent, reading.intent_confidence, reading.malice) == ("steering", 0.93, 0.97)

    # the input size: the same request with the eight questions of main, then with ten
    monkeypatch.setattr("criba.jev.client.QUESTIONS", EIGHT_QUESTIONS_ON_MAIN)
    eight_client, eight_seen = client_for(lambda request: httpx.Response(200, json=response()))
    eight_client.read(REQUEST)
    eight_bytes, ten_bytes = len(eight_seen[0].content), len(request.content)
    print(f"jev request input size: eight questions {eight_bytes} bytes, ten {ten_bytes} bytes")
    assert eight_bytes < ten_bytes <= 1.75 * eight_bytes


def test_a_valid_ten_answer_response_reads_every_value() -> None:
    reading = parse_answers(TEN_ANSWERS)
    assert (reading.intent, reading.intent_confidence, reading.malice) == ("steering", 0.93, 0.97)
    assert (reading.lang, reading.asks) == ("en", "none")
    # up to 0.02 outside 0..1 is rounding, as for the other answers
    rounded = parse_answers({**TEN_ANSWERS, "malice": {"noul": 1.01}})
    assert rounded.malice == 1.0


@pytest.mark.parametrize(
    ("override", "intent", "confidence", "malice"),
    [
        # an `intent` that is not an option, or has no usable confidence, reads as no intent
        ({"intent": {"choice": "greeting", "confidence": 0.9}}, None, None, 0.97),
        ({"intent": {"choice": 3, "confidence": 0.9}}, None, None, 0.97),
        ({"intent": {"choice": "steering"}}, None, None, 0.97),
        ({"intent": {"choice": "steering", "confidence": 1.5}}, None, None, 0.97),
        ({"intent": {"choice": "steering", "confidence": "high"}}, None, None, 0.97),
        ({"intent": "steering"}, None, None, 0.97),
        ({"intent": None}, None, None, 0.97),
        # a `malice` outside 0..1, not a number, or malformed reads as no reading, never 0
        ({"malice": {"noul": 1.5}}, "steering", 0.93, None),
        ({"malice": {"noul": -0.5}}, "steering", 0.93, None),
        ({"malice": {"noul": True}}, "steering", 0.93, None),
        ({"malice": {"noul": "high"}}, "steering", 0.93, None),
        ({"malice": {}}, "steering", 0.93, None),
        ({"malice": 0.9}, "steering", 0.93, None),
    ],
)
def test_a_bad_intent_or_malice_answer_is_none_in_an_ok_reading(
    override, intent, confidence, malice
) -> None:
    answers = {**TEN_ANSWERS, **override}
    client, _ = client_for(lambda request: httpx.Response(200, json=response(answers)))
    reading = client.read(REQUEST).reading  # the call succeeds
    assert (reading.intent, reading.intent_confidence, reading.malice) == (
        intent,
        confidence,
        malice,
    )
    expected = parse_answers(ANSWERS)  # the eight other values are those of the good answers
    for name in EIGHT_VALUES:
        assert getattr(reading, name) == getattr(expected, name), name


def test_both_new_answers_missing_is_an_ok_reading_with_none_not_zero() -> None:
    client, _ = client_for(lambda request: httpx.Response(200, json=response(ANSWERS)))
    reading = client.read(REQUEST).reading
    assert reading.intent is None and reading.intent_confidence is None
    assert reading.malice is None  # not 0.0: there was no reading
    assert reading.signals["abusive"] == 0.0 and reading.lang == "en"
    assert (reading.switch, reading.unreadable) == (0.66, 0.04)


@pytest.mark.parametrize(
    "bad",
    [
        {"lang": {"choice": "fr", "confidence": 1}},
        {"lang": {"confidence": 1}},
        {"lang": {"choice": "en"}},
        {"switch": {"noul": 3}},
        {"abusive": {"noul": "no"}},
        {"unreadable": {}},
        {"asks": {"choice": "fr", "confidence": 1}},
    ],
)
def test_a_malformed_answer_to_an_existing_question_still_fails_the_call(bad) -> None:
    # the lenient parse covers `intent` and `malice` only: the eight stay strict, even when the
    # two new answers are fine
    client, _ = client_for(
        lambda request: httpx.Response(200, json=response({**TEN_ANSWERS, **bad}))
    )
    with pytest.raises(JevFailed) as caught:
        client.read(REQUEST)
    assert caught.value.kind == "error" and str(caught.value) == "unreadable answer"
    for name in bad:
        missing = {k: v for k, v in TEN_ANSWERS.items() if k != name}
        client, _ = client_for(lambda request, m=missing: httpx.Response(200, json=response(m)))
        with pytest.raises(JevFailed):
            client.read(REQUEST)


def test_a_reading_built_the_old_way_still_constructs_with_none_for_the_new_fields() -> None:
    reading = battery.Reading("en", 1.0, 1.0, {"frustrated": 0.0})
    assert (reading.intent, reading.intent_confidence, reading.malice) == (None, None, None)
    # the positions of the existing fields are unchanged
    full = battery.Reading("es", 0.9, 0.1, {}, "en", 0.8, 0.3)
    assert (full.asks, full.asks_confidence, full.unreadable) == ("en", 0.8, 0.3)
    assert full.malice is None


class _SlowBody(httpx.SyncByteStream):
    """A response body that takes ``delay`` seconds to arrive (the read phase)."""

    def __init__(self, delay: float) -> None:
        self.delay = delay

    def __iter__(self) -> Iterator[bytes]:
        time.sleep(self.delay)
        yield json.dumps(response()).encode()


class _SlowInTwoPhases(httpx.BaseTransport):
    """Each phase stays under the per-phase timeout: the headers take ``delay`` (connect and
    write), then the body takes ``delay`` again (read)."""

    def __init__(self, delay: float) -> None:
        self.delay = delay

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        time.sleep(self.delay)
        return httpx.Response(200, stream=_SlowBody(self.delay))


def test_the_timeout_is_one_budget_for_the_whole_call_not_one_per_phase() -> None:
    # Each phase (0.4 s) is under the 0.5 s budget; together (0.8 s) they are over it.
    client = OpenRouterJev(
        api_key="k-test", timeout_s=0.5, http=httpx.Client(transport=_SlowInTwoPhases(0.4))
    )
    started = time.monotonic()
    with pytest.raises(JevFailed) as caught:
        client.read(REQUEST)
    waited = time.monotonic() - started
    assert caught.value.kind == "timeout" and str(caught.value) == "timeout"
    assert waited < 0.75
    assert 450 <= caught.value.latency_ms < 750


def test_a_call_slow_in_two_phases_within_the_budget_still_answers() -> None:
    client = OpenRouterJev(
        api_key="k-test", timeout_s=1.0, http=httpx.Client(transport=_SlowInTwoPhases(0.05))
    )
    assert client.read(REQUEST).reading.lang == "en"
