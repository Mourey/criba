"""src/scripts/export_personas.py: persona export, detect corpus, `--check` and `--record`.

Each test builds a small layout in a temporary directory and points the script at it with
`--root`, so the real `personas.json`, the real corpus and the real persona YAML are never read or
written here: a persona edit never fails this file (the persona drift is `--check`'s job in
`check.sh full`). The script and the schema are the real ones, because the script imports them
from its own checkout.
"""

import asyncio
import importlib
import importlib.util
import json
import socket
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

SCRIPT = Path(__file__).resolve().parents[4] / "src" / "scripts" / "export_personas.py"
spec = importlib.util.spec_from_file_location("export_personas", SCRIPT)
assert spec is not None and spec.loader is not None
export_personas = importlib.util.module_from_spec(spec)
sys.modules["export_personas"] = export_personas
spec.loader.exec_module(export_personas)

PERSONAS_REL = Path("src/tests/sim/personas")
PERSONAS_JSON_REL = Path("src/web/src/stage/personas.json")
CORPUS_REL = Path("src/web/tests/fixtures/detect-corpus.json")

ALPHA = """\
id: alpha_es
description: Knockout with a Zone list, a correction at the confirm step and a reaction.
quirks: [ambiguous_zone, corrections]
identity:
  name: Ana Alfa
  phone: "+34600000001"
  country: ES
  locale: es-ES
opening:
  text: Hola, quiero trabajar
  covers: [full_name]
  value: {full_name: Ana Alfa}
answers:
  zone:
    - {text: Madrid}
    - {text: "Centro, Madrid", pick_option: 1, query: "Centro, Madrid"}
  driver_license:
    - {text: "No tengo carnet", value: "no", lang: es}
zone_choice: Centro
confirm:
  - {text: "no, mi zona es Vallecas", field: zone, query: "Vallecas, Madrid"}
  - {text: "sí"}
interrupts:
  - at: after_knockout
    say: {text: revisión}
    then: stop
expect:
  stage: driver_license
  status: closed
  outcome: knockout
  knockout_criterion: driver_license
  events: [knockout, review_requested]
  fields_unanswered: [availability]
"""

BRAVO = """\
id: bravo_mx
description: Moves the sim clock before it answers again.
identity:
  name: Beto Bravo
  phone: "+525500000101"
  country: MX
  locale: es-MX
  entry: outreach_reply
opening: {text: Hola}
interrupts:
  - at: preferred_schedule
    advance: [1h, 19h]
    say: {text: sigo aquí}
expect: {stage: greeting, status: open}
"""

CHARLIE = """\
id: charlie_en
description: Sends a voice note.
identity:
  name: Charlie Chaplin
  phone: "+34600000003"
  country: ES
  locale: en
opening: {text: Hi there}
answers:
  full_name:
    - {text: Charlie Chaplin, value: Charlie Chaplin, media: voice_note}
expect: {stage: greeting, status: open}
"""


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def sim(name: str) -> Any:
    export_personas.load_sim()
    return importlib.import_module(name)


