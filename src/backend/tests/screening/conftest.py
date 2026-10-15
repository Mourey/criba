"""Synthetic reference data for the validator tests (not Grupo Sazón's real Service Areas)."""

import pytest

from criba.screening import ZoneRef

MADRID = "Europe/Madrid"
CANARY = "Atlantic/Canary"
MEXICO_CITY = "America/Mexico_City"
CANCUN = "America/Cancun"
TIJUANA = "America/Tijuana"


def zone(
    zid: str,
    country: str,
    city: str,
    name: str,
    tz: str,
    *,
    covered: bool = True,
    aliases: tuple[str, ...] = (),
) -> ZoneRef:
    return ZoneRef(
        id=zid,
        country=country,
        city=city,
        name=name,
        timezone=tz,
        covered=covered,
        aliases=aliases,
    )


ZONES: tuple[ZoneRef, ...] = (
    # Spain
    zone("zone_es_madrid_chamberi", "ES", "Madrid", "Chamberí", MADRID),
    zone("zone_es_madrid_centro", "ES", "Madrid", "Centro", MADRID, aliases=("Sol",)),
    zone("zone_es_madrid_salamanca", "ES", "Madrid", "Barrio de Salamanca", MADRID),
    zone(
        "zone_es_madrid_vallecas",
        "ES",
        "Madrid",
        "Vallecas",
        MADRID,
        aliases=("Puente de Vallecas",),
    ),
    zone("zone_es_getafe", "ES", "Getafe", "Getafe", MADRID),
    zone("zone_es_lpgc_vegueta", "ES", "Las Palmas de Gran Canaria", "Vegueta", CANARY),
    zone("zone_es_sevilla_centro", "ES", "Sevilla", "Centro", MADRID, covered=False),
    zone("zone_es_sevilla_triana", "ES", "Sevilla", "Triana", MADRID, covered=False),
    # Mexico
    zone("zone_mx_puebla_centro", "MX", "Puebla", "Centro", MEXICO_CITY),
    zone(
        "zone_mx_cdmx_centro",
        "MX",
        "CDMX",
        "Centro",
        MEXICO_CITY,
        aliases=("Centro Histórico",),
    ),
    zone(
        "zone_mx_cdmx_roma",
        "MX",
        "CDMX",
        "Roma Norte",
        MEXICO_CITY,
        aliases=("la Roma", "colonia Roma"),
    ),
    zone("zone_mx_qro_centro", "MX", "Querétaro", "Centro", MEXICO_CITY),
    zone("zone_mx_tijuana_rio", "MX", "Tijuana", "Zona Río", TIJUANA),
    zone(
        "zone_mx_cancun_hotelera",
        "MX",
        "Cancún",
        "Hotelera",
        CANCUN,
        covered=False,
        aliases=("Zona Hotelera",),
    ),
)


@pytest.fixture
def zones() -> tuple[ZoneRef, ...]:
    return ZONES
