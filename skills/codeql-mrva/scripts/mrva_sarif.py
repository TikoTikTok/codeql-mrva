#!/usr/bin/env python3
"""Summarise SARIF results from `gh mrva download`, VS Code exports or local `codeql database analyze` runs.

  mrva_sarif.py PATH [PATH ...]            files, or directories searched recursively for *.sarif
     --min-results N        only repositories with at least N findings (default 1; 0 lists empty ones)
     --max-per-repo N       findings listed per repository in the markdown (default 25)
     --exclude-paths REGEX  drop findings whose file path matches (e.g. '(^|/)(tests?|spec|examples?)/')
     --grep REGEX           keep findings whose message or path matches
     --rule ID              keep one rule id
     --paths                under each finding, list every step of its code flow (path-problem queries)
     --json                 print findings as JSON instead of markdown (always includes the steps)
     --repo-list NAME       write the repositories with findings as a list (--out FILE, --format F),
                            the same thing VS Code's "Copy repository list" produces

The repository name comes from runs[].versionControlProvenance[].repositoryUri when
present, else from the file name: gh-mrva writes <owner>_<repo>_<runId>.sarif and
mrva_dbs.py names databases <owner>__<repo>__<language>.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import Counter, OrderedDict, defaultdict
from typing import Dict, Iterable, List, Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import mrva_list as ml  # noqa: E402


def sarif_files(paths: Iterable[str]) -> List[str]:
    out: List[str] = []
    for p in paths:
        if os.path.isdir(p):
            for dirpath, _dirs, files in os.walk(p):
                out += sorted(os.path.join(dirpath, f) for f in files if f.lower().endswith(".sarif"))
        elif os.path.isfile(p):
            out.append(p)
        else:
            print(f"warning: {p} does not exist", file=sys.stderr)
    return out


def repo_from_run(run: dict, path: str) -> str:
    for vcp in run.get("versionControlProvenance") or []:
        uri = vcp.get("repositoryUri") or ""
        m = re.search(r"github\.com/([^/]+/[^/]+?)(?:\.git)?/?$", uri)
        if m:
            return m.group(1)
    stem = os.path.splitext(os.path.basename(path))[0]
    if "__" in stem:                                   # owner__repo__language
        parts = stem.split("__")
        if len(parts) >= 2:
            return f"{parts[0]}/{parts[1]}"
    stem = re.sub(r"_\d+$", "", stem)                 # owner_repo_<runId>
    if "_" in stem:                                    # GitHub owners cannot contain '_'
        owner, repo = stem.split("_", 1)
        return f"{owner}/{repo}"
    return stem


def _loc(location: dict) -> (str, Optional[int]):
    phys = (location or {}).get("physicalLocation") or {}
    uri = (phys.get("artifactLocation") or {}).get("uri") or ""
    line = (phys.get("region") or {}).get("startLine")
    return uri, line


def load_findings(paths: Iterable[str]) -> (List[dict], Dict[str, int]):
    """Returns (findings, repositories seen -> result count before filtering)."""
    findings: List[dict] = []
    seen: Dict[str, int] = OrderedDict()
    for path in sarif_files(paths):
        try:
            with open(path, encoding="utf-8-sig") as fh:
                doc = json.load(fh)
        except (OSError, json.JSONDecodeError) as exc:
            print(f"warning: skipping {path}: {exc}", file=sys.stderr)
            continue
        for run in doc.get("runs") or []:
            repo = repo_from_run(run, path)
            results = run.get("results") or []
            seen[repo] = seen.get(repo, 0) + len(results)
            rules = {r.get("id"): r for r in ((run.get("tool") or {}).get("driver") or {}).get("rules") or []}
            for res in results:
                uri, line = _loc((res.get("locations") or [{}])[0])
                flows = res.get("codeFlows") or []
                path_len = 0
                source = ""
                steps: List[dict] = []
                if flows:
                    locs = ((flows[0].get("threadFlows") or [{}])[0].get("locations") or [])
                    path_len = len(locs)
                    for step in locs:
                        s_uri, s_line = _loc(step.get("location") or {})
                        note = _clean_message(((step.get("location") or {}).get("message") or {}).get("text") or "")
                        steps.append({"file": s_uri, "line": s_line, "note": note})
                    if steps:
                        s0 = steps[0]
                        source = f"{s0['file']}:{s0['line']}" if s0["line"] else s0["file"]
                rule_id = res.get("ruleId") or (res.get("rule") or {}).get("id") or "?"
                rule = rules.get(rule_id) or {}
                findings.append({
                    "repo": repo,
                    "file": uri,
                    "line": line,
                    "rule": rule_id,
                    "level": res.get("level") or (rule.get("defaultConfiguration") or {}).get("level") or "",
                    "security_severity": (rule.get("properties") or {}).get("security-severity"),
                    "message": _clean_message((res.get("message") or {}).get("text") or ""),
                    "path_len": path_len,
                    "source": source,
                    "steps": steps,
                    "fingerprint": (res.get("partialFingerprints") or {}).get("primaryLocationLineHash"),
                    "sarif": path,
                })
    return findings, seen


def apply_filters(findings: List[dict], exclude_paths: Optional[str], grep: Optional[str],
                  rule: Optional[str]) -> List[dict]:
    ex = re.compile(exclude_paths) if exclude_paths else None
    gr = re.compile(grep, re.IGNORECASE) if grep else None
    out = []
    for f in findings:
        if rule and f["rule"] != rule:
            continue
        if ex and ex.search(f["file"] or ""):
            continue
        if gr and not (gr.search(f["message"]) or gr.search(f["file"] or "")):
            continue
        out.append(f)
    return out


def _clean_message(text: str) -> str:
    """Collapse whitespace and drop SARIF's related-location markers: '[x](1)' -> 'x'."""
    text = re.sub(r"\[([^\]]*)\]\(\d+\)", r"\1", text or "")
    return re.sub(r"\s+", " ", text).strip()


