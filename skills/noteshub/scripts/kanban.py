#!/usr/bin/env python3
"""Read and edit NotesHub Kanban boards (*.kanban.md) exactly the way the app does.

The parser and serializer mirror NotesHub's own `boardUtils.ts`, so a board this
script writes is byte-for-byte what NotesHub itself would save. Python 3.8+,
standard library only.

Usage (run `kanban.py <command> -h` for details):

  kanban.py show   BOARD [--json]
  kanban.py lint   BOARD
  kanban.py new    BOARD [--lists "To Do,Doing,Done"]
  kanban.py add    BOARD --list NAME --title TEXT [--tag T]... [--mention M]...
                         [--due YYYY-MM-DD] [--estimate 2h] [--details TEXT] [--top]
  kanban.py update BOARD --card REF [--title T] [--add-tag T] [--remove-tag T]
                         [--due D | --clear-due] [--estimate E | --clear-estimate]
                         [--complete | --uncomplete] [--details T | --append-details T]
  kanban.py move   BOARD --card REF --to LIST [--top | --position N]
  kanban.py remove BOARD --card REF
  kanban.py task   BOARD --card REF (--add TEXT | --check TEXT | --uncheck TEXT)
  kanban.py add-list    BOARD --name NAME [--position N]
  kanban.py rename-list BOARD --list NAME --name NEW
  kanban.py remove-list BOARD --list NAME [--force]
  kanban.py normalize   BOARD

A card REF is either "List name/N" (N is 1-based within that list), an exact card
title (case-insensitive), or a unique substring of a title. Add --list to narrow
the search to one list.

Every command that writes refuses to run when the existing file contains content
NotesHub would drop on its next save (text outside cards, H1 headings, `*` bullets,
etc.), unless --force is given. Run `lint` to see what would be dropped.
"""

import argparse
import datetime as _dt
import json
import re
import sys
import unicodedata

# --------------------------------------------------------------------------------------
# Faithful port of NotesHub src/components/kanban/boardUtils.ts
# --------------------------------------------------------------------------------------

# JavaScript's `.` does not match line terminators; Python's matches \r,  ,  .
_DOT = r"[^\n\r  ]"
COLUMN_TITLE_RE = re.compile(r"^#{2,4} (" + _DOT + r"+)")
CARD_RAW_TITLE_RE = re.compile(r"^- (\[( |x)\] )?(" + _DOT + r"+)")
CARD_DETAILS_RE = re.compile(r"^(\t| {2})(" + _DOT + r"*)")
ESTIMATES_RE = re.compile(r"^(" + _DOT + r"+?)~([0-9]+)(m|h|d|w)?$")
DUE_DATE_RE = re.compile(r"^(" + _DOT + r"+?)([0-9]{4}-[0-9]{2}-[0-9]{2})$")
ISO_DATE_RE = re.compile(r"^([0-9]{4})-([0-9]{1,2})-([0-9]{1,2})$")
TASK_RE = re.compile(r"^\s*[*+-] \[(x| )\]", re.I | re.M)

# Characters JavaScript's String.prototype.trim() removes.
_JS_WS = (
    "\t\n\v\f\r            "
    "      　﻿"
)


def js_trim(s):
    return s.strip(_JS_WS)


def _is_letter_or_number(ch):
    return unicodedata.category(ch)[0] in ("L", "N")


def _match_suffix_token(title, sigil):
    """Mirror /(.+?)<sigil>([\\p{L}\\p{N}]+)$/u: earliest sigil (not at index 0)
    whose remainder is one or more letters/numbers. Returns (prefix, token) or None."""
    for i, ch in enumerate(title):
        if ch != sigil or i == 0:
            continue
        prefix, rest = title[:i], title[i + 1:]
        if any(c in "\n\r  " for c in prefix):
            continue
        if rest and all(_is_letter_or_number(c) for c in rest):
            return prefix, rest
    return None


def parse_serialized_date(s):
    """Mirror parseSerializedDate: `new Date(y, m - 1, d)` including JS rollover
    (2023-02-30 -> 2023-03-02, month 00 -> previous December, years 0-99 -> 19xx)."""
    m = ISO_DATE_RE.match(s)
    if not m:
        return None
    year, month, day = int(m.group(1)), int(m.group(2)), int(m.group(3))
    if 0 <= year <= 99:
        year += 1900
    month_index = month - 1
    year += month_index // 12
    month_index %= 12
    try:
        return _dt.date(year, month_index + 1, 1) + _dt.timedelta(days=day - 1)
    except (OverflowError, ValueError):
        return None