def fake_result(persona_id: str = "alpha_es") -> Any:
    """A driver result with everything the committed corpus must not keep."""
    driver = sim("sim.driver")
    zone_prompt = driver.Entry(
        direction="outbound",
        sender="agent",
        text="Encontré varias zonas con ese nombre. ¿Cuál es la tuya?",
        t=2.5,
        id=12,
        sender_label="Sazón Empleo",
        interactive={
            "style": "buttons",
            "options": [
                {"id": "zone_angelopolis", "label": "Angelópolis, Puebla"},
                {"id": "zone_centro", "label": "Centro, Puebla"},
            ],
        },
        created_at="2026-10-09T10:00:02Z",
    )
    first_question = driver.Entry(
        direction="outbound",
        sender="agent",
        text="¡Hola! Soy el asistente virtual de Grupo Sazón. ¿En qué ciudad vives?",
        t=1.2,
        id=11,
        created_at="2026-10-09T10:00:01Z",
    )
    opening = driver.Entry(
        direction="inbound",
        sender="candidate",
        text="Hola, quiero trabajar",
        t=0.1,
        id=10,
        message_id="8b3c4d5e-6f7a-4b8c-9d0e-1f2a3b4c5d6e",
        lang="es",
        purpose="opening",
    )
    tap = driver.Entry(
        direction="inbound",
        sender="candidate",
        text="Centro, Puebla",
        t=3.0,
        id=13,
        message_id="9c4d5e6f-7a8b-4c9d-8e1f-2a3b4c5d6e7f",
        reply_to={"message_id": 12, "option_id": "zone_centro"},
        lang="es",
        purpose="answer:zone",
    )
    typed = driver.Entry(
        direction="inbound",
        sender="candidate",
        text="No tengo carnet",
        t=6.0,
        id=15,
        message_id="ad5e6f7a-8b9c-4d0e-9f2a-3b4c5d6e7f80",
        lang="es",
        purpose="answer:driver_license",
    )
    knockout = driver.Entry(
        direction="outbound",
        sender="agent",
        text="Gracias por tu interés. Necesitamos licencia de manejo vigente.",
        t=7.0,
        id=16,
        created_at="2026-10-09T10:00:07Z",
    )
    license_question = driver.Entry(
        direction="outbound",
        sender="agent",
        text="Gracias. ¿Tienes licencia de conducir?",
        t=4.0,
        id=14,
        created_at="2026-10-09T10:00:04Z",
    )
    turns = [
        driver.Turn(sent=opening, replies=[first_question, zone_prompt]),
        driver.Turn(sent=tap, replies=[license_question]),
        driver.Turn(sent=typed, replies=[knockout]),
    ]
    return driver.RunResult(
        persona_id=persona_id,
        conversation_id="conv-1",
        phone="+34600000099",
        ended="knockout",
        entries=[opening, first_question, zone_prompt, tap, license_question, typed, knockout],
        turns=turns,
        clock_steps=[],
        elapsed_s=12.3,
    )


@pytest.fixture
def root(tmp_path: Path) -> Path:
    """A small layout: three personas, the exported JSON and a corpus with one recorded run."""
    layout = tmp_path / "layout"
    write(layout / PERSONAS_REL / "alpha_es.yaml", ALPHA)
    write(layout / PERSONAS_REL / "bravo_mx.yaml", BRAVO)
    write(layout / PERSONAS_REL / "charlie_en.yaml", CHARLIE)
    assert export_personas.main(["--root", str(layout)]) == 0
    digest = persona_entry(layout, "alpha_es")["persona_digest"]
    run = export_personas.run_record(digest, fake_result())
    write(layout / CORPUS_REL, export_personas.dumps({"entries": [], "runs": [run]}))
    assert export_personas.main(["--root", str(layout)]) == 0
    return layout


def persona_entry(layout: Path, persona_id: str) -> dict[str, Any]:
    doc = read_json(layout / PERSONAS_JSON_REL)
    return next(p for p in doc["personas"] if p["id"] == persona_id)


def run_cli(
    capsys: pytest.CaptureFixture[str], *args: str, **injected: Any
) -> tuple[int, str, str]:
    code = export_personas.main(list(args), **injected)
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def cause_lines(err: str) -> list[str]:
    return [line for line in err.splitlines() if line.strip()]


def keys_everywhere(value: Any) -> set[str]:
    found: set[str] = set()
    if isinstance(value, dict):
        for key, inner in value.items():
            found.add(key)
            found |= keys_everywhere(inner)
    elif isinstance(value, list):
        for inner in value:
            found |= keys_everywhere(inner)
    return found


# ---- export shape ---------------------------------------------------------------------------


def test_export_keeps_what_the_stage_shows(root: Path) -> None:
    alpha = persona_entry(root, "alpha_es")
    assert alpha["identity"] == {
        "name": "Ana Alfa",
        "phone": "+34600000001",
        "country": "ES",
        "locale": "es-ES",
        "entry": "candidate_writes_first",
    }
    assert alpha["opening"] == {
        "text": "Hola, quiero trabajar",
        "lang": None,
        "pick_option": None,
        "covers": ["full_name"],
    }
    assert alpha["answers"]["zone"] == [
        {"text": "Madrid", "lang": None, "pick_option": None, "covers": []},
        {"text": "Centro, Madrid", "lang": None, "pick_option": 1, "covers": []},
    ]
    assert alpha["answers"]["driver_license"][0]["lang"] == "es"
    assert alpha["zone_choice"] == "Centro"
    assert alpha["interrupts"] == [
        {
            "at": "after_knockout",
            "advance": [],
            "say": {"text": "revisión", "lang": None, "pick_option": None, "covers": []},
            "then": "stop",
        }
    ]
    assert alpha["expect"] == {
        "stage": "driver_license",
        "status": "closed",
        "outcome": "knockout",
        "events": ["knockout", "review_requested"],
    }
    assert alpha["id"] == "alpha_es"
    assert set(alpha) == {
        "id",
        "identity",
        "opening",
        "answers",
        "zone_choice",
        "confirm",
        "interrupts",
        "expect",
        "playable",
        "unplayable_reason",
        "persona_digest",
    }


