"""Knockout messages (ADR 0016), chosen by criterion and ``Knockout.reason``.

**SYNTHETIC placeholder wording**, pending Grupo Sazón's approved templates. Each
message carries the five parts ADR 0016 requires: the reason in plain words, why the
requirement exists, the requirements link, an invitation to write again, and how to
ask for a person to review the decision. One template per variant and language
(es-ES, es-MX, en); the agent sends ``message`` verbatim.
"""

from __future__ import annotations

SYNTHETIC = True
REQUIREMENTS_URL = "https://empleo.gruposazon.example/requisitos"

# Template variant per (criterion, Knockout.reason). Unknown reasons fall back to the
# criterion's base variant.
_VARIANT: dict[tuple[str, str], str] = {
    ("zone", "outside_service_areas"): "knockout_zone",
    ("driver_license", "no"): "knockout_driver_license",
    ("driver_license", "expired"): "knockout_driver_license_expired",
    ("driver_license", "suspended"): "knockout_driver_license_suspended",
    ("driver_license", "pending"): "knockout_driver_license_pending",
}
_BASE = {"zone": "knockout_zone", "driver_license": "knockout_driver_license"}

_ES_ES = {
    "knockout_zone": (
        "Gracias{name}. Por ahora no tenemos ningún local que reparta en {zone}, y para "
        "repartir hay que trabajar dentro del área de un local. Aquí tienes los requisitos: "
        "{url}. Si te mudas o abrimos local cerca, escríbenos y seguimos donde lo dejamos. "
        'Si crees que hay un error, responde "revisión" y una persona lo verá.'
    ),
    "knockout_driver_license": (
        "Gracias{name}. Por ahora necesitamos carnet de conducir en vigor: es un requisito "
        "legal y del seguro para repartir. Aquí tienes los requisitos: {url}. Cuando lo "
        "tengas, escríbenos y seguimos donde lo dejamos. Si crees que hay un error, "
        'responde "revisión" y una persona lo verá.'
    ),
    "knockout_driver_license_expired": (
        "Gracias{name}. Para repartir el carnet de conducir tiene que estar en vigor: es un "
        "requisito legal y del seguro. Aquí tienes los requisitos: {url}. Cuando lo "
        "renueves, escríbenos y seguimos donde lo dejamos. Si crees que hay un error, "
        'responde "revisión" y una persona lo verá.'
    ),
    "knockout_driver_license_suspended": (
        "Gracias{name}. Para repartir hace falta un carnet de conducir en vigor y sin "
        "suspensión: es un requisito legal y del seguro. Aquí tienes los requisitos: {url}. "
        "Cuando vuelvas a tenerlo, escríbenos y seguimos donde lo dejamos. Si crees que hay "
        'un error, responde "revisión" y una persona lo verá.'
    ),
    "knockout_driver_license_pending": (
        "Gracias{name}. Para empezar necesitamos que ya tengas el carnet de conducir: es un "
        "requisito legal y del seguro para repartir. Aquí tienes los requisitos: {url}. En "
        "cuanto lo tengas, escríbenos y seguimos donde lo dejamos. Si crees que hay un "
        'error, responde "revisión" y una persona lo verá.'
    ),
}

