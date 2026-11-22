#!/usr/bin/env bash
# QA for plugins: the checks every plugin must pass. No AI needed.
#
#   tools/QA.sh                       every plugin
#   tools/QA.sh triager_kev           one plugin (the folder name under src/oaken_plugins)
#   tools/QA.sh provider_imap formatter_rfc822
#
# Per plugin: (1) the shared checks (layout, extra, catalog entry, metadata, tags), (2) the plugin's own tests, (3) lint.
# Needs `uv` (https://docs.astral.sh/uv/). Exits 0 only if everything passed.
set -u
cd "$(dirname "$0")/.."
RUN=${RUN:-uv run}

if [ $# -gt 0 ]; then plugins=("$@"); else plugins=($(cd src/oaken_plugins && ls -d */ | tr -d / | grep -vE '^(mixins|__pycache__)$')); fi

failed=()
for p in "${plugins[@]}"; do
  dir=src/oaken_plugins/$p
  if [ ! -d "$dir" ]; then echo "== $p: no such plugin folder ($dir)"; failed+=("$p"); continue; fi
  echo "== $p"
  kind=$(echo "$p" | sed -E 's/^(llm_backend|[a-z]+)_.*/\1/'); name=${p#"${kind}"_}; dotted="${kind}.${name//_/-}"
  ok=1
  # 1. shared checks: -k picks this plugin's cases out of the parametrized tests
  $RUN pytest -q -p no:cacheprovider tests/test_layout.py tests/test_extras.py tests/test_plugin_metadata.py tests/test_catalog_declared.py \
      -k "$p or $dotted" 2>&1 | tail -3 || ok=0
  [ "${PIPESTATUS[0]}" = 0 ] || ok=0
  # 2. the plugin's own tests
  $RUN pytest -q -p no:cacheprovider "$dir" 2>&1 | tail -3
  [ "${PIPESTATUS[0]}" = 0 ] || ok=0
  # 3. lint
  $RUN ruff check -q "$dir" || ok=0
  if [ $ok = 1 ]; then echo "   PASS"; else echo "   FAIL"; failed+=("$p"); fi
done

echo
if [ ${#failed[@]} -eq 0 ]; then echo "QA passed: ${#plugins[@]} plugin(s)"; else echo "QA FAILED: ${failed[*]}"; exit 1; fi
