#!/usr/bin/env bash
# Start a new plugin from a working template, then run QA on it. No AI needed.
#
#   tools/new_plugin.sh KIND NAME        tools/new_plugin.sh triager semantic
#
# KIND is a kind that has a template under template/ (today: triager, enricher). The new folder is
# src/oaken_plugins/KIND_NAME; its extra, `oaken-plugins[KIND.NAME]`, is added to pyproject.toml.
# Edit the folder, run tools/QA.sh KIND_NAME until it passes, then `oaken plugin add KIND.NAME`.
set -eu
cd "$(dirname "$0")/.."
kind=${1:-}; name=${2:-}
[ -n "$kind" ] && [ -n "$name" ] || { echo "usage: tools/new_plugin.sh KIND NAME   (e.g. triager semantic)"; exit 2; }
[ -d "template/${kind}_example" ] || { echo "no template for kind '$kind'; templates: $(cd template && ls | sed 's/_example//' | tr '\n' ' ')"; exit 2; }
echo "$name" | grep -qE '^[a-z][a-z0-9_]*$' || { echo "NAME is lowercase letters, digits and _ (starting with a letter): '$name'"; exit 2; }
dest=src/oaken_plugins/${kind}_${name}
[ ! -e "$dest" ] || { echo "$dest already exists"; exit 2; }

camel=$(echo "$name" | sed -E 's/(^|_)([a-z0-9])/\U\2/g')
cp -r "template/${kind}_example" "$dest"
find "$dest" -name __pycache__ -prune -exec rm -rf {} +
find "$dest" -type f | while read -r f; do
  sed -i -e "s/${kind}_example/${kind}_${name}/g" -e "s/Example/${camel}/g" -e "s/example/${name}/g" "$f"
done
for f in $(find "$dest" -name '*example*'); do mv "$f" "${f//example/$name}"; done

# its extra: libraries it needs go in the list; empty means it needs nothing beyond oaken-core
python3 - "$kind" "$name" <<'PY'
import re, sys
kind, name = sys.argv[1], sys.argv[2].replace("_", "-")
s = open("pyproject.toml").read()
line = f'"{kind}.{name}" = []\n'
rows = re.findall(r'^"[a-z_]+\.[a-z0-9-]+" = \[.*\]\n', s, re.M)
rows = sorted(rows + [line])
first, last = s.index(re.findall(r'^"[a-z_]+\.[a-z0-9-]+" = \[.*\]\n', s, re.M)[0]), s.index(re.findall(r'^"[a-z_]+\.[a-z0-9-]+" = \[.*\]\n', s, re.M)[-1])
last = s.index("\n", last) + 1
open("pyproject.toml", "w").write(s[:first] + "".join(rows) + s[last:])
PY
echo "created $dest and the extra \"${kind}.${name//_/-}\""
echo
tools/QA.sh "${kind}_${name}"
echo
echo "Next: edit $dest/__init__.py, then re-run: tools/QA.sh ${kind}_${name}"
