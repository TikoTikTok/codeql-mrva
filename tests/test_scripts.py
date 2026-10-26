"""Offline tests for the codeql-mrva helper scripts (no network, no gh calls)."""
import json
import os
import pathlib
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "skills" / "codeql-mrva" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import mrva_dbs as md  # noqa: E402
import mrva_list as ml  # noqa: E402
import mrva_sarif as ms  # noqa: E402


# --- mrva_list --------------------------------------------------------------

@pytest.mark.parametrize("raw,expected", [
    ("psf/requests", "psf/requests"),
    ("  https://github.com/Psf/Requests.git ", "Psf/Requests"),
    ("git@github.com:rails/rails.git", "rails/rails"),
    ("https://github.com/owner/repo/tree/main/sub", "owner/repo"),
    ("# comment", None),
    ("just-a-name", None),
    ("", None),
])
def test_norm_repo(raw, expected):
    assert ml.norm_repo(raw) == expected


def test_dedupe_is_case_insensitive_and_keeps_order():
    assert ml.dedupe(["a/b", "A/B", "c/d", "a/b"]) == ["a/b", "c/d"]


def test_norm_lang_aliases():
    assert ml.norm_lang("py") == "python"
    assert ml.norm_lang("TypeScript") == "javascript"
    assert ml.norm_lang("c++") == "cpp"
    assert ml.norm_lang("java-kotlin") == "java"
    assert md.ml.norm_lang(None) is None


def test_write_and_read_gh_mrva_format(tmp_path):
    out = tmp_path / "databases.json"
    assert ml.write_lists(str(out), {"one": ["a/b", "c/d"]}) == "gh-mrva"
    assert ml.write_lists(str(out), {"two": ["e/f"]}) == "gh-mrva"  # merge keeps "one"
    doc = json.loads(out.read_text())
    assert doc == {"one": ["a/b", "c/d"], "two": ["e/f"]}
    assert ml.read_lists(str(out)) == {"one": ["a/b", "c/d"], "two": ["e/f"]}


def test_write_and_read_vscode_format(tmp_path):
    out = tmp_path / "databases.json"
    assert ml.write_lists(str(out), {"one": ["a/b"]}, "vscode") == "vscode"
    # a second write in the other format keeps the file's existing format
    assert ml.write_lists(str(out), {"one": ["a/b", "x/y"], "two": ["c/d"]}, "gh-mrva") == "vscode"
    doc = json.loads(out.read_text())
    assert doc["version"] == 1
    lists = doc["databases"]["variantAnalysis"]["repositoryLists"]
    assert lists == [{"name": "one", "repositories": ["a/b", "x/y"]}, {"name": "two", "repositories": ["c/d"]}]
    assert doc["selected"] == {"kind": "variantAnalysisUserDefinedList", "listName": "two"}
    assert ml.read_lists(str(out)) == {"one": ["a/b", "x/y"], "two": ["c/d"]}


def test_read_copy_repository_list_clipboard_shape(tmp_path):
    p = tmp_path / "copied.json"
    p.write_text(json.dumps({"name": "hits", "repositories": ["a/b", "a/b", "c/d"]}))
    assert ml.read_lists(str(p)) == {"hits": ["a/b", "c/d"]}


def test_read_plain_text_list(tmp_path):
    p = tmp_path / "targets.txt"
    p.write_text("# targets\npsf/requests\nhttps://github.com/pallets/flask\n\n")
    assert ml.read_lists(str(p)) == {"targets": ["psf/requests", "pallets/flask"]}


def test_chunk():
    repos = [f"o/r{i}" for i in range(5)]
    assert ml.chunk("x", repos, 2) == {"x-1": repos[:2], "x-2": repos[2:4], "x-3": repos[4:]}
    assert ml.chunk("x", repos, 0) == {"x": repos}
    assert ml.chunk("x", repos, 10) == {"x": repos}


