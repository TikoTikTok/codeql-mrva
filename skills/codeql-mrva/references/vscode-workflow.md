# MRVA from VS Code, click by click

Prerequisite: the **CodeQL** extension (`GitHub.vscode-codeql`) and a
workspace that contains your query inside a CodeQL pack (a folder with
`qlpack.yml`). The extension downloads and manages its own CodeQL CLI unless
`codeQL.cli.executablePath` points at one.

## 1. Controller repository

1. Open the CodeQL view (left bar) and find **Variant Analysis Repositories**.
2. Click **Set up controller repository**, type `owner/repo`, press Enter.
3. Sign in to GitHub when asked (the "GitHub Authentication" prompt opens in
   VS Code). The value is stored in `codeQL.variantAnalysis.controllerRepo`.

The repository must exist and have one commit (`scripts/mrva_controller.sh`
creates it). Public controller for public targets, private for anything else.

## 2. Pick targets

The view shows, per language, built-in lists **Top 10 / Top 100 / Top 1000**
public repositories (GitHub.com only; absent when the controller is on a
`*.ghe.com` host), plus whatever you add:

- **+** then *From a GitHub repository*: one `owner/repo`.
- **+** then *All repositories of GitHub org or owner*: an org or user (the
  run caps at 1,000).
- **Add list** icon: a named custom list; select it and **+** to add repos.
- Right-click a custom list, **Add repositories with GitHub code search**:
  pick a language, type a legacy-syntax code search (`org:rails`,
  `path:*.py pickle.loads`), Enter. Up to 1,000 repositories per search;
  Cancel adds none.

Lists are stored in `databases.json` in the extension's workspace storage,
editable through the `{ }` button:

```json
{
  "version": 1,
  "databases": {
    "variantAnalysis": {
      "repositoryLists": [
        { "name": "py-pickle", "repositories": ["psf/requests", "pallets/flask"] }
      ],
      "owners": ["rails"],
      "repositories": ["google/flatbuffers"]
    }
  },
  "selected": { "kind": "variantAnalysisUserDefinedList", "listName": "py-pickle" }
}
```

`selected.kind` is `variantAnalysisSystemDefinedList` for the Top-N lists
(`listName`: `top_10`, `top_100`, `top_1000`), `variantAnalysisUserDefinedList`
for yours, `variantAnalysisOwner` and `variantAnalysisRepository` for the
other two. `scripts/mrva_list.py --format vscode` writes this file, and the
list objects it contains are what **Copy repository list** (below) produces,
so you can paste between them.

## 3. Run

Select the target (click its row), open the `.ql` file, right-click in the
editor, **CodeQL: Run Variant Analysis**. The extension builds the pack,
posts it, and opens **Variant Analysis Results** as soon as the workflow is
scheduled. **Stop query** in that view cancels the run.

Packaging or compilation errors appear bottom-right and in the **Problems**
view before anything is submitted; run-time problems (a repository without a
database, access denied, job failures with a link to the workflow log in the
controller repository) appear inside the results view.

## 4. Read results

Each row is a repository: result count, visibility, running/finished, star
count. Sort by results, stars or last updated; filter to "with results".
Click a repository to see each finding with a link to the file on GitHub at
the analysed commit. For `path-problem` queries every finding has **Show
paths**.

Two buttons you will use on every run:

- **Export results**: a secret gist or a Markdown file in the workspace;
  good for handing a maintainer the evidence.
- **Copy repository list**: JSON of only the repositories that had results.
  Paste it into `databases.json` as a new list and re-run the tightened
  query on just those.

## 5. Where things go wrong

| Symptom | Cause / fix |
|---|---|
| "Controller repository not found" | Repository does not exist, wrong `owner/repo`, or token lacks `repo` scope; sign out and in again |
| "does not have a branch 'main'" | Empty controller; push one commit |
| Repository listed as skipped | No CodeQL database (enable code scanning with CodeQL on it, or build and analyse locally), or visibility mismatch with the controller |
| Query compiles locally, fails remotely | Library version mismatch; pin `codeql/<lang>-all` to a range the action's CLI can satisfy, run `codeql pack install` so the lock file is in the pack |
| Top 10/100/1000 missing | Controller on GitHub Enterprise host; build a custom list instead |
| Very slow / queued | Shared Actions capacity; set `MRVA_RUNNER_OS` to self-hosted labels on the controller |

## Downloading one database into VS Code

For local iteration before the big run: CodeQL view, **Databases**,
**Download database from GitHub** (cloud icon), type `owner/repo`, choose the
language. With `codeQL.githubDatabase.download: ask` the extension also offers
this when you open a workspace whose repository has a database. Then run the
query with **CodeQL: Run Query on Selected Database** and iterate on precision.
