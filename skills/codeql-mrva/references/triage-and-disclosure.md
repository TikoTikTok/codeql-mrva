# Triage and disclosure

## SARIF, the parts that matter

One file per repository (`<owner>_<repo>_<runId>.sarif` from `gh mrva
download`, or whatever you named local runs). Inside:

```
runs[0].tool.driver.rules[]          id, shortDescription, properties.security-severity, properties.precision
runs[0].results[]
   .ruleId                           your @id
   .level                            error | warning | note   (from @problem.severity)
   .message.text                     the select message with $@ expanded
   .locations[0].physicalLocation    artifactLocation.uri, region.startLine/startColumn/endLine
   .codeFlows[]                      for path-problem: threadFlows[0].locations[] = source ... sink
   .partialFingerprints              stable id for dedupe across runs
runs[0].versionControlProvenance[]   repositoryUri, revisionId   (when present)
```

`scripts/mrva_sarif.py` flattens all of that:

```bash
python scripts/mrva_sarif.py results/pickle-2026-10            # markdown: per repo, per rule, findings
python scripts/mrva_sarif.py results/ --json > findings.json    # one object per finding
python scripts/mrva_sarif.py results/ --exclude-paths '(^|/)(tests?|spec|examples?|docs?)/' --min-results 1
python scripts/mrva_sarif.py results/ --repo-list hits --out databases.json   # repos with results, as a list
```

## Order of work

1. **Rule-level sanity**: the per-rule table. One rule with 5,000 results is
   a sink definition that matched a framework; fix the query before reading
   findings.
2. **Repository-level prioritisation**: results per repository against
   stars / last push / whether it is a library (its sinks are reachable from
   every dependent) or an application (only its own inputs).
3. **Finding-level confirmation**, one by one for the top candidates:
   - open the file at `database_commit_sha` (per-repository API response or
     the VS Code link); confirm the code is still there on `HEAD`;
   - for path results, read the first step (is the source really external?)
     and the last (is the sink really the dangerous overload?); only then
     the middle;
   - decide reachability: is there a route from an unauthenticated or
     low-privilege caller to the source? That is what separates a CVSS 9 from
     a note.
4. **Classify** every reviewed finding with one of: `TP`, `TP-low` (real but
   needs privileges / unusual config), `FP-source` (not attacker-controlled),
   `FP-sanitised` (guarded elsewhere), `FP-dead` (unreachable or test code),
   `unsure`. The FP categories are query bugs; fix them (see the checklist in
   `references/query-authoring.md`) and re-run on the `hits` list until the
   residue is small enough to read in full.

## What confirming a finding takes

A sink-only query's raw output is mostly test code. A test-path exclusion
(`--exclude-paths '(^\|/)(tests?\|t\|testing\|benchmarks?)/\|(^\|/)test_[^/]*\.py$\|_test\.py$'`)
removes most of it and the residue is usually explainable in minutes: the
sink query measures how common the API is; the taint query with
`RemoteFlowSource` plus a test-path exclusion is what produces candidates
worth a report. What confirming one takes, beyond the path:

1. **Check the runtime and the types at the sink.** A JSON value is `str`.
   Python 3's `pickle.loads` raises `TypeError` on `str`, Python 2 accepts it
   for ASCII protocol-0 pickles (what Python 2's `pickle.dumps` writes). A
   `Dockerfile` (`FROM python:2`) can decide exploitability, not the query.
2. **Check reachability**: bind address (`host='0.0.0.0'`), route
   registration, authentication before the sink, whether "validation" runs
   before or after deserialization (after is too late for pickle).
3. **Grep the hit repository for the sink** (`pickle.load`) before writing
   anything. Sources the query does not model (a peer's HTTP response, not a
   `RemoteFlowSource`) hide siblings of the same bug; the widened query in
   `references/query-authoring.md` confirms them. A report that covers all of
   them is the one the maintainer can actually fix from.

## Before you write anything down

- Keep findings out of any repository that is or may become public. Inside a
  research repository, add the folder to `.git/info/exclude` (local, never
  committed) rather than `.gitignore`, so not even the folder name ships.
- Check the reporting channel first, read-only:

  ```bash
  gh api repos/OWNER/REPO/private-vulnerability-reporting      # {"enabled": true|false}
  gh api repos/OWNER/REPO/community/profile --jq .files.security
  gh issue list --repo OWNER/REPO --state all --search "pickle"   # already known?
  ```

- Write the report as a draft. Sending it, opening the "ask for a private
  channel" issue, or emailing a maintainer is a message on the researcher's
  behalf; an agent should show the draft and wait for an explicit go-ahead.

## Findings tracker

Keep one row per (repository, finding) from the first review onward; it is
the disclosure log later.

| repo | file:line | rule | class | severity | reported | channel | response | fixed | advisory |
|---|---|---|---|---|---|---|---|---|---|
| org/app | api/upload.py:88 | py/variant/unsafe-pickle-request | TP | high | 2026-10-26 | PVR | ack 10-27 | | |

`class` is the triage label above; `channel` is private vulnerability
reporting (PVR), a `SECURITY.md` contact, or an email.

## Disclosure

The run was read-only static analysis; the findings are not. Treat the SARIF
of true positives as sensitive from this point.

- **One report per repository**, through GitHub private vulnerability
  reporting when the repository has it (Security tab, "Report a
  vulnerability"), else the `SECURITY.md` contact, else a maintainer email,
  else a neutral issue asking for a private channel. Never the full detail in
  a public issue.
- **What to include**: the vulnerable lines with the commit SHA, the
  source-to-sink path in words, impact and preconditions, a fix suggestion
  (what the original fix did), the query itself or a link to it so they can
  re-run it, and your timeline. A proof of concept only when it is safe to
  write and helps them reproduce.
- **Timeline**: say it in the first message. 90 days to public disclosure is
  the GitHub Security Lab norm; extend for a good-faith maintainer who is
  shipping a fix.
- **Many repositories, one root cause**: if the sink lives in a shared
  library, report upstream first and let the library's advisory carry the
  variants; coordinate dates so the first public fix does not expose the
  rest. For very wide impact a coordinator (the Security Lab, a CERT, the
  ecosystem's security team) can carry the load.
- **Afterwards**: request a CVE through the repository's GitHub advisory,
  ask for credit, and consider contributing the query to the community packs
  so the class stays covered.
- **Private or internal repositories**: results stay inside the organisation
  that owns the code and go through its own vulnerability process; do not
  move them to a public gist or a personal tracker.
- **Bug bounties**: scope first. Static findings in repositories outside a
  program's scope are reports, not submissions.
