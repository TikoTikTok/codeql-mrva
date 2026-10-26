---
name: codeql-mrva
description: Run one CodeQL query across up to 1,000 GitHub repositories at once with Multi-Repository Variant Analysis (MRVA), from VS Code, the gh-mrva CLI, the REST API, or locally against downloaded CodeQL databases. Covers the whole variant-analysis loop - turn a known bug or CVE into a precise query, test it on one database, build a target repository list (top-N, org, code search), set up the controller repository, submit, poll, download SARIF, triage and report responsibly. Use it whenever the user mentions MRVA, variant analysis, "run this CodeQL query across many repos", "find other projects with this bug pattern", a controller repository, gh mrva, CodeQL databases from GitHub, or wants to hunt one vulnerability class across an org or ecosystem, even without the word MRVA.
license: MIT
metadata:
  version: 1.0.0
  author: TikoTikTok
  hermes:
    tags: [codeql, mrva, variant-analysis, security-research, sast, github]
    category: security
---

# CodeQL multi-repository variant analysis (MRVA)

Variant analysis is the step after a bug is found: model it as a CodeQL query,
then run that query everywhere the same pattern can live. MRVA is GitHub's
hosted way to do the "everywhere" part. You hand it a query pack and a list of
up to 1,000 repositories; a dynamic GitHub Actions workflow in a *controller
repository* you own fans out one job per repository, runs the query against
the CodeQL database GitHub already stores for that repository's default
branch, and streams SARIF results back as each job finishes. Nothing is cloned
or built on your machine, and GitHub stores databases for thousands of public
repositories (every one that runs code scanning with CodeQL, plus many that do
not), so a first run usually works the first time.

Treat it as a precision instrument, not a search engine. A query that is 90 %
noise on one repository is 900 findings of noise on 1,000. The loop below
spends most of its effort before the big run, on making the query tight.

## Choose how to run it

| Situation | Use | Read |
|---|---|---|
| Iterating on a query, want to click through results | VS Code CodeQL extension, "Run Variant Analysis" | `references/vscode-workflow.md` |
| Scripted or repeated runs, want SARIF on disk | `gh mrva` CLI extension (unofficial, GitHub Security Lab) | `references/gh-mrva-cli.md` |
| Own automation, CI, no VS Code | REST API `POST /repos/{controller}/code-scanning/codeql/variant-analyses` | `references/how-mrva-works.md` |
| Under ~50 repositories, offline, custom CLI flags, no controller repo | Download databases and run `codeql` locally (`mrva` by Trail of Bits, or by hand) | `references/local-variant-analysis.md` |

Hosted MRVA needs: a GitHub token with the `repo` scope (what `gh auth login`
and the VS Code sign-in give you), a controller repository with at least one
commit, and a query inside a CodeQL pack. The CodeQL CLI is needed by
`gh mrva` (it bundles the pack) and for local testing; the VS Code extension
manages its own copy.

Languages the hosted service accepts: `actions`, `cpp`, `csharp`, `go`,
`java` (incl. Kotlin), `javascript` (incl. TypeScript), `python`, `ruby`,
`rust`, `swift`. One language per run.

## The loop

### 1. Frame the variant before writing QL

Start from the thing that is known to be wrong: a CVE, an advisory, a fix
commit, a bug you just found. Write down one line each:

- **sink**: the dangerous operation (`pickle.loads`, `exec`, a shell call, a
  path join that reaches `open`, a deserializer, an `innerHTML` assignment)
