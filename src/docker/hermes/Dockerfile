# Hermes agent runtime, from the official image (Docker Hub nousresearch/hermes-agent,
# documented in hermes-agent website/docs/user-guide/docker.md). Pinned by tag and
# multi-arch index digest. The spikes ran 0.21.5, which has no published image tag;
# v0.21.6 is the only stable v0.21.x tag on Docker Hub as of 2026-10-09.
FROM nousresearch/hermes-agent:v0.21.6@sha256:55e192fba0cd4fde61142abbff5adeacff40efdb482ccd0ff877924bd274f909

# Criba sources, baked into /opt/criba. The entrypoint copies them into the Hermes home
# (/opt/data, a volume) on every start, so the volume never holds a stale copy.
#
# Build contexts (docker-compose.yml, hermes service, additional_contexts):
#   hermes_src  = src/hermes   profiles and the criba-tools plugin
#   skills_src  = src/skills   the Screener skill
#   contracts   = contracts    tools.json, the single source of the tool schemas
COPY --from=hermes_src . /opt/criba/hermes
COPY --from=skills_src . /opt/criba/skills
COPY --from=contracts tools.json /opt/criba/hermes/plugins/criba_tools/tools.json

COPY --chmod=0755 entrypoint.sh /usr/local/bin/criba-hermes-entrypoint.sh

# Our entrypoint writes the root config, each profile and its secrets into $HERMES_HOME,
# then execs the image's own dispatcher, which stays PID 1 and starts s6-overlay as usual.
ENTRYPOINT ["/usr/local/bin/criba-hermes-entrypoint.sh"]
CMD ["gateway", "run"]
