#!/usr/bin/env python3
"""Export the sim personas for the Demo stage, and keep the detector corpus.

Writes two committed files from `src/tests/sim/personas/*.yaml` and the Python prompt detector:

- `src/web/src/stage/personas.json`: what the browser player needs to make the Python driver's
  choices (identity, opening, answers, zone choice, confirm list, interrupts, the end state),
  without the parts that only steer the stub model server-side (`value`, `query`, `field`).
- `src/web/tests/fixtures/detect-corpus.json`: agent messages with the label the Python
  `detect_prompt` gives each one (`entries`, hand-written real-model phrasings plus the stub's own
  turns), and one recorded run per playable persona (`runs`). `detect.ts` and `playback.ts` are
  tested against it.

Three modes, one option:

    export_personas.py                  write both files; keep the recorded runs
    export_personas.py --check          offline; exit 1 with one line per cause of drift
    export_personas.py --record URL     play every playable persona against a sim stack and
                                        rewrite `runs` and the stub entries
    --root DIR                          read and write another layout (the tests use it)

The schema, the loader and the detector are always the ones of this checkout: `src/tests` is put on
`sys.path` from this file's location, never from `--root`, so `--root` redirects only the data.
Nothing under `src/tests` is edited. The output is deterministic and holds no time, message id,
conversation id, phone or elapsed time that a run produced.
"""

from __future__ import annotations

import argparse
import asyncio
import functools
import hashlib
import importlib
import json
import sys
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
PERSONAS_REL = Path("src/tests/sim/personas")
PERSONAS_JSON_REL = Path("src/web/src/stage/personas.json")
CORPUS_REL = Path("src/web/tests/fixtures/detect-corpus.json")
CLOCK_PATH = "/api/sim/clock"

# A kind needs this many entries, so a regex change that loses one is not enough to hide it.
MIN_PER_KIND = 3
MIN_NO_LABEL = 5

HttpGet = Callable[[str], tuple[int, str]]
Play = Callable[[str, Any], Any]


class RecordRefused(RuntimeError):
    """`--record` will not start: the stack is not a sim stack, or does not answer."""


# ---- the sim package, read only ---------------------------------------------------------------


@functools.cache
def load_sim() -> SimpleNamespace:
    """Import the schema, loader and detector from this checkout's `src/tests`."""
    tests = str(REPO_ROOT / "src" / "tests")
    if tests not in sys.path:
        sys.path.insert(0, tests)
    detect = importlib.import_module("sim.detect")
    loader = importlib.import_module("sim.personas.loader")
    schema = importlib.import_module("sim.personas.schema")
    return SimpleNamespace(
        detect=detect,
        loader=loader,
        FIELD_ORDER=schema.FIELD_ORDER,
        PersonaError=loader.PersonaError,
    )


# ---- JSON -------------------------------------------------------------------------------------


def dumps(value: Any) -> str:
    """The one canonical form: sorted keys, two-space indent, no escaping, a trailing newline."""
    return json.dumps(value, sort_keys=True, indent=2, ensure_ascii=False) + "\n"


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(dumps(value).encode("utf-8"))


def digest_of(entry: dict[str, Any]) -> str:
    """SHA-256 of a persona's exported JSON (without the digest itself)."""
    body = {k: v for k, v in entry.items() if k != "persona_digest"}
    return hashlib.sha256(dumps(body).encode("utf-8")).hexdigest()


# ---- persona export ---------------------------------------------------------------------------


def _reply(reply: Any) -> dict[str, Any]:
    """A Candidate message as the player sends it (`value`, `query`, `field`, `media` stay out)."""
    return {
        "text": reply.text,
        "lang": reply.lang,
        "pick_option": reply.pick_option,
        "covers": list(reply.covers),
    }


def _confirm_reply(reply: Any) -> dict[str, Any]:
    """A confirm reply. The driver remembers a correction by its `field` and picks a corrected Zone
    option by the words of its `query` (driver.py `_next_reply`, `_zone_pick`), so a correction
    keeps both, under the names `corrects` and `hint`."""
    out = _reply(reply)
    out["corrects"] = reply.field
    out["hint"] = reply.query if reply.field is not None else None
    return out


