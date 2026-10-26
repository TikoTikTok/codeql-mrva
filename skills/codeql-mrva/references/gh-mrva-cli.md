# gh-mrva: MRVA from the command line

`gh-mrva` is a GitHub CLI extension by GitHub Security Lab
(https://github.com/GitHubSecurityLab/gh-mrva). It is unofficial and not
supported by GitHub; the latest release is v1.0.8 (October 2023) with prebuilt
binaries for Linux, macOS, Windows (amd64/arm64) and more. It does the same
REST calls as the VS Code extension and writes SARIF to disk, which makes it
the right tool for scripted, repeated or large runs.

## Install

```bash
gh extension install GitHubSecurityLab/gh-mrva
gh mrva --help
```

It shells out to a `codeql` binary on `PATH` for `codeql pack install` and
`codeql pack bundle`. The quickest way to get one:

```bash
gh extension install github/gh-codeql     # downloads the CLI on first use
gh codeql install-stub                    # puts a `codeql` shim on PATH (prints the directory to add)
codeql version
```

or download a release of `github/codeql-cli-binaries` and add it to `PATH`.

**Windows.** `gh codeql install-stub` needs a directory argument and writes a
POSIX shell stub, which a Go program such as `gh-mrva` cannot execute. Put the
CLI that `gh-codeql` downloaded on `PATH` instead; it lives at
`%LOCALAPPDATA%\GitHub CLI\extensions\gh-codeql\dist\release\v<version>\codeql.exe`:

```powershell
$env:PATH = "$env:LOCALAPPDATA\GitHub CLI\extensions\gh-codeql\dist\release\v2.27.3;$env:PATH"
```

```bash
export PATH="$(cygpath -u "$LOCALAPPDATA/GitHub CLI/extensions/gh-codeql/dist/release/v2.27.3"):$PATH"   # Git Bash
```

## Configure

`~/.config/gh-mrva/config.yml` (Windows too: `C:\Users\<you>\.config\gh-mrva\config.yml`,
the directory is created on first run together with `sessions.yml`, but
`config.yml` itself is not; write it yourself):

```yaml
controller: OWNER/mrva-controller          # NWO of the controller repository
list_file: /abs/path/to/databases.json      # repository lists, see below
# codeql_path: /abs/path/to/github-codeql-checkout   # optional: checkout of github/codeql at the tag
#                                                    # matching your CLI (codeql-cli/vX.Y.Z); only needed
#                                                    # when your pack does not declare its dependencies
```

Every option can be overridden per command with `--controller`, `--list-file`
and `--codeql-path`. A template is at `assets/gh-mrva.config.example.yml`.

## Repository list file

A flat JSON object: list name to array of `owner/repo`:

```json
{
  "py-pickle": ["psf/requests", "pallets/flask"],
  "rails-org": ["rails/rails", "rails/thor"]
}
```

This is not the VS Code `databases.json` schema; `scripts/mrva_list.py`
converts between the two (`--format gh-mrva` is the default).

## Commands

### submit

```bash
gh mrva submit --language <lang> --session <name> --list <list-name> \
   [--list-file <file>] [--controller <owner/repo>] [--codeql-path <dir>] \
   (--query <file.ql> | --query-suite <file.qls>) \
   [--additional-packs <dir>] [--action-branch main]
```

| Flag | Short | Meaning |
|---|---|---|
| `--session` | `-s` | required; name you will use for `status`/`download`; sessions are kept in a YAML file next to the config |
| `--language` | `-l` | required; `actions cpp csharp go java javascript python ruby rust swift` |
| `--query` | `-q` | path to one `.ql` file (inside a pack with `qlpack.yml`) |
| `--query-suite` | `-x` | path to a `.qls` suite instead of one query |
| `--list` | `-i` | name of the list inside the list file |
| `--list-file` | `-f` | overrides `list_file` |
| `--controller` | `-c` | overrides `controller` |
| `--codeql-path` | `-p` | overrides `codeql_path` |
| `--additional-packs` | `-a` | extra pack search path passed to `codeql pack` |
| `--action-branch` | `-b` | branch of `github/codeql-variant-analysis-action` (default `main`) |

The pack is copied to a temporary directory with every other query stripped
out ("QLPack exists, stripping all other queries"), `codeql pack install` and
`codeql pack bundle` run on the copy, the tarball is Base64-encoded, and the
list is split into chunks of 1,000; each chunk is one variant-analysis run,
all recorded under the session name in `sessions.yml` (run id, query, query
id, controller, list file, list, language, repository count). One query per
submission; a multi-query pack is fine as the source.

### status

```bash
gh mrva status --session <name> [--json]
```

Per run and per repository: `analysis_status`, result counts, failures, and
the skipped buckets (access mismatch, not found, no database, over limit).
Re-run until every repository is `succeeded`, `failed`, `canceled` or
`timed_out`.

Known crash in v1.0.8: while the run is still `pending` (the controller
workflow has not started its jobs yet) `status` panics with
`interface conversion: interface {} is nil, not []interface {}` at
`cmd/status.go:95`. Nothing is wrong with the run. Wait for the workflow and
retry; the cleanest wait is on the controller's Actions run:

```bash
gh run list --repo OWNER/CONTROLLER --limit 1            # the "CodeQL query - <lang> query on N repositories" run
gh run watch <run-id> --repo OWNER/CONTROLLER --exit-status
```

or read the REST summary directly
(`gh api repos/OWNER/CONTROLLER/code-scanning/codeql/variant-analyses/<id>`,
the id is in `sessions.yml`), which also carries `actions_workflow_run_id`.

### download

```bash
gh mrva download --session <name> --output-dir <dir> [--download-dbs] [--nwo owner/repo]
```

Writes, per repository with an artifact:

```
<owner>_<repo>_<runId>.sarif
<owner>_<repo>_<runId>.bqrs
<owner>_<repo>_<runId>_<language>_db.zip     # only with --download-dbs
```

(`/` in the NWO becomes `_`; GitHub owner names cannot contain `_`, so the
first underscore always separates owner from repo.) `--nwo` restricts the
download to one repository. Existing files are skipped, so the command is
safe to re-run while jobs finish. Repositories that finished with zero
results have no artifact and therefore no file; `status` is the record for
them. `scripts/mrva_sarif.py <dir>` summarises the directory.

The slowest repository sets the wall time: one large database can run for
hours while every other job is done in minutes. Cap database size when
building the list (`mrva_dbs.py --max-db-mb 150`), or drop the slowest
repositories of a first, cheap run before the expensive one. Results stream per repository, so
`gh mrva download` can be re-run while stragglers finish and triage can start
early.

### list

```bash
gh mrva list [--json]
```

Every session with its runs and controller.

## A complete session

```bash
gh mrva submit -l python -s pickle-2026-10 -i py-pickle-db -q ./my-pack/UnsafePickleVariant.ql
gh mrva status -s pickle-2026-10
gh mrva download -s pickle-2026-10 --output-dir results/pickle-2026-10
python scripts/mrva_sarif.py results/pickle-2026-10 --repo-list pickle-hits --out databases.json > triage.md
# tighten the query, then:
gh mrva submit -l python -s pickle-2026-10-v2 -i pickle-hits -q ./my-pack/UnsafePickleVariant.ql
```

## Known rough edges

- Unofficial; an API change on GitHub's side can break it without a release.
  The REST calls it makes are in `references/how-mrva-works.md`.
- The README calls the config key `codeql_path` and the note `codeql-dist`;
  the flag is `--codeql-path`.
- It needs `codeql` on `PATH` even though the analysis itself runs on GitHub.
- No chunk-size flag; lists above the limit are chunked for you.
- Windows: paths in `config.yml` with backslashes must be quoted; forward
  slashes work.
