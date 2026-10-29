# NotesHub Kanban boards (`*.kanban.md`)

A board is a Markdown file whose name ends in `.kanban.md`. Without the app it reads as a
plain list (that's deliberate: no lock-in). NotesHub parses it on open and **re-serializes
the whole file on every change**, so whatever its parser ignores is lost on the next save.
`scripts/kanban.py` is a faithful port of that parser/serializer (verified against the app's
TypeScript on thousands of generated boards), so prefer it over manual edits.

## Format

```markdown
## To Do
- Replace faucets #Kitchen  
  some details
- Call plumber ~1h #Kitchen @alex 2026-11-03  
  Ask about:
  - [ ] price
  - [ ] timing

## Done
- [x] Sand and repair floor #LivingRoom 2023-06-22  
- [ ] Paint shed  
```

- **Lists (columns):** a heading `## Name` — `##`, `###` or `####` followed by a space. `#` (H1)
  and `#####`+ are *not* lists and are dropped.
- **Cards:** a line starting exactly with `- ` (dash + space) at column 0, under a list.
  `*`/`+` bullets, indented dashes and cards before the first list are dropped.
- **Card details:** following lines indented by **2 spaces or a tab**. Everything inside is
  ordinary Markdown (task lists, code, images, `## sub-headings` are fine because they're
  indented). A blank line *inside* details must still carry the 2-space indent, otherwise
  it's dropped (harmless) — but an unindented *text* line ends the card and is dropped.
- **Title metadata** is peeled off the *end* of the title line, in any order:
  - `#Tag` — letters/digits only (Unicode OK: `#Кухня`, `#日本`). `#home-reno` is not a tag;
    it stays in the title.
  - `@mention` — same character rule.
  - `~N` estimate with optional unit `m`, `h`, `d`, `w` (`~2`, `~30m`, `~1w`).
  - `YYYY-MM-DD` due date (invalid dates roll over like JavaScript: `2026-02-30` → `2026-03-02`).
  Because of this, a title that merely *ends* with `#123`, `@home`, `~5` or a date loses that
  text into metadata. `kanban.py` refuses such titles — reword them (`Fix bug (#123)`).
- **Completion:** `- [x] ` / `- [ ] ` before the title. In the app, "complete" means *the due
  date is done* (green due chip); the card editor only allows completing cards that have a
  due date. If any card in a list is completed, NotesHub writes checkboxes for **every** card
  in that list (`- [ ] …`); otherwise no checkboxes at all. Only lowercase `x` counts.
- **Subtasks:** `- [ ]` / `- [x]` lines in the details; the app shows "2/5" progress.
- **Canonical output** (what the app writes and `kanban.py` reproduces): `## Name`, cards as
  `- ` + title + ` ~est` + ` #tags` + ` @mentions` + ` date` + **two trailing spaces**, details
  indented with two spaces, one blank line between lists, no trailing newline.
- Moving a card to a "Done" list does **not** mark it complete; completion is only the
  checkbox. Do what the user's board convention implies (ask if unsure).

## kanban.py reference

```
show   BOARD [--json]                   lists with numbered cards (and dropped-line warnings)
lint   BOARD                            exit 1 if NotesHub would drop content; prints the lines
new    BOARD [--lists "A,B,C"]          create a board (file must end in .kanban.md)
add    BOARD --list L --title T [--tag X]... [--mention M]... [--due D] [--estimate E]
             [--details TEXT] [--complete] [--top]
update BOARD --card REF [--list L] [--title T] [--add-tag X] [--remove-tag X]
             [--add-mention M] [--remove-mention M] [--due D | --clear-due]
             [--estimate E | --clear-estimate] [--details T | --append-details T]
             [--complete | --uncomplete]
move   BOARD --card REF --to L [--top | --position N]
remove BOARD --card REF
task   BOARD --card REF (--add TEXT | --check TEXT | --uncheck TEXT)
add-list BOARD --name N [--position N]; rename-list --list L --name N; remove-list --list L [--force]
normalize BOARD                         rewrite in canonical form (only after lint is clean)
```

- **REF** = `"List/N"` (1-based), exact title (case-insensitive), or a unique substring.
  Ambiguous refs fail with the candidates listed — pick one and retry.
- Any write refuses to run if the file *already* contains lines NotesHub would drop; run
  `lint`, show the user those lines, and either fix them (e.g. indent a stray line into the
  card it belongs to) or rerun with `--force` once the user agrees to lose them.
- `--details` replaces the card body; `--append-details` adds to it. Pass multi-line text
  with real newlines (`$'line1\nline2'` in bash) — the script indents it for you.
- `show --json` gives machine-readable output (titles, tags, due dates, task counts, details).

## Common requests

- "Add X to my board": `add --list "To Do"` (or the board's first list) unless the user says
  otherwise. Put dates/tags in flags, not in the title.
- "What's on my plate today/this week": `show --json`, filter by `dueDate` in the user's time zone.
- "Mark X done": move it to the done list if the board has one, and `--complete` it if it has a
  due date. Say which you did.
- Daily/weekly boards: keep the user's existing list names; when rolling over a day, move
  unfinished cards rather than recreating them so their details/history survive.