def _unplayable_reason(persona: Any) -> str | None:
    """Why the browser cannot play this persona, or None. The player moves no clock and sends no
    non-text message."""
    for interrupt in persona.interrupts:
        if interrupt.advance:
            return f"moves the sim clock (interrupt at {interrupt.at})"
    places: list[tuple[str, Any]] = [("opening", persona.opening)]
    places += [(f"answers.{f}", r) for f, replies in persona.answers.items() for r in replies]
    places += [(f"interrupts.{i.at}", i.say) for i in persona.interrupts if i.say is not None]
    places += [("confirm", r) for r in persona.confirm]
    for where, reply in places:
        if reply.media is not None:
            return f"sends a {reply.media} ({where})"
    return None


def export_persona(persona: Any) -> dict[str, Any]:
    ident = persona.identity
    expect = persona.expect
    reason = _unplayable_reason(persona)
    entry: dict[str, Any] = {
        "id": persona.id,
        "identity": {
            "name": ident.name,
            "phone": ident.phone,
            "country": ident.country,
            "locale": ident.locale,
            "entry": ident.entry,
        },
        "opening": _reply(persona.opening),
        "answers": {f: [_reply(r) for r in replies] for f, replies in persona.answers.items()},
        "zone_choice": persona.zone_choice,
        "confirm": [_confirm_reply(r) for r in persona.confirm],
        "interrupts": [
            {
                "at": i.at,
                "advance": list(i.advance),
                "say": _reply(i.say) if i.say is not None else None,
                "then": i.then,
            }
            for i in persona.interrupts
        ],
        "expect": {
            "stage": expect.stage,
            "status": expect.status,
            "outcome": expect.outcome,
            "events": list(expect.events),
        },
        "playable": reason is None,
        "unplayable_reason": reason,
    }
    entry["persona_digest"] = digest_of(entry)
    return entry


def load_personas(root: Path) -> dict[str, Any]:
    return load_sim().loader.load_personas(root / PERSONAS_REL)


def export_all(root: Path) -> dict[str, Any]:
    personas = load_personas(root)
    return {
        "fieldOrder": list(load_sim().FIELD_ORDER),
        "personas": [export_persona(personas[pid]) for pid in sorted(personas)],
    }


# ---- the corpus -------------------------------------------------------------------------------

# Hand-written phrasings, as a real model might ask them. Each is (text, interactive options or
# None). The labels are never written here: `detect_prompt` gives them. The set covers a question
# and a statement per field, "etc." inside a list of apps, a Knockout that mentions the license
# without asking, a duration question that names apps, a summary question with extra sentences,
# upper-case and accented variants, an emoji and non-ASCII letters, in Spanish and English.
_ZONES = [
    {"id": "zone_angelopolis", "label": "Angelópolis, Puebla"},
    {"id": "zone_centro", "label": "Centro, Puebla"},
    {"id": "zone_la_paz", "label": "La Paz, Puebla"},
]
_AVAILABILITY = [
    {"id": "full_time", "label": "Tiempo completo"},
    {"id": "part_time", "label": "Medio tiempo"},
    {"id": "weekends", "label": "Fines de semana"},
]
_SCHEDULE = [
    {"id": "morning", "label": "Mañana"},
    {"id": "afternoon", "label": "Tarde"},
    {"id": "evening", "label": "Noche"},
    {"id": "flexible", "label": "Flexible"},
]
_PLATFORMS = [
    {"id": "glovo", "label": "Glovo"},
    {"id": "uber_eats", "label": "Uber Eats"},
    {"id": "rappi", "label": "Rappi"},
    {"id": "none", "label": "Ninguna"},
]
_DATES = [
    {"id": "2026-10-12", "label": "lun 12 oct"},
    {"id": "2026-10-13", "label": "mar 13 oct"},
]
_YES_NO = [{"id": "yes", "label": "Sí"}, {"id": "no", "label": "No"}]

