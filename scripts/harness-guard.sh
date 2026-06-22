#!/usr/bin/env bash
# Harness shell guard — source this to make EVERY bio-tool invocation visible to
# the operator. Each call is appended to the central audit log; a call made
# OUTSIDE a harness run (HARNESS_RUN_ID unset) prints a loud warning, so nobody
# (human, AI, or agent) can run an analysis tool off the books unnoticed.
#
#   source scripts/harness-guard.sh
#
# Honest scope: this is visibility/enforcement-by-logging at the shell level, not
# a kernel sandbox. The real isolation is the harness sandbox (bwrap/apptainer).

_HARNESS_GUARD_TOOLS="mash mafft muscle fasttree FastTree iqtree iqtree2 iqtree3 raxmlHPC raxml-ng astral astral4 cactus samtools bcftools trimal"

_harness_audit_append() {
  local tool="$1"; shift
  local log="${HARNESS_AUDIT_LOG:-$HOME/.harness/audit.jsonl}"
  mkdir -p "$(dirname "$log")"
  local ts; ts="$(date -Is)"
  # JSON-escape cwd/args minimally.
  local cwd="${PWD//\\/\\\\}"; cwd="${cwd//\"/\\\"}"
  printf '{"ts":"%s","event":"tool_call","tool":"%s","harness_run_id":%s,"cwd":"%s","user":"%s","host":"%s","pid":%d}\n' \
    "$ts" "$tool" \
    "$([ -n "$HARNESS_RUN_ID" ] && printf '"%s"' "$HARNESS_RUN_ID" || printf 'null')" \
    "$cwd" "${USER:-?}" "$(hostname)" "$$" >> "$log"
}

_harness_make_wrapper() {
  local tool="$1"
  command -v "$tool" >/dev/null 2>&1 || return 0
  eval "
  ${tool}() {
    _harness_audit_append '${tool}'
    if [ -z \"\$HARNESS_RUN_ID\" ]; then
      printf '\033[1;33m⚠ [harness-guard] %s ejecutado FUERA del harness — registrado pero NO auditable como run (sin manifest/guards). Usa: python -m harness ...\033[0m\n' '${tool}' >&2
    fi
    command ${tool} \"\$@\"
  }
  "
}

for _t in $_HARNESS_GUARD_TOOLS; do _harness_make_wrapper "$_t"; done
unset _t
echo "[harness-guard] activo: toda llamada a herramientas bio queda en ${HARNESS_AUDIT_LOG:-$HOME/.harness/audit.jsonl}"
echo "[harness-guard] ver con: python -m harness audit"