- **source**: where attacker data enters (HTTP params, headers, file contents,
  env, argv; a library's public API when the library is the target)
- **sanitizer / barrier**: what the fix added (an allowlist, an escape, a type
  check); its absence is what makes a variant a variant
- **what counts as a true positive** in a repository you have never seen, so
  that triage later is a checklist, not a judgment call

The fix diff is the best spec: the lines it removed are the sink pattern, the
guard it added is the barrier.

### 2. Write the query as a pack, sink-first

Copy `assets/qlpack-template/` (a `qlpack.yml` plus a Python `path-problem`
example) and rename the pack. Two rules that save hours:

- Write a **sink-only `@kind problem` query first**. It is cheap, tells you
  how common the API is across the ecosystem, and checks that your model of
  the sink matches real code. Promote it to `@kind path-problem` with
  `TaintTracking::Global<Config>` only once the sink predicate is right.
- Keep the **required metadata**: `@name`, `@id` (unique, e.g.
  `py/variant/<slug>`), `@kind` (`problem` or `path-problem`),
  `@problem.severity`. A pack whose query does not compile fails the whole
  run, and `path-problem` is what gives the "Show paths" view in results.

`references/query-authoring.md` has the modern dataflow skeleton for each
language, the metadata table, pack dependency resolution
(`codeql pack install`) and the precision-tuning checklist. Mine the Security
Lab's community packs (`githubsecuritylab/codeql-<lang>-queries`) for sinks
and sources before modelling your own.

### 3. Test on one database before 1,000

```bash
python scripts/mrva_dbs.py owner/repo --language python --download dbs/ --extract
codeql database analyze dbs/owner__repo__python my-pack/UnsafePickleVariant.ql \
  --format=sarif-latest --output=one.sarif --download
python scripts/mrva_sarif.py one.sarif
```

Pick two or three repositories: the one where the bug is known to exist, and
a popular one that should be clean. Zero results on the known-bad one means
the model is wrong; a flood on the clean one means the sink is too wide or a
barrier is missing. Only then scale.

### 4. Build the target list

```bash
python scripts/mrva_list.py top    --language python --limit 500 --name py-top500 --out databases.json
python scripts/mrva_list.py org    rails --name rails-org --out databases.json
python scripts/mrva_list.py search 'pickle.loads language:python' --name py-pickle --out databases.json
python scripts/mrva_dbs.py --file databases.json --list py-pickle --language python \
  --write-list py-pickle-db --out databases.json
```

The script writes the `gh-mrva` list format by default and the VS Code
`databases.json` schema with `--format vscode`, dedupes, warns above 1,000
(`--truncate` cuts instead), `merge` unions several lists, and `--chunk`
splits bigger lists into `name-1`, `name-2`, ... A code-search list is the
highest-signal start: it only contains repositories that mention your sink
(one search returns at most 1,000 files, so under 1,000 repositories; pass
the language with `--language`, not in the query text). Then filter it
through `mrva_dbs.py` so the run is not full of "no database" skips; its
`--sort stars --top 1000` is how an oversized list becomes the 1,000 that
matter. `references/target-lists.md` covers the search qualifiers, the
built-in Top 10/100/1000 lists, org-wide lists, dependents and the
1,000-per-search cap.

### 5. Set up the controller repository once

```bash
bash scripts/mrva_controller.sh OWNER/mrva-controller            # public: public targets only, free
bash scripts/mrva_controller.sh OWNER/mrva-controller --private  # required for private/internal targets
```

It creates the repository with one commit (an empty repository fails with
"does not have a branch") and can set the `MRVA_RUNNER_OS` Actions variable
for self-hosted runners. A public controller may only analyse public
repositories and costs nothing; a private controller may analyse private and
internal ones, and Actions minutes beyond the free tier are billed to its
owner. Point VS Code at it with `codeQL.variantAnalysis.controllerRepo`, or
`gh mrva` with `controller:` in `~/.config/gh-mrva/config.yml` (see
`assets/gh-mrva.config.example.yml`).

### 6. Submit

VS Code: select the list in "Variant Analysis Repositories", right-click in
the `.ql` file, **CodeQL: Run Variant Analysis**.

CLI:

```bash
gh mrva submit --language python --session pickle-2026-10 \
  --list py-pickle-db --query my-pack/UnsafePickleVariant.ql
gh mrva status   --session pickle-2026-10
gh mrva download --session pickle-2026-10 --output-dir results/pickle-2026-10
```

Name sessions after the query and date; `gh mrva list` is the only index of
them. Lists above the per-run limit are split into several runs under one
session automatically. When a repository fails, its workflow log is under the
controller repository's Actions tab.

### 7. Triage like a reviewer, not a scanner

```bash
python scripts/mrva_sarif.py results/pickle-2026-10 --repo-list hits --out databases.json > triage.md
```

That gives per-repository and per-rule counts, every finding as
`repo path:line message` (`--paths` adds each step of the code flow), and a
`hits` list of only the repositories with results (what VS Code's **Copy
repository list** does) so the refined query can be re-run on exactly those.
Then, per finding:

1. Open the file at the commit the database was built from
   (`database_commit_sha` in the run, or the link VS Code gives), not `HEAD`;
   the code may have moved.
2. Walk the path for `path-problem` results; the first and last steps usually
   decide it.
3. Check what the path cannot show: the runtime and the types at the sink
   (Python 3's `pickle.loads` rejects the `str` a JSON field gives, Python 2
   accepts it), the bind address, authentication, whether validation runs
   before the dangerous call or after it.
4. Grep the hit repository for the sink itself. Sources the query does not
   model (HTTP client responses, sockets, queues) hide siblings of the same
   bug; `assets/qlpack-template/UnsafePickleWideSources.ql` shows how to
   widen sources.
5. Classify: true positive / unreachable source / sanitised elsewhere / test
   or example code / dead code. Everything but the first is query feedback.
6. Fold the feedback into the query (a barrier, a narrower source, a path
   filter such as `not f.getRelativePath().matches("%test%")`) and re-run on
   `hits`.

`references/triage-and-disclosure.md` has the SARIF field map, the
prioritisation heuristics (stars, last push, whether the sink is reachable
from the package's public surface) and a findings-tracker template.

### 8. Report responsibly

The analysis is read-only, but the output is a list of probably real
vulnerabilities in other people's code. Default to GitHub private
vulnerability reporting on each repository (or the contact in `SECURITY.md`),
one report per repository, with the query attached so maintainers can re-run
it, and a disclosure date (90 days is the Security Lab norm). Do not publish
the raw SARIF or a list of affected repositories before fixes ship, and keep
anything from private repositories inside the organisation that owns them.
For bug-bounty work, check program scope before the run, not after.

Write findings and report drafts outside anything public (a folder listed in
`.git/info/exclude` works inside a research repository). Opening an issue,
filing an advisory or emailing a maintainer speaks for the user: draft it,
show it, and send only on their explicit go-ahead.

## Limits and gotchas

- **1,000 repositories per run**, one language per run, and only
  repositories that already have a CodeQL database; the rest are reported as
  skipped, not analysed. `mrva_dbs.py` tells you in advance. Expect 10 to 20 %
  of code-search hits to have one.
- **The slowest database sets the wall time.** One large database can hold
  up the whole run long after every other job is done. Use
  `mrva_dbs.py --max-db-mb`.
- The controller repository must have at least one commit so its default
  branch exists. Public controller for public targets; private controller for
  anything private or internal. Keep it free of code: its Actions logs are
  where failures show up, and anyone with write access can read them.
- Built-in Top 10/100/1000 lists exist on GitHub.com only (not `*.ghe.com`).
  Code-search lists add at most 1,000 repositories per search.
- The pack is compiled by the action with its own CodeQL version; a query
  that compiles only on a bleeding-edge local CLI fails remotely. Depend on
  `codeql/<lang>-all` with a range, not an exact old version.
- **Renamed repositories are skipped as "not found"**: MRVA does not follow
  GitHub redirects (`tiangolo/fastapi` is skipped, `fastapi/fastapi` runs).
  `mrva_dbs.py` resolves every name to its current `full_name` first.
- `gh mrva` is an unofficial Security Lab tool; its latest release is v1.0.8
  (October 2023) and it shells out to a `codeql` binary on `PATH` (on
  Windows, add gh-codeql's `dist/release/v<version>` folder to `PATH`).
  `gh mrva status` panics while a run is still pending; wait with
  `gh run watch <id> --repo <controller>` and retry. If it breaks for good,
  the REST calls in `references/how-mrva-works.md` are what it does.
- Stored databases cover the **default branch** only and are refreshed when
  code scanning runs; some are months old. Verify against `HEAD` before
  reporting.
- Results are Actions artifacts of the controller run and follow that
  repository's artifact retention; download what you need to keep.

## Files in this skill

| Read when | File |
|---|---|
| Architecture, REST API, billing, database availability, VS Code setting IDs | `references/how-mrva-works.md` |
| Click-by-click VS Code workflow, `databases.json` schema, code-search lists, export | `references/vscode-workflow.md` |
| `gh mrva` install, config, every flag, output file naming | `references/gh-mrva-cli.md` |
| The same analysis locally: downloading databases, Trail of Bits `mrva`, `codeql database analyze` | `references/local-variant-analysis.md` |
| Writing and tuning the query, pack layout, metadata, per-language dataflow skeletons | `references/query-authoring.md` |
| Building and filtering repository lists | `references/target-lists.md` |
| SARIF triage, prioritisation, disclosure | `references/triage-and-disclosure.md` |
| Installing this skill into Claude Code, Hermes Agent, Codex | `references/install.md` |

Scripts (Python 3.9+, standard library only; `gh` does anything remote):

| Script | Does |
|---|---|
| `scripts/mrva_dbs.py` | Which repositories have a CodeQL database (per language, size, age); filter a list to them; download and extract databases |
| `scripts/mrva_list.py` | Build lists from top-N by stars, an owner, a code search, or files; merge, dedupe, cap, chunk, convert between gh-mrva and VS Code formats |
| `scripts/mrva_sarif.py` | Summarise SARIF files (from `gh mrva download` or local runs) into a triage table and a hits list |
| `scripts/mrva_controller.sh` | Create a controller repository with its first commit; optional self-hosted runner labels |

Assets: `assets/qlpack-template/qlpack.yml`, `assets/qlpack-template/PickleLoadsSinks.ql`
(sink-only), `assets/qlpack-template/UnsafePickleVariant.ql` (incoming requests),
`assets/qlpack-template/UnsafePickleWideSources.ql` (plus outgoing HTTP responses),
`assets/qlpack-template/README.md`, `assets/databases.example.json`,
`assets/gh-mrva.config.example.yml`.