def to_serialized_date_string(d):
    return "%d-%02d-%02d" % (d.year, d.month, d.day)


class Card(object):
    def __init__(self, title, is_completed=False, details="", due_date=None, tags=None,
                 mentions=None, estimates=None):
        self.title = title
        self.is_completed = is_completed
        self.details = details
        self.due_date = due_date          # datetime.date or None
        self.tags = list(tags or [])
        self.mentions = list(mentions or [])
        self.estimates = estimates        # (value:int, unit:str|None) or None
        self.total_tasks = 0
        self.completed_tasks = 0

    def update_computed(self):
        if self.details:
            matches = TASK_RE.findall(self.details)
            self.total_tasks = len(matches)
            self.completed_tasks = len([x for x in matches if x != " "])
        else:
            self.total_tasks = self.completed_tasks = 0

    def to_dict(self):
        return {
            "title": self.title,
            "isCompleted": self.is_completed,
            "dueDate": to_serialized_date_string(self.due_date) if self.due_date else None,
            "tags": self.tags,
            "mentions": self.mentions,
            "estimates": ("%d%s" % (self.estimates[0], self.estimates[1] or "")) if self.estimates else None,
            "tasks": {"total": self.total_tasks, "completed": self.completed_tasks},
            "details": self.details,
        }


class KList(object):
    def __init__(self, name, cards=None):
        self.name = name
        self.cards = list(cards or [])


class Board(object):
    def __init__(self, lists=None):
        self.lists = list(lists or [])

    def to_dict(self):
        return {"lists": [{"name": l.name, "cards": [c.to_dict() for c in l.cards]} for l in self.lists]}


def create_card(raw_title):
    """Mirror createCard: peel #tags, @mentions, ~estimates and a due date off the end."""
    title = js_trim(raw_title)
    due_date = None
    tags, mentions = [], []
    estimates = None
    while True:
        m = _match_suffix_token(title, "#")
        if m:
            title = js_trim(m[0])
            if m[1] not in tags:
                tags.insert(0, m[1])
            continue
        m = _match_suffix_token(title, "@")
        if m:
            title = js_trim(m[0])
            if m[1] not in mentions:
                mentions.insert(0, m[1])
            continue
        m = ESTIMATES_RE.match(title)
        if m:
            title = js_trim(m.group(1))
            if not estimates:
                estimates = (int(m.group(2)), m.group(3))
            continue
        m = DUE_DATE_RE.match(title)
        if m:
            title = js_trim(m.group(1))
            if not due_date:
                due_date = parse_serialized_date(m.group(2))
            continue
        break
    return Card(title, False, "", due_date, tags, mentions, estimates)


class ParseResult(object):
    def __init__(self, board, dropped):
        self.board = board
        self.dropped = dropped  # list of (line_number, text) NotesHub ignores


def parse_board(markdown):
    """Mirror markdownToKanbanBoard, and also report non-blank lines it ignores."""
    board = Board()
    dropped = []
    current_list = None
    current_card = None
    for n, line in enumerate(markdown.split("\n"), start=1):
        m = COLUMN_TITLE_RE.match(line)
        if m:
            current_list = KList(js_trim(m.group(1)))
            board.lists.append(current_list)
            continue
        m = CARD_RAW_TITLE_RE.match(line)
        if m and current_list is not None:
            current_card = create_card(js_trim(m.group(3)))
            current_card.is_completed = m.group(2) == "x"
            current_list.cards.append(current_card)
            continue
        m = CARD_DETAILS_RE.match(line)
        if m and current_card is not None:
            text = m.group(2)
            current_card.details = (current_card.details + "\n" + text) if current_card.details else (current_card.details + text)
            continue
        if js_trim(line):
            dropped.append((n, line))
    for l in board.lists:
        for c in l.cards:
            c.update_computed()
    return ParseResult(board, dropped)


