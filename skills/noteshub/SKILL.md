---
name: noteshub
description: Read, add and update content in NotesHub notebooks — Markdown notes (.md), Kanban boards (.kanban.md) and Excalidraw whiteboards (.excalidraw.svg / .excalidraw) — stored in a Git/GitHub repo or a local/iCloud folder. Use whenever the user asks to add or edit notes, cards, tasks, boards or diagrams in NotesHub, or in a notes/second-brain repo that NotesHub syncs.
---

# NotesHub notebooks

NotesHub (noteshub.app) keeps everything as plain files in a folder ("notebook") that it
syncs with GitHub, any Git server, iCloud Drive or the local file system. There is no
database: editing the files *is* editing the notebook. The app picks up your changes on
its next sync, so the job is to produce files exactly the way NotesHub writes them.

| File | What it is | How to edit |
|---|---|---|
| `*.md` | note | edit directly; rules in [references/notes.md](references/notes.md) |
| `*.kanban.md` | Kanban board | **only via `scripts/kanban.py`** — see [references/kanban.md](references/kanban.md) |
| `*.excalidraw.svg` | whiteboard (SVG preview + embedded Excalidraw scene) | **only via `scripts/whiteboard.py`** — see [references/whiteboards.md](references/whiteboards.md) |
| `*.excalidraw` | whiteboard (plain Excalidraw JSON) | `scripts/whiteboard.py` |
| `.attachments/<sha1>.<ext>` | images/files attached to the notes in the same folder | see notes.md |
| `AGENTS.md` / `CLAUDE.md` at the notebook root | the owner's conventions for this notebook | read first, follow it |

Scripts live in this skill's `scripts/` folder (run them with `python3`; standard library
only). Each has `--help`.

## 1. Find the notebook

- Use what you know about the user (their notebook list, memory, a personal skill, or the
  repo they named). If several notebooks could match, or none is known, ask which one.
- Read `AGENTS.md` (or `CLAUDE.md`) at the notebook root if present: it says where things
  go (folders, board names, tags, naming). It overrides the defaults below.
- No conventions file? Look at the existing folders and file names and follow the pattern
  (e.g. PARA folders `Projects/ Areas/ Resources/ Archives/`, title-case file names). If the
  user wants agents to handle the notebook regularly, offer to create one from
  [assets/AGENTS.template.md](assets/AGENTS.template.md) with what you learn.

## 2. Get the files and save them back

Pick the lightest route that can **write** in your environment (details and commands in
[references/sync.md](references/sync.md)):

1. **Git repo (GitHub or other):** `scripts/notebook_sync.py open OWNER/REPO DIR` makes a
   shallow, text-only checkout in ~1–2 s (or updates an existing one), then edit, then
   `scripts/notebook_sync.py save DIR -m "…"` commits, rebases on the latest remote and pushes.
2. **One quick read** (look something up, show a board): `scripts/github_file.py get/ls` reads
   via the GitHub API without cloning. Writing through the API (`put`) is faster still where
   it is allowed; if it fails with "not permitted", use route 1.
