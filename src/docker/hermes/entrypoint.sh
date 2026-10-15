#!/bin/sh
# Prepares the Hermes home from the image's baked sources and the Compose secrets, then
# hands off to the official image's entrypoint.
#
# What it writes, on every start (so editing a source file or rotating a secret and
# restarting the container is enough):
#
#   $HERMES_HOME/config.yaml                       root profile config   (src/hermes/root/)
#   $HERMES_HOME/<env file>                        root keys and secrets
#   $HERMES_HOME/profiles/<name>/config.yaml       role profile config   (src/hermes/profiles/<name>/)
#   $HERMES_HOME/profiles/<name>/SOUL.md           identity, plus the skills in skills.txt
#   $HERMES_HOME/profiles/<name>/plugins/criba_tools/   the criba-tools plugin
#   $HERMES_HOME/profiles/<name>/skills/<skill>/        each listed skill (for later use)
#   $HERMES_HOME/profiles/<name>/<env file>        that profile's API_SERVER_KEY (+ provider key)
#
# Why env files and not `environment:`: once a Hermes home has an env file, Hermes reads
# API_SERVER_KEY and provider keys only from it, and `${VAR}` in config.yaml is not expanded
# (spike 1, gotcha 4). The `/p/<profile>/` route accepts only that profile's own key
# (docs: multi-profile-gateways.md, "Authentication follows the profile named in the URL").
set -eu

HERMES_HOME="${HERMES_HOME:-/opt/data}"
SECRETS_DIR="${CRIBA_SECRETS_DIR:-/run/secrets}"
CRIBA_SRC="${CRIBA_SRC:-/opt/criba}"
ENV_NAME=".env"
ROOT_ENV="$HERMES_HOME/$ENV_NAME"

# Role profiles to provision. Each is a directory under $CRIBA_SRC/hermes/profiles/.
# `-sim` profiles point at the stub model server (spec 004 task 4) and never receive the
# OpenRouter key, so a simulator run cannot reach a paid provider by accident. The `-voice`
# profiles hold voice calls, a flow of their own (ADR 0059).
PROFILES="${CRIBA_HERMES_PROFILES:-criba-recruiter criba-recruiter-sim criba-recruiter-voice criba-recruiter-voice-sim}"

# Placeholders in profile config files are written @@NAME@@ and filled from these variables.
# The simulator's stub model server is lane W1-E's; its address is configurable.
SIM_LLM_BASE_URL="${CRIBA_SIM_LLM_BASE_URL:-http://stub-llm:8000/v1}"
SIM_LLM_MODEL="${CRIBA_SIM_LLM_MODEL:-stub-model}"

die() {
    echo "[criba] $*" >&2
    exit 1
}

read_secret() {
    # Prints the first line of a secret file, without trailing whitespace.
    if [ -s "$SECRETS_DIR/$1" ]; then
        head -n 1 "$SECRETS_DIR/$1" | tr -d '\r\n\t '
    fi
}

upsert() {
    # upsert FILE NAME VALUE: replace any NAME= line in FILE with NAME=VALUE.
    file="$1"
    tmp="$file.criba-tmp"
    if [ -f "$file" ]; then
        grep -v "^$2=" "$file" > "$tmp" || true
    else
        : > "$tmp"
    fi
    printf '%s=%s\n' "$2" "$3" >> "$tmp"
    mv "$tmp" "$file"
}

safe_value() {
    # Values substituted into YAML must not break out of the line.
    case "$1" in
        *'|'* | *'
'* | *'"'* | *' '*) return 1 ;;
    esac
    return 0
}

render() {
    # render SRC DEST: copy SRC to DEST, filling @@NAME@@ placeholders.
    sed \
        -e "s|@@CRIBA_SIM_LLM_BASE_URL@@|$SIM_LLM_BASE_URL|g" \
        -e "s|@@CRIBA_SIM_LLM_MODEL@@|$SIM_LLM_MODEL|g" \
        "$1" > "$2"
}

safe_value "$SIM_LLM_BASE_URL" || die "CRIBA_SIM_LLM_BASE_URL contains a character that is not allowed"
safe_value "$SIM_LLM_MODEL" || die "CRIBA_SIM_LLM_MODEL contains a character that is not allowed"

umask 077
mkdir -p "$HERMES_HOME"