def test_export_drops_what_only_steers_the_stub(root: Path) -> None:
    doc = read_json(root / PERSONAS_JSON_REL)
    assert not keys_everywhere(doc) & {"value", "query", "field", "media"}


def test_a_correction_keeps_what_the_driver_reads_from_it(root: Path) -> None:
    """driver.py reads `field` (corrected map) and `query` (Zone pick) on a confirm reply."""
    confirm = persona_entry(root, "alpha_es")["confirm"]
    assert confirm[0]["text"] == "no, mi zona es Vallecas"
    assert confirm[0]["corrects"] == "zone"
    assert confirm[0]["hint"] == "Vallecas, Madrid"
    assert confirm[1]["text"] == "sí"
    assert confirm[1]["corrects"] is None
    assert confirm[1]["hint"] is None


def test_field_order_is_the_schemas(root: Path) -> None:
    schema = sim("sim.personas.schema")
    assert read_json(root / PERSONAS_JSON_REL)["fieldOrder"] == list(schema.FIELD_ORDER)


def test_personas_are_listed_by_id(root: Path) -> None:
    ids = [p["id"] for p in read_json(root / PERSONAS_JSON_REL)["personas"]]
    assert ids == ["alpha_es", "bravo_mx", "charlie_en"]


def test_playable_and_the_reason_when_it_is_not(root: Path) -> None:
    alpha = persona_entry(root, "alpha_es")
    bravo = persona_entry(root, "bravo_mx")
    charlie = persona_entry(root, "charlie_en")
    assert alpha["playable"] is True
    assert alpha["unplayable_reason"] is None
    assert bravo["playable"] is False
    assert "clock" in bravo["unplayable_reason"]
    assert "preferred_schedule" in bravo["unplayable_reason"]
    assert charlie["playable"] is False
    assert "voice_note" in charlie["unplayable_reason"]
    assert "answers.full_name" in charlie["unplayable_reason"]


# ---- determinism ----------------------------------------------------------------------------


def test_two_default_runs_are_byte_identical(root: Path) -> None:
    before = {p: (root / p).read_bytes() for p in (PERSONAS_JSON_REL, CORPUS_REL)}
    assert export_personas.main(["--root", str(root)]) == 0
    after = {p: (root / p).read_bytes() for p in (PERSONAS_JSON_REL, CORPUS_REL)}
    assert before == after


