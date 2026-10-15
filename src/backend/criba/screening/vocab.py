"""Option labels for the fixed-option fields, in es-ES, es-MX and en.

The labels are shown to the Candidate on buttons and list rows (ADR 0037), so they keep
their accents. Their keys are the option codes, in the contract's enum order. The word
lists the old free-text parsers used are gone: the agent maps the Candidate's words to
these codes (ADR 0042).
"""

# fmt: off

# Option labels per field and language (reply buttons <= 20 chars, list rows <= 24).
LABELS: dict[str, dict[str, dict[str, str]]] = {
    "driver_license": {
        "es-ES": {"yes": "Sí, tengo carnet", "no": "No"},
        "es-MX": {"yes": "Sí, tengo licencia", "no": "No"},
        "en": {"yes": "Yes, I have one", "no": "No"},
    },
    "availability": {
        "es-ES": {"full_time": "Jornada completa", "part_time": "Media jornada",
                  "weekends": "Fines de semana"},
        "es-MX": {"full_time": "Tiempo completo", "part_time": "Medio tiempo",
                  "weekends": "Fines de semana"},
        "en": {"full_time": "Full-time", "part_time": "Part-time", "weekends": "Weekends"},
    },
    "preferred_schedule": {
        "es-ES": {"morning": "Mañana", "afternoon": "Tarde", "evening": "Noche",
                  "flexible": "Flexible"},
        "es-MX": {"morning": "Mañana", "afternoon": "Tarde", "evening": "Noche",
                  "flexible": "Flexible"},
        "en": {"morning": "Morning", "afternoon": "Afternoon", "evening": "Evening",
               "flexible": "Flexible"},
    },
    "experience_platforms": {
        "es-ES": {"glovo": "Glovo", "uber_eats": "Uber Eats", "just_eat": "Just Eat",
                  "rappi": "Rappi", "didi_food": "DiDi Food", "other": "Otra"},
        "es-MX": {"glovo": "Glovo", "uber_eats": "Uber Eats", "just_eat": "Just Eat",
                  "rappi": "Rappi", "didi_food": "DiDi Food", "other": "Otra"},
        "en": {"glovo": "Glovo", "uber_eats": "Uber Eats", "just_eat": "Just Eat",
               "rappi": "Rappi", "didi_food": "DiDi Food", "other": "Other"},
    },
}

# Day and month names for a start-date choice's buttons ("Domingo 11 oct", ADR 0054), Monday
# first as in ``date.weekday()``. The longest label is 17 characters, under the 20 a reply
# button allows.
WEEKDAY_NAMES: dict[str, tuple[str, ...]] = {
    "es": ("Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo"),
    "en": ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"),
}
MONTH_NAMES: dict[str, tuple[str, ...]] = {
    "es": ("ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sept", "oct", "nov", "dic"),
    "en": ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"),
}

# The platforms question's last row (ADR 0053). Not a platform code: a tap on it is recorded
# as the empty list, the contract's value for no app (they delivered on their own).
NO_PLATFORM = "none"
NO_PLATFORM_LABELS: dict[str, str] = {"es-ES": "Ninguna app", "es-MX": "Ninguna app",
                                      "en": "No app"}
