#!/usr/bin/env bash
# Contract gate (spec 005): every frozen contract in contracts/ must load or validate.
# Run from anywhere; it works from the repository root.
set -euo pipefail

cd "$(dirname "$0")/../.."

echo "==> schema.sql loads into an empty SQLite database"
sqlite3 :memory: < contracts/schema.sql

echo "==> tools.json is valid Draft 2020-12 JSON Schema"
uvx check-jsonschema --check-metaschema contracts/tools.json

echo "==> OpenAPI 3.1 documents are valid: api.openapi.yaml, webchat.openapi.yaml, voice.openapi.yaml"
uvx --from openapi-spec-validator openapi-spec-validator contracts/api.openapi.yaml contracts/webchat.openapi.yaml contracts/voice.openapi.yaml

echo "==> contract gate passed"
