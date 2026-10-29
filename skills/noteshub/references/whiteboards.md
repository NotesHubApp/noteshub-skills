# NotesHub whiteboards (Excalidraw)

NotesHub's whiteboard editor is Excalidraw. A whiteboard is saved as either:

- `Name.excalidraw.svg` (default) — a normal SVG picture with the full Excalidraw scene
  embedded in a comment (`<!-- payload-start -->…<!-- payload-end -->`, base64 + zlib).
  GitHub and file browsers show the picture; NotesHub **only reads the embedded scene**.
- `Name.excalidraw` — the plain Excalidraw JSON (`{"type":"excalidraw","version":2,"elements":[…]}`).

Never edit the SVG markup or the payload by hand: `scripts/whiteboard.py` decodes, edits,
validates and re-encodes the scene, and redraws a preview. Files it writes load in the real
Excalidraw library exactly like files saved by the app (verified with `loadFromBlob`).

## Commands

```
info    FILE                     outline: every shape with id, label, position/size, colors;
                                 arrows as "from -> to"; free text; free space to the right/below
decode  FILE [-o scene.json]     full scene JSON (elements, appState, files)
new     FILE --from SPEC.json    create (.excalidraw.svg or .excalidraw by extension)
add     FILE --from SPEC.json    append elements; {"type":"delete","ids":"a,b"} removes elements
set-label FILE --id ID --text T  change a text element or the label of a shape/arrow
                                 (re-wraps, re-centers, grows the shape, keeps arrows attached)
apply   FILE --scene scene.json  replace the scene with an edited `decode` output
preview FILE                     rebuild the simplified preview from the scene
check   FILE                     validate (ids, bindings, sync-conflict leftovers)
render-exact FILE                redraw the preview with the real Excalidraw renderer
```

## Element spec (for `new` / `add`)

A JSON array. Same compact format as Excalidraw's own MCP server ("labels" on shapes, arrows
bound by id), so diagrams you'd draw there work here. Required: `type`; give every element a
short readable `id` (`"api"`, `"db"`, `"a1"`) so arrows and later edits can refer to it.

**Shapes** — `rectangle`, `ellipse`, `diamond`:
```json
{"type": "rectangle", "id": "api", "x": 100, "y": 100, "width": 180, "height": 80,
 "backgroundColor": "#a5d8ff", "label": "API server"}
```
- `label`: a string or `{"text": "…", "fontSize": 20, "strokeColor": "#1e1e1e"}`; centered,
  word-wrapped; the shape grows if the text doesn't fit. Use `\n` for explicit line breaks.
- Rectangles default to rounded corners (like the app); `"roundness": null` for sharp ones.

**Arrows / lines** — `arrow` (arrowhead at the end by default) or `line`:
```json
{"type": "arrow", "id": "a1", "from": "api", "to": "db", "label": "SQL"}
```
- `from`/`to`: element ids. The arrow is drawn straight between the two outlines and
  *bound* to both shapes, so it follows them when the user drags them in NotesHub.
- Pin an end to a side with MCP-style bindings: `"startBinding": {"elementId": "api",
  "fixedPoint": [0.5, 1]}` (top `[0.5,0]`, bottom `[0.5,1]`, left `[0,0.5]`, right `[1,0.5]`).
- Free-form: `"x", "y", "points": [[0,0],[200,0],[200,100]]` (points relative to x,y).
- `startArrowhead` / `endArrowhead`: `null`, `"arrow"`, `"triangle"`, `"bar"`, `"dot"`,
  `"circle"`, `"diamond"` (and `_outline` variants).
- `label` works like on shapes (default font 16).

**Text** (titles, notes):
```json
{"type": "text", "id": "title", "x": 100, "y": 20, "text": "Architecture", "fontSize": 28}
```
`x`,`y` is the top-left corner. Width ≈ `len(text) × fontSize × 0.55`.

**Frame**: `{"type": "frame", "id": "f1", "x": 0, "y": 0, "width": 800, "height": 500, "name": "v2"}`.

**Common style fields** (all optional): `strokeColor` (#1e1e1e), `backgroundColor`
("transparent"), `fillStyle` ("solid" | "hachure" | "cross-hatch"), `strokeWidth` (1, 2, 4),
`strokeStyle` ("solid" | "dashed" | "dotted"), `roughness` (0 = clean, 1 = hand-drawn, 2 = sketchy),
`opacity` (0–100), `angle` (radians), `fontFamily` for text/labels (`"excalifont"` default
hand-drawn, `"nunito"` normal, `"code"`, `"virgil"`, or Excalidraw's numeric ids 5/6/3/1),
`groupIds`, `link`, `locked`.

`cameraUpdate` and `restoreCheckpoint` entries (MCP-only) are ignored, so output written for
Excalidraw's MCP can be passed through as-is.

## Layout guidance

- Plan a grid: shapes ~160–220 × 70–100, 80–120 px gaps; flows left→right or top→bottom.
- Fonts: titles 24–32, labels 16–20, never below 14.
- Palette (Excalidraw's): fills `#a5d8ff` blue, `#b2f2bb` green, `#ffd8a8` orange,
  `#d0bfff` purple, `#ffc9c9` red, `#fff3bf` yellow, `#c3fae8` teal; strokes `#1e1e1e`,
  `#1971c2`, `#2f9e44`, `#e8590c`, `#e03131`, `#6741d9`. Text on white: `#1e1e1e` or `#495057`,
  never light gray.
- No emoji in labels (Excalidraw's hand-drawn font can't render them).
- For an existing board, run `info` first and place new content in the free space it reports,
  matching the board's existing colors and font sizes.

## Editing existing whiteboards

- **Add things:** `add --from more.json`; new arrows can connect to existing ids from `info`.
- **Rename/relabel:** `set-label --id <shape-or-text-id> --text "…"`.
- **Delete:** `add` with `[{"type": "delete", "ids": "a1,api"}]` (removes bound labels and
  detaches arrows). Deleted elements stay in the file marked `isDeleted` — that's how
  Excalidraw syncs; don't strip them.
- **Anything else** (move, recolor, resize, restyle): `decode -o scene.json`, edit the
  elements, `apply --scene scene.json`. `apply` bumps element versions, keeps `text` and
  `originalText` in sync, re-measures changed text, moves labels with their shapes and keeps
  bound arrows attached to moved/resized shapes. Don't change `id`s; don't touch `files`
  (embedded images) or `index` unless you know why.
- Freehand drawings (`freedraw`) and images are preserved but can't be created here.

## The preview

Files written by `whiteboard.py` carry a simplified, clean (not hand-drawn) preview, marked
`data-noteshub-preview="simplified"`. It shows the right content on GitHub; NotesHub itself
ignores it and redraws the full hand-drawn preview the next time the whiteboard is saved in
the app.

`render-exact` replaces it with the real Excalidraw rendering (identical to what NotesHub
saves). It needs Node.js 18+, npm registry access, and Playwright + Chromium; the first run
installs the Excalidraw library into `~/.cache/noteshub-skills` (~15 s; override with
`NOTESHUB_RENDER_CACHE`). It also drops `isDeleted` elements, exactly like an app save. If it
can't run, keep the simplified preview and mention it.

## Sync conflicts

If NotesHub couldn't merge a whiteboard, the file may contain two `<svg>…</svg>` documents
back to back; NotesHub asks the user to pick one when they open it. `whiteboard.py` refuses to
edit such a file — tell the user to resolve it in the app (or, if they ask you to, keep the
version they choose by deleting the other `<svg>` block).
