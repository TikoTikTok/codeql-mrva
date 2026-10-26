# Variant analysis without the hosted service

Use the local route when the target set is small (under ~50 repositories), the
network is restricted, you need CLI flags MRVA does not expose (`--threads`,
`--ram`, evaluator options, custom extractor settings), you want BQRS for
custom post-processing, or the repositories have no stored database and you
must build one. It is also the fastest loop for tuning a query before a
hosted run.

## 1. Get databases

### From GitHub (no build needed)

GitHub stores the default-branch database of every repository that runs
CodeQL code scanning plus many popular public ones.

```bash
# which languages exist, how big, how old
python scripts/mrva_dbs.py psf/requests pallets/flask --language python
# download + extract into dbs/<owner>__<repo>__<language>/
python scripts/mrva_dbs.py psf/requests pallets/flask --language python --download dbs/ --extract
```

By hand:

```bash
gh api repos/psf/requests/code-scanning/codeql/databases/python \
   -H "Accept: application/zip" > requests-python.zip
codeql database unbundle requests-python.zip --target dbs/ --name requests-python
```

The zip is the output of `codeql database bundle`; `unbundle` restores it to a
usable directory (the one containing `codeql-database.yml`).

### Trail of Bits `mrva` (terminal MRVA)

https://github.com/trailofbits/mrva (AGPL-3.0) wraps download + analyse +
pretty-print:

```bash
python -m pip install mrva           # or pipx / uv
mkdir dbs
mrva download --token $GITHUB_TOKEN --language ruby dbs/ top --limit 100   # top by stars
mrva download --token $GITHUB_TOKEN --language ruby dbs/ org rails
mrva download --token $GITHUB_TOKEN --language ruby dbs/ repo rails/rails
mrva download --token $GITHUB_TOKEN --language ruby dbs/ from-file repos.txt
mrva analyze dbs/ /path/to/queries -- --rerun --threads=0                   # flags after -- go to codeql
mrva pprint dbs/                                                            # SARIF to terminal
mrva analyze dbs/ ./q --select 'rails/*' --ignore '*/test*'
```

It needs `codeql` on `PATH` and uses `$GITHUB_TOKEN` when `--token` is
absent. Prefer `--threads` over running several `mrva analyze` in parallel.

### Build your own (repository without a database)

```bash
git clone --depth 1 https://github.com/owner/repo
codeql database create dbs/repo-python --language=python --source-root repo
# compiled languages need a working build:
codeql database create dbs/repo-cpp --language=cpp --source-root repo --command "make -j"
```

Interpreted languages (Python, JavaScript/TypeScript, Ruby) and Java with
build mode `none` extract without building. If you then enable CodeQL code
scanning on the repository (yours, or a maintainer's), MRVA can reach it next
time.

## 2. Run the query

```bash
codeql pack install my-pack                      # resolve qlpack.yml dependencies once
for db in dbs/*/; do
  name=$(basename "$db")
  codeql database analyze "$db" my-pack/UnsafePickleVariant.ql \
     --format=sarif-latest --output="results/$name.sarif" --threads=0 --rerun
done
python scripts/mrva_sarif.py results/ > triage.md
```

`--sarif-add-file-contents` embeds snippets for offline review;
`--sarif-category` tags the run. For raw tables instead of SARIF:

```bash
codeql database run-queries dbs/repo-python my-pack/PickleLoadsSinks.ql
codeql bqrs decode --format=csv --output=sinks.csv \
   dbs/repo-python/results/<pack>/PickleLoadsSinks.bqrs
```

Any SARIF produced this way is read by `scripts/mrva_sarif.py` like MRVA
output; the repository name is taken from `versionControlProvenance` when
present, else from the file name.

## 3. Mixed strategy that works well

1. Download 3 databases, iterate on the query locally until precision is good.
2. Run hosted MRVA on the 1,000-repository list.
3. `--download-dbs` only for the repositories with results, and re-run the
   final query locally with `--sarif-add-file-contents` for the write-up.
