#!/usr/bin/env python3
"""Which repositories have a CodeQL database on GitHub, and fetch them.

  mrva_dbs.py owner/repo [owner/repo ...] [--language L]
  mrva_dbs.py --file LIST [--list NAME] [--language L] [--write-list NAME --out FILE [--format F]]
  mrva_dbs.py owner/repo --language L --download DIR [--extract]

Options
  --language L        CodeQL language id (python, javascript, java, cpp, csharp, go, ruby, rust, swift, actions)
  --max-age-days N    treat databases older than N days as missing when filtering
  --min-stars N       keep only repositories with at least N stars
  --max-db-mb N       drop repositories whose --language database is larger than N MB (one huge
                      database can set the wall time of a whole MRVA run)
  --sort stars|pushed order the written list by stars or by last push (default: input order)
  --top N             keep only the first N repositories of the (sorted) list
  --write-list NAME   write the selected repositories as a list (needs --out)
  --download DIR      download the --language database zip of each selected repository
  --extract           after --download, unpack into DIR/<owner>__<repo>__<language>/ (a usable database)
  --workers N         parallel API calls (default 8)
  --json              machine-readable output

MRVA only analyses repositories that have a stored CodeQL database for the
default branch; everything else is skipped. This script first resolves each
name through GET /repos/{owner}/{repo} (so a renamed repository is written
under its current name: MRVA reports the old name as "not found" even though
the database endpoint follows the redirect; the same call yields stars and
last push for ordering), then asks
GET /repos/{owner}/{repo}/code-scanning/codeql/databases through `gh api`.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import shutil
import subprocess
import sys
import zipfile
from concurrent.futures import ThreadPoolExecutor
from typing import List, Optional, Tuple

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import mrva_list as ml  # noqa: E402


def run_gh(args: List[str], stdout=None) -> Tuple[int, str, str]:
    try:
        proc = subprocess.run(["gh", *args], stdout=stdout or subprocess.PIPE, stderr=subprocess.PIPE)
    except FileNotFoundError:
        sys.exit("error: `gh` (GitHub CLI) is not on PATH. Install it from "
                 "https://cli.github.com and run `gh auth login`.")
    out = "" if stdout else proc.stdout.decode("utf-8", "replace")
    return proc.returncode, out, proc.stderr.decode("utf-8", "replace")


def _gh_error(err: str) -> str:
    low = err.lower()
    if "rate limit" in low:
        return "API rate limit; retry later"
    if "(http 404)" in low or "not found" in low:
        return "not found"
    if "(http 403)" in low:
        return "forbidden"
    return err.strip().splitlines()[-1] if err.strip() else "gh api failed"


def age_days(iso: str) -> Optional[int]:
    if not iso:
        return None
    try:
        when = dt.datetime.strptime(iso[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=dt.timezone.utc)
    except ValueError:
        return None
    return (dt.datetime.now(dt.timezone.utc) - when).days


def resolve_repo(repo: str) -> Tuple[Optional[dict], Optional[str]]:
    """({full_name, archived, stars, pushed_at}, error). Follows renames through GET /repos/{owner}/{repo}."""
    rc, out, err = run_gh(["api", f"repos/{repo}", "--jq", "{full_name, archived, stargazers_count, pushed_at}"])
    if rc != 0:
        return None, _gh_error(err)
    try:
        meta = json.loads(out)
    except json.JSONDecodeError:
        return None, "unexpected response"
    return {"full_name": meta.get("full_name") or repo, "archived": bool(meta.get("archived")),
            "stars": int(meta.get("stargazers_count") or 0), "pushed_at": meta.get("pushed_at") or ""}, None


def fetch_dbs(repo: str) -> dict:
    rec = {"repo": repo, "canonical": repo, "renamed": False, "archived": False, "stars": 0,
           "pushed_at": "", "pushed_days": None, "missing": False, "databases": [], "error": None}
    meta, err = resolve_repo(repo)
    if err == "not found":
        rec["missing"] = True
        return rec
    if err:
        rec["error"] = err
        return rec
    rec.update(canonical=meta["full_name"], renamed=meta["full_name"].lower() != repo.lower(),
               archived=meta["archived"], stars=meta["stars"], pushed_at=meta["pushed_at"],
               pushed_days=age_days(meta["pushed_at"]))

    rc, out, err = run_gh(["api", f"repos/{rec['canonical']}/code-scanning/codeql/databases"])
    if rc != 0:
        reason = _gh_error(err)
        if reason not in ("not found", "forbidden"):   # 404/403 here simply mean "no database"
            rec["error"] = reason
        return rec
    try:
        rows = json.loads(out) if out.strip() else []
    except json.JSONDecodeError:
        rec["error"] = "unexpected response"
        return rec
    for row in rows or []:
        updated = row.get("updated_at") or row.get("created_at") or ""
        rec["databases"].append({
            "language": ml.norm_lang(row.get("language")) or row.get("language"),
            "size_mb": round((row.get("size") or 0) / 1048576, 1),
            "updated_at": updated,
            "age_days": age_days(updated),
            "commit_oid": (row.get("commit_oid") or "")[:12],
        })
    return rec


def has_db(record: dict, language: Optional[str], max_age: Optional[int],
           max_mb: Optional[float] = None) -> bool:
    for db in record["databases"]:
        if language and db["language"] != language:
            continue
        if max_age is not None and db["age_days"] is not None and db["age_days"] > max_age:
            continue
        if max_mb is not None and (db.get("size_mb") or 0) > max_mb:
            continue
        return True
    return False


def pick(records: List[dict], language: Optional[str], max_age: Optional[int],
         min_stars: Optional[int] = None, sort: str = "none", top: Optional[int] = None,
         max_mb: Optional[float] = None) -> List[str]:
    """Canonical names of the records that have a matching database, filtered, ordered and capped."""
    rows = [r for r in records if has_db(r, language, max_age, max_mb)
            and (min_stars is None or (r.get("stars") or 0) >= min_stars)]
    if sort == "stars":
        rows.sort(key=lambda r: -(r.get("stars") or 0))
    elif sort == "pushed":
        rows.sort(key=lambda r: r.get("pushed_at") or "", reverse=True)
    names = ml.dedupe(r["canonical"] for r in rows)
    return names[:top] if top else names


def _safe_extract(zpath: str, target: str) -> None:
    base = os.path.realpath(target)
    with zipfile.ZipFile(zpath) as zf:
        for member in zf.infolist():
            dest = os.path.realpath(os.path.join(base, member.filename))
            if dest != base and not dest.startswith(base + os.sep):
                raise RuntimeError(f"refusing to extract {member.filename!r}: path escapes {target}")
        zf.extractall(base)


def _find_db_root(root: str) -> Optional[str]:
    for dirpath, _dirs, files in os.walk(root):
        if "codeql-database.yml" in files:
            return dirpath
    return None


def download(repo: str, language: str, outdir: str, extract: bool) -> Tuple[str, Optional[str]]:
    outdir = os.path.normpath(outdir)
    os.makedirs(outdir, exist_ok=True)
    name = f"{repo.replace('/', '__')}__{language}"
    zpath = os.path.join(outdir, name + ".zip")
    target = os.path.join(outdir, name)
    if extract and os.path.isdir(target):
        return target, None
    if not os.path.exists(zpath):
        tmp = zpath + ".part"
        with open(tmp, "wb") as fh:
            rc, _out, err = run_gh(["api", f"repos/{repo}/code-scanning/codeql/databases/{language}",
                                    "-H", "Accept: application/zip"], stdout=fh)
        if rc != 0:
            os.remove(tmp)
            return "", err.strip().splitlines()[-1] if err.strip() else "download failed"
        os.replace(tmp, zpath)
    if not extract:
        return zpath, None
    tmpdir = os.path.join(outdir, f".tmp-{name}")
    shutil.rmtree(tmpdir, ignore_errors=True)
    try:
        _safe_extract(zpath, tmpdir)
        root = _find_db_root(tmpdir)
        if root is None:
            return "", f"{zpath} contains no codeql-database.yml"
        shutil.move(root, target)
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
    return target, None


def _label(r: dict) -> str:
    return f"{r['repo']} -> {r['canonical']}" if r["renamed"] else r["repo"]


def render_table(records: List[dict], language: Optional[str]) -> str:
    width = max([len(_label(r)) for r in records] + [10])
    lines = []
    if language:
        lines.append(f"{'repository'.ljust(width)}  {language:<8} {'stars':>7} {'size':>8} {'age':>6}  commit")
    else:
        lines.append(f"{'repository'.ljust(width)}  {'stars':>7}  databases (language:size/age)")
    for r in records:
        label = _label(r).ljust(width)
        flag = "  [archived]" if r["archived"] else ""
        stars = f"{r.get('stars') or 0:>7}"
        if r["error"]:
            lines.append(f"{label}  error: {r['error']}")
            continue
        if r["missing"]:
            lines.append(f"{label}  (repository not found)")
            continue
        if language:
            dbs = [d for d in r["databases"] if d["language"] == language]
            if not dbs:
                lines.append(f"{label}  {'-':<8} {stars} {'':>8} {'':>6}  (no {language} database){flag}")
                continue
            d = dbs[0]
            age = f"{d['age_days']}d" if d["age_days"] is not None else "?"
            lines.append(f"{label}  {'yes':<8} {stars} {str(d['size_mb']) + 'MB':>8} {age:>6}  {d['commit_oid']}{flag}")
        elif not r["databases"]:
            lines.append(f"{label}  {stars}  (none){flag}")
        else:
            cells = ", ".join(f"{d['language']}:{d['size_mb']}MB/{d['age_days']}d" for d in r["databases"])
            lines.append(f"{label}  {stars}  {cells}{flag}")
    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("repos", nargs="*", help="owner/repo")
    ap.add_argument("--file", help="list file (gh-mrva or VS Code format) or text file")
    ap.add_argument("--list", dest="list_name", help="list name inside --file")
    ap.add_argument("--language")
    ap.add_argument("--max-age-days", type=int)
    ap.add_argument("--min-stars", type=int)
    ap.add_argument("--max-db-mb", type=float)
    ap.add_argument("--sort", choices=["none", "stars", "pushed"], default="none")
    ap.add_argument("--top", type=int)
    ap.add_argument("--write-list", metavar="NAME")
    ap.add_argument("--out")
    ap.add_argument("--format", choices=["gh-mrva", "vscode"])
    ap.add_argument("--download", metavar="DIR")
    ap.add_argument("--extract", action="store_true")
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    repos: List[str] = list(args.repos)
    if args.file:
        _name, from_file = ml.build_file(args.file, args.list_name)
        repos += from_file
    repos = ml.dedupe(repos)
    if not repos:
        ap.error("no repositories given (arguments or --file)")
    language = ml.norm_lang(args.language)
    if language and language not in ml.LANG_NAMES:
        ap.error(f"unknown language {args.language!r}; use one of {', '.join(sorted(ml.LANG_NAMES))}")
    if (args.write_list or args.download) and not language:
        ap.error("--write-list and --download need --language")
    if args.write_list and not args.out:
        ap.error("--write-list needs --out FILE")

    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
        records = list(pool.map(fetch_dbs, repos))

    matching = [r for r in records if has_db(r, language, args.max_age_days)]
    selected = pick(records, language, args.max_age_days, args.min_stars, args.sort, args.top, args.max_db_mb)
    errors = sum(1 for r in records if r["error"])

    if args.json:
        print(json.dumps({"language": language, "selected": selected, "records": records}, indent=2))
    else:
        print(render_table(records, language))
    sys.stdout.flush()
    for r in records:
        if r["renamed"]:
            print(f"note: {r['repo']} is now {r['canonical']}; MRVA reports the old name as not found, "
                  f"so the list uses the new one", file=sys.stderr)
    what = f"a {language} database" if language else "at least one database"
    if args.max_age_days is not None:
        what += f" newer than {args.max_age_days} days"
    print(f"{len(matching)}/{len(records)} repositories have {what}"
          + (f"; {len(selected)} selected after --min-stars/--max-db-mb/--top and rename resolution"
             if len(selected) != len(matching) else "")
          + (f" ({errors} errors)" if errors else ""), file=sys.stderr)

    if args.write_list:
        fmt = ml.write_lists(args.out, {args.write_list: selected}, args.format)
        print(f"wrote {args.write_list}: {len(selected)} repositories -> {args.out} ({fmt})", file=sys.stderr)

    if args.download:
        for repo in selected:
            path, err = download(repo, language, args.download, args.extract)
            if err:
                print(f"{repo}: {err}", file=sys.stderr)
            else:
                print(f"{repo}: {path}", file=sys.stderr)
        if not args.extract:
            print("unpack with: codeql database unbundle <zip> --target <dir>  (or re-run with --extract)",
                  file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