HAND_PHRASINGS: tuple[tuple[str, list[dict[str, str]] | None], ...] = (
    # full_name
    ("¡Hola! Soy el asistente virtual de Grupo Sazón. ¿Cómo te llamas?", None),
    ("Hi! I'm the virtual assistant for Grupo Sazón. What's your full name?", None),
    ("Necesito tu nombre y apellido.", None),
    ("Please tell me your name.", None),
    ("¿CUÁL ES TU NOMBRE COMPLETO?", None),
    # zone
    ("¿En qué ciudad o colonia vives?", None),
    ("Which city or neighborhood do you live in?", None),
    ("Dime en qué colonia vives.", None),
    ("Tell me your city.", None),
    ("¿Me ayudas con un dato más? Necesito tu zona.", None),
    ("Encontré varias zonas con ese nombre. ¿Cuál es la tuya?", _ZONES),
    # driver_license
    ("¿Tienes licencia de conducir vigente?", None),
    ("Do you have a valid driver's license?", None),
    ("Cuéntame, ¿cuentas con permiso de manejo?", None),
    ("Please confirm you hold a valid driving license.", None),
    ("Anotado, gracias. Y una cosa más: ¿tienes carnet de conducir?", _YES_NO),
    # availability
    ("¿Qué disponibilidad tienes: tiempo completo, medio tiempo o fines de semana?", None),
    ("What's your availability: full-time, part-time or weekends?", None),
    ("Elige la disponibilidad que mejor te encaje.", _AVAILABILITY),
    ("¿Podrías trabajar jornada completa?", None),
    # preferred_schedule
    ("¿En qué horario prefieres trabajar?", None),
    ("Which shift do you prefer: morning, afternoon or evening?", None),
    ("Anotado: licencia vigente. ¿Qué turno te viene mejor?", None),
    ("Choose the schedule you prefer.", _SCHEDULE),
    # experience_years
    ("¿Cuánta experiencia tienes como repartidor?", None),
    ("How many years of experience do you have delivering?", None),
    ("¿Cuántos años llevas repartiendo?", None),
    ("¿Cuántos años de experiencia tienes en apps de reparto?", None),
    ("How many years have you been on delivery apps?", None),
    ("How long have you worked for apps like Glovo or Uber Eats?", None),
    # experience_platforms
    ("¿Con qué plataformas has trabajado (Glovo, Uber Eats, etc.)?", None),
    ("Which delivery apps have you used?", None),
    ("¿Qué aplicaciones de reparto usas actualmente?", None),
    ("Which apps have you used, and for how many years?", None),
    ("Selecciona las plataformas en las que has trabajado.", _PLATFORMS),
    # start_date
    ("¿Cuándo podrías empezar?", None),
    ("When could you start?", None),
    ("¿Qué fecha te viene bien para incorporarte?", None),
    ("Dime desde cuándo puedes empezar.", None),
    ("What day works for you to start? Please pick one.", _DATES),
    # confirm
    (
        "Resumen: Diego Martínez, Centro, Puebla, con licencia y tiempo completo. ¿Es correcto?",
        None,
    ),
    ("Here's your summary: name, zone, license, schedule. Does everything look right?", None),
    ("Todo listo con tus datos. ¿Todo bien?", None),
    ("Is all of this correct?", None),
    ("¿Es correcto este resumen? Si algo está mal, dímelo y lo corrijo.", None),
    ("Please confirm the summary is right.", None),
    # no label: statements and questions with no recognisable topic
    (
        "Gracias por tu interés. Para este puesto necesitamos licencia de manejo vigente. "
        "Escríbenos cuando la tengas.",
        None,
    ),
    (
        "Thanks for your interest. This role needs a valid driver's license, "
        "so we can't continue for now.",
        None,
    ),
    ("¡Gracias! Tu solicitud quedó registrada y el equipo la revisará pronto.", None),
    ("Thanks, that's everything. Our team will review your application and be in touch.", None),
    ("Hola, soy el asistente virtual de Grupo Sazón y te ayudo con tu solicitud.", None),
    ("¡Listo! 🎉 Ya tenemos todo lo que necesitamos.", None),
    ("¿Puedes repetirlo, por favor?", None),
    ("Sorry, I didn't get that. Could you say it again?", None),
    ("Entendido. Lamentamos que hayas cambiado de opinión. 👋", None),
)


def entry_categories(text: str, interactive: dict[str, Any] | None, label: str | None) -> set[str]:
    """The corpus categories one entry counts towards (see `missing_categories`)."""
    sim = load_sim()
    detect = sim.detect
    found: set[str] = {f"kind:{label}" if label else "no_label"}
    if interactive:
        found.add("option_ids")
    question = detect.last_question(text)
    if question is not None:
        found.add("question")
    elif detect.last_request(text) is not None:
        found.add("request")
    if "etc." in text:
        found.add("etc_shield")
    if detect.EMOJI_RE.search(text):
        found.add("emoji")
    if any(c.isalpha() and ord(c) > 127 for c in text):
        found.add("non_ascii")
    if label == "experience_years" and question is not None:
        platform = dict(detect._PROMPT_PATTERNS)["experience_platforms"]
        if platform.search(detect.normalize(question)):
            found.add("duration_vs_platform")
    return found


