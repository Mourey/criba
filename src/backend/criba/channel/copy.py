"""Fixed Candidate-facing copy sent from code, not written by the agent.

SYNTHETIC: every string in this module is placeholder wording written by us. Grupo Sazón
supplies the real copy, and the WhatsApp templates need Meta's approval, before any of it
reaches a real Candidate. Replace the strings here; callers look them up by key.

Stalled ladder (Phase 1 detailed spec section 4.1), keyed by the scheduler's
``NudgeRequest.kind``:

- ``nudge`` at 1 hour: free-form, points back to the pending question;
- ``last_nudge`` at 20 hours: the last free-form message inside WhatsApp's 24-hour
  window, "we'll keep your place";
- ``template`` at 48 hours: the ``stalled_resume`` template, "want to pick up where you
  left off?".

Rules (section 6): one or two short sentences in messaging register, *tú*, no restating
the Candidate's answers (the copy refers to "the last question" generically), and an easy
way to stop. Replying STOP or BAJA is recognised by code (:mod:`criba.channel.optout`)
and marks the Candidate Opted out (section 4.2, "stop messaging me"); the reply is
:data:`OPT_OUT_CONFIRMATION`.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Final, Literal

SYNTHETIC: Final = True  # see the module docstring; flip only when the real copy lands

Language = Literal["es-ES", "es-MX", "en"]
LANGUAGES: Final[tuple[Language, ...]] = ("es-ES", "es-MX", "en")
NudgeCopyKind = Literal["nudge", "last_nudge", "template"]

# SYNTHETIC copy, keyed by nudge kind, then language.
NUDGE_COPY: Final[dict[str, dict[str, str]]] = {
    # SYNTHETIC: nudge 1, 1 hour after the Candidate's last message.
    "nudge": {
        "es-ES": (
            "¿Sigues por ahí? Te queda poco: contesta la última pregunta cuando puedas y "
            "seguimos (responde STOP si prefieres que no te escribamos)."
        ),
        "es-MX": (
            "¿Sigues por ahí? Ya casi terminamos: contesta la última pregunta cuando puedas "
            "y seguimos (responde STOP si prefieres que no te escribamos)."
        ),
        "en": (
            "Still there? You're nearly done: answer the last question when you can and "
            "we'll carry on (reply STOP if you'd rather we didn't message you)."
        ),
    },
    # SYNTHETIC: last free-form nudge, 20 hours after the Candidate's last message.
    "last_nudge": {
        "es-ES": (
            "Te guardamos el sitio: cuando quieras, contesta la última pregunta y seguimos "
            "donde lo dejamos. Responde STOP si prefieres que no te escribamos."
        ),
        "es-MX": (
            "Te guardamos tu lugar: cuando quieras, contesta la última pregunta y seguimos "
            "donde nos quedamos. Responde STOP si prefieres que no te escribamos."
        ),
        "en": (
            "We'll keep your place: whenever you're ready, answer the last question and "
            "we'll pick up where we left off. Reply STOP if you'd rather we didn't message you."
        ),
    },
    # SYNTHETIC: body of the `stalled_resume` WhatsApp template, 48 hours after.
    "template": {
        "es-ES": (
            "¡Hola! ¿Quieres retomar tu solicitud de repartidor donde la dejaste? Contesta a "
            "este mensaje y seguimos, o responde STOP si prefieres que no te escribamos."
        ),
        "es-MX": (
            "¡Hola! ¿Quieres retomar tu solicitud de repartidor donde la dejaste? Contesta "
            "este mensaje y seguimos, o responde STOP si prefieres que no te escribamos."
        ),
        "en": (
            "Hi! Want to pick up your delivery driver application where you left off? Reply "
            "to this message and we'll continue, or reply STOP if you'd rather we didn't "
            "message you."
        ),
    },
}

# SYNTHETIC: the one reply to a STOP / BAJA message. It answers the Candidate's own
# message, so it is allowed although they are now Opted out.
OPT_OUT_CONFIRMATION: Final[dict[str, str]] = {
    "es-ES": "Hecho, no te enviaremos más mensajes. Si cambias de idea, escríbenos cuando quieras.",
    "es-MX": (
        "Listo, ya no te enviaremos más mensajes. Si cambias de opinión, escríbenos cuando quieras."
    ),
    "en": "Done, we won't message you again. If you change your mind, write to us any time.",
}

_COUNTRY_LANGUAGE: Final[dict[str, Language]] = {"ES": "es-ES", "MX": "es-MX"}


def copy_language(language: str | None, country: str | None = None) -> Language:
    """The copy variant for a Candidate: their language, else their country's Spanish,
    else Spain's."""
    if language in LANGUAGES:
        return language
    return _COUNTRY_LANGUAGE.get(country or "", "es-ES")


