#!/usr/bin/env python3
"""Get a NotesHub notebook repo onto disk fast, and save changes back safely (git).

  notebook_sync.py open URL_OR_OWNER/REPO DIR [--full] [--branch B]
      Clone (or update, if DIR already holds it) a shallow copy. By default only text
      files NotesHub cares about are checked out (*.md, *.excalidraw, *.excalidraw.svg,
      AGENTS.md/CLAUDE.md...), skipping images/attachments - typically ~1-2 s.
      --full checks out everything (needed to add or inspect attachments).

  notebook_sync.py save DIR -m "Message" [--author "Name <email>"]
      Stage all changes, commit, rebase onto the latest remote, push. Retries once if
      someone (e.g. the NotesHub app) pushed in between. Never force-pushes.

  notebook_sync.py status DIR
      Show what would be committed.

Python 3.8+, standard library only; needs `git` with push access to the repo.
"""

import argparse
import os
import subprocess
import sys

TEXT_PATTERNS = ["*.md", "*.markdown", "*.txt", "*.excalidraw", "*.excalidraw.svg", "*.excalidrawlib",
                 "*.json", "*.yml", "*.yaml", ".gitignore", ".gitattributes"]


class SyncError(Exception):
    pass


def git(args, cwd=None, check=True, capture=True):
    res = subprocess.run(["git"] + args, cwd=cwd, capture_output=capture, text=True)
    if check and res.returncode != 0:
        raise SyncError("git %s failed:\n%s%s" % (" ".join(args), res.stdout or "", res.stderr or ""))
    return res


def repo_url(spec):
    if "://" in spec or spec.startswith("git@"):
        return spec
    if spec.count("/") == 1:
        return "https://github.com/%s" % spec
    raise SyncError("Give a clone URL or OWNER/REPO, got %r" % spec)


def cmd_open(a):
    if os.path.isdir(os.path.join(a.dir, ".git")):
        branch = a.branch or git(["rev-parse", "--abbrev-ref", "HEAD"], cwd=a.dir).stdout.strip()
        dirty = git(["status", "--porcelain"], cwd=a.dir).stdout.strip()
        if dirty:
            print("Note: %s has uncommitted changes; updating with --autostash" % a.dir)
        git(["pull", "--rebase", "--autostash", "--depth", "1", "origin", branch], cwd=a.dir)
        if a.full and os.path.exists(os.path.join(a.dir, ".git", "info", "sparse-checkout")):
            git(["sparse-checkout", "disable"], cwd=a.dir)
        print("Updated %s (%s)" % (a.dir, git(["log", "-1", "--format=%h %cr: %s"], cwd=a.dir).stdout.strip()))
        return
    url = repo_url(a.repo)
    args = ["clone", "--depth", "1", "--filter=blob:none", "--no-checkout"]
    if a.branch:
        args += ["--branch", a.branch]
    git(args + [url, a.dir], capture=True)
    if not a.full:
        git(["sparse-checkout", "set", "--no-cone"] + TEXT_PATTERNS, cwd=a.dir)
    git(["checkout"], cwd=a.dir)
    mode = "everything" if a.full else "text files only (images/attachments skipped; use --full to get them)"
    print("Cloned %s into %s: %s" % (url, a.dir, mode))
    for name in ("AGENTS.md", "CLAUDE.md"):
        if os.path.exists(os.path.join(a.dir, name)):
            print("This notebook has %s - read it for its conventions." % name)


def cmd_status(a):
    print(git(["status", "--short"], cwd=a.dir).stdout or "Nothing to commit.")


def cmd_save(a):
    git(["add", "-A", "--sparse", "."], cwd=a.dir)
    if not git(["status", "--porcelain"], cwd=a.dir).stdout.strip():
        print("Nothing to commit.")
        return
    commit = ["commit", "-q", "-m", a.message]
    if a.author:
        commit += ["--author", a.author]
    git(commit, cwd=a.dir)
    branch = git(["rev-parse", "--abbrev-ref", "HEAD"], cwd=a.dir).stdout.strip()
    for attempt in (1, 2):
        pull = git(["pull", "--rebase", "--depth", "1", "origin", branch], cwd=a.dir, check=False)
        if pull.returncode != 0:
            git(["rebase", "--abort"], cwd=a.dir, check=False)
            raise SyncError("Your change conflicts with a newer version on the remote. Nothing was pushed.\n"
                            "Re-open the notebook, re-apply the edit to the fresh files, and save again.\n" +
                            (pull.stdout or "") + (pull.stderr or ""))
        push = git(["push", "origin", "HEAD:%s" % branch], cwd=a.dir, check=False)
        if push.returncode == 0:
            print("Pushed to %s: %s" % (branch, git(["log", "-1", "--format=%h %s"], cwd=a.dir).stdout.strip()))
            return
        if attempt == 2:
            raise SyncError("Push failed:\n%s%s" % (push.stdout or "", push.stderr or ""))


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = p.add_subparsers(dest="cmd")
    sub.required = True
    o = sub.add_parser("open")
    o.add_argument("repo")
    o.add_argument("dir")
    o.add_argument("--full", action="store_true")
    o.add_argument("--branch")
    o.set_defaults(fn=cmd_open)
    s = sub.add_parser("save")
    s.add_argument("dir")
    s.add_argument("-m", "--message", required=True)
    s.add_argument("--author")
    s.set_defaults(fn=cmd_save)
    st = sub.add_parser("status")
    st.add_argument("dir")
    st.set_defaults(fn=cmd_status)
    a = p.parse_args(argv)
    try:
        a.fn(a)
    except SyncError as e:
        print("ERROR: %s" % e, file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