3. **Local / iCloud / file-system notebook:** work on the files in place (via whatever file
   access you have to the user's computer). Don't create a git repo there.

Always start from the latest version (open/pull right before editing), keep each commit to
one logical change with a clear message ("Kanban: move 'Call plumber' to Done"), commit
straight to the branch NotesHub syncs (the default branch — never a PR unless asked), and
never force-push. If a push is rejected because the app pushed meanwhile, re-open and redo
the edit on the fresh files.

## 3. Notes (`*.md`)

GitHub-flavored Markdown plus NotesHub extras (callouts, `==highlight==`, math, Mermaid,
ABC notation, YAML frontmatter, embedded whiteboards via `![](Board.excalidraw.svg)`). Key rules — full list in notes.md:
- A single newline is a visible line break in NotesHub; write each paragraph on one line.
- Link notes with relative paths, spaces as `%20`: `[Plan](../Projects/Trip%20Plan.md)`.
- Never leave `:::conflict{variant=…}` blocks or git conflict markers in a file. If you
  find NotesHub conflict blocks, help the user pick a variant; don't silently choose.
- Keep the existing structure/heading style of a note when editing it; append rather than
  rewrite unless asked.

## 4. Kanban boards (`*.kanban.md`)

NotesHub rewrites boards from its own parser, and **silently drops anything it doesn't
recognize** (intro text, `# H1`, `*` bullets, unindented lines inside a card…). So never
hand-edit a board; use `scripts/kanban.py`, which is a line-for-line port of the app's parser
and writes byte-identical output:

```bash
python3 scripts/kanban.py show  "Board.kanban.md"            # lists, numbered cards, metadata
python3 scripts/kanban.py add   "Board.kanban.md" --list "To Do" --title "Call plumber" \
        --tag Kitchen --due 2026-11-03 --estimate 1h --details "Ask about price"
python3 scripts/kanban.py move  "Board.kanban.md" --card "plumber" --to "Done"
python3 scripts/kanban.py update "Board.kanban.md" --card "Doing/2" --complete
python3 scripts/kanban.py task  "Board.kanban.md" --card "plumber" --check "price"
python3 scripts/kanban.py lint  "Board.kanban.md"             # what NotesHub would drop
```

Card refs: `"List/N"` (1-based), an exact title, or a unique part of a title. The script
refuses ambiguous refs and refuses to write if the file already contains content NotesHub
would drop (fix it, or `--force` after telling the user). Tags/mentions are letters and digits
only; due dates are `YYYY-MM-DD` (compute "today"/"Friday" in the user's time zone); a card
can only be marked complete if it has a due date. New board: `kanban.py new "X.kanban.md"
--lists "To Do,Doing,Done"`.

## 5. Whiteboards (`*.excalidraw.svg`, `*.excalidraw`)

NotesHub reads only the Excalidraw scene embedded in the file; the SVG drawing is a preview
for GitHub and file browsers. Never hand-edit the SVG. Use `scripts/whiteboard.py`:

```bash
python3 scripts/whiteboard.py info  "Diagram.excalidraw.svg"      # shapes, labels, arrows, ids, free space
python3 scripts/whiteboard.py new   "Diagram.excalidraw.svg" --from spec.json
python3 scripts/whiteboard.py add   "Diagram.excalidraw.svg" --from more.json
python3 scripts/whiteboard.py set-label "Diagram.excalidraw.svg" --id gh --text "GitHub"
python3 scripts/whiteboard.py decode "Diagram.excalidraw.svg" -o scene.json   # then edit JSON and:
python3 scripts/whiteboard.py apply "Diagram.excalidraw.svg" --scene scene.json
```

`spec.json` is a JSON array in the compact format Excalidraw's own agent tools use — shapes
with labels, arrows connected by id:

```json
[
  {"type": "text", "id": "title", "x": 100, "y": 20, "text": "Sync flow", "fontSize": 28},
  {"type": "rectangle", "id": "app", "x": 100, "y": 100, "width": 180, "height": 80,
   "backgroundColor": "#a5d8ff", "label": "NotesHub app"},
  {"type": "ellipse", "id": "gh", "x": 420, "y": 90, "width": 180, "height": 100,
   "backgroundColor": "#b2f2bb", "label": {"text": "GitHub", "fontSize": 20}},
  {"type": "arrow", "id": "a1", "from": "app", "to": "gh", "label": "push / pull"}
]
```

Layout, colors, every option, editing existing boards and gotchas:
[references/whiteboards.md](references/whiteboards.md) — read it before drawing anything
non-trivial. Files written by the script carry a simplified (non-hand-drawn) preview. Where
Node.js and Playwright are available, run `whiteboard.py render-exact FILE` to redraw the
real Excalidraw preview; otherwise tell the user the preview refreshes when they next save
the whiteboard in NotesHub.

## 6. Before you save

- `kanban.py lint` on every board you touched; `whiteboard.py check` on every whiteboard.
- `git diff --stat` (or review the changed files): only the files you meant to change.
- No conflict markers, no temp files (`*.sha`, `*.tmp-noteshub`, scratch JSON) inside the notebook.

Then tell the user what changed in a sentence or two (which board/note, what moved or was
added) — they will see it in NotesHub after its next sync.
