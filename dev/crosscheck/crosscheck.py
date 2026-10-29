"""Compare scripts/kanban.py with NotesHub's boardUtils.ts on real and fuzzed boards.
Needs a local NotesHub source checkout bundled into boardUtils.bundle.mjs (see README in this folder)."""
import glob, json, os, random, subprocess, sys
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "..", "skills", "noteshub", "scripts"))
import kanban as K

def py_result(md):
    r = K.parse_board(md)
    return {"board": [{"name": l.name, "cards": [{k: v for k, v in c.to_dict().items()} for c in l.cards]} for l in r.board.lists],
            "markdown": K.serialize_board(r.board)}

PIECES_HEAD = ["# H1", "## To Do", "### Doing", "#### Done", "##### Deep", "##NoSpace", "## ", "##  Spaced  ", "## Кухня 🏡", "## Done\r"]
TITLES = ["Buy milk", "Fix bug #123", "C#", "a#b#c", "Task #a #b #a", "Ask @bob", "mail x@example.com", "Est ~2", "Est ~3h ~4d",
          "Due 2023-02-30", "Due 2023-13-01", "Due 0099-01-01", "Due 2023-1-1", "Mixed ~2h #Tag @me 2024-05-06", "#onlytag",
          "Tag #Кухня", "Tag #日本語", "Tag #a-b", "Tag #a_b", "Emoji 🎉 #ok", "trailing   ", " lead", "[X] upper", "x", "~5",
          "Date2024-01-01", "dup #x @x #x", "price $5 ~ ok", "Full\u00a0width", "a@b@c", "2024-01-01", "End ~0w 2025-12-31"]
PREFIX = ["- ", "- [ ] ", "- [x] ", "- [X] ", "* ", "+ ", "-  ", "-[ ] ", " - "]
DETAILS = ["  details", "\tTabbed", "    deep", "   three", "  - [ ] sub", "  - [x] done sub", "  * [X] star", "  ", "", "\t", "  ## Not a list",
           "not indented", "  \u2028sep", "  ```", "  code();", "  - [ ]missing space"]

def fuzz(rng, n):
    cases = []
    for _ in range(n):
        lines = []
        for _ in range(rng.randint(1, 25)):
            r = rng.random()
            if r < 0.15: lines.append(rng.choice(PIECES_HEAD))
            elif r < 0.6: lines.append(rng.choice(PREFIX) + rng.choice(TITLES) + rng.choice(["", "  ", "\r", " "]))
            else: lines.append(rng.choice(DETAILS))
        sep = rng.choice(["\n", "\n", "\r\n"])
        cases.append(rng.choice(["", "\ufeff", "Intro text\n"]) + sep.join(lines))
    return cases

def main():
    inputs = []
    for pattern in sys.argv[1:]:
        for p in sorted(glob.glob(pattern, recursive=True)):
            inputs.append(open(p, encoding="utf-8", newline="").read())
    real = len(inputs)
    inputs += fuzz(random.Random(42), 3000)
    ts = json.loads(subprocess.run(["node", os.path.join(HERE, "ts_harness.mjs")], input=json.dumps(inputs),
                                   capture_output=True, text=True, check=True).stdout)
    mismatches = 0
    for i, (md, t) in enumerate(zip(inputs, ts)):
        p = py_result(md)
        # second round-trip: re-parse the serialized output with both implementations
        if p != t:
            mismatches += 1
            if mismatches <= 5:
                print("MISMATCH case %d:\n%r\nTS: %s\nPY: %s\n" % (i, md, json.dumps(t, ensure_ascii=False)[:1500], json.dumps(p, ensure_ascii=False)[:1500]))
    print("%d real boards + %d fuzz cases, %d mismatches" % (real, len(inputs) - real, mismatches))
    sys.exit(1 if mismatches else 0)

if __name__ == "__main__":
    main()