def required_categories() -> dict[str, int]:
    kinds = load_sim().detect.PromptKind.__args__
    need: dict[str, int] = {f"kind:{kind}": MIN_PER_KIND for kind in kinds}
    need["no_label"] = MIN_NO_LABEL
    for name in (
        "option_ids",
        "question",
        "request",
        "etc_shield",
        "duration_vs_platform",
        "non_ascii",
        "emoji",
    ):
        need[name] = 1
    return need


def missing_categories(entries: list[dict[str, Any]]) -> list[tuple[str, int, int]]:
    """Every required category the entries do not meet, as (name, needed, found)."""
    counts: dict[str, int] = {}
    for entry in entries:
        for name in entry_categories(entry["text"], entry["interactive"], entry["label"]):
            counts[name] = counts.get(name, 0) + 1
    return [
        (name, need, counts.get(name, 0))
        for name, need in required_categories().items()
        if counts.get(name, 0) < need
    ]


def _interactive(raw: dict[str, Any] | None) -> dict[str, Any] | None:
    """The part of an `interactive` block the detector and the player read: option ids, labels."""
    if not raw:
        return None
    options = [{"id": str(o["id"]), "label": str(o.get("label", ""))} for o in raw["options"]]
    return {"options": options}


def label_of(text: str, interactive: dict[str, Any] | None) -> str | None:
    return load_sim().detect.detect_prompt(text, interactive)


def _entry(source: str, text: str, interactive: dict[str, Any] | None) -> dict[str, Any]:
    return {
        "source": source,
        "text": text,
        "interactive": interactive,
        "label": label_of(text, interactive),
    }


