"""Cut-off prose is never stored: the writer retries once with a bigger budget, then stores
no prose. A fake OpenRouter (httpx mock transport) stands in for the model; no network."""

import json
import logging

import httpx
import pytest

from criba.screening.clock import FakeClock
from criba.summary import (
    PROSE_MAX_TOKENS,
    PROSE_RETRY_MAX_TOKENS,
    SYSTEM_PROMPT,
    OpenRouterSummaryClient,
    ProseRequest,
    ProseResult,
    SummaryWriter,
    is_truncated,
)

COMPLETE = "Ana completó el cribado. No hay contradicciones. Conviene confirmar la fecha."
CUT_OFF = "Ana completó el cribado y el sistema la fijó en el lunes 12 de octubre de 2026"

Reply = tuple[str, str, int] | int | dict  # (content, finish_reason, tokens), a status, or an error


class FakeOpenRouter:
    """Answers each call from a script, in order, and keeps every request body."""

    def __init__(self, *replies: Reply) -> None:
        self.replies = list(replies)
        self.bodies: list[dict] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.bodies.append(json.loads(request.content))
        reply = self.replies[len(self.bodies) - 1]
        if isinstance(reply, int):
            return httpx.Response(reply, json={"error": "boom"})
        if isinstance(reply, dict):
            return httpx.Response(reply["status"], json={"error": {"message": reply["message"]}})
        content, finish_reason, tokens = reply
        return httpx.Response(
            200,
            json={
                "model": "anthropic/claude-sonnet-5.5",
                "choices": [{"message": {"content": content}, "finish_reason": finish_reason}],
                "usage": {
                    "prompt_tokens": 1350,
                    "completion_tokens": tokens,
                    "completion_tokens_details": {"reasoning_tokens": tokens - 100},
                },
            },
        )

    def client(self) -> OpenRouterSummaryClient:
        http = httpx.Client(transport=httpx.MockTransport(self.handler))
        return OpenRouterSummaryClient("test-key", http=http)


def write(conn, app, fake: FakeOpenRouter, clock: FakeClock) -> dict:
    return SummaryWriter(fake.client(), clock, environment="test").write(conn, app["id"])


def usage_rows(conn, application_id: str) -> list[dict]:
    sql = "SELECT * FROM usage WHERE application_id = ? ORDER BY id"
    return [dict(r) for r in conn.execute(sql, (application_id,)).fetchall()]


def test_a_complete_answer_is_stored_as_is_after_one_call(
    conn, clock, qualified_application
) -> None:
    app = qualified_application(conn)
    fake = FakeOpenRouter((COMPLETE, "stop", 70))
    done = write(conn, app, fake, clock)
    assert done["summary_prose"] == COMPLETE
    assert len(fake.bodies) == 1
    [row] = usage_rows(conn, app["id"])
    assert (row["status"], row["error"]) == ("ok", None)


def test_a_length_finish_is_retried_once_with_a_bigger_budget(
    conn, clock, qualified_application, caplog
) -> None:
    app = qualified_application(conn)
    fake = FakeOpenRouter((CUT_OFF, "length", 500), (COMPLETE, "stop", 90))
    with caplog.at_level(logging.WARNING, logger="criba.summary"):
        done = write(conn, app, fake, clock)

    assert done["summary_prose"] == COMPLETE
    assert [b["max_tokens"] for b in fake.bodies] == [PROSE_MAX_TOKENS, PROSE_RETRY_MAX_TOKENS]
    assert PROSE_RETRY_MAX_TOKENS > PROSE_MAX_TOKENS
    assert "cut off" in caplog.text and "finish_reason=length" in caplog.text
    first, second = usage_rows(conn, app["id"])
    assert (first["status"], first["error"], first["output_tokens"]) == ("ok", "truncated", 500)
    assert (second["status"], second["error"]) == ("ok", None)


def test_text_that_ends_mid_sentence_is_retried_even_when_the_finish_looks_normal(
    conn, clock, qualified_application
) -> None:
    app = qualified_application(conn)
    fake = FakeOpenRouter((CUT_OFF, "stop", 120), (COMPLETE, "stop", 90))
    assert write(conn, app, fake, clock)["summary_prose"] == COMPLETE
    assert len(fake.bodies) == 2


def test_prose_still_cut_off_after_the_retry_is_not_stored(
    conn, clock, qualified_application, caplog
) -> None:
    app = qualified_application(conn)
    fake = FakeOpenRouter((CUT_OFF, "length", 600), (CUT_OFF, "length", 1500))
    with caplog.at_level(logging.WARNING, logger="criba.summary"):
        done = write(conn, app, fake, clock)

    assert len(fake.bodies) == 2  # one retry, never more
    assert done["summary_prose"] is None
    assert done["summary_structured"]["stage"] == "qualified"  # the structured part is kept
    assert "still cut off" in caplog.text
    assert [r["error"] for r in usage_rows(conn, app["id"])] == ["truncated", "truncated"]


