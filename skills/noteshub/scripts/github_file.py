#!/usr/bin/env python3
"""Read or update a single file in a GitHub repo without cloning (uses the `gh` CLI).

Fast path for one-file edits (move a card, tick a task, fix a note):

  github_file.py get OWNER/REPO "Path/To/Board.kanban.md" -o board.kanban.md
      -> downloads the file and prints its blob SHA (also saved to board.kanban.md.sha)
  ... edit board.kanban.md locally (e.g. with kanban.py) ...
  github_file.py put OWNER/REPO "Path/To/Board.kanban.md" board.kanban.md -m "Move card to Done"
      -> commits it back; uses the saved SHA so the write is rejected if the file changed
         on GitHub in the meantime (then: get again, redo the edit, put again)

  github_file.py ls OWNER/REPO [DIR] [--recursive] [--pattern "*.kanban.md"]
      -> list files (recursive uses the git tree API, one request for the whole repo)

Options: --ref/--branch BRANCH (default: the repo's default branch), --new for creating
a file that doesn't exist yet. Requires `gh` to be installed and authenticated (or a
session that proxies GitHub auth). Python 3.8+, standard library only.
"""

import argparse
import base64
import fnmatch
import json
import os
import subprocess
import sys
from urllib.parse import quote


class GhError(Exception):
    pass


def gh_api(args, data=None):
    cmd = ["gh", "api"] + args
    try:
        res = subprocess.run(cmd, input=(json.dumps(data) if data is not None else None),
                             capture_output=True, text=True)
    except FileNotFoundError:
        raise GhError("The GitHub CLI (`gh`) isn't installed here. Clone the repo with git instead.")
    if res.returncode != 0:
        msg = (res.stdout or "") + (res.stderr or "")
        raise GhError(msg.strip() or "gh api failed")
    return res.stdout


def contents_path(repo, path):
    return "repos/%s/contents/%s" % (repo, quote(path.strip("/"), safe="/"))


def cmd_get(a):
    meta_args = [contents_path(a.repo, a.path)]
    if a.ref:
        meta_args[0] += "?ref=" + quote(a.ref)
    meta = json.loads(gh_api(meta_args))
    if isinstance(meta, list):
        raise GhError("%s is a directory; use `ls`" % a.path)
    raw_args = ["-H", "Accept: application/vnd.github.raw"] + meta_args
    res = subprocess.run(["gh", "api"] + raw_args, capture_output=True)
    if res.returncode != 0:
        raise GhError(res.stderr.decode("utf-8", "replace"))
    out = a.output or os.path.basename(a.path)
    with open(out, "wb") as f:
        f.write(res.stdout)
    with open(out + ".sha", "w") as f:
        f.write(meta["sha"])
    print(json.dumps({"path": a.path, "sha": meta["sha"], "saved_to": out, "bytes": len(res.stdout)}))


def cmd_put(a):
    with open(a.local, "rb") as f:
        content = f.read()
    sha = a.sha
    if not sha and not a.new:
        side = a.local + ".sha"
        if not os.path.exists(side):
            raise GhError("No SHA given and no %s found. Run `get` first (or pass --new for a new file)." % side)
        with open(side) as f:
            sha = f.read().strip()
    body = {"message": a.message, "content": base64.b64encode(content).decode("ascii")}
    if sha:
        body["sha"] = sha
    if a.branch:
        body["branch"] = a.branch
    try:
        out = gh_api(["-X", "PUT", contents_path(a.repo, a.path), "--input", "-"], data=body)
    except GhError as e:
        text = str(e)
        if "409" in text or "does not match" in text or "sha" in text.lower() and "422" in text:
            raise GhError("The file changed on GitHub since you fetched it (or the SHA is wrong). "
                          "Run `get` again, re-apply your edit to the fresh copy, then `put`.\n" + text)
        raise
    res = json.loads(out)
    new_sha = res["content"]["sha"]
    with open(a.local + ".sha", "w") as f:
        f.write(new_sha)
    print(json.dumps({"path": a.path, "commit": res["commit"]["sha"], "new_sha": new_sha,
                      "url": res["commit"].get("html_url")}))


def cmd_ls(a):
    if a.recursive:
        ref = a.ref or json.loads(gh_api(["repos/%s" % a.repo]))["default_branch"]
        tree = json.loads(gh_api(["repos/%s/git/trees/%s?recursive=1" % (a.repo, quote(ref, safe=""))]))
        prefix = (a.dir.strip("/") + "/") if a.dir else ""
        for item in tree.get("tree", []):
            if item["type"] != "blob" or not item["path"].startswith(prefix):
                continue
            if a.pattern and not fnmatch.fnmatch(os.path.basename(item["path"]), a.pattern):
                continue
            print(item["path"])
        if tree.get("truncated"):
            print("(listing truncated by GitHub; clone the repo for a full listing)", file=sys.stderr)
        return
    args = [contents_path(a.repo, a.dir or "")]
    if a.ref:
        args[0] += "?ref=" + quote(a.ref)
    for item in json.loads(gh_api(args)):
        if a.pattern and item["type"] == "file" and not fnmatch.fnmatch(item["name"], a.pattern):
            continue
        print(("%s/" % item["path"]) if item["type"] == "dir" else item["path"])


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = p.add_subparsers(dest="cmd")
    sub.required = True
    g = sub.add_parser("get", help="download a file and remember its SHA")
    g.add_argument("repo")
    g.add_argument("path")
    g.add_argument("-o", "--output")
    g.add_argument("--ref")
    g.set_defaults(fn=cmd_get)
    u = sub.add_parser("put", help="commit a local file to the repo")
    u.add_argument("repo")
    u.add_argument("path")
    u.add_argument("local")
    u.add_argument("-m", "--message", required=True)
    u.add_argument("--sha")
    u.add_argument("--branch")
    u.add_argument("--new", action="store_true", help="the file doesn't exist on GitHub yet")
    u.set_defaults(fn=cmd_put)
    l = sub.add_parser("ls", help="list files")
    l.add_argument("repo")
    l.add_argument("dir", nargs="?", default="")
    l.add_argument("--ref")
    l.add_argument("--recursive", action="store_true")
    l.add_argument("--pattern")
    l.set_defaults(fn=cmd_ls)
    a = p.parse_args(argv)
    try:
        a.fn(a)
    except GhError as e:
        print("ERROR: %s" % e, file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