def build_entries(runs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The hand phrasings, then every distinct agent message of the runs, each freshly labelled."""
    entries = [
        _entry("hand", text, _interactive({"options": o}) if o else None)
        for text, o in HAND_PHRASINGS
    ]
    seen: set[tuple[str, str]] = set()
    for run in runs:
        for turn in run["turns"]:
            for reply in turn["replies"]:
                key = (reply["text"], json.dumps(reply["interactive"], sort_keys=True))
                if key not in seen:
                    seen.add(key)
                    entries.append(_entry("stub", reply["text"], reply["interactive"]))
    return entries


def run_record(persona_digest: str, result: Any) -> dict[str, Any]:
    """What a driver run leaves in the corpus: the persona, the driver's end, and per turn what it
    sent and the agent messages it drew. No time, message id, conversation id, phone or elapsed
    time. A tap names the option and where its prompt sat: (turn index, reply index)."""
    replies_by_id: dict[int, tuple[int, int]] = {}
    turns: list[dict[str, Any]] = []
    for t_index, turn in enumerate(result.turns):
        sent = turn.sent
        tap = sent.reply_to
        if tap is None:
            sent_record = {
                "option_id": None,
                "prompt_index": None,
                "prompt_turn": None,
                "purpose": sent.purpose,
                "text": sent.text,
            }
        else:
            where = replies_by_id.get(tap["message_id"])
            if where is None:
                raise ValueError(
                    f"{result.persona_id}: a tap answers message {tap['message_id']}, "
                    "which no earlier turn drew"
                )
            sent_record = {
                "option_id": str(tap["option_id"]),
                "prompt_index": where[1],
                "prompt_turn": where[0],
                "purpose": sent.purpose,
                "text": None,
            }
        replies = []
        for r_index, reply in enumerate(turn.replies):
            if reply.id is not None:
                replies_by_id[reply.id] = (t_index, r_index)
            replies.append({"text": reply.text, "interactive": _interactive(reply.interactive)})
        turns.append({"sent": sent_record, "replies": replies})
    return {
        "persona_id": result.persona_id,
        "persona_digest": persona_digest,
        "ended": result.ended,
        "turns": turns,
    }


def build_corpus(runs: list[dict[str, Any]]) -> dict[str, Any]:
    return {"entries": build_entries(runs), "runs": runs}


def read_runs(root: Path) -> list[dict[str, Any]]:
    path = root / CORPUS_REL
    if not path.exists():
        return []
    return list(json.loads(path.read_text(encoding="utf-8")).get("runs", []))


# ---- modes ------------------------------------------------------------------------------------


def export(root: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    """Default mode: write both files, keeping the recorded runs."""
    doc = export_all(root)
    corpus = build_corpus(read_runs(root))
    write_json(root / PERSONAS_JSON_REL, doc)
    write_json(root / CORPUS_REL, corpus)
    return doc, corpus


def _check_personas(root: Path, fresh: dict[str, Any]) -> list[str]:
    path = root / PERSONAS_JSON_REL
    if not path.exists():
        return [f"{PERSONAS_JSON_REL.name}: missing (run export_personas.py)"]
    text = path.read_text(encoding="utf-8")
    try:
        current = json.loads(text)
    except ValueError:
        return [f"{PERSONAS_JSON_REL.name}: not valid JSON (run export_personas.py)"]
    causes: list[str] = []
    name = PERSONAS_JSON_REL.name
    if current.get("fieldOrder") != fresh["fieldOrder"]:
        causes.append(f"{name}: fieldOrder differs from FIELD_ORDER")
    have = {p.get("id"): p for p in current.get("personas", [])}
    want = {p["id"]: p for p in fresh["personas"]}
    for pid in sorted(want.keys() - have.keys(), key=str):
        causes.append(f"{name}: persona {pid} is missing")
    for pid in sorted(have.keys() - want.keys(), key=str):
        causes.append(f"{name}: persona {pid} has no YAML file")
    for pid in sorted(want.keys() & have.keys()):
        if have[pid] != want[pid]:
            causes.append(f"{name}: persona {pid} differs from its YAML export")
    if not causes and text != dumps(fresh):
        causes.append(f"{name}: not in canonical form (run export_personas.py)")
    return causes


def _check_corpus(root: Path, fresh: dict[str, Any]) -> list[str]:
    name = CORPUS_REL.name
    path = root / CORPUS_REL
    if not path.exists():
        return [f"{name}: missing (run export_personas.py)"]
    try:
        corpus = json.loads(path.read_text(encoding="utf-8"))
        entries = corpus["entries"]
        runs = corpus["runs"]
    except (ValueError, KeyError, TypeError):
        return [f"{name}: not valid JSON with entries and runs (run export_personas.py)"]
    causes: list[str] = []
    for index, entry in enumerate(entries):
        now = label_of(entry["text"], entry["interactive"])
        if entry["label"] != now:
            snippet = entry["text"][:40]
            causes.append(
                f"{name}: entry {index} label {entry['label']!r} but detect_prompt gives "
                f"{now!r} for {snippet!r}"
            )
    digests = {p["id"]: p["persona_digest"] for p in fresh["personas"]}
    for run in runs:
        pid = run["persona_id"]
        if pid not in digests:
            causes.append(f"{name}: run for persona {pid}, which has no YAML file")
        elif run["persona_digest"] != digests[pid]:
            causes.append(f"{name}: run {pid} persona_digest is stale (the persona changed)")
    for category, need, have in missing_categories(entries):
        causes.append(f"{name}: missing category {category} (need {need}, have {have})")
    return causes


def check(root: Path) -> list[str]:
    """Offline drift check. One line per cause; empty when nothing drifted."""
    fresh = export_all(root)
    return _check_personas(root, fresh) + _check_corpus(root, fresh)


def _http_get(url: str) -> tuple[int, str]:
    import httpx

    try:
        response = httpx.get(url, timeout=5.0)
    except httpx.HTTPError as exc:
        raise ConnectionError(str(exc)) from exc
    return response.status_code, response.text


def probe_sim_clock(base_url: str, get: HttpGet) -> str:
    """The sim clock's `now`, or RecordRefused when the stack is not a sim stack."""
    url = base_url.rstrip("/") + CLOCK_PATH
    try:
        status, body = get(url)
    except OSError as exc:
        raise RecordRefused(f"{url} is unreachable: {exc}") from exc
    if status != 200:
        raise RecordRefused(
            f"GET {url} answered {status}, not 200: this is not a sim stack "
            "(CRIBA_ENV=sim), so a recording could spend real model money"
        )
    try:
        return str(json.loads(body)["now"])
    except (ValueError, KeyError, TypeError) as exc:
        raise RecordRefused(f"GET {url} answered 200 but not with a sim clock") from exc


class ClockRefused(RuntimeError):
    """A recording tried to move the sim clock."""


class FrozenClock:
    """The clock a recording hands the driver.

    `PersonaDriver._need_clock` (driver.py) fails for any interrupt, even one with an empty
    `advance` list, when the driver has no clock. A recording plays only personas that never move
    time, so it passes an object that satisfies that check and refuses everything else: a bug that
    would move the clock fails loudly instead of changing the stack's time.
    """

    jobs_run = 0

    def __getattr__(self, name: str) -> Any:
        async def refuse(*args: Any, **kwargs: Any) -> Any:
            raise ClockRefused(f"a recording never moves the sim clock (called {name})")

        return refuse


def build_driver(http: Any) -> Any:
    """The driver a recording uses: a frozen clock, 60 s reply timeout, 5 s long-poll window."""
    sim_driver = importlib.import_module("sim.driver")
    return sim_driver.PersonaDriver(http, clock=FrozenClock(), reply_timeout_s=60, poll_wait_s=5)


def play_with_driver(base_url: str, persona: Any, *, transport: Any = None) -> Any:
    """Play one persona against `base_url`: a frozen clock, a fresh generated phone."""
    import httpx

    sim_stack = importlib.import_module("sim.stack")

    async def run() -> Any:
        async with httpx.AsyncClient(
            base_url=base_url, timeout=httpx.Timeout(15.0), transport=transport
        ) as http:
            driver = build_driver(http)
            phone = sim_stack.new_phone(persona.identity.country)
            return await driver.run(persona, phone=phone, align_clock=False)

    return asyncio.run(run())


def record(
    root: Path, base_url: str, *, get: HttpGet, play: Play, out: Callable[[str], None]
) -> list[dict[str, Any]]:
    """Play every playable persona and return the new runs. Writes nothing."""
    now = probe_sim_clock(base_url, get)
    out(f"sim clock now: {now}")
    doc = export_all(root)
    personas = load_personas(root)
    runs: list[dict[str, Any]] = []
    for entry in doc["personas"]:
        if not entry["playable"]:
            continue
        pid = entry["id"]
        try:
            result = play(base_url, personas[pid])
        except Exception as exc:
            raise RecordRefused(f"{pid}: the run failed: {exc}") from exc
        out(f"{pid}: ended={result.ended} elapsed={result.elapsed_s:.1f}s")
        runs.append(run_record(entry["persona_digest"], result))
    return runs


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Export the sim personas and the detector corpus for the Demo stage."
    )
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--check", action="store_true", help="offline; exit 1 on drift")
    modes.add_argument(
        "--record", metavar="BASE_URL", help="play the personas on a sim stack, rewrite the runs"
    )
    parser.add_argument(
        "--root", type=Path, default=REPO_ROOT, help="layout to read and write (default: repo)"
    )
    return parser


