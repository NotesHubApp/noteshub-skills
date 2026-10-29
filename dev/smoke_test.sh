#!/usr/bin/env bash
# End-to-end checks for the NotesHub skill scripts. Run from anywhere:
#   bash dev/smoke_test.sh            # Python-only checks
#   EXACT=1 bash dev/smoke_test.sh    # also load every output with the real Excalidraw (needs Node + Playwright)
#   NOTESHUB_SRC=~/code/noteshub/src bash dev/smoke_test.sh   # also cross-check kanban.py against the app's parser
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
S="$HERE/../skills/noteshub/scripts"
OUT="$HERE/out"
rm -rf "$OUT" && mkdir -p "$OUT"
cp "$HERE/fixtures/"* "$OUT/"
cd "$OUT"

echo "== kanban"
K="python3 $S/kanban.py"
$K lint "Home Renovation.kanban.md"
$K add "Home Renovation.kanban.md" --list "To Do" --title "Call plumber" --tag Kitchen --due 2026-11-03 --estimate 1h --top \
  --details $'Ask about:\n- [ ] price\n- [ ] timing'
$K move "Home Renovation.kanban.md" --card plumber --to Doing --top
$K task "Home Renovation.kanban.md" --card plumber --check price
$K update "Home Renovation.kanban.md" --card "Doing/1" --complete --add-tag Urgent
! $K add "Home Renovation.kanban.md" --list "To Do" --title "Fix bug #123" 2>/dev/null
! $K add "Home Renovation.kanban.md" --list "To Do" --title "x" --tag home-reno 2>/dev/null
! $K move "Home Renovation.kanban.md" --card Paint --to Done 2>/dev/null
$K lint "Home Renovation.kanban.md"
grep -q -- "- \[x\] Call plumber ~1h #Kitchen #Urgent 2026-11-03  " "Home Renovation.kanban.md"

echo "== whiteboard"
W="python3 $S/whiteboard.py"
$W new diagram.excalidraw.svg --from diagram-spec.json
$W add diagram.excalidraw.svg --from diagram-add.json
$W set-label diagram.excalidraw.svg --id gh --text 'GitHub\nMySecondBrain'
$W set-label diagram.excalidraw.svg --id a4 --text sync
$W new diagram.excalidraw --from diagram-spec.json
$W decode Brainstorming.excalidraw.svg -o scene.json
python3 - <<'EOF'
import json
s = json.load(open("scene.json"))
for e in s["elements"]:
    if e["type"] == "text" and e["text"] == "NotesHub":
        e["text"] = "NotesHub app"
    if e["type"] == "rectangle" and e["strokeColor"] == "#e03131":
        e["x"] += 40
json.dump(s, open("scene.json", "w"))
EOF
$W apply Brainstorming.excalidraw.svg --scene scene.json
for f in diagram.excalidraw.svg diagram.excalidraw Brainstorming.excalidraw.svg; do $W check "$f"; done
$W info diagram.excalidraw.svg | grep -q 'arrow a4: phone "Phone" -> gh "GitHub / MySecondBrain" "sync"'

if [[ "${EXACT:-}" == "1" ]]; then
  echo "== real Excalidraw (loadFromBlob + exportToSvg)"
  node "$S/excalidraw-render/render.mjs" --verify diagram.excalidraw.svg diagram.excalidraw Brainstorming.excalidraw.svg
  cp diagram.excalidraw.svg diagram-exact.excalidraw.svg
  node "$S/excalidraw-render/render.mjs" diagram-exact.excalidraw.svg
  $W check diagram-exact.excalidraw.svg
fi

if [[ -n "${NOTESHUB_SRC:-}" ]]; then
  echo "== kanban.py vs NotesHub boardUtils.ts"
  (cd "$HERE/crosscheck" && npm install --silent --no-audit --no-fund esbuild@0.24.0 >/dev/null &&
   NODE_PATH="$NOTESHUB_SRC" npx esbuild entry.ts --bundle --platform=node --format=esm \
     --outfile=boardUtils.bundle.mjs --log-level=warning &&
   python3 crosscheck.py "$HERE/fixtures/*.kanban.md")
fi
echo "All checks passed."
