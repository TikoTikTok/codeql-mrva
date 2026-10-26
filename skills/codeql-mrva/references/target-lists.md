# Building the target repository list

The list decides what the run can find. The ceiling is 1,000 repositories
per run and only repositories with a stored CodeQL database count, so the
work is: start wide, filter to "has a database", order by what matters, cut
at 1,000, chunk the rest.

## Sources of candidates

| Source | How | Notes |
|---|---|---|
| Built-in Top 10 / 100 / 1000 | VS Code list, or API `repository_lists: ["top_1000"]` | per language, curated with the code search team, GitHub.com only |
| Most-starred in a language | `python scripts/mrva_list.py top --language python --limit 1000` | `gh search repos --sort stars`, cap 1,000 |
| Everything an org/user owns | `python scripts/mrva_list.py org rails` or API `repository_owners` | archived and forks excluded by default; `--include-archived`, `--include-forks` |
| Code that mentions the sink | `python scripts/mrva_list.py search 'pickle.loads language:python'` or VS Code "Add repositories with GitHub code search" | highest signal; 1,000 results per search, default branches only, files under 384 KB, 10 searches/min |
| Dependents of a package | GitHub "Used by" (manual), deps.dev `:dependents` endpoint, libraries.io, or a code search for the import line (`"from yaml import load" language:python`) | the realistic way to find "everyone who uses library X" |
| Advisory / SBOM data | `gh api /advisories?ecosystem=pip&affects=<pkg>`; OSV | for "who still ships the vulnerable version" |
| A previous run's hits | `python scripts/mrva_sarif.py results/ --repo-list hits --out databases.json` | iterate the query on exactly the repositories that matter |

## Code search qualifiers that pay off

The VS Code integration uses the legacy code search syntax; `gh search code`
is the same engine. Useful qualifiers:

```
language:python  path:*.py  filename:settings.py  extension:yml
org:rails  user:someone  repo:owner/name
in:file     size:<10000
```

Examples:

```
pickle.loads language:python -path:test -path:tests
"yaml.load(" language:python NOT "Loader="
Runtime.getRuntime().exec language:java
"child_process" "req.query" language:javascript
```

Code search ranks by relevance and returns at most 1,000 *files*, so one
search yields fewer than 1,000 repositories, and two narrower searches (split by
`path:` or `org:`) beat one broad one when you need more candidates. Keep the
lists separate, then union them with the best-ranked list first:

```bash
python scripts/mrva_list.py search 'pickle.loads' --language python --name s1 --out lists.json
python scripts/mrva_list.py search 'pickle.loads path:src' --language python --name s2 --out lists.json
python scripts/mrva_list.py merge lists.json --lists s2,s1 --name py-pickle --max 1000 --truncate --out lists.json
```

`gh search code` quirk: when the free-text query holds two `key:value`
qualifiers (`language:python path:src`) it re-quotes them into one broken
value and the API answers `422 unable to parse query`. Pass the language with
`--language` and keep one qualifier in the text.

## Filter to repositories with a database

```bash
python scripts/mrva_dbs.py --file databases.json --list py-pickle --language python \
   --write-list py-pickle-db --out databases.json
```

Prints a table (language, stars, size, days since the database was built)
and writes the filtered list under each repository's current name (renames
resolved). `--min-stars N`, `--sort stars|pushed` and `--top N` apply the
ordering rules below in the same pass, so a 1,500-candidate list becomes
"the 1,000 most-starred that have a database" in one command. Roughly:
popular repositories almost always have one, small ones only if they run
code scanning. Anything without a database needs `codeql database create`
locally (see `references/local-variant-analysis.md`) or the maintainer
enabling code scanning.

Code search favours small projects: expect 10 to 20 % of its hits to have
a database, or start from the Top-1000 list (every entry has a database)
when breadth of popular code matters more than the sink being present. The filter costs two API
calls per repository against the 5,000/hour core quota; check with
`gh api rate_limit` before filtering more than ~2,000 candidates.

## Ordering and cutting

When a candidate set is bigger than 1,000, keep what helps the goal:

- **Impact**: stars, dependents, downloads; `gh search repos` already sorts by stars.
- **Freshness**: `pushed:>2025-01-01` drops abandoned forks; an old database
  on a dead project is a finding nobody will fix.
- **Relevance**: a code-search list over a stars list; the sink has to be there.
- **Diversity**: one repository per org when a monorepo would otherwise
  dominate the results.
- **Run time**: the slowest database sets the wall time of the whole run.
  `mrva_dbs.py --max-db-mb 150` drops the giants; run them separately if they
  matter.

Then `--chunk 1000` and submit each chunk as its own session.

## File formats

gh-mrva (`scripts/mrva_list.py` default):

```json
{ "py-pickle": ["psf/requests", "pallets/flask"] }
```

VS Code (`--format vscode`), full schema in `references/vscode-workflow.md`:

```json
{ "version": 1, "databases": { "variantAnalysis": {
    "repositoryLists": [ { "name": "py-pickle", "repositories": ["psf/requests"] } ],
    "owners": [], "repositories": [] } },
  "selected": { "kind": "variantAnalysisUserDefinedList", "listName": "py-pickle" } }
```

Both are detected automatically when read. Keep the list files in the
research repository next to the query: they are the reproducibility record,
and a maintainer asking "how did you find us" deserves an answer.