def serialize_board(board):
    """Mirror kanbanBoardToMarkdown."""
    lines = []
    for index, lst in enumerate(board.lists):
        lines.append("## %s" % lst.name)
        has_completed = any(c.is_completed for c in lst.cards)
        for card in lst.cards:
            title_line = js_trim(card.title)
            if card.estimates:
                title_line += " ~%d%s" % (card.estimates[0], card.estimates[1] or "")
            if card.tags:
                title_line += " " + " ".join("#" + t for t in card.tags)
            if card.mentions:
                title_line += " " + " ".join("@" + x for x in card.mentions)
            if card.due_date:
                title_line += " " + to_serialized_date_string(card.due_date)
            if has_completed:
                lines.append("- [%s] %s  " % ("x" if card.is_completed else " ", title_line))
            else:
                lines.append("- %s  " % title_line)
            if card.details:
                for detail_line in card.details.split("\n"):
                    lines.append("  " + detail_line)
        if index < len(board.lists) - 1:
            lines.append("")
    return "\n".join(lines)


# --------------------------------------------------------------------------------------
# Helpers for safe editing
# --------------------------------------------------------------------------------------

class KanbanError(Exception):
    pass


def _read(path):
    with open(path, "r", encoding="utf-8", newline="") as f:
        return f.read()


def _write(path, text):
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(text)


def _check_name_token(value, kind):
    if not value or not all(_is_letter_or_number(c) for c in value):
        raise KanbanError(
            "%s %r can only contain letters and digits (no spaces, '-', '_' or '.'); "
            "NotesHub would otherwise keep it as part of the title." % (kind, value))
    return value


