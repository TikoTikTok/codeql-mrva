# codeql-mrva

[![tests](https://github.com/offensiveadhd/codeql-mrva/actions/workflows/test.yml/badge.svg)](https://github.com/offensiveadhd/codeql-mrva/actions/workflows/test.yml)

An agent skill for GitHub's **multi-repository variant analysis** (MRVA): run
one CodeQL query across up to 1,000 repositories at once, then turn the
results into confirmed, responsibly reported vulnerabilities. One skill folder,
installable into **Claude Code**, **Hermes Agent** and **OpenAI Codex** (and any
other agent that reads the [Agent Skills](https://agentskills.io) format).

MRVA was introduced by GitHub in March 2023
([blog post](https://github.blog/security/vulnerability-research/multi-repository-variant-analysis-a-powerful-new-way-to-perform-security-research-across-github/)).
You give it a CodeQL query pack and a list of repositories; a dynamic GitHub
Actions workflow in a *controller repository* you own runs the query against
the CodeQL database GitHub already stores for each repository's default
branch and streams SARIF back as each job finishes. GitHub's Security Lab
used it to find Android advisories affecting more than 10 million installs.

## What the skill teaches the agent

The loop that makes MRVA useful instead of noisy:

1. Frame the variant (sink, source, barrier) from the CVE or fix commit.
2. Write the query as a pack, sink-only first, then taint tracking
   (`TaintTracking::Global<Config>` skeletons for Python, Java, JavaScript,
   Go, Ruby, C/C++).
3. Test on one or three downloaded databases before scaling.
4. Build the target list: top-N by stars, an org, a code search, dependents;
   filter it to repositories that actually have a CodeQL database.
5. Set up the controller repository once (public vs private rules, Actions
   billing, self-hosted runners).
6. Submit from VS Code, from `gh mrva`, or straight against the REST API.
7. Triage SARIF like a reviewer: per-rule sanity, prioritisation,
   confirmation at the analysed commit, FP categories that feed back into the
   query, re-run on the hits only.
8. Report: private vulnerability reporting, one report per repository, 90-day
   timeline, upstream-first for shared root causes.

Plus the limits that bite (1,000 repositories per run, one language, default
branch only, stored-database availability, controller visibility) and the
fully local alternative (download databases via the API, Trail of Bits `mrva`).

## Layout

```
skills/codeql-mrva/
├── SKILL.md                         the skill: workflow, limits, file map
├── references/
│   ├── how-mrva-works.md            architecture, REST API, billing, setting IDs
│   ├── vscode-workflow.md           click-by-click, databases.json schema, code-search lists
│   ├── gh-mrva-cli.md               install, config, every flag, output file naming
│   ├── local-variant-analysis.md    databases via API, Trail of Bits mrva, codeql CLI loop
│   ├── query-authoring.md           pack layout, metadata, per-language skeletons, precision tuning
│   ├── target-lists.md              building and filtering repository lists
│   ├── triage-and-disclosure.md     SARIF field map, triage order, tracker, disclosure
│   └── install.md                   Claude Code / Hermes / Codex install
├── scripts/
│   ├── mrva_list.py                 build, merge, chunk, convert repository lists (gh-mrva <-> VS Code)
│   ├── mrva_dbs.py                  which repos have a CodeQL database; filter; download + extract
│   ├── mrva_sarif.py                SARIF directory -> triage markdown / JSON / hits list
│   └── mrva_controller.sh           create the controller repository with its first commit
└── assets/
    ├── qlpack-template/             qlpack.yml + sink-only and path-problem example queries
    ├── databases.example.json       gh-mrva list format
    └── gh-mrva.config.example.yml   gh-mrva configuration
```

Scripts are Python 3.9+ with the standard library only; every remote call
goes through the GitHub CLI (`gh`), so your existing `gh auth login` is the
only credential.

## Install

### Claude Code

```bash
claude plugin marketplace add offensiveadhd/codeql-mrva
claude plugin install codeql-mrva@codeql-mrva
```

or copy the skill folder into `~/.claude/skills/` (personal) or
`.claude/skills/` (project), or `npx skills add offensiveadhd/codeql-mrva -a claude-code -g`.
Invoke with `/codeql-mrva` or let it trigger on MRVA / variant-analysis requests.

### Hermes Agent

```bash
hermes skills install offensiveadhd/codeql-mrva/skills/codeql-mrva --category security
```

or copy the folder into `~/.hermes/skills/security/` (Windows:
`%LOCALAPPDATA%\hermes\skills\security\`), or project-local under
`./.hermes/skills/` followed by `hermes skills trust`. Hermes profiles have
their own skills tree: `hermes -p NAME skills install ...`, or the installers
below with `--hermes-profile default,NAME` / `-HermesProfile default,NAME`.
Invoke with `/codeql-mrva <request>`.

### Codex

```bash
npx skills add offensiveadhd/codeql-mrva -a codex -g
```

or copy the folder into `~/.agents/skills/` (user) or `.agents/skills/`
(project). The repository also carries `.codex-plugin/plugin.json` and
`.agents/plugins/marketplace.json`, so `codex plugin marketplace add
offensiveadhd/codeql-mrva` registers it as a plugin marketplace. Invoke with
`$codeql-mrva`.

### All three at once

```bash
git clone https://github.com/offensiveadhd/codeql-mrva
bash codeql-mrva/install.sh                 # detects Claude Code, Hermes, Codex; copies into each
bash codeql-mrva/install.sh --link          # symlinks instead, so git pull updates all of them
pwsh codeql-mrva/install.ps1                # native Windows (or: powershell -ExecutionPolicy Bypass -File install.ps1)
npx skills add offensiveadhd/codeql-mrva --agent '*' -g -y   # or the skills CLI
```

Full details and the tool prerequisites (GitHub CLI, CodeQL CLI, gh-mrva, VS
Code extension) are in [skills/codeql-mrva/references/install.md](skills/codeql-mrva/references/install.md).

## Quick start

```bash
S=skills/codeql-mrva/scripts
bash   $S/mrva_controller.sh OWNER/mrva-controller                       # once
python $S/mrva_list.py search 'pickle.loads language:python' --name py-pickle --out databases.json
python $S/mrva_dbs.py --file databases.json --list py-pickle --language python \
       --write-list py-pickle-db --out databases.json                     # keep repos that have a DB
python $S/mrva_dbs.py psf/requests --language python --download dbs/ --extract   # one DB to test on
codeql database analyze dbs/psf__requests__python skills/codeql-mrva/assets/qlpack-template/UnsafePickleVariant.ql \
       --format=sarif-latest --output=one.sarif --download
gh extension install GitHubSecurityLab/gh-mrva
gh mrva submit  --language python --session pickle-1 --list py-pickle-db \
       --query skills/codeql-mrva/assets/qlpack-template/UnsafePickleVariant.ql
gh mrva status  --session pickle-1
gh mrva download --session pickle-1 --output-dir results/pickle-1
python $S/mrva_sarif.py results/pickle-1 --repo-list hits --out databases.json > triage.md
```

## Tests

```bash
python -m pip install pytest
python -m pytest -q
```

The tests cover list parsing and conversion, SARIF summarisation and the
scripts' command lines; nothing in them touches the network.

## Sources

- GitHub blog, 9 March 2023: [Multi-repository variant analysis: a powerful new way to perform security research across GitHub](https://github.blog/security/vulnerability-research/multi-repository-variant-analysis-a-powerful-new-way-to-perform-security-research-across-github/)
- GitHub Docs: [About multi-repository variant analysis](https://docs.github.com/en/code-security/concepts/code-scanning/multi-repository-variant-analysis), [Running CodeQL queries at scale with MRVA](https://docs.github.com/en/code-security/how-tos/scan-code-for-vulnerabilities/scan-from-vs-code/running-codeql-queries-at-scale-with-multi-repository-variant-analysis), [Code scanning REST API](https://docs.github.com/en/rest/code-scanning/code-scanning)
- GitHub changelog, June 2023: [Use GitHub code search to support security research with MRVA](https://github.blog/changelog/2023-06-23-use-github-code-search-to-support-security-research-with-multi-repostiory-variant-analysis-for-codeql-beta/)
- [GitHubSecurityLab/gh-mrva](https://github.com/GitHubSecurityLab/gh-mrva), [github/codeql-variant-analysis-action](https://github.com/github/codeql-variant-analysis-action), [github/vscode-codeql](https://github.com/github/vscode-codeql)
- [GitHubSecurityLab/CodeQL-Community-Packs](https://github.com/GitHubSecurityLab/CodeQL-Community-Packs), [trailofbits/mrva](https://github.com/trailofbits/mrva)
- Skill formats: [Claude Code skills](https://code.claude.com/docs/en/skills), [Hermes Agent skills](https://hermes-agent.nousresearch.com/docs/user-guide/features/skills), [Codex skills](https://developers.openai.com/codex/skills), [agentskills.io](https://agentskills.io)

## License

MIT. The example queries are illustrations; verify them against your CodeQL
CLI version before relying on them.