def nudge_text(kind: str, language: str | None, country: str | None = None) -> str:
    """The SYNTHETIC copy for one step of the Stalled ladder. Raises KeyError for an
    unknown kind."""
    return NUDGE_COPY[kind][copy_language(language, country)]


# SYNTHETIC: the confirmation of a spoken opt-out (owner decision 64): nothing about writing.
# Spoken agents speak `es` only (parent R18), so the `en` key holds Spain's Spanish line.
_SPOKEN_OPT_OUT_ES: Final = (
    "Hecho, no te contactaremos más. Si cambias de idea, vuelve a contactarnos cuando quieras."
)
SPOKEN_OPT_OUT_CONFIRMATION: Final[dict[str, str]] = {
    "es-ES": _SPOKEN_OPT_OUT_ES,
    "es-MX": (
        "Listo, no te contactaremos más. Si cambias de opinión, vuelve a contactarnos cuando "
        "quieras."
    ),
    "en": _SPOKEN_OPT_OUT_ES,
}


def opt_out_confirmation(
    language: str | None, country: str | None = None, *, spoken: bool = False
) -> str:
    """The SYNTHETIC confirmation of an opt-out, in the Candidate's language (``spoken``: the
    line for a turn of a voice call)."""
    table = SPOKEN_OPT_OUT_CONFIRMATION if spoken else OPT_OUT_CONFIRMATION
    return table[copy_language(language, country)]


# SYNTHETIC: the Qualified close that `finish_screening` returns for the agent to send as given.
# Built from the stored language so the last message cannot disagree with the record. ``{name}``
# is ", <first name>" or empty.
QUALIFIED_CLOSE: Final[dict[str, str]] = {
    "es-ES": (
        "¡Listo{name}! Un reclutador de Grupo Sazón revisará tu solicitud y te escribirá por aquí."
    ),
    "es-MX": (
        "¡Listo{name}! Un reclutador de Grupo Sazón revisará tu solicitud y te escribirá por aquí."
    ),
    "en": (
        "All set{name}! A Grupo Sazón recruiter will review your application and write to you here."
    ),
}


# SYNTHETIC: the Qualified close on a spoken turn (owner decision 64): nothing about writing or
# "here". Spoken agents speak `es` only, so every key holds the Spanish line.
SPOKEN_QUALIFIED_CLOSE: Final[dict[str, str]] = {
    key: (
        "¡Listo{name}! Un reclutador de Grupo Sazón revisará tu solicitud y se pondrá en "
        "contacto contigo."
    )
    for key in LANGUAGES
}


def qualified_close(
    language: str | None,
    country: str | None = None,
    first_name: str | None = None,
    *,
    spoken: bool = False,
) -> str:
    """The SYNTHETIC Qualified close, in the Candidate's stored language (``spoken``: the line
    for a turn of a voice call)."""
    name = f", {first_name}" if first_name else ""
    table = SPOKEN_QUALIFIED_CLOSE if spoken else QUALIFIED_CLOSE
    return table[copy_language(language, country)].format(name=name)