def _parse_date_arg(value):
    if not re.match(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$", value or ""):
        raise KanbanError("Due date must be YYYY-MM-DD, got %r" % value)
    d = parse_serialized_date(value)
    if d is None or to_serialized_date_string(d) != value:
        raise KanbanError("%r is not a real calendar date" % value)
    return d


def _parse_estimate_arg(value):
    m = re.match(r"^([0-9]+)(m|h|d|w)?$", value or "")
    if not m:
        raise KanbanError("Estimate must look like 30m, 2h, 3d, 1w or a bare number, got %r" % value)
    return (int(m.group(1)), m.group(2))


def _validate_title(title):
    if not js_trim(title or ""):
        raise KanbanError("Card title can't be empty")
    if any(c in title for c in "\n\r  "):
        raise KanbanError("Card title must be a single line; put extra text in --details")
    probe = create_card(title)
    if (js_trim(probe.title) != js_trim(title) or probe.tags or probe.mentions
            or probe.estimates or probe.due_date):
        raise KanbanError(
            "The title %r ends with text NotesHub reads as metadata (a #tag, @mention, "
            "~estimate or YYYY-MM-DD date), so it would be split off. Pass metadata with "
            "--tag/--mention/--estimate/--due, or reword the end of the title "
            "(e.g. 'Fix bug (#123)')." % title)
    if title.startswith("[ ] ") or title.startswith("[x] "):
        raise KanbanError("Don't put a checkbox in the title; use --complete")
    return js_trim(title)


def _clean_details(text):
    text = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    lines = text.split("\n")
    while lines and not js_trim(lines[0]):
        lines.pop(0)
    while lines and not js_trim(lines[-1]):
        lines.pop()
    return "\n".join(lines)


def find_list(board, name):
    exact = [l for l in board.lists if l.name == name]
    if exact:
        return exact[0]
    ci = [l for l in board.lists if l.name.lower() == name.lower()]
    if len(ci) == 1:
        return ci[0]
    raise KanbanError("No list named %r. Lists: %s" % (name, ", ".join(repr(l.name) for l in board.lists) or "(none)"))


def find_card(board, ref, list_name=None):
    """Return (list, index, card)."""
    lists = [find_list(board, list_name)] if list_name else board.lists
    m = re.match(r"^(.*)/([0-9]+)$", ref)
    if m and not list_name:
        try:
            lst = find_list(board, m.group(1))
            i = int(m.group(2)) - 1
            if 0 <= i < len(lst.cards):
                return lst, i, lst.cards[i]
            raise KanbanError("List %r has %d cards; %s is out of range" % (lst.name, len(lst.cards), ref))
        except KanbanError:
            if "/" not in m.group(1):
                raise
    candidates = [(l, i, c) for l in lists for i, c in enumerate(l.cards)]
    for pred in (lambda c: c.title == ref,
                 lambda c: c.title.lower() == ref.lower(),
                 lambda c: ref.lower() in c.title.lower()):
        hits = [x for x in candidates if pred(x[2])]
        if len(hits) == 1:
            return hits[0]
        if len(hits) > 1:
            raise KanbanError("%r matches %d cards: %s. Use 'List/N' or --list." % (
                ref, len(hits), "; ".join("%s/%d %r" % (l.name, i + 1, c.title) for l, i, c in hits)))
    raise KanbanError("No card matches %r" % ref)


def load(path, force=False):
    text = _read(path)
    result = parse_board(text)
    if result.dropped and not force:
        details = "\n".join("  line %d: %s" % (n, t) for n, t in result.dropped[:20])
        raise KanbanError(
            "%s contains %d line(s) NotesHub ignores and would drop on its next save:\n%s\n"
            "Fix or remove them first, or re-run with --force to drop them." % (
                path, len(result.dropped), details))
    return result.board


def save(path, board):
    _write(path, serialize_board(board))


# --------------------------------------------------------------------------------------
# Commands
# --------------------------------------------------------------------------------------

def cmd_show(a):
    result = parse_board(_read(a.board))
    if a.json:
        out = result.board.to_dict()
        out["droppedLines"] = [{"line": n, "text": t} for n, t in result.dropped]
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return
    for lst in result.board.lists:
        print("## %s (%d)" % (lst.name, len(lst.cards)))
        for i, c in enumerate(lst.cards, start=1):
            bits = []
            if c.due_date:
                bits.append(("due %s%s" % (to_serialized_date_string(c.due_date), " done" if c.is_completed else "")))
            if c.estimates:
                bits.append("~%d%s" % (c.estimates[0], c.estimates[1] or ""))
            bits += ["#" + t for t in c.tags] + ["@" + x for x in c.mentions]
            if c.total_tasks:
                bits.append("tasks %d/%d" % (c.completed_tasks, c.total_tasks))
            if c.details and not c.total_tasks:
                bits.append("has details")
            print("  %d. %s%s" % (i, c.title, ("  [" + ", ".join(bits) + "]") if bits else ""))
        print()
    if result.dropped:
        print("WARNING: %d line(s) are ignored by NotesHub (run `lint`)." % len(result.dropped))


def cmd_lint(a):
    text = _read(a.board)
    result = parse_board(text)
    canonical = serialize_board(result.board)
    if result.dropped:
        print("PROBLEM: %d line(s) are not part of any list/card; NotesHub drops them on its next save:" % len(result.dropped))
        for n, t in result.dropped:
            print("  line %d: %s" % (n, t))
        print("Hints: lists are '## Name' (## to ####); cards are '- Title' at column 0; card details are "
              "indented by 2 spaces or a tab (blank lines inside details need the indent too); "
              "only '-' bullets and lowercase [x] are recognized; nothing may come before the first list.")
    if not result.board.lists:
        print("PROBLEM: no lists found (a list is a '## Name' heading).")
    if canonical == text:
        print("OK: already in the exact format NotesHub writes.")
    elif not result.dropped:
        print("OK: no content loss. Formatting differs from what NotesHub writes "
              "(spacing, checkboxes or token order); `normalize` would rewrite it.")
    sys.exit(1 if result.dropped or not result.board.lists else 0)


def cmd_new(a):
    import os
    if os.path.exists(a.board) and not a.force:
        raise KanbanError("%s already exists (use --force to overwrite)" % a.board)
    if not a.board.lower().endswith(".kanban.md"):
        raise KanbanError("Board files must end with .kanban.md")
    names = [js_trim(x) for x in a.lists.split(",") if js_trim(x)]
    if not names:
        raise KanbanError("Give at least one list name")
    save(a.board, Board([KList(n) for n in names]))
    print("Created %s with lists: %s" % (a.board, ", ".join(names)))


def _apply_card_fields(card, a):
    if getattr(a, "tag", None):
        for t in a.tag:
            t = _check_name_token(t.lstrip("#"), "Tag")
            if t not in card.tags:
                card.tags.append(t)
    if getattr(a, "mention", None):
        for x in a.mention:
            x = _check_name_token(x.lstrip("@"), "Mention")
            if x not in card.mentions:
                card.mentions.append(x)
    if getattr(a, "due", None):
        card.due_date = _parse_date_arg(a.due)
    if getattr(a, "estimate", None):
        card.estimates = _parse_estimate_arg(a.estimate.lstrip("~"))
    if getattr(a, "details", None) is not None:
        card.details = _clean_details(a.details)


def cmd_add(a):
    board = load(a.board, a.force)
    lst = find_list(board, a.list)
    card = Card(_validate_title(a.title))
    _apply_card_fields(card, a)
    if a.complete:
        if not card.due_date:
            raise KanbanError("NotesHub only marks cards with a due date as complete; add --due")
        card.is_completed = True
    card.update_computed()
    if a.top:
        lst.cards.insert(0, card)
    else:
        lst.cards.append(card)
    save(a.board, board)
    print("Added %r to %r" % (card.title, lst.name))


def cmd_update(a):
    board = load(a.board, a.force)
    lst, i, card = find_card(board, a.card, a.list)
    if a.title is not None:
        card.title = _validate_title(a.title)
    for t in a.add_tag or []:
        t = _check_name_token(t.lstrip("#"), "Tag")
        if t not in card.tags:
            card.tags.append(t)
    for t in a.remove_tag or []:
        t = t.lstrip("#")
        if t in card.tags:
            card.tags.remove(t)
    for x in a.add_mention or []:
        x = _check_name_token(x.lstrip("@"), "Mention")
        if x not in card.mentions:
            card.mentions.append(x)
    for x in a.remove_mention or []:
        x = x.lstrip("@")
        if x in card.mentions:
            card.mentions.remove(x)
    if a.due:
        card.due_date = _parse_date_arg(a.due)
    if a.clear_due:
        card.due_date = None
        card.is_completed = False
    if a.estimate:
        card.estimates = _parse_estimate_arg(a.estimate.lstrip("~"))
    if a.clear_estimate:
        card.estimates = None
    if a.details is not None:
        card.details = _clean_details(a.details)
    if a.append_details is not None:
        extra = _clean_details(a.append_details)
        card.details = (card.details + "\n" + extra) if card.details else extra
    if a.complete:
        if not card.due_date:
            raise KanbanError("NotesHub only marks cards with a due date as complete; set --due too")
        card.is_completed = True
    if a.uncomplete:
        card.is_completed = False
    card.update_computed()
    save(a.board, board)
    print("Updated %s/%d %r" % (lst.name, board.lists[board.lists.index(lst)].cards.index(card) + 1, card.title))


def cmd_move(a):
    board = load(a.board, a.force)
    src, i, card = find_card(board, a.card, a.list)
    dst = find_list(board, a.to)
    src.cards.pop(i)
    if a.top:
        dst.cards.insert(0, card)
    elif a.position:
        dst.cards.insert(max(0, a.position - 1), card)
    else:
        dst.cards.append(card)
    save(a.board, board)
    print("Moved %r from %r to %r" % (card.title, src.name, dst.name))


def cmd_remove(a):
    board = load(a.board, a.force)
    lst, i, card = find_card(board, a.card, a.list)
    lst.cards.pop(i)
    save(a.board, board)
    print("Removed %r from %r" % (card.title, lst.name))


def cmd_task(a):
    board = load(a.board, a.force)
    lst, i, card = find_card(board, a.card, a.list)
    lines = card.details.split("\n") if card.details else []
    if a.add:
        if "\n" in a.add:
            raise KanbanError("A task must be a single line")
        lines.append("- [ ] " + js_trim(a.add))
    else:
        target, want = (a.check, "x") if a.check else (a.uncheck, " ")
        pat = re.compile(r"^(\s*[*+-] \[)(x| )(\] )(.*)$", re.I)
        hits = [k for k, l in enumerate(lines) if pat.match(l)
                and target.lower() in pat.match(l).group(4).lower()]
        if len(hits) != 1:
            raise KanbanError("%d tasks match %r in %r" % (len(hits), target, card.title))
        k = hits[0]
        mm = pat.match(lines[k])
        lines[k] = mm.group(1) + want + mm.group(3) + mm.group(4)
    card.details = "\n".join(lines)
    card.update_computed()
    save(a.board, board)
    print("%r tasks: %d/%d done" % (card.title, card.completed_tasks, card.total_tasks))


def cmd_add_list(a):
    board = load(a.board, a.force)
    name = js_trim(a.name)
    if not name or "\n" in name:
        raise KanbanError("List name must be a non-empty single line")
    if any(l.name.lower() == name.lower() for l in board.lists):
        raise KanbanError("A list named %r already exists" % name)
    pos = len(board.lists) if not a.position else max(0, a.position - 1)
    board.lists.insert(pos, KList(name))
    save(a.board, board)
    print("Added list %r" % name)


def cmd_rename_list(a):
    board = load(a.board, a.force)
    lst = find_list(board, a.list)
    old, lst.name = lst.name, js_trim(a.name)
    save(a.board, board)
    print("Renamed list %r to %r" % (old, lst.name))


def cmd_remove_list(a):
    board = load(a.board, a.force)
    lst = find_list(board, a.list)
    if lst.cards and not a.force:
        raise KanbanError("List %r has %d card(s); move them first or use --force" % (lst.name, len(lst.cards)))
    board.lists.remove(lst)
    save(a.board, board)
    print("Removed list %r" % lst.name)


def cmd_normalize(a):
    board = load(a.board, a.force)
    save(a.board, board)
    print("Rewrote %s in NotesHub's canonical format" % a.board)


def build_parser():
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = p.add_subparsers(dest="cmd")
    sub.required = True

    def board_cmd(name, fn, help_text, force=True):
        sp = sub.add_parser(name, help=help_text)
        sp.add_argument("board", help="path to a .kanban.md file")
        if force:
            sp.add_argument("--force", action="store_true", help="proceed even if lines would be dropped")
        sp.set_defaults(fn=fn)
        return sp

    sp = board_cmd("show", cmd_show, "print the board", force=False)
    sp.add_argument("--json", action="store_true")
    board_cmd("lint", cmd_lint, "report content NotesHub would drop", force=False)

    sp = board_cmd("new", cmd_new, "create a board")
    sp.add_argument("--lists", default="To Do,Doing,Done")

    sp = board_cmd("add", cmd_add, "add a card")
    sp.add_argument("--list", required=True)
    sp.add_argument("--title", required=True)
    sp.add_argument("--tag", action="append")
    sp.add_argument("--mention", action="append")
    sp.add_argument("--due")
    sp.add_argument("--estimate")
    sp.add_argument("--details")
    sp.add_argument("--complete", action="store_true")
    sp.add_argument("--top", action="store_true", help="insert at the top of the list")

    sp = board_cmd("update", cmd_update, "edit a card")
    sp.add_argument("--card", required=True)
    sp.add_argument("--list")
    sp.add_argument("--title")
    sp.add_argument("--add-tag", action="append")
    sp.add_argument("--remove-tag", action="append")
    sp.add_argument("--add-mention", action="append")
    sp.add_argument("--remove-mention", action="append")
    g = sp.add_mutually_exclusive_group()
    g.add_argument("--due")
    g.add_argument("--clear-due", action="store_true")
    g = sp.add_mutually_exclusive_group()
    g.add_argument("--estimate")
    g.add_argument("--clear-estimate", action="store_true")
    g = sp.add_mutually_exclusive_group()
    g.add_argument("--details")
    g.add_argument("--append-details")
    g = sp.add_mutually_exclusive_group()
    g.add_argument("--complete", action="store_true")
    g.add_argument("--uncomplete", action="store_true")

    sp = board_cmd("move", cmd_move, "move a card to another list")
    sp.add_argument("--card", required=True)
    sp.add_argument("--list")
    sp.add_argument("--to", required=True)
    g = sp.add_mutually_exclusive_group()
    g.add_argument("--top", action="store_true")
    g.add_argument("--position", type=int, help="1-based position in the target list")

    sp = board_cmd("remove", cmd_remove, "delete a card")
    sp.add_argument("--card", required=True)
    sp.add_argument("--list")

    sp = board_cmd("task", cmd_task, "add or tick a checklist item in a card's details")
    sp.add_argument("--card", required=True)
    sp.add_argument("--list")
    g = sp.add_mutually_exclusive_group(required=True)
    g.add_argument("--add")
    g.add_argument("--check")
    g.add_argument("--uncheck")

    sp = board_cmd("add-list", cmd_add_list, "add a list (column)")
    sp.add_argument("--name", required=True)
    sp.add_argument("--position", type=int, help="1-based column position")

    sp = board_cmd("rename-list", cmd_rename_list, "rename a list")
    sp.add_argument("--list", required=True)
    sp.add_argument("--name", required=True)

    sp = board_cmd("remove-list", cmd_remove_list, "delete a list")
    sp.add_argument("--list", required=True)

    board_cmd("normalize", cmd_normalize, "rewrite in NotesHub's exact output format")
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        args.fn(args)
    except KanbanError as e:
        print("ERROR: %s" % e, file=sys.stderr)
        sys.exit(2)
    except FileNotFoundError as e:
        print("ERROR: %s" % e, file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
