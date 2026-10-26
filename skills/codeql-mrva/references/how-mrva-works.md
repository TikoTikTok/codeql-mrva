# How MRVA works

Multi-repository variant analysis (MRVA) was announced as a public beta on
9 March 2023 (GitHub blog, Walker Chabbott and James Fletcher: "Multi-repository
variant analysis: a powerful new way to perform security research across
GitHub"). GitHub's Security Lab had already used it to find the ownCloud
Android advisories GHSL-2022-059 / GHSL-2022-060, which affected applications
with more than 10 million installs. Code-search-built repository lists were
added in June 2023. The feature is part of the CodeQL for VS Code extension
and the code scanning REST API; it is free for public repositories.

## Pipeline

1. **Pack**. The client (VS Code extension, `gh mrva`, or your script) takes
   the query, resolves its library dependencies, and bundles everything into
   one CodeQL pack tarball: `codeql pack install <dir>` then
   `codeql pack bundle -o pack.tgz <dir>`.
2. **Submit**. The tarball is Base64-encoded and posted, with the language
   and the target list, to the controller repository:

   ```
   POST /repos/{owner}/{controller}/code-scanning/codeql/variant-analyses
   {
     "language": "python",                 // actions cpp csharp go java javascript python ruby rust swift
     "query_pack": "<base64 tgz>",
     "repositories":      ["owner/repo", ...]   // exactly one of these three
     "repository_lists":  ["top_1000"],         // system list names
     "repository_owners": ["rails"]             // every repo of an org/user (1,000 cap)
   }
   ```

   The token needs the `repo` scope. The response is `201` with the
   variant-analysis `id`, `controller_repo`, `actor`, `query_language`,
   `query_pack_url`, `status`, `actions_workflow_run_id`,
   `scanned_repositories[]` and `skipped_repositories{}`.
3. **Fan-out**. GitHub creates a dynamic workflow run in the controller
   repository (you never write it; it checks out
   `github/codeql-variant-analysis-action`). One parallel job per target
   repository downloads that repository's stored CodeQL database, runs the
   query, and uploads `results.sarif` and `results.bqrs` as an artifact.
4. **Collect**. Clients poll:

   ```
   GET /repos/{owner}/{controller}/code-scanning/codeql/variant-analyses/{id}
   GET /repos/{owner}/{controller}/code-scanning/codeql/variant-analyses/{id}/repos/{repo_owner}/{repo_name}
   ```

   The per-repository response carries `analysis_status` (`pending`,
   `in_progress`, `succeeded`, `failed`, `canceled`, `timed_out`),
   `result_count`, `artifact_size_in_bytes`, `artifact_url` (a zip with the
   SARIF and BQRS), `database_commit_sha` (the commit the database was built
   from; use it when you open files) and `source_location_prefix`. Reading
   needs `security_events` (private) or `public_repo` (public).

`skipped_repositories` tells you why a target was not analysed:
`access_mismatch_repos` (visibility does not match the controller),
`not_found_repos`, `no_codeql_db_repos`, `over_limit_repos` (beyond 1,000).
`not_found_repos` includes **renamed or transferred repositories**: MRVA does
not follow GitHub's redirect even though the database endpoint does, so
`tiangolo/fastapi` is skipped while `fastapi/fastapi` runs. Always submit the
current `full_name`; `scripts/mrva_dbs.py` resolves names through
`GET /repos/{owner}/{repo}` and writes the canonical ones into the list.

The summary's `actions_workflow_run_id` is the controller's workflow run.
`gh run watch <id> --repo OWNER/CONTROLLER --exit-status` blocks until every
per-repository job has finished, which is the simplest way to wait.

## Controller repository

- Any repository you can push to. It can be empty of code, but it **must have
  at least one commit** so a default branch exists; otherwise the run fails
  with "does not have a branch".
- **Public** controller (GitHub.com): may analyse public repositories only;
  Actions minutes are free.
- **Private** controller: required to analyse any private or internal
  repository; Actions minutes above the free allowance are billed to the
  repository owner.
- Workflow logs for every run (and the failures) are under its Actions tab.
  Anyone with read access to the controller sees which repositories you are
  querying, so do not share a controller with people who should not know.
- Self-hosted runners: create the Actions **repository variable**
  `MRVA_RUNNER_OS` with a JSON list of labels, e.g.
  `["self-hosted", "linux", "x64"]`. It must be a repository variable, not a
  secret, an environment variable or anything in a workflow file. No
  workflow changes are needed.
- One controller can be shared by VS Code, `gh mrva` and API scripts.

## Which repositories can be analysed

Only repositories that have a stored CodeQL database. GitHub builds and keeps
the latest database for the **default branch** of:

- every repository that runs code scanning with CodeQL (default or advanced
  setup), public or private (private needs read access and the right
  controller visibility);
- thousands of additional popular public repositories that do not run code
  scanning at all. This set changes over time.

Check before running:

```bash
gh api repos/OWNER/REPO/code-scanning/codeql/databases \
  --jq '.[] | "\(.language) \(.size/1048576|floor)MB \(.updated_at) \(.commit_oid)"'
```

A `404` or `403` means no database. Download one (zip) with
`-H "Accept: application/zip"`; the response is a `302` to blob storage,
which `gh api` and `curl -L` follow. `scripts/mrva_dbs.py` wraps both.

## Limits

| Limit | Value |
|---|---|
| Repositories per run | 1,000 |
| Languages per run | 1 |
| Repositories added per code search | 1,000 |
| Built-in lists | Top 10, Top 100, Top 1000 per language, GitHub.com only |
| Result retention | Actions artifacts of the controller run (repository artifact retention applies) |

## VS Code setting IDs

| Setting | Default | Meaning |
|---|---|---|
| `codeQL.variantAnalysis.controllerRepo` | "" | `owner/repo` of the controller |
| `codeQL.variantAnalysis.defaultResultsFilter` | `all` | results view filter |
| `codeQL.variantAnalysis.defaultResultsSort` | `numberOfResults` | results view sort |
| `codeQL.cli.executablePath` | "" | use an existing CodeQL CLI instead of the managed one |
| `codeQL.githubDatabase.download` | `ask` | offer to download a repository's database when a workspace opens |

The repository lists live in `databases.json` under the workspace storage of
the extension (open with the `{ }` button of the "Variant Analysis
Repositories" view); the schema is in `references/vscode-workflow.md`.

## Driving the API by hand

```bash
codeql pack install my-pack
codeql pack bundle -o my-pack.tgz my-pack
PACK=$(base64 -w0 my-pack.tgz)                 # macOS: base64 < my-pack.tgz | tr -d '\n'
printf '{"language":"python","query_pack":"%s","repositories":["psf/requests","pallets/flask"]}' "$PACK" \
  | gh api -X POST repos/OWNER/CONTROLLER/code-scanning/codeql/variant-analyses --input - > run.json
ID=$(jq .id run.json)
gh api repos/OWNER/CONTROLLER/code-scanning/codeql/variant-analyses/$ID --jq '.status'
gh api repos/OWNER/CONTROLLER/code-scanning/codeql/variant-analyses/$ID/repos/psf/requests \
  --jq '{analysis_status, result_count, artifact_url, database_commit_sha}'
curl -L -o requests.zip "<artifact_url>"       # results.sarif + results.bqrs inside
```

This is exactly what `gh mrva submit` / `status` / `download` do, plus
chunking of lists above the limit and a local sessions file.

## Sources

- https://github.blog/security/vulnerability-research/multi-repository-variant-analysis-a-powerful-new-way-to-perform-security-research-across-github/
- https://docs.github.com/en/code-security/concepts/code-scanning/multi-repository-variant-analysis
- https://docs.github.com/en/code-security/how-tos/scan-code-for-vulnerabilities/scan-from-vs-code/running-codeql-queries-at-scale-with-multi-repository-variant-analysis
- https://docs.github.com/en/rest/code-scanning/code-scanning (CodeQL databases and variant analyses endpoints)
- https://github.com/github/codeql-variant-analysis-action (the action the dynamic workflow runs)
- https://github.blog/changelog/2023-06-23-use-github-code-search-to-support-security-research-with-multi-repostiory-variant-analysis-for-codeql-beta/
