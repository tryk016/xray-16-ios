#!/usr/bin/env bash
# Thin launcher: gate selection and process handling live in the audited Python tool.
set -eu
exec python3 "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/run_gate_logged.py" "$@"