def main(
    argv: list[str] | None = None,
    *,
    get: HttpGet | None = None,
    play: Play | None = None,
) -> int:
    args = _parser().parse_args(argv)
    root: Path = args.root
    try:
        if args.check:
            causes = check(root)
            if causes:
                print("\n".join(causes), file=sys.stderr)
                return 1
            doc = export_all(root)
            entries = json.loads((root / CORPUS_REL).read_text(encoding="utf-8"))
            print(
                f"export_personas: ok ({len(doc['personas'])} personas, "
                f"{len(entries['entries'])} corpus entries, {len(entries['runs'])} runs)"
            )
            return 0
        if args.record:
            runs = record(
                root,
                args.record,
                get=get or _http_get,
                play=play or play_with_driver,
                out=print,
            )
            write_json(root / CORPUS_REL, build_corpus(runs))
            print(f"export_personas: recorded {len(runs)} runs")
            return 0
        doc, corpus = export(root)
        print(
            f"export_personas: wrote {len(doc['personas'])} personas, "
            f"{len(corpus['entries'])} corpus entries, {len(corpus['runs'])} runs"
        )
        return 0
    except RecordRefused as exc:
        print(f"export_personas: {exc}", file=sys.stderr)
        return 1
    except load_sim().PersonaError as exc:
        print(f"export_personas: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
