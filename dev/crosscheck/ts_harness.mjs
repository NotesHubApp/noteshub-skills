// Runs NotesHub's real boardUtils.ts (bundled) over JSON inputs from stdin.
import { markdownToKanbanBoard, kanbanBoardToMarkdown, toSerializedDateString } from './boardUtils.bundle.mjs';
let raw = '';
process.stdin.on('data', d => raw += d);
process.stdin.on('end', () => {
  const inputs = JSON.parse(raw);
  const out = inputs.map(md => {
    const board = markdownToKanbanBoard(md).value;
    return {
      board: board.lists.map(l => ({ name: l.name, cards: l.cards.map(c => ({
        title: c.title, isCompleted: c.isCompleted,
        dueDate: c.dueDate ? toSerializedDateString(c.dueDate) : null,
        tags: c.tags, mentions: c.mentions,
        estimates: c.estimates ? `${c.estimates.value}${c.estimates.type ?? ''}` : null,
        tasks: { total: c.totalTasks, completed: c.completedTasks }, details: c.details })) })),
      markdown: kanbanBoardToMarkdown(board)
    };
  });
  process.stdout.write(JSON.stringify(out));
});
