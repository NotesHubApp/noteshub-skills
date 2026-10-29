# kanban.py vs NotesHub's parser

`crosscheck.py` feeds real boards plus 3,000 generated edge cases through both
`skills/noteshub/scripts/kanban.py` and the app's `src/components/kanban/boardUtils.ts`
(bundled with esbuild from a local NotesHub checkout) and compares the parsed boards and the
re-serialized Markdown. Run it via `NOTESHUB_SRC=/path/to/noteshub/src bash dev/smoke_test.sh`.
The bundle is git-ignored because it contains app source.

Note for whiteboards: the Excalidraw MCP server's element format that `whiteboard.py`
accepts comes from https://github.com/excalidraw/excalidraw-mcp (MIT).
