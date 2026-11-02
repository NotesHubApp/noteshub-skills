#!/usr/bin/env bash
# End-to-end checks for the NotesHub skill scripts. Run from anywhere:
#   bash dev/smoke_test.sh            # Python-only checks
#   EXACT=1 bash dev/smoke_test.sh    # also load every output with the real Excalidraw and measure its text (needs Node + Playwright)
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

echo "== text measurement"
$W new text.excalidraw.svg --from text-spec.json
$W check text.excalidraw.svg
$W decode text.excalidraw.svg -o text-scene.json
python3 - <<'EOF'
import json
els = {e["id"]: e for e in json.load(open("text-scene.json"))["elements"]}
# NotesHub clips text wider than its stored width, so these must never go below the real widths:
# canvas measureText with Excalidraw's fonts. EXACT=1 measures them live with font_metrics.mjs.
real = {"title": 840.15, "notes": 633.46, "right": 388.64, "virgil": 402.26, "helvetica": 408.0,
        "code": 480.47, "nunito": 394.36, "lilita": 347.4, "comic": 495.0, "liberation": 413.58}
for id, width in real.items():
    assert width <= els[id]["width"] <= width * 1.03, (id, els[id]["width"], width)
# A label wraps by the width of its own font and fits, so the shape doesn't need to grow...
shrink = {"rectangle": 1.0, "ellipse": 0.7071, "diamond": 0.5}
for id, width in {"box": 200, "mono": 200, "oval": 240, "gem": 280}.items():
    shape = els[id]
    label = els[next(b["id"] for b in shape["boundElements"] if b["type"] == "text")]
    assert shape["width"] == width and label["width"] + 10 <= width * shrink[shape["type"]], id
# ...unless it has nowhere to wrap: this one's real width is 289.02.
assert els["word"]["width"] >= 299.02, els["word"]["width"]
EOF
$W set-label text.excalidraw.svg --id mono --text 'wrap this other label by the code font width too'
$W info text.excalidraw.svg | grep -q 'rectangle mono "wrap this other label by the code font width too" at (800, [0-9.]*) 200x'
$W decode text.excalidraw.svg -o text-restyle.json
python3 - <<'EOF'
import json
s = json.load(open("text-restyle.json"))
els = {e["id"]: e for e in s["elements"]}
els["title"]["fontSize"] = 60
els["notes"]["fontFamily"] = 3
els[next(b["id"] for b in els["box"]["boundElements"] if b["type"] == "text")]["fontSize"] = 30
els["oval"]["width"] = 140
json.dump(s, open("text-restyle.json", "w"))
EOF
$W apply text.excalidraw.svg --scene text-restyle.json
$W decode text.excalidraw.svg -o text-restyle.json
python3 - <<'EOF'
import json
els = {e["id"]: e for e in json.load(open("text-restyle.json"))["elements"]}
# Restyling through apply re-measures too: the title at twice the size, the notes in Cascadia...
assert 1680.3 <= els["title"]["width"] <= 1680.3 * 1.03, els["title"]["width"]
assert els["title"]["height"] == 75, els["title"]["height"]
assert 703.13 <= els["notes"]["width"] <= 703.13 * 1.03, els["notes"]["width"]
assert els["notes"]["lineHeight"] == 1.2 and els["notes"]["height"] == 72, els["notes"]["height"]
# ...and a bigger label rewraps, keeps its wording and still fits its shape.
box = els["box"]
label = els[next(b["id"] for b in box["boundElements"] if b["type"] == "text")]
assert label["originalText"] == "Deploy the TODO list to GitHub on 2026-11-03" and "\n" in label["text"]
assert label["width"] + 10 <= box["width"] and label["height"] + 10 <= box["height"], label["text"]
# Narrowing a shape rewraps its label without changing the wording...
oval = els[next(b["id"] for b in els["oval"]["boundElements"] if b["type"] == "text")]
assert oval["originalText"] == "Review 2026 GitHub Issues" and "\n" in oval["text"], oval["originalText"]
els["oval"]["width"] = 500
json.dump({"elements": list(els.values())}, open("text-widen.json", "w"))
EOF
$W apply text.excalidraw.svg --scene text-widen.json
$W decode text.excalidraw.svg -o text-widen.json
python3 - <<'EOF'
import json
els = {e["id"]: e for e in json.load(open("text-widen.json"))["elements"]}
# ...so widening it again puts the label back on one line.
oval = els[next(b["id"] for b in els["oval"]["boundElements"] if b["type"] == "text")]
assert oval["text"] == oval["originalText"] == "Review 2026 GitHub Issues", oval["text"]
EOF

if [[ "${EXACT:-}" == "1" ]]; then
  echo "== real Excalidraw (loadFromBlob + exportToSvg)"
  node "$S/excalidraw-render/render.mjs" --verify diagram.excalidraw.svg diagram.excalidraw Brainstorming.excalidraw.svg text.excalidraw.svg
  cp diagram.excalidraw.svg diagram-exact.excalidraw.svg
  node "$S/excalidraw-render/render.mjs" diagram-exact.excalidraw.svg
  $W check diagram-exact.excalidraw.svg
  echo "== stored text widths vs the real fonts"
  node "$HERE/font_metrics.mjs" check diagram.excalidraw.svg text.excalidraw.svg
fi

if [[ -n "${NOTESHUB_SRC:-}" ]]; then
  echo "== kanban.py vs NotesHub boardUtils.ts"
  (cd "$HERE/crosscheck" && npm install --silent --no-audit --no-fund esbuild@0.24.0 >/dev/null &&
   NODE_PATH="$NOTESHUB_SRC" npx esbuild entry.ts --bundle --platform=node --format=esm \
     --outfile=boardUtils.bundle.mjs --log-level=warning &&
   python3 crosscheck.py "$HERE/fixtures/*.kanban.md")
fi
echo "All checks passed."
