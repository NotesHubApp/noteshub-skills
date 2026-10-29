# NotesHub skills for AI agents

Lets Claude (and other agents that support [Agent Skills](https://agentskills.io)) work
directly in your [NotesHub](https://noteshub.app) notebooks: add and update **notes**, move
cards on **Kanban boards**, and draw or edit **Excalidraw whiteboards** — in the exact format
NotesHub reads and writes, so nothing gets lost on the app's next save.

> "Add *call the plumber* to my renovation board for Monday, tag it Kitchen"
> "Move everything finished this week to Done"
> "Draw our sync architecture as a whiteboard in Projects/NotesHub"
> "Append today's meeting notes to the Q4 planning note"

## Install

**Claude (web, desktop, mobile)** — skills need code execution turned on in Settings.
Open **Customize → Plugins**, add this GitHub marketplace: `NotesHubApp/noteshub-skills`,
then install **NotesHub**. It's attached to your account, so it's also available in Claude
Code sessions signed in with the same account.

**Claude Code**

```bash
claude plugin marketplace add NotesHubApp/noteshub-skills
claude plugin install noteshub@noteshub-skills
```

**Other agents** (Codex, Cursor, GitHub Copilot, Gemini CLI, …)

```bash
npx skills add NotesHubApp/noteshub-skills
```

Your notebook must be reachable by the agent: a GitHub/Git repo it can clone and push to, or
a local/iCloud folder it can access.

## Tell the agent about your notebook (optional, recommended)

Add an `AGENTS.md` (or `CLAUDE.md`) at the root of a notebook describing where things go —
folders, which board is your daily board, tags you use, what not to touch. The skill reads it
before every change. Start from [the template](skills/noteshub/assets/AGENTS.template.md).

## What's inside

| | |
|---|---|
| [`skills/noteshub/SKILL.md`](skills/noteshub/SKILL.md) | The skill: how NotesHub stores notebooks and how to change them safely |
| `scripts/kanban.py` | Kanban boards — a line-for-line port of NotesHub's own board parser/serializer (output is byte-identical to the app's) |
| `scripts/whiteboard.py` | Excalidraw whiteboards — create from simple specs (Excalidraw MCP-compatible), add/relabel/move/delete, validate, preview |
| `scripts/excalidraw-render/` | Optional: redraw whiteboard previews with the real Excalidraw renderer (Node.js + Playwright) |
| `scripts/notebook_sync.py` | Fast text-only Git checkout of a notebook and safe commit/rebase/push |
| `scripts/github_file.py` | Read (and, where allowed, write) single files via the GitHub API without cloning |
| `references/` | Format details for notes, boards, whiteboards and syncing |

Scripts need Python 3.8+ (standard library only) and `git`; `gh` and Node.js are optional.

## Development

```bash
bash dev/smoke_test.sh                                    # Python checks
EXACT=1 bash dev/smoke_test.sh                            # + load outputs in real Excalidraw
NOTESHUB_SRC=/path/to/noteshub/src bash dev/smoke_test.sh # + cross-check kanban.py against the app
```

`claude plugin validate .` checks the plugin and marketplace manifests. The plugin has no
`version` on purpose: every commit to `main` is a new version, so installs that sync or
auto-update pick up each push. (The validator's "No version specified" warning is expected.)

## License

MIT
