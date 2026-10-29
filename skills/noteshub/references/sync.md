# Getting notebook files and saving them back

NotesHub syncs a notebook folder with one of: GitHub, any Git server, iCloud Drive (Apple
devices), or the local file system. Work on the same storage the app uses; it merges
your changes on its next sync (it pulls periodically and on open).

## Git / GitHub notebooks (most common)

### Default: shallow text-only checkout — `scripts/notebook_sync.py`

```bash
python3 scripts/notebook_sync.py open OWNER/REPO /tmp/nb        # ~1–2 s; reuses/updates if it exists
# ... edit files under /tmp/nb with kanban.py / whiteboard.py / your editor ...
python3 scripts/notebook_sync.py status /tmp/nb
python3 scripts/notebook_sync.py save /tmp/nb -m "Kanban: add 'Call plumber' to To Do"
```

- `open` clones with `--depth 1 --filter=blob:none` and a sparse checkout of text files only
  (`*.md`, `*.excalidraw`, `*.excalidraw.svg`, configs), so image-heavy notebooks stay fast.
  Use `--full` when you need attachments (adding an image, reading a PDF).
  For a URL that isn't GitHub, pass the full clone URL.
- `save` stages everything, commits, `pull --rebase`s onto the latest remote and pushes,
  retrying once if the app pushed in between. On a real conflict it aborts without pushing:
  re-open and redo the edit on the fresh files.
- Coming back to the same session later? Run `open` again on the same folder — it pulls
  instead of re-cloning (~0.5 s).
- In hosted agent sessions the repo may first need to be attached/authorized for the session
  (e.g. an "add repository" tool); do that before cloning a private repo.

Plain git works just as well (`git clone --depth 1 …`, `git pull --rebase`, `git push`);
the script just bundles the safe defaults.

### Read-only lookups without cloning — `scripts/github_file.py`

```bash
python3 scripts/github_file.py ls  OWNER/REPO --recursive --pattern "*.kanban.md"
python3 scripts/github_file.py get OWNER/REPO "Projects/Home.kanban.md" -o /tmp/home.kanban.md
python3 scripts/kanban.py show /tmp/home.kanban.md
```

Uses the `gh` CLI. `put` can commit a single file straight through the GitHub API with
optimistic locking (the file's SHA from `get`, so a concurrent change is rejected rather than
overwritten). Some hosted sessions block API writes ("not permitted through this proxy") —
then use `notebook_sync.py`. Keep downloaded copies and their `.sha` files outside the notebook.

### Commit hygiene

- One logical change per commit; message says what changed in NotesHub terms
  ("Notes: add 'Kyoto day 3'", "Whiteboard: add payment service to Architecture").
- Commit to the branch NotesHub syncs (the repo's default branch) — NotesHub doesn't see PRs
  or other branches. Never force-push, never rewrite history.
- Don't commit tool leftovers (`*.sha`, `*.tmp-noteshub`, scratch JSON, `__pycache__`).

## iCloud Drive / local folder notebooks

The notebook is a folder on the user's device. iCloud notebooks live in NotesHub's iCloud
container, shown in Finder as **iCloud Drive › NotesHub** (on disk:
`~/Library/Mobile Documents/iCloud~com~talex~www~noteshub/Documents/`). File-system notebooks
are whatever folder the user picked. Edit files in place through whatever access you have to that
computer; the scripts take plain file paths. Don't `git init` there. iCloud syncs on its own;
NotesHub picks changes up when it next reads the file.

## Finding the right file

- Boards: `*.kanban.md`; whiteboards: `*.excalidraw.svg` / `*.excalidraw`; everything else `*.md`.
- `AGENTS.md`/`CLAUDE.md` at the root is the owner's map of the notebook — read it first.
- Search by content with `grep -ril "term" --include="*.md" .` in a checkout.