# SYNTHETIC: the answer to a tap on "other time" after Approval (ADR 0064): the booking page, which
# shows every open slot. ``{booking_url}`` is the one booking-link format (ADR 0040). The router
# makes sure it is in the chat whatever the agent writes.
BOOKING_LINK_MESSAGE: Final[dict[str, str]] = {
    "es-ES": "Claro, aquí puedes ver todos los horarios libres y elegir el que mejor te venga: "
    "{booking_url}",
    "es-MX": "Claro, aquí puedes ver todos los horarios libres y elegir el que mejor te acomode: "
    "{booking_url}",
    "en": "Of course, here you can see every open time and pick the one that suits you: "
    "{booking_url}",
}


def booking_link_message(booking_url: str, language: str | None, country: str | None = None) -> str:
    """The SYNTHETIC "other time" reply with the booking link, in the Candidate's language."""
    return BOOKING_LINK_MESSAGE[copy_language(language, country)].format(booking_url=booking_url)


# SYNTHETIC: said once, in the first message of a conversation that does not already say it
# (ADR 0044). The agent is told to disclose; this is the guarantee.
AI_DISCLOSURE: Final[dict[str, str]] = {
    "es-ES": "Soy un asistente virtual (IA) de Grupo Sazón.",
    "es-MX": "Soy un asistente virtual (IA) de Grupo Sazón.",
    "en": "I'm Grupo Sazón's virtual (AI) assistant.",
}

# "IA"/"AI", "inteligencia artificial", "bot": what makes a message say it is not a person.
# "asistente virtual" alone does not (the persona name of a human-sounding assistant would).
_AI_WORDS: Final = re.compile(
    r"\b(?:ia|ai)\b|inteligencia artificial|artificial intelligence|\bchat ?bot\b|\bbot\b",
    re.IGNORECASE,
)
_VIRTUAL_ES: Final = re.compile(r"\basistente virtual\b(?! \((?:IA|AI)\))", re.IGNORECASE)
_VIRTUAL_EN: Final = re.compile(r"\bvirtual assistant\b", re.IGNORECASE)


def discloses_ai(text: str) -> bool:
    """Whether ``text`` says the sender is an AI (or a bot)."""
    return _AI_WORDS.search(text) is not None


def with_ai_disclosure(
    text: str,
    earlier: Iterable[str],
    *,
    language: str | None = None,
    country: str | None = None,
) -> str:
    """``text`` as it should go out, given the conversation's ``earlier`` outbound messages.

    Unchanged when one of them, or ``text`` itself, already discloses. Otherwise the existing
    "asistente virtual" / "virtual assistant" gets its "(IA)" / "(AI)", or, with no such
    phrase, the disclosure sentence leads the message.
    """
    if discloses_ai(text) or any(discloses_ai(body) for body in earlier):
        return text
    patched, hits = _VIRTUAL_ES.subn(lambda m: f"{m.group(0)} (IA)", text, count=1)
    if not hits:
        patched, hits = _VIRTUAL_EN.subn("virtual (AI) assistant", text, count=1)
    if hits:
        return patched
    return f"{AI_DISCLOSURE[copy_language(language, country)]} {text}"


# SYNTHETIC: the privacy notice link, in the first message of a conversation that does not already
# carry it (ADR 0068). The agent is told to link it; this is the guarantee, as for the disclosure.
PRIVACY_URL: Final = "https://empleo.gruposazon.example/privacidad"
PRIVACY_NOTICE: Final[dict[str, str]] = {
    "es-ES": "Tus datos se tratan según nuestra política de privacidad: {url}",
    "es-MX": "Tus datos se tratan según nuestro aviso de privacidad: {url}",
    "en": "Your data is handled under our privacy notice: {url}",
}


def with_privacy_notice(
    text: str,
    earlier: Iterable[str],
    *,
    language: str | None = None,
    country: str | None = None,
) -> str:
    """``text`` as it should go out, given the conversation's ``earlier`` outbound messages.

    Unchanged when one of them, or ``text`` itself, already carries the privacy notice link.
    Otherwise a line with the notice and the link follows the text.
    """
    if PRIVACY_URL in text or any(PRIVACY_URL in body for body in earlier):
        return text
    notice = PRIVACY_NOTICE[copy_language(language, country)].format(url=PRIVACY_URL)
    return f"{text}\n{notice}"