_ES_MX = {
    "knockout_zone": (
        "Gracias{name}. Por ahora no tenemos ninguna sucursal que reparta en {zone}, y para "
        "repartir hay que trabajar dentro del área de una sucursal. Aquí están los "
        "requisitos: {url}. Si te cambias de zona o abrimos sucursal cerca, escríbenos y "
        'seguimos donde lo dejamos. Si crees que hay un error, responde "revisión" y una '
        "persona lo verá."
    ),
    "knockout_driver_license": (
        "Gracias{name}. Por ahora necesitamos licencia de manejo vigente: es un requisito "
        "legal y del seguro para repartir. Aquí están los requisitos: {url}. Cuando la "
        "tengas, escríbenos y seguimos donde lo dejamos. Si crees que hay un error, "
        'responde "revisión" y una persona lo verá.'
    ),
    "knockout_driver_license_expired": (
        "Gracias{name}. Para repartir la licencia de manejo tiene que estar vigente: es un "
        "requisito legal y del seguro. Aquí están los requisitos: {url}. Cuando la "
        "renueves, escríbenos y seguimos donde lo dejamos. Si crees que hay un error, "
        'responde "revisión" y una persona lo verá.'
    ),
    "knockout_driver_license_suspended": (
        "Gracias{name}. Para repartir se necesita una licencia de manejo vigente y sin "
        "suspensión: es un requisito legal y del seguro. Aquí están los requisitos: {url}. "
        "Cuando vuelvas a tenerla, escríbenos y seguimos donde lo dejamos. Si crees que hay "
        'un error, responde "revisión" y una persona lo verá.'
    ),
    "knockout_driver_license_pending": (
        "Gracias{name}. Para empezar necesitamos que ya tengas la licencia de manejo: es un "
        "requisito legal y del seguro para repartir. Aquí están los requisitos: {url}. En "
        "cuanto la tengas, escríbenos y seguimos donde lo dejamos. Si crees que hay un "
        'error, responde "revisión" y una persona lo verá.'
    ),
}

_EN = {
    "knockout_zone": (
        "Thank you{name}. Right now none of our locations deliver in {zone}, and drivers "
        "need to work inside a location's delivery area. Here are the requirements: {url}. "
        "If you move or we open nearby, write to us and we'll pick up where we left off. "
        'If you think this is a mistake, reply "review" and a person will look at it.'
    ),
    "knockout_driver_license": (
        "Thank you{name}. For now we need a valid driving licence: it's a legal and "
        "insurance requirement for delivery. Here are the requirements: {url}. Once you "
        "have one, write to us and we'll pick up where we left off. If you think this is "
        'a mistake, reply "review" and a person will look at it.'
    ),
    "knockout_driver_license_expired": (
        "Thank you{name}. To deliver, your driving licence has to be valid: it's a legal "
        "and insurance requirement. Here are the requirements: {url}. Once you renew it, "
        "write to us and we'll pick up where we left off. If you think this is a mistake, "
        'reply "review" and a person will look at it.'
    ),
    "knockout_driver_license_suspended": (
        "Thank you{name}. To deliver, you need a valid driving licence that isn't "
        "suspended: it's a legal and insurance requirement. Here are the requirements: "
        "{url}. Once you have it back, write to us and we'll pick up where we left off. If "
        'you think this is a mistake, reply "review" and a person will look at it.'
    ),
    "knockout_driver_license_pending": (
        "Thank you{name}. To start, you need to already hold a driving licence: it's a "
        "legal and insurance requirement for delivery. Here are the requirements: {url}. "
        "As soon as you have it, write to us and we'll pick up where we left off. If you "
        'think this is a mistake, reply "review" and a person will look at it.'
    ),
}

TEMPLATES: dict[str, dict[str, str]] = {"es-ES": _ES_ES, "es-MX": _ES_MX, "en": _EN}

# Spoken variants (owner decision 64): the Candidate only hears them, so no link, no "write to us"
# and no "reply review"; the review is asked for by voice. Spoken agents speak `es` only (parent
# R18), so an `en` Candidate gets Spain's line, as the stored line must equal what was said.
_SPOKEN_REVIEW = "Si crees que es un error, dímelo y una persona del equipo lo revisará."