def test_a_retry_that_fails_keeps_both_ledger_rows_and_stores_no_prose(
    conn, clock, qualified_application
) -> None:
    app = qualified_application(conn)
    fake = FakeOpenRouter((CUT_OFF, "length", 500), 500)
    done = write(conn, app, fake, clock)
    assert done["summary_prose"] is None
    rows = usage_rows(conn, app["id"])
    assert [(r["status"], r["error"]) for r in rows] == [
        ("ok", "truncated"),
        ("error", "HTTPStatusError"),
    ]


def test_the_request_asks_for_reasoning_effort_low_and_sets_the_default_budget(
    conn, clock, qualified_application
) -> None:
    """Reasoning cannot be switched off for Sonnet 5.5 (HTTP 400): the request asks for the
    lowest effort instead."""
    app = qualified_application(conn)
    fake = FakeOpenRouter((COMPLETE, "stop", 70))
    write(conn, app, fake, clock)
    body = fake.bodies[0]
    assert body["reasoning"] == {"effort": "low"}
    assert "enabled" not in body["reasoning"]  # the API refuses reasoning switched off
    assert body["max_tokens"] == PROSE_MAX_TOKENS


def test_the_system_prompt_carries_the_length_instruction() -> None:
    assert "3 to 5 sentences" in SYSTEM_PROMPT
    assert "at most 100 words" in SYSTEM_PROMPT
    assert "Finish every sentence" in SYSTEM_PROMPT


def test_the_prompt_sent_to_the_model_carries_the_length_instruction() -> None:
    fake = FakeOpenRouter((COMPLETE, "stop", 70))
    fake.client().write_prose(ProseRequest({}, [], "finished", "es-ES"))
    system = fake.bodies[0]["messages"][0]
    assert system["role"] == "system" and "3 to 5 sentences" in system["content"]


def test_the_client_reports_the_finish_reason_and_reasoning_tokens() -> None:
    fake = FakeOpenRouter((CUT_OFF, "length", 500))
    result = fake.client().write_prose(ProseRequest({}, [], "finished", "es-ES"))
    assert result.finish_reason == "length" and result.reasoning_tokens == 400


@pytest.mark.parametrize(
    ("text", "finish_reason", "expected"),
    [
        ("Una frase.", "stop", False),
        ("Una frase.", None, False),
        ("¿Es cierto? Sí!", "stop", False),
        ("Dijo «hola.»", "stop", False),
        ('Dijo "hola."', "stop", False),
        ("Terminó así…", "stop", False),
        ("", "stop", False),
        ("Una frase.", "length", True),
        ("Una frase.", "max_tokens", True),
        ("", "length", True),
        ("Una frase sin cerrar", "stop", True),
        ("Una frase. Otra sin cerrar", None, True),
        ("Una frase, la fecha 12.\n", "stop", False),
    ],
)
def test_is_truncated(text: str, finish_reason: str | None, expected: bool) -> None:
    result = ProseResult(text=text, model="m", finish_reason=finish_reason)
    assert is_truncated(result) is expected


REASONING_REQUIRED = "Reasoning is mandatory for this endpoint and cannot be disabled."


def test_a_400_stores_the_structured_part_without_prose_logs_the_message_and_does_not_retry(
    conn, clock, qualified_application, caplog
) -> None:
    app = qualified_application(conn)
    fake = FakeOpenRouter({"status": 400, "message": REASONING_REQUIRED})
    with caplog.at_level(logging.INFO, logger="criba.summary"):
        done = write(conn, app, fake, clock)

    assert len(fake.bodies) == 1  # a rejected request is not a truncation: no retry
    assert done["summary_prose"] is None
    assert done["summary_structured"]["stage"] == "qualified"  # the structured part is kept
    assert "HTTP 400" in caplog.text and REASONING_REQUIRED in caplog.text
    assert "cut off" not in caplog.text
    [row] = usage_rows(conn, app["id"])
    assert (row["status"], row["error"]) == ("error", "HTTPStatusError")


def test_a_failure_with_a_body_that_is_not_json_still_logs_its_text(
    conn, clock, qualified_application, caplog
) -> None:
    app = qualified_application(conn)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(502, text="bad gateway")

    client = OpenRouterSummaryClient("k", http=httpx.Client(transport=httpx.MockTransport(handler)))
    with caplog.at_level(logging.INFO, logger="criba.summary"):
        done = SummaryWriter(client, clock, environment="test").write(conn, app["id"])
    assert done["summary_prose"] is None
    assert "HTTP 502 bad gateway" in caplog.text


def test_every_call_logs_finish_reason_and_token_counts_at_info_level(
    conn, clock, qualified_application, caplog
) -> None:
    app = qualified_application(conn)
    fake = FakeOpenRouter((COMPLETE, "stop", 70))
    with caplog.at_level(logging.INFO, logger="criba.summary"):
        write(conn, app, fake, clock)
    [record] = [r for r in caplog.records if r.name == "criba.summary"]
    assert record.levelno == logging.INFO
    assert "finish_reason=stop" in record.getMessage()
    assert "output_tokens=70" in record.getMessage()
    assert "reasoning_tokens=" in record.getMessage()
    assert "cut off" not in caplog.text