def test_output_has_sorted_keys_and_a_trailing_newline(root: Path) -> None:
    def assert_sorted(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        keys = [k for k, _ in pairs]
        assert keys == sorted(keys)
        return dict(pairs)

    for rel in (PERSONAS_JSON_REL, CORPUS_REL):
        text = (root / rel).read_text(encoding="utf-8")
        assert text.endswith("}\n")
        assert not text.endswith("\n\n")
        json.loads(text, object_pairs_hook=assert_sorted)
        assert "  " in text  # two-space indent
        assert "\\u" not in text  # no ASCII escaping
    assert "revisión" in (root / PERSONAS_JSON_REL).read_text(encoding="utf-8")


def test_the_digest_changes_with_a_reply(root: Path) -> None:
    before = persona_entry(root, "alpha_es")["persona_digest"]
    path = root / PERSONAS_REL / "alpha_es.yaml"
    write(path, path.read_text(encoding="utf-8").replace("text: Madrid}", "text: Madrid capital}"))
    assert export_personas.main(["--root", str(root)]) == 0
    after = persona_entry(root, "alpha_es")["persona_digest"]
    assert len(after) == 64
    assert after != before
    assert persona_entry(root, "bravo_mx")["persona_digest"] != after


def test_the_digest_is_the_sha256_of_the_exported_entry(root: Path) -> None:
    import hashlib

    entry = persona_entry(root, "alpha_es")
    digest = entry.pop("persona_digest")
    assert digest == hashlib.sha256(export_personas.dumps(entry).encode("utf-8")).hexdigest()


# ---- what each file may hold ----------------------------------------------------------------


def test_personas_json_keeps_the_id_and_identity(root: Path) -> None:
    for entry in read_json(root / PERSONAS_JSON_REL)["personas"]:
        assert set(entry["identity"]) == {"name", "phone", "country", "locale", "entry"}
        assert entry["id"]
        assert entry["identity"]["phone"].startswith("+")


def test_the_corpus_holds_no_time_id_or_phone(root: Path) -> None:
    corpus = read_json(root / CORPUS_REL)
    assert corpus["runs"], "the fixture records one run"
    forbidden = {
        "t",
        "created_at",
        "conversation_id",
        "message_id",
        "message_ids",
        "phone",
        "elapsed_s",
        "latency_first_s",
        "latency_last_s",
        "sender_label",
    }
    assert not keys_everywhere(corpus) & forbidden
    text = (root / CORPUS_REL).read_text(encoding="utf-8")
    assert "conv-1" not in text
    assert "+34600000099" not in text
    assert "2026-10-09T" not in text
    assert "12.3" not in text
    assert "8b3c4d5e" not in text

    def ids_outside_options(value: Any, inside_options: bool = False) -> list[Any]:
        found: list[Any] = []
        if isinstance(value, dict):
            for key, inner in value.items():
                if key == "id" and not inside_options:
                    found.append(inner)
                found += ids_outside_options(inner, key == "options")
        elif isinstance(value, list):
            for inner in value:
                found += ids_outside_options(inner, inside_options)
        return found

    assert ids_outside_options(corpus) == []


def test_the_run_record_has_the_agreed_shape(root: Path) -> None:
    run = read_json(root / CORPUS_REL)["runs"][0]
    assert set(run) == {"persona_id", "persona_digest", "ended", "turns"}
    assert run["persona_id"] == "alpha_es"
    assert run["persona_digest"] == persona_entry(root, "alpha_es")["persona_digest"]
    assert run["ended"] == "knockout"
    opening, tap, typed = run["turns"]
    assert opening["sent"] == {
        "option_id": None,
        "prompt_index": None,
        "prompt_turn": None,
        "purpose": "opening",
        "text": "Hola, quiero trabajar",
    }
    assert [r["text"] for r in opening["replies"]] == [
        "¡Hola! Soy el asistente virtual de Grupo Sazón. ¿En qué ciudad vives?",
        "Encontré varias zonas con ese nombre. ¿Cuál es la tuya?",
    ]
    assert opening["replies"][0]["interactive"] is None
    assert opening["replies"][1]["interactive"] == {
        "options": [
            {"id": "zone_angelopolis", "label": "Angelópolis, Puebla"},
            {"id": "zone_centro", "label": "Centro, Puebla"},
        ]
    }
    assert tap["sent"] == {
        "option_id": "zone_centro",
        "prompt_index": 1,
        "prompt_turn": 0,
        "purpose": "answer:zone",
        "text": None,
    }
    assert typed["sent"]["text"] == "No tengo carnet"
    assert typed["sent"]["option_id"] is None
    for turn in run["turns"]:
        assert set(turn) == {"sent", "replies"}
        for reply in turn["replies"]:
            assert set(reply) == {"text", "interactive"}


def test_a_tap_with_no_prompt_in_the_transcript_is_refused(root: Path) -> None:
    result = fake_result()
    result.turns[1].sent.reply_to = {"message_id": 999, "option_id": "zone_centro"}
    with pytest.raises(ValueError, match="999"):
        export_personas.run_record("0" * 64, result)


def test_default_mode_rebuilds_the_entries_and_keeps_the_runs(root: Path) -> None:
    corpus = read_json(root / CORPUS_REL)
    runs = corpus["runs"]
    sources = {e["source"] for e in corpus["entries"]}
    assert sources == {"hand", "stub"}
    stub_texts = [e["text"] for e in corpus["entries"] if e["source"] == "stub"]
    assert stub_texts == [
        "¡Hola! Soy el asistente virtual de Grupo Sazón. ¿En qué ciudad vives?",
        "Encontré varias zonas con ese nombre. ¿Cuál es la tuya?",
        "Gracias. ¿Tienes licencia de conducir?",
        "Gracias por tu interés. Necesitamos licencia de manejo vigente.",
    ]
    # An edited entry and a lost entry are rebuilt; the run is kept as recorded.
    corpus["entries"] = corpus["entries"][5:]
    corpus["entries"][0]["label"] = "zone"
    write(root / CORPUS_REL, export_personas.dumps(corpus))
    assert export_personas.main(["--root", str(root)]) == 0
    rebuilt = read_json(root / CORPUS_REL)
    assert rebuilt["runs"] == runs
    assert rebuilt["entries"] == export_personas.build_entries(runs)


def test_default_mode_without_a_corpus_starts_with_no_runs(tmp_path: Path) -> None:
    layout = tmp_path / "fresh"
    write(layout / PERSONAS_REL / "alpha_es.yaml", ALPHA)
    assert export_personas.main(["--root", str(layout)]) == 0
    corpus = read_json(layout / CORPUS_REL)
    assert corpus["runs"] == []
    assert {e["source"] for e in corpus["entries"]} == {"hand"}


# ---- --check --------------------------------------------------------------------------------


def test_check_passes_on_a_fresh_layout(root: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code, out, err = run_cli(capsys, "--check", "--root", str(root))
    assert (code, err) == (0, "")
    assert "3 personas" in out


def test_check_names_an_edited_personas_json_reply(
    root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    doc = read_json(root / PERSONAS_JSON_REL)
    alpha = next(p for p in doc["personas"] if p["id"] == "alpha_es")
    alpha["answers"]["zone"][0]["text"] = "Barcelona"
    write(root / PERSONAS_JSON_REL, export_personas.dumps(doc))
    code, _, err = run_cli(capsys, "--check", "--root", str(root))
    assert code == 1
    lines = cause_lines(err)
    assert len(lines) == 1
    assert "personas.json" in lines[0]
    assert "alpha_es" in lines[0]


def test_check_names_an_edited_corpus_label(root: Path, capsys: pytest.CaptureFixture[str]) -> None:
    corpus = read_json(root / CORPUS_REL)
    index = next(i for i, e in enumerate(corpus["entries"]) if e["label"] == "full_name")
    corpus["entries"][index]["label"] = "zone"
    write(root / CORPUS_REL, export_personas.dumps(corpus))
    code, _, err = run_cli(capsys, "--check", "--root", str(root))
    assert code == 1
    lines = cause_lines(err)
    assert len(lines) == 1
    assert "label" in lines[0]
    assert f"entry {index}" in lines[0]
    assert "full_name" in lines[0]


def test_check_names_a_stale_persona_digest(root: Path, capsys: pytest.CaptureFixture[str]) -> None:
    corpus = read_json(root / CORPUS_REL)
    corpus["runs"][0]["persona_digest"] = "0" * 64
    write(root / CORPUS_REL, export_personas.dumps(corpus))
    code, _, err = run_cli(capsys, "--check", "--root", str(root))
    assert code == 1
    lines = cause_lines(err)
    assert len(lines) == 1
    assert "persona_digest" in lines[0]
    assert "alpha_es" in lines[0]


def test_check_names_a_persona_edited_with_the_runs_untouched(
    root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = root / PERSONAS_REL / "alpha_es.yaml"
    write(path, path.read_text(encoding="utf-8").replace("text: Madrid}", "text: Madrid capital}"))
    code, _, err = run_cli(capsys, "--check", "--root", str(root))
    assert code == 1
    text = err
    assert "personas.json" in text
    assert "persona_digest" in text


def test_check_names_a_missing_category(root: Path, capsys: pytest.CaptureFixture[str]) -> None:
    corpus = read_json(root / CORPUS_REL)
    kept = [
        e
        for e in corpus["entries"]
        if "emoji" not in export_personas.entry_categories(e["text"], e["interactive"], e["label"])
    ]
    assert len(kept) < len(corpus["entries"])
    assert export_personas.missing_categories(kept) == [("emoji", 1, 0)]
    corpus["entries"] = kept
    write(root / CORPUS_REL, export_personas.dumps(corpus))
    code, _, err = run_cli(capsys, "--check", "--root", str(root))
    assert code == 1
    lines = cause_lines(err)
    assert len(lines) == 1
    assert "missing category emoji" in lines[0]


def test_check_names_a_kind_with_too_few_entries(
    root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    corpus = read_json(root / CORPUS_REL)
    seen = 0
    kept = []
    for entry in corpus["entries"]:
        if entry["label"] == "start_date":
            seen += 1
            if seen > 2:
                continue
        kept.append(entry)
    assert seen > 2
    corpus["entries"] = kept
    write(root / CORPUS_REL, export_personas.dumps(corpus))
    code, _, err = run_cli(capsys, "--check", "--root", str(root))
    assert code == 1
    lines = cause_lines(err)
    assert any("missing category kind:start_date" in line for line in lines)


def test_check_reports_missing_files(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    layout = tmp_path / "bare"
    write(layout / PERSONAS_REL / "alpha_es.yaml", ALPHA)
    code, _, err = run_cli(capsys, "--check", "--root", str(layout))
    assert code == 1
    lines = cause_lines(err)
    assert len(lines) == 2
    assert any("personas.json" in line and "missing" in line for line in lines)
    assert any("detect-corpus.json" in line and "missing" in line for line in lines)


def test_check_reports_a_run_for_a_persona_that_is_gone(
    root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    corpus = read_json(root / CORPUS_REL)
    corpus["runs"][0]["persona_id"] = "gone_es"
    write(root / CORPUS_REL, export_personas.dumps(corpus))
    code, _, err = run_cli(capsys, "--check", "--root", str(root))
    assert code == 1
    assert "gone_es" in err


def test_check_reports_a_personas_json_that_is_not_canonical(
    root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    doc = read_json(root / PERSONAS_JSON_REL)
    write(root / PERSONAS_JSON_REL, json.dumps(doc))
    code, _, err = run_cli(capsys, "--check", "--root", str(root))
    assert code == 1
    lines = cause_lines(err)
    assert len(lines) == 1
    assert "canonical" in lines[0]


def test_check_reports_a_personas_json_that_is_not_json(
    root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    write(root / PERSONAS_JSON_REL, "{not json")
    code, _, err = run_cli(capsys, "--check", "--root", str(root))
    assert code == 1
    assert "not valid JSON" in err


def test_check_reports_added_and_removed_personas(
    root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    doc = read_json(root / PERSONAS_JSON_REL)
    doc["personas"] = [p for p in doc["personas"] if p["id"] != "bravo_mx"]
    doc["fieldOrder"] = doc["fieldOrder"][::-1]
    extra = dict(doc["personas"][0], id="extra_es")
    doc["personas"].append(extra)
    write(root / PERSONAS_JSON_REL, export_personas.dumps(doc))
    code, _, err = run_cli(capsys, "--check", "--root", str(root))
    assert code == 1
    assert "fieldOrder" in err
    assert "bravo_mx" in err
    assert "extra_es" in err


def test_check_never_contacts_a_stack(
    root: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    def refuse(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("--check opened a socket")

    monkeypatch.setattr(socket, "socket", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
    code, _, err = run_cli(capsys, "--check", "--root", str(root))
    assert (code, err) == (0, "")


def test_an_invalid_persona_fails_every_mode_with_its_file(
    root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    write(root / PERSONAS_REL / "broken_es.yaml", "id: wrong_name\n")
    for mode in ([], ["--check"]):
        code, _, err = run_cli(capsys, *mode, "--root", str(root))
        assert code == 1
        assert "broken_es.yaml" in err


def test_the_command_line_runs_from_another_directory(root: Path, tmp_path: Path) -> None:
    """The script finds the schema from its own checkout, whatever the cwd and `--root`."""
    done = subprocess.run(
        [sys.executable, str(SCRIPT), "--check", "--root", str(root)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    assert (done.returncode, done.stderr) == (0, "")
    assert "3 personas" in done.stdout


def test_the_repository_root_is_the_default_root() -> None:
    assert SCRIPT.parents[2] == export_personas.REPO_ROOT
    assert (export_personas.REPO_ROOT / PERSONAS_REL).is_dir()


# ---- --record -------------------------------------------------------------------------------


def never_played(base_url: str, persona: Any) -> Any:
    raise AssertionError("--record played a persona before the probe passed")


def test_record_refuses_a_stack_that_answers_404(
    root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    asked: list[str] = []

    def get(url: str) -> tuple[int, str]:
        asked.append(url)
        return 404, "not found"

    before = (root / CORPUS_REL).read_bytes()
    code, out, err = run_cli(
        capsys,
        "--record",
        "http://localhost:18199",
        "--root",
        str(root),
        get=get,
        play=never_played,
    )
    assert code == 1
    assert asked == ["http://localhost:18199/api/sim/clock"]
    assert "404" in err
    assert out == ""
    assert (root / CORPUS_REL).read_bytes() == before


def test_record_refuses_an_unreachable_stack(
    root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    def get(url: str) -> tuple[int, str]:
        raise ConnectionRefusedError("connection refused")

    before = (root / CORPUS_REL).read_bytes()
    code, _, err = run_cli(
        capsys, "--record", "http://localhost:1", "--root", str(root), get=get, play=never_played
    )
    assert code == 1
    assert "unreachable" in err
    assert (root / CORPUS_REL).read_bytes() == before


def test_record_refuses_an_answer_that_is_not_a_clock(
    root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    def get(url: str) -> tuple[int, str]:
        return 200, "<html>a web page</html>"

    code, _, err = run_cli(
        capsys, "--record", "http://localhost:8080", "--root", str(root), get=get, play=never_played
    )
    assert code == 1
    assert "clock" in err


def test_record_rewrites_the_runs_and_the_stub_entries(
    root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    played: list[tuple[str, str]] = []

    def get(url: str) -> tuple[int, str]:
        return 200, json.dumps({"now": "2026-10-10T09:00:00Z", "offset_seconds": 0})

    def play(base_url: str, persona: Any) -> Any:
        played.append((base_url, persona.id))
        result = fake_result(persona.id)
        result.turns[2].replies[0].text = "Gracias por tu interés. Esto es otra respuesta."
        return result

    personas_before = (root / PERSONAS_JSON_REL).read_bytes()
    code, out, err = run_cli(
        capsys, "--record", "http://localhost:18109", "--root", str(root), get=get, play=play
    )
    assert (code, err) == (0, "")
    assert played == [("http://localhost:18109", "alpha_es")]  # the other two are not playable
    assert out.count("2026-10-10T09:00:00Z") == 1
    assert "alpha_es: ended=knockout elapsed=12.3s" in out
    corpus = read_json(root / CORPUS_REL)
    [run] = corpus["runs"]
    assert run["persona_id"] == "alpha_es"
    assert run["persona_digest"] == persona_entry(root, "alpha_es")["persona_digest"]
    assert run["turns"][2]["replies"][0]["text"] == (
        "Gracias por tu interés. Esto es otra respuesta."
    )
    stub = [e["text"] for e in corpus["entries"] if e["source"] == "stub"]
    assert "Gracias por tu interés. Esto es otra respuesta." in stub
    assert "Gracias por tu interés. Necesitamos licencia de manejo vigente." not in stub
    assert (root / PERSONAS_JSON_REL).read_bytes() == personas_before
    code, _, err = run_cli(capsys, "--check", "--root", str(root))
    assert (code, err) == (0, "")


def test_record_writes_nothing_when_a_persona_fails_to_play(
    root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    def get(url: str) -> tuple[int, str]:
        return 200, json.dumps({"now": "2026-10-10T09:00:00Z"})

    def play(base_url: str, persona: Any) -> Any:
        raise ConnectionError("stack went away")

    before = (root / CORPUS_REL).read_bytes()
    code, _, err = run_cli(
        capsys, "--record", "http://localhost:18109", "--root", str(root), get=get, play=play
    )
    assert code == 1
    assert "alpha_es" in err
    assert (root / CORPUS_REL).read_bytes() == before


def test_the_check_and_record_modes_exclude_each_other(
    root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    with pytest.raises(SystemExit) as stop:
        export_personas.main(["--check", "--record", "http://localhost:1", "--root", str(root)])
    assert stop.value.code == 2
    capsys.readouterr()


# ---- the corpus categories ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "interactive", "expected"),
    [
        ("¡Listo! 🎉 Ya tenemos todo.", None, {"emoji", "no_label"}),
        ("Gracias, mañana te escribimos.", None, {"non_ascii", "no_label"}),
        ("Thanks, that is all.", None, {"no_label"}),
        ("¿Cuál es tu nombre?", None, {"question", "non_ascii", "kind:full_name"}),
        ("Necesito tu nombre y apellido.", None, {"request", "kind:full_name"}),
        (
            "Which apps (Glovo, Uber Eats, etc.) have you used?",
            None,
            {"question", "etc_shield", "kind:experience_platforms"},
        ),
        (
            "How many years have you been on delivery apps?",
            None,
            {"question", "duration_vs_platform", "kind:experience_years"},
        ),
        (
            "How long have you worked?",
            None,
            {"question", "kind:experience_years"},
        ),
        (
            "Choose your availability",
            {"options": [{"id": "full_time", "label": "Full time"}]},
            {"option_ids", "kind:availability"},
        ),
    ],
)
def test_each_category_detector(
    text: str, interactive: dict[str, Any] | None, expected: set[str]
) -> None:
    label = sim("sim.detect").detect_prompt(text, interactive)
    assert export_personas.entry_categories(text, interactive, label) == expected


def test_a_question_with_a_request_after_it_counts_as_a_question() -> None:
    text = "¿Me ayudas con un dato más? Necesito tu zona."
    label = sim("sim.detect").detect_prompt(text, None)
    assert label == "zone"
    assert "question" in export_personas.entry_categories(text, None, label)
    assert "request" not in export_personas.entry_categories(text, None, label)


def test_missing_categories_counts_what_is_short() -> None:
    assert ("no_label", 5, 0) in export_personas.missing_categories([])
    assert ("kind:zone", 3, 0) in export_personas.missing_categories([])
    assert ("emoji", 1, 0) in export_personas.missing_categories([])


def test_the_hand_phrasings_meet_every_category() -> None:
    entries = export_personas.build_entries([])
    hand = [e for e in entries if e["source"] == "hand"]
    assert len(hand) >= 40
    assert export_personas.missing_categories(entries) == []
    detect = sim("sim.detect")
    languages = [detect.guess_language(e["text"]) for e in hand]
    assert languages.count("es") >= 15
    assert languages.count("en") >= 15


def test_every_label_comes_from_detect_prompt() -> None:
    detect = sim("sim.detect")
    for entry in export_personas.build_entries([]):
        assert entry["label"] == detect.detect_prompt(entry["text"], entry["interactive"])
        assert set(entry) == {"source", "text", "interactive", "label"}


def test_the_hand_phrasings_have_no_duplicates() -> None:
    hand = [e for e in export_personas.build_entries([]) if e["source"] == "hand"]
    keys = [(e["text"], json.dumps(e["interactive"], sort_keys=True)) for e in hand]
    assert len(keys) == len(set(keys))


# ---- the recording's driver -----------------------------------------------------------------

REACTS = """\
id: reacts_es
description: Says one more line at the start and moves no time.
identity:
  name: Rita Reacts
  phone: "+34600000007"
  country: ES
  locale: es-ES
opening: {text: Hola}
interrupts:
  - {at: start, say: {text: buenas}}
expect: {stage: greeting, status: open}
"""


@pytest.mark.parametrize(
    "call",
    [
        ("advance", ("1h",)),
        ("advance_to_local_time", ("14:00", "ES")),
        ("advance_steps", (["1h"],)),
        ("advance_to", (None,)),
        ("state", ()),
        ("now", ()),
        ("reset", ()),
    ],
)
def test_the_frozen_clock_refuses_every_call(call: tuple[str, tuple[Any, ...]]) -> None:
    name, args = call
    clock = export_personas.FrozenClock()
    with pytest.raises(export_personas.ClockRefused, match=name):
        asyncio.run(getattr(clock, name)(*args))


def test_the_recording_driver_gets_the_frozen_clock_and_the_agreed_timeouts() -> None:
    import httpx

    driver = export_personas.build_driver(httpx.AsyncClient(base_url="http://unused.invalid"))
    assert isinstance(driver.clock, export_personas.FrozenClock)
    assert driver.reply_timeout_s == 60
    assert driver.poll_wait_s == 5
    assert driver.chat is not None


def test_a_persona_with_an_interrupt_and_no_advance_plays_without_a_real_clock(
    tmp_path: Path,
) -> None:
    """driver.py asks for a clock before any interrupt, even one that advances nothing."""
    import httpx

    fake_module = sim("sim.fake_webchat")
    fake = fake_module.FakeWebChat()
    write(tmp_path / "reacts_es.yaml", REACTS)
    persona = export_personas.load_sim().loader.load_persona(tmp_path / "reacts_es.yaml")
    result = export_personas.play_with_driver(
        "http://fake", persona, transport=httpx.ASGITransport(app=fake.app)
    )
    assert result.persona_id == "reacts_es"
    assert result.turns[0].sent.purpose == "interrupt:start"
    assert result.turns[0].sent.text == "buenas"
    assert result.clock_steps == []
    assert result.phone != persona.identity.phone
    assert result.phone.startswith("+346")