# SYNTHETIC: the Zone funnel (ADR 0068). A Candidate's place matched no Zone, so the backend asks
# once which of our cities they can work in; the cities are the ones with a Service Area in the
# Application's country (the list is derived from the seed, so ``{cities}`` is never typed here).
SERVICE_CITIES_QUESTION: Final[dict[str, str]] = {
    "es-ES": "Contratamos repartidores en estas ciudades: {cities}. ¿Puedes trabajar en alguna?",
    "es-MX": "Contratamos repartidores en estas ciudades: {cities}. ¿Puedes trabajar en alguna?",
    "en": "We hire delivery drivers in these cities: {cities}. Can you work in one of them?",
}
NO_SERVICE_CITY_LABEL: Final[dict[str, str]] = {
    "es-ES": "Ninguna de estas",
    "es-MX": "Ninguna de estas",
    "en": "None of these",
}


def service_cities_question(
    cities: Iterable[str], language: str | None, country: str | None = None
) -> str:
    """The SYNTHETIC funnel question naming ``cities``, in the Candidate's language."""
    return SERVICE_CITIES_QUESTION[copy_language(language, country)].format(
        cities=", ".join(cities)
    )


def no_service_city_label(language: str | None, country: str | None = None) -> str:
    """The label of the funnel's "none of these" option, in the Candidate's language."""
    return NO_SERVICE_CITY_LABEL[copy_language(language, country)]


# --- spoken copy (spec voice-agent session-start SE10, SE15) -------------------------------
# Spoken agents speak `es` only (parent R18), so every key holds the Spanish line that is
# actually spoken; the `en` key repeats Spain's, so the stored line equals what was said.

# SYNTHETIC: a short spoken greeting after the disclosure, the agents' `first_message`.
_SPOKEN_GREETING: Final = "¡Hola! Te ayudo con tu solicitud de repartidor."

# SYNTHETIC: the first thing a voice call says, stored in the chat when the session is minted
# (parent R17). The country's AI_DISCLOSURE line, then the greeting.
SPOKEN_DISCLOSURE: Final[dict[str, str]] = {
    "es-ES": f"{AI_DISCLOSURE['es-ES']} {_SPOKEN_GREETING}",
    "es-MX": f"{AI_DISCLOSURE['es-MX']} {_SPOKEN_GREETING}",
    "en": f"{AI_DISCLOSURE['es-ES']} {_SPOKEN_GREETING}",
}

# SYNTHETIC: spoken when a Takeover ends the call (parent R16). A voice call has no chat to
# continue in (ADR 0059).
SPOKEN_HANDOVER: Final[dict[str, str]] = {
    key: "Una persona del equipo de Grupo Sazón se pondrá en contacto contigo." for key in LANGUAGES
}

# SYNTHETIC: spoken when a yes/no answer was not understood (parent R14).
SPOKEN_REASK: Final[dict[str, str]] = {
    key: "Perdona, no te he oído bien. ¿Es sí o no?" for key in LANGUAGES
}


def spoken_disclosure(language: str | None, country: str | None = None) -> str:
    """The SYNTHETIC spoken first message: the AI disclosure and a short greeting."""
    return SPOKEN_DISCLOSURE[copy_language(language, country)]


def spoken_handover(language: str | None, country: str | None = None) -> str:
    """The SYNTHETIC line spoken before a Takeover ends the call."""
    return SPOKEN_HANDOVER[copy_language(language, country)]


def spoken_reask(language: str | None, country: str | None = None) -> str:
    """The SYNTHETIC re-ask for a yes/no answer that was not understood."""
    return SPOKEN_REASK[copy_language(language, country)]