_SPOKEN_ES_ES = {
    "knockout_zone": (
        "Gracias{name}. Por ahora no tenemos ningún local que reparta en {zone}, y para repartir "
        "hay que trabajar dentro del área de un local. Si te mudas o abrimos local cerca, vuelve "
        "a llamarnos y seguimos donde lo dejamos. " + _SPOKEN_REVIEW
    ),
    "knockout_driver_license": (
        "Gracias{name}. Por ahora necesitamos carnet de conducir en vigor: es un requisito legal "
        "y del seguro para repartir. Cuando lo tengas, vuelve a llamarnos y seguimos donde lo "
        "dejamos. " + _SPOKEN_REVIEW
    ),
    "knockout_driver_license_expired": (
        "Gracias{name}. Para repartir el carnet de conducir tiene que estar en vigor: es un "
        "requisito legal y del seguro. Cuando lo renueves, vuelve a llamarnos y seguimos donde "
        "lo dejamos. " + _SPOKEN_REVIEW
    ),
    "knockout_driver_license_suspended": (
        "Gracias{name}. Para repartir hace falta un carnet de conducir en vigor y sin "
        "suspensión: es un requisito legal y del seguro. Cuando vuelvas a tenerlo, llámanos de "
        "nuevo y seguimos donde lo dejamos. " + _SPOKEN_REVIEW
    ),
    "knockout_driver_license_pending": (
        "Gracias{name}. Para empezar necesitamos que ya tengas el carnet de conducir: es un "
        "requisito legal y del seguro para repartir. En cuanto lo tengas, vuelve a llamarnos y "
        "seguimos donde lo dejamos. " + _SPOKEN_REVIEW
    ),
}

_SPOKEN_ES_MX = {
    "knockout_zone": (
        "Gracias{name}. Por ahora no tenemos ninguna sucursal que reparta en {zone}, y para "
        "repartir hay que trabajar dentro del área de una sucursal. Si te cambias de zona o "
        "abrimos sucursal cerca, vuelve a llamarnos y seguimos donde lo dejamos. " + _SPOKEN_REVIEW
    ),
    "knockout_driver_license": (
        "Gracias{name}. Por ahora necesitamos licencia de manejo vigente: es un requisito legal "
        "y del seguro para repartir. Cuando la tengas, vuelve a llamarnos y seguimos donde lo "
        "dejamos. " + _SPOKEN_REVIEW
    ),
    "knockout_driver_license_expired": (
        "Gracias{name}. Para repartir la licencia de manejo tiene que estar vigente: es un "
        "requisito legal y del seguro. Cuando la renueves, vuelve a llamarnos y seguimos donde "
        "lo dejamos. " + _SPOKEN_REVIEW
    ),
    "knockout_driver_license_suspended": (
        "Gracias{name}. Para repartir se necesita una licencia de manejo vigente y sin "
        "suspensión: es un requisito legal y del seguro. Cuando vuelvas a tenerla, llámanos de "
        "nuevo y seguimos donde lo dejamos. " + _SPOKEN_REVIEW
    ),
    "knockout_driver_license_pending": (
        "Gracias{name}. Para empezar necesitamos que ya tengas la licencia de manejo: es un "
        "requisito legal y del seguro para repartir. En cuanto la tengas, vuelve a llamarnos y "
        "seguimos donde lo dejamos. " + _SPOKEN_REVIEW
    ),
}

SPOKEN_TEMPLATES: dict[str, dict[str, str]] = {
    "es-ES": _SPOKEN_ES_ES,
    "es-MX": _SPOKEN_ES_MX,
    "en": _SPOKEN_ES_ES,  # Spanish on purpose: spoken agents speak es only (parent R18)
}


def knockout_message(
    criterion: str,
    reason: str,
    language: str,
    *,
    first_name: str | None = None,
    zone: str | None = None,
    spoken: bool = False,
) -> tuple[str, str]:
    """``(template_name, message)`` for a Knockout, e.g. ``knockout_zone.es-MX``. ``spoken`` picks
    the variant with no link and no typed instruction, for a turn of a voice call."""
    variant = _VARIANT.get((criterion, reason)) or _BASE[criterion]
    lang = language if language in TEMPLATES else "es-ES"
    text = (SPOKEN_TEMPLATES if spoken else TEMPLATES)[lang][variant].format(
        name=f", {first_name}" if first_name else "",
        zone=zone or ("your area" if lang == "en" else "tu zona"),
        url=REQUIREMENTS_URL,
    )
    return f"{variant}.{lang}", text
