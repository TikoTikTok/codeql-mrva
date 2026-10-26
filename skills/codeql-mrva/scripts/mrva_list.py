#!/usr/bin/env python3
"""Build, merge, filter and convert MRVA repository lists.

Standard library only; anything remote goes through the GitHub CLI (`gh`).

Subcommands
  top     --language L [--limit N] [--query 'stars:>50 pushed:>2025-01-01']
  org     OWNER [--language L] [--limit N] [--include-archived] [--include-forks]
  search  'pickle.loads language:python' [--language L] [--limit N]
  file    PATH [--list NAME]       an existing list file (gh-mrva or VS Code format)
                                   or a text file with one owner/repo per line
  merge   PATH [--lists a,b,c]     union of several lists of one file (all of them by default),
                                   first occurrence wins, so put the best-ranked list first
  repos   owner/repo [owner/repo ...]

Common options
  --name NAME        list name to write (default: derived from the subcommand)
  --out FILE         write/merge into FILE; without it the list is printed as JSON
  --format F         gh-mrva (default) or vscode; auto-detected when --out exists
  --exclude FILE     drop every repository that appears in any list of FILE
  --chunk N          split into NAME-1, NAME-2, ... of at most N repositories
  --max N            warn above N repositories (default 1000, the MRVA per-run limit)
  --truncate         cut the list at --max instead of warning
  --json             also print the written list(s) as JSON to stdout

Formats
  gh-mrva : {"list-name": ["owner/repo", ...], ...}
  vscode  : the CodeQL extension's databases.json
            {"version": 1, "databases": {"variantAnalysis": {"repositoryLists": [
               {"name": "...", "repositories": [...]}], "owners": [], "repositories": []}},
             "selected": {...}}
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from typing import Dict, Iterable, List, Optional, Tuple

MAX_PER_RUN = 1000
GH_SEARCH_CAP = 1000  # GitHub search returns at most 1,000 results per query

# CodeQL / MRVA language id -> GitHub "primary language" display names it covers
LANG_NAMES = {
    "cpp": {"C", "C++", "Objective-C", "Objective-C++", "Cuda"},
    "csharp": {"C#"},
    "go": {"Go"},
    "java": {"Java", "Kotlin"},
    "javascript": {"JavaScript", "TypeScript", "Vue", "Svelte"},
    "python": {"Python"},
    "ruby": {"Ruby"},
    "rust": {"Rust"},
    "swift": {"Swift"},
    "actions": set(),  # GitHub Actions workflows have no primary-language signal
}
ALIASES = {
    "py": "python", "js": "javascript", "ts": "javascript", "typescript": "javascript",
    "javascript-typescript": "javascript", "c": "cpp", "c++": "cpp", "cxx": "cpp", "c-cpp": "cpp",
    "c#": "csharp", "cs": "csharp", "golang": "go", "kotlin": "java", "java-kotlin": "java",
    "rb": "ruby", "rs": "rust", "gha": "actions", "github-actions": "actions",
}
REPO_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})/[A-Za-z0-9._-]{1,100}$")


def norm_lang(lang: Optional[str]) -> Optional[str]:
    if not lang:
        return None
    low = lang.strip().lower()
    return ALIASES.get(low, low)


def norm_repo(value: str) -> Optional[str]:
    """'https://github.com/Owner/Repo.git/tree/x' -> 'Owner/Repo'; None when not a repository."""
    s = (value or "").strip()
    if not s or s.startswith("#"):
        return None
    s = re.sub(r"^(?:https?://)?(?:www\.)?github\.com/", "", s)
    s = re.sub(r"^git@github\.com:", "", s)
    parts = [p for p in s.strip("/").split("/") if p]
    if len(parts) < 2:
        return None
    repo = "/".join(parts[:2])
    if repo.endswith(".git"):
        repo = repo[:-4]
    return repo if REPO_RE.match(repo) else None


def dedupe(repos: Iterable[str]) -> List[str]:
    seen = set()
    out: List[str] = []
    for r in repos:
        n = norm_repo(r)
        if n is None:
            continue
        key = n.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(n)
    return out


# --- gh ---------------------------------------------------------------------

def gh_try(args: List[str]) -> Tuple[int, str, str]:
    try:
        proc = subprocess.run(["gh", *args], capture_output=True, text=True,
                              encoding="utf-8", errors="replace")
    except FileNotFoundError:
        sys.exit("error: `gh` (GitHub CLI) is not on PATH. Install it from "
                 "https://cli.github.com and run `gh auth login`.")
    return proc.returncode, proc.stdout, proc.stderr


def gh(args: List[str]) -> str:
    rc, out, err = gh_try(args)
    if rc != 0:
        sys.exit(f"error: gh {' '.join(args[:2])} failed:\n{(err or out).strip()}")
    return out


def gh_json(args: List[str]):
    out = gh(args).strip()
    return json.loads(out) if out else []


# --- list files -------------------------------------------------------------

def detect_format(doc) -> str:
    if isinstance(doc, dict):
        if isinstance(doc.get("databases"), dict):
            return "vscode"
        if isinstance(doc.get("name"), str) and isinstance(doc.get("repositories"), list):
            return "vscode-list"  # what "Copy repository list" puts on the clipboard
        if all(isinstance(v, list) for v in doc.values()):
            return "gh-mrva"
    raise ValueError("not a gh-mrva or VS Code repository list")


def lists_from_doc(doc) -> Dict[str, List[str]]:
    fmt = detect_format(doc)
    if fmt == "gh-mrva":
        return {k: dedupe(v) for k, v in doc.items()}
    if fmt == "vscode-list":
        return {doc["name"]: dedupe(doc["repositories"])}
    va = doc["databases"].get("variantAnalysis", {}) or {}
    return {
        entry["name"]: dedupe(entry.get("repositories", []))
        for entry in va.get("repositoryLists", []) or []
        if isinstance(entry, dict) and isinstance(entry.get("name"), str)
    }


def read_lists(path: str) -> Dict[str, List[str]]:
    with open(path, encoding="utf-8-sig") as fh:
        text = fh.read()
    if text.lstrip().startswith("{"):
        return lists_from_doc(json.loads(text))
    name = os.path.splitext(os.path.basename(path))[0]
    return {name: dedupe(text.splitlines())}


def _empty_vscode() -> dict:
    return {"version": 1,
            "databases": {"variantAnalysis": {"repositoryLists": [], "owners": [], "repositories": []}},
            "selected": None}


def write_lists(path: str, lists: Dict[str, List[str]], fmt: Optional[str] = None) -> str:
    """Write or merge `lists` into `path`. Returns the format used."""
    doc = None
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8-sig") as fh:
                doc = json.load(fh)
            existing = detect_format(doc)
        except Exception as exc:  # noqa: BLE001
            sys.exit(f"error: {path} exists but is not a list file I understand ({exc}); choose another --out")
        if existing == "vscode-list":  # a pasted "Copy repository list" object: promote it to a full file
            single = doc
            doc = _empty_vscode()
            doc["databases"]["variantAnalysis"]["repositoryLists"].append(
                {"name": single["name"], "repositories": dedupe(single["repositories"])})
            existing = "vscode"
        if fmt and fmt != existing:
            print(f"note: {path} is in {existing} format; keeping that format", file=sys.stderr)
        fmt = existing
    fmt = fmt or "gh-mrva"

    if fmt == "gh-mrva":
        if not isinstance(doc, dict):
            doc = {}
        for name, repos in lists.items():
            doc[name] = repos
    else:
        if not isinstance(doc, dict):
            doc = _empty_vscode()
        va = doc.setdefault("databases", {}).setdefault("variantAnalysis", {})
        rl = va.setdefault("repositoryLists", [])
        va.setdefault("owners", [])
        va.setdefault("repositories", [])
        for name, repos in lists.items():
            for entry in rl:
                if isinstance(entry, dict) and entry.get("name") == name:
                    entry["repositories"] = repos
                    break
            else:
                rl.append({"name": name, "repositories": repos})
        doc.setdefault("version", 1)
        doc["selected"] = {"kind": "variantAnalysisUserDefinedList", "listName": list(lists)[-1]}

    with open(path, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=2)
        fh.write("\n")
    return fmt


def vscode_doc(lists: Dict[str, List[str]]) -> dict:
    doc = _empty_vscode()
    for name, repos in lists.items():
        doc["databases"]["variantAnalysis"]["repositoryLists"].append({"name": name, "repositories": repos})
    if lists:
        doc["selected"] = {"kind": "variantAnalysisUserDefinedList", "listName": list(lists)[-1]}
    return doc


def chunk(name: str, repos: List[str], size: int) -> Dict[str, List[str]]:
    if size <= 0 or len(repos) <= size:
        return {name: repos}
    return {f"{name}-{i // size + 1}": repos[i:i + size] for i in range(0, len(repos), size)}


# --- builders ---------------------------------------------------------------

def build_top(language: str, limit: int, query: Optional[str]) -> List[str]:
    lang = norm_lang(language)
    if lang not in LANG_NAMES:
        sys.exit(f"error: unknown language {language!r}; use one of {', '.join(sorted(LANG_NAMES))}")
    limit = min(limit, GH_SEARCH_CAP)
    args = ["search", "repos"]
    if query:
        args.append(query)
    if lang == "actions":
        args += ["path:.github/workflows"] if not query else []
    else:
        args += ["--language", lang]
    args += ["--sort", "stars", "--order", "desc", "--limit", str(limit), "--json", "fullName"]
    return [row["fullName"] for row in gh_json(args)]


def build_org(owner: str, language: Optional[str], limit: int,
              include_archived: bool, include_forks: bool) -> List[str]:
    lang = norm_lang(language)
    rows = gh_json(["repo", "list", owner, "--limit", str(limit),
                    "--json", "nameWithOwner,isArchived,isFork,primaryLanguage"])
    out = []
    wanted = LANG_NAMES.get(lang or "", set())
    for row in rows:
        if row.get("isArchived") and not include_archived:
            continue
        if row.get("isFork") and not include_forks:
            continue
        if lang and lang != "actions":
            primary = (row.get("primaryLanguage") or {}).get("name")
            if primary not in wanted:
                continue
        out.append(row["nameWithOwner"])
    return out


def build_search(query: str, language: Optional[str], limit: int) -> List[str]:
    """Repositories owning the files a code search matches.

    Pages through GET /search/code directly (100 files per page, 1,000 cap) so that
    the 10-requests-per-minute code-search limit pauses the search instead of
    discarding it, and a failure on a later page keeps the pages already fetched.
    """
    limit = min(limit, GH_SEARCH_CAP)
    lang = norm_lang(language)
    q = f"{query} language:{lang}" if lang and lang != "actions" else query
    repos: List[str] = []
    files = 0
    page = 1
    while files < limit and page <= GH_SEARCH_CAP // 100:
        per_page = min(100, limit - files)
        rc, out, err = gh_try(["api", "-X", "GET", "search/code", "-f", f"q={q}",
                               "-F", f"per_page={per_page}", "-F", f"page={page}",
                               "--jq", ".items[].repository.full_name"])
        if rc != 0:
            low = err.lower()
            if "rate limit" in low:
                print("code search rate limit (10 requests/min); waiting 60 s", file=sys.stderr)
                time.sleep(60)
                continue
            if page > 1:
                print(f"warning: page {page} failed, keeping {files} files from earlier pages: "
                      f"{err.strip().splitlines()[-1] if err.strip() else rc}", file=sys.stderr)
                break
            sys.exit(f"error: code search failed:\n{(err or out).strip()}")
        names = out.split()
        if not names:
            break
        repos += names
        files += len(names)
        page += 1
        if len(names) < per_page:
            break
    print(f"code search: {files} files, {len(dedupe(repos))} repositories", file=sys.stderr)
    return repos


def build_file(path: str, list_name: Optional[str]) -> Tuple[str, List[str]]:
    lists = read_lists(path)
    if not lists:
        sys.exit(f"error: no lists found in {path}")
    if list_name:
        if list_name not in lists:
            sys.exit(f"error: list {list_name!r} not in {path}; available: {', '.join(lists)}")
        return list_name, lists[list_name]
    if len(lists) == 1:
        name = next(iter(lists))
        return name, lists[name]
    sys.exit(f"error: {path} holds several lists ({', '.join(lists)}); pick one with --list")


def build_merge(path: str, names: Optional[str]) -> Tuple[str, List[str]]:
    lists = read_lists(path)
    if not lists:
        sys.exit(f"error: no lists found in {path}")
    wanted = [n.strip() for n in names.split(",")] if names else list(lists)
    missing = [n for n in wanted if n not in lists]
    if missing:
        sys.exit(f"error: list(s) {', '.join(missing)} not in {path}; available: {', '.join(lists)}")
    merged: List[str] = []
    for n in wanted:
        merged += lists[n]
    return "merged", dedupe(merged)


# --- cli --------------------------------------------------------------------

def main(argv: Optional[List[str]] = None) -> int:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--name", help="list name to write")
    common.add_argument("--out", help="write/merge into this file")
    common.add_argument("--format", choices=["gh-mrva", "vscode"], help="output format (default gh-mrva)")
    common.add_argument("--exclude", help="list file whose repositories are dropped")
    common.add_argument("--chunk", type=int, default=0, help="split into chunks of N")
    common.add_argument("--max", type=int, default=MAX_PER_RUN, help="warn above N repositories")
    common.add_argument("--truncate", action="store_true", help="cut the list at --max instead of warning")
    common.add_argument("--json", action="store_true", help="print the resulting list(s) as JSON")

    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("top", parents=[common], help="most-starred repositories in a language")
    p.add_argument("--language", required=True)
    p.add_argument("--limit", type=int, default=GH_SEARCH_CAP)
    p.add_argument("--query", help="extra search qualifiers, e.g. 'stars:>50 pushed:>2025-01-01'")

    p = sub.add_parser("org", parents=[common], help="every repository of an owner")
    p.add_argument("owner")
    p.add_argument("--language")
    p.add_argument("--limit", type=int, default=MAX_PER_RUN)
    p.add_argument("--include-archived", action="store_true")
    p.add_argument("--include-forks", action="store_true")

    p = sub.add_parser("search", parents=[common], help="repositories matched by a code search")
    p.add_argument("query")
    p.add_argument("--language")
    p.add_argument("--limit", type=int, default=GH_SEARCH_CAP)

    p = sub.add_parser("file", parents=[common], help="repositories from a list or text file")
    p.add_argument("path")
    p.add_argument("--list", dest="list_name")

    p = sub.add_parser("merge", parents=[common], help="union of several lists in one file")
    p.add_argument("path")
    p.add_argument("--lists", help="comma-separated list names, in priority order (default: all)")

    p = sub.add_parser("repos", parents=[common], help="literal owner/repo arguments")
    p.add_argument("repos", nargs="+")

    args = parser.parse_args(argv)

    if args.cmd == "top":
        lang = norm_lang(args.language)
        name, repos = args.name or f"{lang}-top{min(args.limit, GH_SEARCH_CAP)}", build_top(args.language, args.limit, args.query)
    elif args.cmd == "org":
        name = args.name or f"{args.owner}-org" + (f"-{norm_lang(args.language)}" if args.language else "")
        repos = build_org(args.owner, args.language, args.limit, args.include_archived, args.include_forks)
    elif args.cmd == "search":
        name, repos = args.name or "search", build_search(args.query, args.language, args.limit)
    elif args.cmd == "file":
        name, repos = build_file(args.path, args.list_name)
        name = args.name or name
    elif args.cmd == "merge":
        name, repos = build_merge(args.path, args.lists)
        name = args.name or name
    else:
        name, repos = args.name or "repos", args.repos

    repos = dedupe(repos)
    if args.exclude:
        excluded = {r.lower() for lst in read_lists(args.exclude).values() for r in lst}
        before = len(repos)
        repos = [r for r in repos if r.lower() not in excluded]
        print(f"excluded {before - len(repos)} repositories listed in {args.exclude}", file=sys.stderr)
    if args.truncate and len(repos) > args.max:
        print(f"truncated {len(repos)} repositories to the first {args.max}", file=sys.stderr)
        repos = repos[: args.max]

    lists = chunk(name, repos, args.chunk)
    for lname, lrepos in lists.items():
        if len(lrepos) > args.max:
            print(f"warning: {lname} has {len(lrepos)} repositories, above the MRVA per-run limit of "
                  f"{args.max}; use --chunk {args.max}", file=sys.stderr)

    if args.out:
        fmt = write_lists(args.out, lists, args.format)
        for lname, lrepos in lists.items():
            print(f"wrote {lname}: {len(lrepos)} repositories -> {args.out} ({fmt})", file=sys.stderr)
        if args.json:
            print(json.dumps(lists, indent=2))
    elif (args.format or "gh-mrva") == "vscode":
        print(json.dumps(vscode_doc(lists), indent=2))
    else:
        print(json.dumps(lists, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