# SYNTHETIC: sent by the router, in code, at the third unreadable message in a row (ADR 0063),
# when the conversation passes to the Recruiters. The agent does not reply after it.
UNREADABLE_HANDOVER: Final[dict[str, str]] = {
    "es-ES": (
        "No consigo entender tus mensajes. Una persona del equipo de Grupo Sazón te escribirá "
        "por aquí."
    ),
    "es-MX": (
        "No logro entender tus mensajes. Una persona del equipo de Grupo Sazón te escribirá "
        "por aquí."
    ),
    "en": (
        "I can't understand your messages. Someone from the Grupo Sazón team will write to you "
        "here."
    ),
}


def unreadable_handover(language: str | None, country: str | None = None) -> str:
    """The SYNTHETIC message sent when unreadable messages hand the chat to the Recruiters."""
    return UNREADABLE_HANDOVER[copy_language(language, country)]


# SYNTHETIC: the reply to the first message a Candidate writes after a Knockout, while the
# Application is still closed (ADR 0072). It says why it is closed and what they can do: give the
# failed answer again (ADR 0033, within the 30-day Reopen window) or ask for a Review. Keyed by
# the Knockout criterion; `None` covers a Knockout with no criterion recorded.
_REVIEW_ES = 'Si crees que hay un error, responde "revisión" y una persona lo verá.'
_REVIEW_EN = 'If you think this is a mistake, reply "review" and a person will look at it.'
KNOCKOUT_CLOSED: Final[dict[str | None, dict[str, str]]] = {
    "driver_license": {
        "es-ES": (
            "Tu solicitud está cerrada: para repartir hace falta carnet de conducir en vigor. "
            "Si ya lo tienes, dímelo y seguimos donde lo dejamos. " + _REVIEW_ES
        ),
        "es-MX": (
            "Tu solicitud está cerrada: para repartir se necesita licencia de manejo vigente. "
            "Si ya la tienes, dime y seguimos donde lo dejamos. " + _REVIEW_ES
        ),
        "en": (
            "Your application is closed: delivering needs a valid driving licence. If you have "
            "one now, tell me and we'll carry on where we left off. " + _REVIEW_EN
        ),
    },
    "zone": {
        "es-ES": (
            "Tu solicitud está cerrada: por ahora no repartimos en tu zona. Si te mudas, dime tu "
            "barrio y ciudad y seguimos donde lo dejamos. " + _REVIEW_ES
        ),
        "es-MX": (
            "Tu solicitud está cerrada: por ahora no repartimos en tu zona. Si te cambias de "
            "zona, dime tu colonia y ciudad y seguimos donde lo dejamos. " + _REVIEW_ES
        ),
        "en": (
            "Your application is closed: we don't deliver in your area for now. If you move, "
            "tell me your neighbourhood and city and we'll carry on where we left off. "
            + _REVIEW_EN
        ),
    },
    None: {
        "es-ES": "Tu solicitud está cerrada. " + _REVIEW_ES,
        "es-MX": "Tu solicitud está cerrada. " + _REVIEW_ES,
        "en": "Your application is closed. " + _REVIEW_EN,
    },
}


def knockout_closed(criterion: str | None, language: str | None, country: str | None = None) -> str:
    """The SYNTHETIC reply to the first message after a Knockout, for its ``criterion``."""
    by_language = KNOCKOUT_CLOSED.get(criterion) or KNOCKOUT_CLOSED[None]
    return by_language[copy_language(language, country)]


# SYNTHETIC: the line that opens the first reply of a new Application when the Candidate's
# previous one ended Withdrawn (ADR 0072): they are told a new Application starts, not left to
# wonder why the questions begin again.
WELCOME_BACK: Final[dict[str, str]] = {
    "es-ES": "¡Qué bien que vuelvas! Como retiraste tu solicitud anterior, empezamos una nueva.",
    "es-MX": "¡Qué bien que regresas! Como retiraste tu solicitud anterior, empezamos una nueva.",
    "en": "Good to have you back! You withdrew your last application, so we'll start a new one.",
}


def welcome_back(language: str | None, country: str | None = None) -> str:
    """The SYNTHETIC welcome-back line, in the Candidate's language."""
    return WELCOME_BACK[copy_language(language, country)]