def test_cli_repos_subcommand_prints_json(tmp_path):
    out = tmp_path / "l.json"
    r = subprocess.run([sys.executable, str(SCRIPTS / "mrva_list.py"), "repos", "a/b", "A/B", "c/d",
                        "--name", "lit", "--out", str(out), "--json"], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert json.loads(r.stdout) == {"lit": ["a/b", "c/d"]}
    assert "wrote lit: 2 repositories" in r.stderr


def test_merge_and_truncate(tmp_path):
    f = tmp_path / "lists.json"
    f.write_text(json.dumps({"s1": ["a/b", "c/d"], "s2": ["e/f", "A/B"], "s3": ["g/h"]}))
    assert ml.build_merge(str(f), "s2,s1") == ("merged", ["e/f", "A/B", "c/d"])
    assert ml.build_merge(str(f), None) == ("merged", ["a/b", "c/d", "e/f", "g/h"])
    r = subprocess.run([sys.executable, str(SCRIPTS / "mrva_list.py"), "merge", str(f), "--lists", "s2,s1,s3",
                        "--name", "all", "--max", "3", "--truncate"], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert json.loads(r.stdout) == {"all": ["e/f", "A/B", "c/d"]}
    assert "truncated 4 repositories to the first 3" in r.stderr


def test_build_search_pages_and_waits_on_rate_limit(monkeypatch):
    calls = []
    seen_queries = set()
    pages = {1: "a/b\nc/d\n" + "x/y\n" * 98, 2: "e/f\n"}   # page 1 full, page 2 partial -> stop
    state = {"limited": False}

    def fake_gh_try(args):
        seen_queries.add([a for a in args if a.startswith("q=")][0])
        page = int([a for a in args if a.startswith("page=")][0].split("=")[1])
        calls.append(page)
        if page == 2 and not state["limited"]:
            state["limited"] = True
            return 1, "", "gh: API rate limit exceeded for user ID 1 (HTTP 403)"
        return 0, pages.get(page, ""), ""

    monkeypatch.setattr(ml, "gh_try", fake_gh_try)
    monkeypatch.setattr(ml.time, "sleep", lambda s: None)
    repos = ml.build_search("pickle.loads path:src", "py", 1000)
    assert ml.dedupe(repos) == ["a/b", "c/d", "x/y", "e/f"]
    assert calls == [1, 2, 2]                      # page 2 retried after the rate limit, then stopped
    assert seen_queries == {"q=pickle.loads path:src language:python"}


def test_pick_filters_sorts_and_caps():
    def rec(name, stars, pushed, lang="python"):
        return {"repo": name, "canonical": name, "renamed": False, "archived": False, "stars": stars,
                "pushed_at": pushed, "pushed_days": None, "missing": False, "error": None,
                "databases": [{"language": lang, "size_mb": 1.0, "updated_at": "", "age_days": 1, "commit_oid": ""}]}
    recs = [rec("a/low", 5, "2026-01-01T00:00:00Z"), rec("b/high", 500, "2025-01-01T00:00:00Z"),
            rec("c/mid", 50, "2026-06-01T00:00:00Z"), rec("d/js", 999, "2026-09-01T00:00:00Z", lang="javascript")]
    assert md.pick(recs, "python", None) == ["a/low", "b/high", "c/mid"]
    assert md.pick(recs, "python", None, sort="stars") == ["b/high", "c/mid", "a/low"]
    assert md.pick(recs, "python", None, sort="pushed") == ["c/mid", "a/low", "b/high"]
    assert md.pick(recs, "python", None, min_stars=50, sort="stars", top=1) == ["b/high"]
    recs[1]["databases"][0]["size_mb"] = 230.0
    assert md.pick(recs, "python", None, sort="stars", max_mb=150) == ["c/mid", "a/low"]


def test_cli_exclude_and_chunk_warning(tmp_path):
    ex = tmp_path / "ex.json"
    ex.write_text(json.dumps({"skip": ["c/d"]}))
    r = subprocess.run([sys.executable, str(SCRIPTS / "mrva_list.py"), "repos", "a/b", "c/d", "e/f",
                        "--exclude", str(ex), "--max", "1"], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert json.loads(r.stdout) == {"repos": ["a/b", "e/f"]}
    assert "excluded 1" in r.stderr and "above the MRVA per-run limit" in r.stderr


# --- mrva_dbs ---------------------------------------------------------------

def test_has_db_filters_language_and_age():
    rec = {"repo": "a/b", "databases": [
        {"language": "python", "age_days": 400}, {"language": "javascript", "age_days": 3}], "error": None}
    assert md.has_db(rec, "python", None)
    assert not md.has_db(rec, "python", 30)
    assert md.has_db(rec, "javascript", 30)
    assert not md.has_db(rec, "ruby", None)
    assert md.has_db(rec, None, None)


def test_age_days_parses_github_timestamps():
    assert md.age_days("2020-01-01T00:00:00Z") > 2000
    assert md.age_days("") is None
    assert md.age_days("garbage") is None


def test_gh_error_classification():
    assert md._gh_error("gh: Not Found (HTTP 404)") == "not found"
    assert md._gh_error("gh: API rate limit exceeded for user (HTTP 403)") == "API rate limit; retry later"
    assert md._gh_error("gh: Forbidden (HTTP 403)") == "forbidden"
    assert md._gh_error("") == "gh api failed"


def test_render_table_handles_renamed_and_missing():
    recs = [
        {"repo": "old/name", "canonical": "new/name", "renamed": True, "archived": False, "missing": False,
         "error": None, "databases": [{"language": "python", "size_mb": 1.0, "updated_at": "", "age_days": 3,
                                       "commit_oid": "abc123"}]},
        {"repo": "gone/repo", "canonical": "gone/repo", "renamed": False, "archived": True, "missing": True,
         "error": None, "databases": []},
    ]
    out = md.render_table(recs, "python")
    assert "old/name -> new/name" in out and "yes" in out and "abc123" in out
    assert "(repository not found)" in out
    assert md._label(recs[0]) == "old/name -> new/name"


def test_safe_extract_refuses_zip_slip(tmp_path):
    import zipfile
    z = tmp_path / "evil.zip"
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("../escape.txt", "x")
    with pytest.raises(RuntimeError):
        md._safe_extract(str(z), str(tmp_path / "out"))


# --- mrva_sarif -------------------------------------------------------------

def _sarif(repo_uri=None, results=()):
    run = {
        "tool": {"driver": {"name": "CodeQL", "rules": [
            {"id": "py/variant/unsafe-pickle-remote", "properties": {"security-severity": "9.8"},
             "defaultConfiguration": {"level": "error"}}]}},
        "results": list(results),
    }
    if repo_uri:
        run["versionControlProvenance"] = [{"repositoryUri": repo_uri, "revisionId": "abc"}]
    return {"version": "2.1.0", "runs": [run]}


def _result(uri, line, msg="pickle.loads of data from [a remote source](1).", flow=None):
    res = {"ruleId": "py/variant/unsafe-pickle-remote", "level": "error", "message": {"text": msg},
           "locations": [{"physicalLocation": {"artifactLocation": {"uri": uri}, "region": {"startLine": line}}}]}
    if flow:
        res["codeFlows"] = [{"threadFlows": [{"locations": [
            {"location": {"physicalLocation": {"artifactLocation": {"uri": f}, "region": {"startLine": n}}}}
            for f, n in flow]}]}]
    return res


def test_clean_message_strips_sarif_link_markers():
    assert ms._clean_message("pickle.loads with argument [ControlFlowNode for x](1).  \n") == \
        "pickle.loads with argument ControlFlowNode for x."
    assert ms._clean_message("") == ""


def test_cell_truncates_with_ascii():
    cell = ms._cell("a" * 200, 50)
    assert len(cell) == 50 and cell.endswith("...") and cell.isascii()
    assert ms._cell("a|b") == "a\\|b"


def test_repo_inference_from_filenames(tmp_path):
    assert ms.repo_from_run({}, "psf_requests_12345.sarif") == "psf/requests"
    assert ms.repo_from_run({}, "Significant-Gravitas_Auto_GPT_7.sarif") == "Significant-Gravitas/Auto_GPT"
    assert ms.repo_from_run({}, "psf__requests__python.sarif") == "psf/requests"
    assert ms.repo_from_run({"versionControlProvenance": [{"repositoryUri": "https://github.com/a/b.git"}]},
                            "whatever.sarif") == "a/b"
    assert ms.repo_from_run({}, "local.sarif") == "local"


def test_summary_counts_filters_and_repo_list(tmp_path):
    d = tmp_path / "results"
    d.mkdir()
    (d / "psf_requests_1.sarif").write_text(json.dumps(_sarif(results=[
        _result("src/api.py", 10, flow=[("src/views.py", 3), ("src/api.py", 10)]),
        _result("tests/test_api.py", 5),
    ])))
    (d / "pallets_flask_1.sarif").write_text(json.dumps(_sarif()))
    (d / "django_django_1.sarif").write_text(json.dumps(_sarif(results=[_result("django/core.py", 1)])))

    findings, seen = ms.load_findings([str(d)])
    assert seen == {"django/django": 1, "pallets/flask": 0, "psf/requests": 2}
    assert len(findings) == 3
    f = next(x for x in findings if x["file"] == "src/api.py")
    assert f["path_len"] == 2 and f["source"] == "src/views.py:3" and f["security_severity"] == "9.8"

    kept = ms.apply_filters(findings, r"(^|/)tests?/", None, None)
    assert [x["file"] for x in kept] == ["django/core.py", "src/api.py"]

    md_text = ms.render_markdown(kept, seen, 1, 25)
    assert "| psf/requests | 1 |" in md_text and "| django/django | 1 |" in md_text
    assert "pallets/flask" not in md_text  # min-results 1 hides empty repositories
    assert "| py/variant/unsafe-pickle-remote | 2 | 2 |" in md_text
    assert "src/api.py:10" in md_text

    out = tmp_path / "databases.json"
    r = subprocess.run([sys.executable, str(SCRIPTS / "mrva_sarif.py"), str(d), "--exclude-paths", r"(^|/)tests?/",
                        "--repo-list", "hits", "--out", str(out)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert json.loads(out.read_text()) == {"hits": ["django/django", "psf/requests"]}
    assert r.stdout.startswith("# MRVA triage")


def test_paths_flag_lists_code_flow_steps(tmp_path):
    p = tmp_path / "owner__repo__python.sarif"
    p.write_text(json.dumps(_sarif(results=[
        _result("lib/chain.py", 105, flow=[("lib/network.py", 41), ("lib/chain.py", 104), ("lib/chain.py", 105)]),
    ])))
    r = subprocess.run([sys.executable, str(SCRIPTS / "mrva_sarif.py"), str(p), "--paths"],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert "Path to `lib/chain.py:105`:" in r.stdout
    assert " 0  lib/network.py:41" in r.stdout and " 2  lib/chain.py:105" in r.stdout
    findings, _ = ms.load_findings([str(p)])
    assert [s["line"] for s in findings[0]["steps"]] == [41, 104, 105]
    r2 = subprocess.run([sys.executable, str(SCRIPTS / "mrva_sarif.py"), str(p)], capture_output=True, text=True)
    assert "Path to" not in r2.stdout


def test_json_output(tmp_path):
    p = tmp_path / "one.sarif"
    p.write_text(json.dumps(_sarif(repo_uri="https://github.com/x/y", results=[_result("a.py", 2)])))
    r = subprocess.run([sys.executable, str(SCRIPTS / "mrva_sarif.py"), str(p), "--json"],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    data = json.loads(r.stdout)
    assert data[0]["repo"] == "x/y" and data[0]["line"] == 2


# --- command lines ----------------------------------------------------------

@pytest.mark.parametrize("script", ["mrva_list.py", "mrva_dbs.py", "mrva_sarif.py"])
def test_help(script):
    r = subprocess.run([sys.executable, str(SCRIPTS / script), "--help"], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert "usage" in r.stdout.lower()


def test_skill_md_mentions_every_support_file():
    """Hermes only fetches support files that SKILL.md names, so every file must be referenced."""
    skill = ROOT / "skills" / "codeql-mrva"
    text = (skill / "SKILL.md").read_text(encoding="utf-8")
    for sub in ("references", "scripts", "assets"):
        for path in (skill / sub).rglob("*"):
            if not path.is_file() or "__pycache__" in path.parts or path.suffix == ".pyc":
                continue  # bytecode never ships in a skill bundle
            rel = path.relative_to(skill).as_posix()
            assert rel in text, f"{rel} is not mentioned in SKILL.md"