api_key="$(read_secret hermes_api_server_key)"
if [ "${#api_key}" -lt 16 ]; then
    die "hermes_api_server_key secret is missing or shorter than 16 characters"
fi
openrouter_key="$(read_secret openrouter_api_key)"
if [ -z "$openrouter_key" ]; then
    echo "[criba] openrouter_api_key secret is empty; model calls through OpenRouter will fail" >&2
fi
tools_secret="$(read_secret criba_tools_secret)"

# --- Root profile -----------------------------------------------------------------------
# Nobody calls the root listener's own routes: the backend uses /p/<profile>/. Its key is
# derived from the profile key so that the two differ (the docs say the root key is rejected
# on /p/ routes) without needing a second Compose secret.
root_key="$(printf 'criba-hermes-root:%s' "$api_key" | sha256sum | cut -d' ' -f1)"

[ -f "$CRIBA_SRC/hermes/root/config.yaml" ] || die "missing $CRIBA_SRC/hermes/root/config.yaml"
cp "$CRIBA_SRC/hermes/root/config.yaml" "$HERMES_HOME/config.yaml"

upsert "$ROOT_ENV" API_SERVER_ENABLED true
upsert "$ROOT_ENV" API_SERVER_HOST 0.0.0.0
upsert "$ROOT_ENV" API_SERVER_PORT 8642
upsert "$ROOT_ENV" API_SERVER_KEY "$root_key"
if [ -n "$openrouter_key" ]; then
    upsert "$ROOT_ENV" OPENROUTER_API_KEY "$openrouter_key"
fi
if [ -n "$tools_secret" ]; then
    upsert "$ROOT_ENV" CRIBA_TOOLS_SECRET "$tools_secret"
fi

# --- Role profiles ----------------------------------------------------------------------
# Plugins load per profile home (hermes_cli/plugins.py, PluginManager keyed by home), and so
# does the secret scope, so each profile gets its own copy of the plugin and its own keys.
for name in $PROFILES; do
    src="$CRIBA_SRC/hermes/profiles/$name"
    [ -f "$src/config.yaml" ] || die "missing $src/config.yaml"
    [ -f "$src/SOUL.md" ] || die "missing $src/SOUL.md"
    home="$HERMES_HOME/profiles/$name"
    mkdir -p "$home" "$home/plugins" "$home/skills/criba" "$home/workspace"

    render "$src/config.yaml" "$home/config.yaml"

    # Replace the plugin and skills wholesale so a deleted file never lingers.
    rm -rf "$home/plugins/criba_tools" "$home/skills/criba"
    mkdir -p "$home/skills/criba"
    cp -R "$CRIBA_SRC/hermes/plugins/criba_tools" "$home/plugins/criba_tools"

    # SOUL.md is the identity. The lean toolset has no skill tools (a skill cannot be loaded
    # on demand, and skill_manage would let the model write to a skill), so each skill the
    # profile lists in skills.txt is also appended to SOUL.md, minus its frontmatter. The
    # skill directory is installed too, for later use by delegate_task children.
    cp "$src/SOUL.md" "$home/SOUL.md"
    if [ -f "$src/skills.txt" ]; then
        grep -v '^[[:space:]]*\(#\|$\)' "$src/skills.txt" | while read -r skill; do
            skill_dir="$CRIBA_SRC/skills/$skill"
            [ -f "$skill_dir/SKILL.md" ] || die "$name lists skill $skill but $skill_dir/SKILL.md is missing"
            mkdir -p "$home/skills/$(dirname "$skill")"
            cp -R "$skill_dir" "$home/skills/$skill"
            printf '\n' >> "$home/SOUL.md"
            awk '
                NR == 1 && $0 == "---" { in_front = 1; next }
                in_front && $0 == "---" { in_front = 0; next }
                !in_front { print }
            ' "$skill_dir/SKILL.md" >> "$home/SOUL.md"
        done
    fi

    env_file="$home/$ENV_NAME"
    upsert "$env_file" API_SERVER_KEY "$api_key"
    case "$name" in
        *-sim) : ;;
        *)
            if [ -n "$openrouter_key" ]; then
                upsert "$env_file" OPENROUTER_API_KEY "$openrouter_key"
            fi
            ;;
    esac
done

unset api_key root_key openrouter_key tools_secret

exec /opt/hermes/docker/entrypoint-dispatch.sh "$@"
