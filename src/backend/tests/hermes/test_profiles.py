"""The profile files under src/hermes/ and the Screener skill, checked as files (no Docker).

Each assertion is one of the deliberate settings from spikes 1 and 2 and ADR 0010; a change
here is a change to what a Candidate's data can reach.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
import yaml

from .conftest import HERMES_SRC, PROFILE_SKILLS, PROFILES, SKILLS_SRC

ROOT_CONFIG = HERMES_SRC / "root" / "config.yaml"
SCREENER = SKILLS_SRC / "criba" / "screener" / "SKILL.md"
VOICE_SCREENER = SKILLS_SRC / "criba" / "voice-screener" / "SKILL.md"
VOICE_SOUL = HERMES_SRC / "profiles" / "criba-recruiter-voice" / "SOUL.md"

# Platforms in hermes-agent v0.21.6: `Platform` in gateway/config.py (minus local and
# api_server) plus the bundled plugin platforms under plugins/platforms/.
MESSAGING_PLATFORMS = {
    "telegram", "discord", "whatsapp", "whatsapp_cloud", "slack", "signal", "mattermost",
    "matrix", "email", "sms", "dingtalk", "webhook", "msgraph_webhook", "feishu", "wecom",
    "wecom_callback", "weixin", "bluebubbles", "qqbot", "yuanbao", "relay", "a2a", "buzz",
    "google_chat", "irc", "line", "ntfy", "photon", "raft", "simplex", "teams",
}  # fmt: skip

DISABLED_TOOLSETS = {
    "memory", "session_search", "web", "browser", "terminal", "file", "code_execution",
    "cronjob", "delegation", "vision", "todo", "clarify", "skills",
}  # fmt: skip


def code_lines(path: Path) -> str:
    """The file without comment lines (comments may mention ${VAR} to explain why it is absent)."""
    lines = path.read_text(encoding="utf-8").splitlines()
    return "\n".join(ln for ln in lines if not ln.lstrip().startswith("#"))


def load(path: Path) -> dict[str, Any]:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def configs() -> dict[str, dict[str, Any]]:
    return {name: load(HERMES_SRC / "profiles" / name / "config.yaml") for name in PROFILES}


def test_the_image_is_pinned_by_tag_and_digest() -> None:
    """Every Hermes fact cited in this lane is from v0.21.6 source; another image voids them."""
    dockerfile = (HERMES_SRC.parent / "docker" / "hermes" / "Dockerfile").read_text("utf-8")
    pinned = re.search(r"^FROM (\S+):(\S+)@sha256:([0-9a-f]{64})$", dockerfile, re.MULTILINE)
    assert pinned, "FROM must pin a tag and a digest"
    assert pinned.group(1) == "nousresearch/hermes-agent"
    assert pinned.group(2) == "v0.21.6"


def test_dockerfile_bakes_the_sources_the_entrypoint_expects() -> None:
    dockerfile = (HERMES_SRC.parent / "docker" / "hermes" / "Dockerfile").read_text("utf-8")
    assert "COPY --from=hermes_src . /opt/criba/hermes" in dockerfile
    assert "COPY --from=skills_src . /opt/criba/skills" in dockerfile
    assert "COPY --from=contracts tools.json /opt/criba/hermes/plugins/criba_tools/tools.json" in (
        dockerfile
    )


def test_compose_gives_the_hermes_build_its_contexts_and_lists_its_environment() -> None:
    compose = yaml.safe_load((HERMES_SRC.parents[1] / "docker-compose.yml").read_text("utf-8"))
    hermes = compose["services"]["hermes"]
    assert hermes["build"]["additional_contexts"] == {
        "hermes_src": "./src/hermes",
        "skills_src": "./src/skills",
        "contracts": "./contracts",
    }
    # Every variable is listed by name; nothing is forwarded from the host shell.
    assert "env_file" not in hermes
    assert set(hermes["environment"]) == {
        "HERMES_HOME",
        "CRIBA_BACKEND_URL",
        "CRIBA_SIM_LLM_BASE_URL",
        "CRIBA_SIM_LLM_MODEL",
    }
    assert hermes["environment"]["CRIBA_BACKEND_URL"] == "http://backend:8000"
    assert set(hermes["secrets"]) == {
        "hermes_api_server_key",
        "openrouter_api_key",
        "criba_tools_secret",
    }
    assert "ports" not in hermes, "Hermes is reachable only on the Compose network"


def test_every_profile_has_its_files() -> None:
    for name in PROFILES:
        directory = HERMES_SRC / "profiles" / name
        for filename in ("config.yaml", "SOUL.md", "skills.txt"):
            assert (directory / filename).is_file(), f"{name}/{filename} is missing"


@pytest.mark.parametrize("name", PROFILES)
def test_lean_toolset_is_only_criba_tools(configs: dict[str, Any], name: str) -> None:
    config = configs[name]
    assert config["platform_toolsets"] == {"api_server": ["criba-tools"]}
    assert config["plugins"]["enabled"] == ["criba-tools"]
    # Tool search would defer our tools behind a bridge (spike 2, finding 11).
    assert str(config["tools"]["tool_search"]["enabled"]).lower() == "off"


@pytest.mark.parametrize("name", PROFILES)
def test_memory_and_session_search_are_off(configs: dict[str, Any], name: str) -> None:
    config = configs[name]
    assert config["memory"]["memory_enabled"] is False
    assert config["memory"]["user_profile_enabled"] is False
    assert set(config["agent"]["disabled_toolsets"]) >= DISABLED_TOOLSETS
    assert "memory" in config["agent"]["disabled_toolsets"]
    assert "session_search" in config["agent"]["disabled_toolsets"]
    assert not config["memory"].get("provider"), "no external memory provider either"


@pytest.mark.parametrize("name", PROFILES)
def test_auxiliary_model_is_pinned_and_background_calls_are_off(
    configs: dict[str, Any], name: str
) -> None:
    aux = configs[name]["auxiliary"]
    assert aux["openrouter_model"] == "anthropic/claude-haiku-5.5"
    assert aux["title_generation"]["enabled"] is False
    assert aux["background_review"]["enabled"] is False


@pytest.mark.parametrize("name", PROFILES)
def test_run_length_is_bounded_and_the_workdir_is_neutral(
    configs: dict[str, Any], name: str
) -> None:
    config = configs[name]
    assert 1 <= config["agent"]["max_turns"] <= 40
    assert config["terminal"]["cwd"] == "/opt/data/workspace"


@pytest.mark.parametrize("name", PROFILES)
def test_a_provider_outage_fails_the_run_fast(configs: dict[str, Any], name: str) -> None:
    """ADR 0073: no recovery ladder (it held a run past the backend's 60 s timeout) and one
    retry per model call; the backend retries the turn. Keys read at agent/agent_init.py:1434-
    1445 of hermes-agent v0.21.6 (defaults 3 and 5)."""
    config = configs[name]
    assert config["agent"]["auto_recovery_cycles"] == 0
    assert config["agent"]["api_max_retries"] == 2


@pytest.mark.parametrize("name", PROFILES)
def test_hermes_prompt_blocks_a_screening_does_not_need_are_off(
    configs: dict[str, Any], name: str
) -> None:
    """Turn size (docs/evidence/turn-size.md): the two generic guidance blocks and the long
    api_server hint cost about 2,000 characters on every model call. Keys read at
    agent/agent_init.py:1417-1422 and :1431-1432 of hermes-agent v0.21.6."""
    config = configs[name]
    assert config["agent"]["task_completion_guidance"] is False
    assert config["agent"]["parallel_tool_call_guidance"] is False
    hint = config["platform_hints"]["api_server"]["replace"]
    assert "markdown" in hint and len(hint) < 120


@pytest.mark.parametrize("name", PROFILES)
def test_role_profiles_disable_every_messaging_platform(configs: dict[str, Any], name: str) -> None:
    platforms = configs[name]["platforms"]
    assert set(platforms) == MESSAGING_PLATFORMS
    assert all(platforms[p] == {"enabled": False} for p in MESSAGING_PLATFORMS)


@pytest.mark.parametrize("name", PROFILES)
def test_role_profiles_disable_every_messaging_platform_too(
    configs: dict[str, Any], name: str
) -> None:
    """A secondary profile with a platform token in its own env file would otherwise run that
    adapter beside the multiplexer."""
    platforms = configs[name]["platforms"]
    assert set(platforms) == MESSAGING_PLATFORMS
    assert all(platforms[p] == {"enabled": False} for p in MESSAGING_PLATFORMS)


def test_root_profile_disables_every_messaging_platform() -> None:
    config = load(ROOT_CONFIG)
    platforms = config["platforms"]
    assert set(platforms) == MESSAGING_PLATFORMS
    assert all(platforms[p] == {"enabled": False} for p in MESSAGING_PLATFORMS)
    assert "api_server" not in platforms, "the Runs API is enabled by the entrypoint's env file"


def test_root_profile_multiplexes_and_has_no_tools() -> None:
    config = load(ROOT_CONFIG)
    assert config["gateway"]["multiplex_profiles"] is True
    assert config["platform_toolsets"] == {"api_server": []}
    assert set(config["agent"]["disabled_toolsets"]) >= DISABLED_TOOLSETS
    assert config["memory"] == {"memory_enabled": False, "user_profile_enabled": False}


def test_root_profile_does_not_enable_the_plugin_or_a_secret() -> None:
    text = code_lines(ROOT_CONFIG)
    assert "criba-tools" not in text
    assert not re.search(r"\$\{[A-Z_]+\}", text), "Hermes does not expand ${VAR} here"


def test_no_profile_config_holds_a_secret_or_a_variable() -> None:
    for name in PROFILES:
        text = code_lines(HERMES_SRC / "profiles" / name / "config.yaml")
        assert not re.search(r"\$\{[A-Za-z_]+\}", text)
        assert "sk-or-" not in text
        assert not re.search(r"^\s*(key|token|secret):", text, re.MULTILINE)


def test_recruiter_uses_openrouter_haiku(configs: dict[str, Any]) -> None:
    assert configs["criba-recruiter"]["model"] == {
        "default": "anthropic/claude-haiku-5.5",
        "provider": "openrouter",
    }


def test_voice_profile_uses_the_same_openrouter_haiku(configs: dict[str, Any]) -> None:
    """ADR 0059: the voice flow has its own profile, on the same model."""
    assert configs["criba-recruiter-voice"]["model"] == configs["criba-recruiter"]["model"]


def test_voice_profile_has_every_setting_of_the_recruiter(configs: dict[str, Any]) -> None:
    """Same lean toolset, memory and session search off, same auxiliary pins (ADR 0059): only
    the identity and the skill differ, and they live outside config.yaml."""
    assert configs["criba-recruiter-voice"] == configs["criba-recruiter"]


def test_voice_sim_profile_differs_from_the_voice_profile_only_in_its_model(
    configs: dict[str, Any],
) -> None:
    voice = {k: v for k, v in configs["criba-recruiter-voice"].items() if k != "model"}
    sim = {k: v for k, v in configs["criba-recruiter-voice-sim"].items() if k != "model"}
    assert sim == voice
    assert configs["criba-recruiter-voice-sim"]["model"] == configs["criba-recruiter-sim"]["model"]


def test_voice_sim_and_voice_share_one_identity() -> None:
    voice = HERMES_SRC / "profiles" / "criba-recruiter-voice"
    sim = HERMES_SRC / "profiles" / "criba-recruiter-voice-sim"
    for filename in ("SOUL.md", "skills.txt"):
        assert (sim / filename).read_text(encoding="utf-8") == (voice / filename).read_text(
            encoding="utf-8"
        )


def test_sim_profile_points_at_a_configurable_stub_model(configs: dict[str, Any]) -> None:
    model = configs["criba-recruiter-sim"]["model"]
    assert model["provider"] == "custom"
    assert model["default"] == "@@CRIBA_SIM_LLM_MODEL@@"
    assert model["base_url"] == "@@CRIBA_SIM_LLM_BASE_URL@@"
    assert "sk-" not in str(model["api_key"])


def test_sim_profile_differs_from_the_recruiter_only_in_its_model(
    configs: dict[str, Any],
) -> None:
    recruiter = {k: v for k, v in configs["criba-recruiter"].items() if k != "model"}
    sim = {k: v for k, v in configs["criba-recruiter-sim"].items() if k != "model"}
    assert sim == recruiter


def test_sim_and_recruiter_share_one_identity() -> None:
    recruiter = HERMES_SRC / "profiles" / "criba-recruiter"
    sim = HERMES_SRC / "profiles" / "criba-recruiter-sim"
    assert (sim / "SOUL.md").read_text(encoding="utf-8") == (recruiter / "SOUL.md").read_text(
        encoding="utf-8"
    )
    assert (sim / "skills.txt").read_text(encoding="utf-8") == (recruiter / "skills.txt").read_text(
        encoding="utf-8"
    )


@pytest.mark.parametrize("name", PROFILES)
def test_listed_skills_exist(name: str) -> None:
    lines = (HERMES_SRC / "profiles" / name / "skills.txt").read_text(encoding="utf-8").splitlines()
    skills = [ln.strip() for ln in lines if ln.strip() and not ln.strip().startswith("#")]
    assert skills == [PROFILE_SKILLS[name]]
    for skill in skills:
        assert (SKILLS_SRC / skill / "SKILL.md").is_file()


# --- the Screener skill ----------------------------------------------------------------------


def screener_parts() -> tuple[dict[str, Any], str]:
    text = SCREENER.read_text(encoding="utf-8")
    match = re.match(r"---\n(.*?)\n---\n(.*)", text, re.DOTALL)
    assert match, "SKILL.md needs YAML frontmatter"
    return yaml.safe_load(match.group(1)), match.group(2)


def test_screener_frontmatter() -> None:
    meta, _ = screener_parts()
    assert meta["name"] == "screener"
    assert len(meta["description"]) > 40


def test_screener_covers_tone_stages_tool_protocol_and_disclosure() -> None:
    _, body = screener_parts()
    for heading in ("## Tool protocol", "## Stages, in order", "## AI disclosure"):
        assert heading in body
    assert "## Tone and format" in body
    assert "virtual (AI) assistant" in body


def test_screener_names_every_tool_and_no_invented_one(contract: dict[str, Any]) -> None:
    _, body = screener_parts()
    for tool in contract["x-tools"]:
        assert f"`{tool}" in body, f"the Screener never mentions {tool}"
    for mentioned in set(re.findall(r"`([a-z_]+)\(", body)):
        assert mentioned in contract["x-tools"], f"the Screener calls unknown tool {mentioned}"


def test_screener_never_exposes_an_id_to_the_model() -> None:
    _, body = screener_parts()
    soul = (HERMES_SRC / "profiles" / "criba-recruiter" / "SOUL.md").read_text(encoding="utf-8")
    for text in (body, soul):
        assert "application_id" not in text
        assert "candidate_id" not in text


def test_screener_uses_the_contract_field_names(contract: dict[str, Any]) -> None:
    _, body = screener_parts()
    for field in ("zone", "driver_license", "availability", "preferred_schedule"):
        assert field in contract["$defs"]["field"]["enum"]
        assert field in body


def test_screener_placeholders_are_all_marked_synthetic() -> None:
    """Persona name, FAQ and templates are placeholders until Grupo Sazón approves real ones."""
    _, body = screener_parts()
    soul = (HERMES_SRC / "profiles" / "criba-recruiter" / "SOUL.md").read_text(encoding="utf-8")
    assert "SYNTHETIC" in soul
    for heading in ("## Approved FAQ (SYNTHETIC)", "## Message templates (SYNTHETIC)"):
        assert heading in body
    assert re.search(r"Persona name: \*\*Sazón Empleo\*\* \(SYNTHETIC\)", body)
    faq = body.split("## Approved FAQ (SYNTHETIC)")[1].split("## Message templates")[0]
    answers = [ln for ln in faq.splitlines() if ln.startswith("- ")]
    assert answers
    assert all(ln.rstrip().endswith("(SYNTHETIC)") for ln in answers)


def test_screener_asks_for_a_pick_when_a_tool_error_carries_options() -> None:
    """Q45: a known city is answered with its Zones as a list; the agent asks for a pick and
    never retypes the options. The case has no `attempt`, so it must not be read as a miss."""
    _, body = screener_parts()
    line = next(ln for ln in body.splitlines() if "`ok: false` with `options`" in ln)
    assert "no `attempt`" in line and "narrowing step, not a miss" in line
    assert "pick their zone from the list" in line
    assert "do not retype them" in line


def test_screener_respects_the_never_list() -> None:
    _, body = screener_parts()
    lowered = body.lower()
    for rule in (
        "promise a job, pay, or a start date",
        "protected attribute",
        "never name criba",
        "other candidates",
    ):
        assert rule in lowered


def test_screener_never_claims_an_invite_was_sent_unless_ics_sent() -> None:
    """book_interview returns ics_sent: false until an .ics is really delivered (booking.py), so
    the skill, examples included, may mention an invite only conditionally on that flag."""
    _, body = screener_parts()
    mention = re.compile(r"invit|\.ics|calendar|calendario", re.IGNORECASE)
    sentences = [
        s
        for para in body.splitlines()
        for s in re.split(r"(?<=[.!?])\s+", para)
        if mention.search(s)
    ]
    assert sentences, "the skill should say what to do about the invite"
    for sentence in sentences:
        assert "`ics_sent`" in sentence, f"unconditional invite claim: {sentence!r}"
        assert re.search(r"\bonly if\b", sentence), f"invite not conditional: {sentence!r}"
    assert "Do not say an invite" in body


def test_screener_knows_which_inputs_are_the_backends_and_keeps_what_it_sent() -> None:
    """ADR 0050. On real Haiku the model raised an Escalation after the slot offer ("the slots
    are not shown to the Candidate") and apologised; finished results are shortened (ADR 0045)
    and the model took back a requirements link and an Interview's details it had sent."""
    _, body = screener_parts()
    intro = body.split("## Tool protocol")[1].split("\n1. ")[0]
    assert "`[system] approved` is the backend's cue to check the record" in intro
    assert "go to step 5 (`get_application` decides); never mention it" in intro
    assert "never take it back" in intro
    step = _protocol_step(5)
    assert "the backend attaches them to that line as buttons" in step
    assert "it is complete without listing them" in step
    assert "A tapped slot arrives as `[tap result]`, already booked" in step
    assert "`book_interview(slot_ref)`" in step


def test_screener_requests_a_review_on_a_dispute_after_a_knockout() -> None:
    """ADR 0033: a message after a Knockout does not reopen it, so the agent itself turns a
    dispute into a Review, and only a new answer to the failed question goes to record_answer."""
    _, body = screener_parts()
    section = body.split("**After a Knockout**")[1].split("\n8. ")[0]
    assert "`status` is closed and `outcome` is knockout" in section
    dispute = next(ln for ln in section.splitlines() if "**A dispute" in ln)
    assert "request_review(note)" in dispute and "revisión" in dispute
    news = next(ln for ln in section.splitlines() if "**News that might change" in ln)
    assert "`record_answer`" in news and "`knockout_criterion`" in news
    other = next(ln for ln in section.splitlines() if "**Anything else**" in ln)
    assert "no new Screening" in other


def test_system_prompt_stays_small() -> None:
    """SOUL.md plus the skill is what the entrypoint puts in the system prompt. The real model
    counts about 1.5 tokens for every 4 characters of it, and a turn (this prompt, the tool
    schemas, the history) has to stay under 10K real input tokens (docs/evidence/turn-size.md),
    so the calibrated estimate of the longest persona must stay at or under 9,500."""
    _, body = screener_parts()
    soul = (HERMES_SRC / "profiles" / "criba-recruiter" / "SOUL.md").read_text(encoding="utf-8")
    # 14,500 was the cap through spec 006b (soul and skill came to 14,200); 12,880 now.
    assert len(soul) + len(body) < 13_200


# --- structured answers (spec 006b, ADR 0042) ------------------------------------------------

SKILL_CHARS_BEFORE_006B = 10_935
SKILL_GROWTH_CAP_CHARS = 2_400  # 600 tokens at 4 characters a token (spec 006b)


def test_screener_growth_stays_within_the_006b_budget() -> None:
    text = SCREENER.read_text(encoding="utf-8")
    assert len(text) - SKILL_CHARS_BEFORE_006B <= SKILL_GROWTH_CAP_CHARS


def _protocol_step(number: int) -> str:
    _, body = screener_parts()
    section = body.split("## Tool protocol")[1].split("## Stages, in order")[0]
    return re.split(rf"^{number}\. ", section, flags=re.MULTILINE)[1].split("\n3. ")[0]


def test_screener_asks_for_structured_values_and_never_words() -> None:
    step = _protocol_step(1) + _protocol_step(2)
    assert "structured value, never their words" in step
    assert "`search_zones(query)`" in step and "before `record_answer`" in step
    assert "`null`" in step and "Never guess" in step
    assert "one short follow-up without calling" in step
    assert "if the next answer still cannot be mapped, call with `null`" in step
    # The date comes from the backend's `[criba date]` line on every call (ADR 0055).
    assert "`YYYY-MM-DD`" in step and "`[criba date]`" in step


def test_screener_names_the_licence_codes_the_contract_accepts(contract: dict[str, Any]) -> None:
    codes = contract["$defs"]["answer_value_driver_license"]["enum"]
    step = _protocol_step(2)
    for code in codes:
        assert f"`{code}`" in step, f"the Screener never names the licence code {code}"


def test_screener_reports_the_language_of_typed_messages_only() -> None:
    step = _protocol_step(1)
    assert "pass `language`" in step and "`es-ES`, `es-MX` or `en`" in step
    assert "After a button tap pass nothing" in step


def test_screener_does_not_switch_language_on_a_one_word_answer() -> None:
    """Phase 1 section 4.3: only a full sentence or a request switches; "Mañana" does not."""
    step = _protocol_step(1)
    assert "only for a clear switch" in step
    assert "full sentence" in step and "or a request" in step
    for example in ('"Mañana"', '"Sí"', '"OK"', "a place", "a number"):
        assert example in step
    assert "never switches" in step and "very short answer" in step


def test_screener_keeps_its_reply_and_the_stored_language_in_step() -> None:
    """The reply follows the `language` the result returns; replying in another means passing it
    on the same call, so the record and the reply cannot disagree."""
    step = _protocol_step(1)
    assert "Reply in the `language` it returns; only passing one changes it" in step
    _, body = screener_parts()
    line = next(ln for ln in body.splitlines() if ln.startswith("- **Language.**"))
    assert "Reply in step 1's `language`" in line


def test_screener_gives_each_spanish_variant_its_own_zone_word() -> None:
    """The demo chat of 2026-10-11 asked a Spain Candidate "¿En qué ciudad y colonia vives?":
    the Zone question's word is the variant's (Spain barrio, Mexico colonia), stated in the
    skill's Language rule next to the other Spain/Mexico words, and only there."""
    _, body = screener_parts()
    line = next(ln for ln in body.splitlines() if ln.startswith("- **Language.**"))
    spain, mexico = line.split("Spain says", 1)[1].split("Mexico says", 1)
    assert '"barrio y ciudad"' in spain and "colonia" not in spain
    assert '"colonia y ciudad"' in mexico and "barrio" not in mexico
    assert "colonia" not in body.replace('"colonia y ciudad"', "")  # no other Mexican wording


def test_screener_gives_english_its_own_zone_words() -> None:
    """The demo chat of 2026-10-12 (an MX chat in English) asked "Which barrio and city do you
    live in?": the Language rule named the Spanish words only, so English gets its own (ADR
    0076)."""
    _, body = screener_parts()
    line = next(ln for ln in body.splitlines() if ln.startswith("- **Language.**"))
    english = line.split("English is", 1)[1].split(".", 1)[0]
    assert '"neighbourhood and city"' in english and "barrio" not in english


def test_voice_screener_gives_each_spanish_variant_its_own_zone_word() -> None:
    _, body = voice_parts()
    line = next(ln for ln in body.splitlines() if ln.startswith("- Speak Spanish"))
    spain, mexico = line.split("Spain says", 1)[1].split("Mexico says", 1)
    assert '"barrio"' in spain and "colonia" not in spain
    assert '"colonia"' in mexico and "barrio" not in mexico
    zone = next(ln for ln in body.splitlines() if "**Zone.**" in ln)
    assert "barrio (Spain) or colonia (Mexico)" in zone and "never the other country's word" in zone


def test_screener_sends_the_qualified_close_from_the_tool_result() -> None:
    """The close is the backend's, in the stored language: the skill carries no es-MX example
    for the model to copy (the real demo ended in Spanish on an English Application)."""
    _, body = screener_parts()
    assert "call `finish_screening`, then send its `message` exactly as given" in body
    assert "revisará tu solicitud" not in body and "Qualified close, es-MX" not in body


def test_screener_confirms_one_uncovered_zone_with_si_and_no() -> None:
    step = _protocol_step(2)
    line = next(ln for ln in step.splitlines() if "**Zone confirmation.**" in ln)
    assert "`needs_choice` with `confirm`" in line and "¿Vives en …?" in line
    assert "Sí passes the Zone's id" in line and "No passes `null`" in line
    assert "ask for the Zone again" in line


def test_screener_does_not_count_a_model_error_as_a_miss() -> None:
    _, body = screener_parts()
    line = next(ln for ln in body.splitlines() if "with no `attempt` and no `options`" in ln)
    assert "invalid_args" in line and "unknown_zone_id" in line
    assert "without re-asking them" in line and "zone_not_confirmed" in line


def test_screener_carries_the_settled_interpretation_rules() -> None:
    step = _protocol_step(2)
    for phrase in (
        # ADR 0058: on the day itself Haiku counted from today; the line lists the dates.
        "a weekday is its next occurrence (never today: `[criba date]`)",
        'bare "próximo"/"next" + weekday: [it, +7 days]',  # ADR 0054: both readings, code asks
        "roll to next year",
        "all three availability options",
        "a negation in the same clause removes one",
        "a range keeps its lower bound",
        "months are divided by 12",
        "an unlisted app is `other`",
        "no app (on their own) `[]`",  # ADR 0053: "por mi cuenta" was asked three times
        '"expires next month" is `yes`',
        '"I don\'t know" is `null`',
    ):
        assert phrase in step, phrase


def test_screener_records_tomorrow_and_named_dates_without_asking() -> None:
    """The demo chat of 2026-10-10: "tomorrow works for me" got "Could you give me the exact
    date ...?" with no tool call, though the `[criba date]` line gave tomorrow. Today, tomorrow,
    a date, "este" + weekday and the following week named outright are one date, recorded with
    no question; only a bare "próximo"/"next" + weekday is the two-date list (ADR 0054)."""
    step = _protocol_step(2)
    # A live rerun of the demo chat answered "I can't rely on a date from the chat": the line
    # rides on the Candidate's message, so the skill says whose it is.
    assert "the backend adds it to their message, so trust it" in step
    rules = next(ln for ln in step.splitlines() if ln.strip().startswith("- Rules:"))
    for phrase in ('"hoy"', '"mañana"/"tomorrow"', "a date", '"este"/"this"'):
        assert phrase in rules, phrase
    assert '"de la próxima semana" + weekday: one date, record it, never ask' in rules
    assert rules.index("never ask") < rules.index('only bare "próximo"/"next" + weekday')
    example = next(ln for ln in step.splitlines() if ln.strip().startswith("- start date"))
    assert '"tomorrow works" [2026-10-10]' in example  # today is Fri 2026-10-09 there


EXAMPLE_FIELDS = (
    "name",
    "zone",
    "licence",
    "availability",
    "schedule",
    "years",
    "platforms",
    "start date",
)


def test_screener_has_a_spanish_and_an_english_example_per_field() -> None:
    step = _protocol_step(2)
    lines = {
        name: next(ln for ln in step.splitlines() if ln.strip().startswith(f"- {name}"))
        for name in EXAMPLE_FIELDS
    }
    spanish = re.compile(r"[áéíóúñ¿]|\b(me|sí|el|la|en|un|por|no|y|da|llamo|Glovo)\b")
    english = re.compile(
        r"\b(it's|near|it expired|full time|whenever|never done it|a local app|next week)\b"
    )
    for name, line in lines.items():
        examples = re.findall(r'"([^"]+)" \[', line)
        assert len(examples) >= 2, f"{name}: needs at least two examples"
        assert any(english.search(e) for e in examples), f"{name}: no English example"
        assert any(spanish.search(e) for e in examples), f"{name}: no Spanish example"
    assert "[Ana García]" in lines["name"] and "[2026-10-12]" in lines["start date"]
    # ADR 0054: "el próximo domingo" on Friday 9 October is both Sundays, as one list.
    assert '"el próximo domingo" [2026-10-11, 2026-10-18]' in lines["start date"]
    # ... and only the bare phrase: the following week named outright is one date.
    assert '"domingo de la próxima semana" [2026-10-18]' in lines["start date"]


# --- the Voice Screener skill (ADR 0059) -----------------------------------------------------

EMOJI = re.compile("[\U0001f000-\U0001faff☀-➿]")


def voice_parts() -> tuple[dict[str, Any], str]:
    text = VOICE_SCREENER.read_text(encoding="utf-8")
    match = re.match(r"---\n(.*?)\n---\n(.*)", text, re.DOTALL)
    assert match, "SKILL.md needs YAML frontmatter"
    return yaml.safe_load(match.group(1)), match.group(2)


def _quoted(body: str) -> list[str]:
    """The Spanish lines the skill gives the agent to say: every double-quoted span after the
    speaking rules (which quote what is banned)."""
    return re.findall(r'"([^"]+)"', body.split("## Tool protocol")[1])


def test_voice_screener_frontmatter() -> None:
    meta, _ = voice_parts()
    assert meta["name"] == "voice-screener"
    assert meta["metadata"]["flow"] == "voice"
    assert len(meta["description"]) > 40


def test_voice_screener_names_every_tool_and_no_invented_one(contract: dict[str, Any]) -> None:
    """The same procedure and tools as the text Screener."""
    _, body = voice_parts()
    for tool in contract["x-tools"]:
        assert f"`{tool}" in body, f"the Voice Screener never mentions {tool}"
    for mentioned in set(re.findall(r"`([a-z_]+)\(", body)):
        assert mentioned in contract["x-tools"], f"the Voice Screener calls unknown {mentioned}"


def test_voice_screener_uses_the_contract_fields_and_licence_codes(
    contract: dict[str, Any],
) -> None:
    _, body = voice_parts()
    for field in contract["$defs"]["field"]["enum"]:
        assert f"`{field}`" in body, field
    for code in contract["$defs"]["answer_value_driver_license"]["enum"]:
        assert f"`{code}`" in body, code


def test_voice_screener_keeps_the_text_procedure() -> None:
    """The steps a change to the procedure must reach in both skills."""
    _, body = voice_parts()
    for phrase in (
        "structured value, never their words",
        "before `record_answer`",
        "if the next answer still cannot be mapped, call with `null`",
        "`[tap result] {…}`",
        "a weekday is its next occurrence (never today: `[criba date]`)",
        "`status: knockout`: say `message` exactly as given",
        "call `finish_screening`, then say its `message` exactly as given",
        "`needs_choice` with `confirm`",
        "`request_review(note)`",
        "`knockout_criterion`",
        "`record_backout(kind: withdrawn)`",
        "`record_backout(kind: opted_out)`",
        "`record_backout(kind: erasure_request)`",
        "`offer_interview_slots(reschedule: true)`",
        "`tone_hint`",
        "`takeover_summary`",
    ):
        assert phrase in body, phrase


def test_voice_screener_passes_one_word_names_and_past_dates_to_the_backend() -> None:
    """The text skill's "Names and dates" rule, in spoken form: the agent records what it heard
    even when the name is one word or odd or the date is past, and leaves the check to the
    backend; the read-back is what catches a misheard name."""
    text_rule = "Names and dates: pass them even if one word, odd or past; the backend checks."
    assert text_rule in SCREENER.read_text(encoding="utf-8")
    _, body = voice_parts()
    line = next(ln for ln in body.splitlines() if "**You interpret, the backend verifies.**" in ln)
    assert (
        "Names and dates: record what they say even if the name is one word or odd, "
        "or the date is past; the backend checks"
    ) in line
    assert "the read-back (6) catches a misheard name" in line
    readbacks = next(ln for ln in body.splitlines() if ln.startswith("6. **Read-backs.**"))
    assert "read back the name" in readbacks


def test_voice_screener_reads_the_application_block_instead_of_calling() -> None:
    """The rule-1 exception lives in the skill now, not in the run input (supersedes voice log
    decision 17)."""
    _, body = voice_parts()
    assert "`[application]` block" in body
    assert "so do not call `get_application` on it" in body


def test_voice_screener_speaks_and_never_points_to_a_screen() -> None:
    _, body = voice_parts()
    rules = body.split("## How you speak")[1].split("## Tool protocol")[0]
    assert "only hears you" in rules
    for banned in ("emoji", "markdown", "lists", "URL"):
        assert banned in rules, banned
    for thing in ("a chat", "a link", "a button", "writing", '"por aquí"'):
        assert thing in rules, thing
    assert "never repeat them" in rules  # the disclosure the backend spoke first
    assert "never correct your own earlier wording out loud" in rules
    assert "Never talk about the system, tools, records" in rules


def test_voice_screener_says_nothing_a_listener_cannot_follow() -> None:
    """What the agent is given to say has no URL, emoji, chat word, link or button."""
    _, body = voice_parts()
    assert not re.search(r"https?://|www\.|\.example", body)
    assert not EMOJI.search(body)
    for line in _quoted(body):
        lowered = line.lower()
        for word in ("chat", "enlace", "link", "botón", "botones", "por aquí", "escrib"):
            assert word not in lowered, (word, line)


def test_voice_screener_reads_options_aloud_from_the_record_only() -> None:
    _, body = voice_parts()
    line = next(ln for ln in body.splitlines() if "`status: needs_choice`" in ln)
    assert "read its options aloud" in line
    assert "Name only the options the result gives, never another" in line
    assert "do not retype" not in body


def test_voice_screener_sums_up_in_sentences_and_reads_the_booking_back() -> None:
    _, body = voice_parts()
    confirm = next(ln for ln in body.splitlines() if "**Confirm.**" in ln)
    assert "one or two natural sentences" in confirm
    approval = next(ln for ln in body.splitlines() if "**After Approval**" in ln)
    for part in ("day, hour, `location_name` and `address`", "`book_interview(slot_ref)`"):
        assert part in approval, part
    assert "Never mention an invite, a calendar file or `booking_url`" in approval
    lines = body.split("## Spoken lines (SYNTHETIC)")[1]
    assert "**Booking read-back:**" in lines


def test_voice_screener_gives_a_short_spoken_privacy_notice_and_next_steps() -> None:
    _, body = voice_parts()
    section = body.split("## AI disclosure and privacy")[1].split("## Never")[0]
    assert "privacy notice once, briefly (SYNTHETIC)" in section
    # The owner's wording (voice log decision 64), with nothing promised in writing.
    assert '"Tus datos se usan solo para tu solicitud de empleo."' in section
    assert "AI assistant" in section
    assert "se pondrá en contacto contigo" in body
    assert "te escribe" not in body


def test_voice_screener_respects_the_never_list() -> None:
    _, body = voice_parts()
    lowered = body.lower()
    for rule in (
        "promise a job, pay, or a start date",
        "protected attribute",
        "never name criba",
        "other candidates",
    ):
        assert rule in lowered


def test_voice_screener_placeholders_are_all_marked_synthetic() -> None:
    _, body = voice_parts()
    assert "SYNTHETIC" in VOICE_SOUL.read_text(encoding="utf-8")
    for heading in ("## Approved FAQ (SYNTHETIC)", "## Spoken lines (SYNTHETIC)"):
        assert heading in body
    assert re.search(r"Persona name: \*\*Sazón Empleo\*\* \(SYNTHETIC\)", body)
    faq = body.split("## Approved FAQ (SYNTHETIC)")[1].split("## Spoken lines")[0]
    answers = [ln for ln in faq.splitlines() if ln.startswith("- ")]
    assert answers and all(ln.rstrip().endswith("(SYNTHETIC)") for ln in answers)


def test_voice_identity_is_a_call_and_exposes_no_id() -> None:
    _, body = voice_parts()
    soul = VOICE_SOUL.read_text(encoding="utf-8")
    assert "voice call" in soul and "over chat" not in soul
    for text in (body, soul):
        assert "application_id" not in text
        assert "candidate_id" not in text


def test_voice_system_prompt_stays_small() -> None:
    """The same cap as the text Screener's prompt (turn-size.md)."""
    _, body = voice_parts()
    assert len(VOICE_SOUL.read_text(encoding="utf-8")) + len(body) < 13_200


def test_voice_screener_carries_the_speech_native_voice_rules() -> None:
    """ADR 0059 moved `VOICE_TURN_RULES` (owner decision 64) out of the run input: each rule is
    in the skill, so the voice profile hears them all without the module."""
    _, body = voice_parts()
    for phrase in (
        "only hears you",
        "Ask for their barrio (Spain) or colonia (Mexico) and their ciudad first",
        "offer the nearest zones only when the latest result gives them",
        "Name only the options the result gives, never another",
        "never repeat them",
        "never correct your own earlier wording out loud",
        '"de la mañana" or "de la tarde"',
        "in one or two natural sentences, never as a list",
        '"Tus datos se usan solo para tu solicitud de empleo."',
        '"Un reclutador de Grupo Sazón revisará tu solicitud y se pondrá en contacto contigo."',
        "not sending a voice note",
        "\"¿Quieres que dejemos de contactarte? Di 'baja'\"",
    ):
        assert phrase in body, phrase