def _cell(text: str, limit: int = 160) -> str:
    text = (text or "").replace("|", "\\|")
    return text if len(text) <= limit else text[: limit - 3] + "..."  # ASCII: Windows consoles mangle U+2026


def render_markdown(findings: List[dict], seen: Dict[str, int], min_results: int, max_per_repo: int,
                    paths: bool = False) -> str:
    per_repo: Dict[str, List[dict]] = defaultdict(list)
    for f in findings:
        per_repo[f["repo"]].append(f)
    repos = [r for r in seen if len(per_repo.get(r, [])) >= min_results]
    repos.sort(key=lambda r: (-len(per_repo.get(r, [])), r.lower()))
    rule_count = Counter(f["rule"] for f in findings)
    rule_repos: Dict[str, set] = defaultdict(set)
    for f in findings:
        rule_repos[f["rule"]].add(f["repo"])

    lines = ["# MRVA triage", ""]
    lines.append(f"- SARIF files: {len({f['sarif'] for f in findings}) if findings else 0} with findings, "
                 f"repositories analysed: {len(seen)}, with findings: {sum(1 for r in seen if per_repo.get(r))}, "
                 f"findings: {len(findings)}")
    lines += ["", "## Repositories", "", "| repository | findings | rules |", "|---|---:|---|"]
    for r in repos:
        rs = sorted({f["rule"] for f in per_repo.get(r, [])})
        lines.append(f"| {r} | {len(per_repo.get(r, []))} | {_cell(', '.join(rs), 120)} |")
    lines += ["", "## Rules", "", "| rule | findings | repositories |", "|---|---:|---:|"]
    for rule, n in rule_count.most_common():
        lines.append(f"| {rule} | {n} | {len(rule_repos[rule])} |")
    lines += ["", "## Findings", ""]
    for r in repos:
        fs = per_repo.get(r, [])
        if not fs:
            continue
        lines += [f"### {r} ({len(fs)})", "", "| location | rule | level | path | message |", "|---|---|---|---:|---|"]
        for f in sorted(fs, key=lambda x: (x["file"] or "", x["line"] or 0))[:max_per_repo]:
            loc = f"{f['file']}:{f['line']}" if f["line"] else (f["file"] or "?")
            path = f"{f['path_len']} ({f['source']})" if f["path_len"] else ""
            lines.append(f"| {_cell(loc, 100)} | {f['rule']} | {f['level']} | {_cell(path, 80)} | {_cell(f['message'])} |")
        if len(fs) > max_per_repo:
            lines.append(f"| ... {len(fs) - max_per_repo} more | | | | |")
        lines.append("")
        if paths:
            for f in sorted(fs, key=lambda x: (x["file"] or "", x["line"] or 0))[:max_per_repo]:
                if not f.get("steps"):
                    continue
                lines += [f"Path to `{f['file']}:{f['line']}`:", "", "```"]
                for i, s in enumerate(f["steps"]):
                    lines.append(f"{i:>2}  {s['file']}:{s['line']}  {s['note']}".rstrip())
                lines += ["```", ""]
    return "\n".join(lines).rstrip() + "\n"


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("paths", nargs="+")
    ap.add_argument("--min-results", type=int, default=1)
    ap.add_argument("--max-per-repo", type=int, default=25)
    ap.add_argument("--exclude-paths", metavar="REGEX")
    ap.add_argument("--grep", metavar="REGEX")
    ap.add_argument("--rule")
    ap.add_argument("--paths", dest="show_paths", action="store_true")  # dest: "paths" is the positional
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--repo-list", metavar="NAME")
    ap.add_argument("--out")
    ap.add_argument("--format", choices=["gh-mrva", "vscode"])
    args = ap.parse_args(argv)

    findings, seen = load_findings(args.paths)
    findings = apply_filters(findings, args.exclude_paths, args.grep, args.rule)

    if args.json:
        print(json.dumps(findings, indent=2))
    else:
        sys.stdout.write(render_markdown(findings, seen, args.min_results, args.max_per_repo, args.show_paths))

    if args.repo_list:
        hits = ml.dedupe(f["repo"] for f in findings)
        if args.out:
            fmt = ml.write_lists(args.out, {args.repo_list: hits}, args.format)
            print(f"wrote {args.repo_list}: {len(hits)} repositories -> {args.out} ({fmt})", file=sys.stderr)
        else:
            print(json.dumps({args.repo_list: hits}, indent=2), file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
