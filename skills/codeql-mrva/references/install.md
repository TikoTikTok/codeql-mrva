# Installing this skill

The skill folder is `skills/codeql-mrva/` in
https://github.com/offensiveadhd/codeql-mrva. It is a plain Agent Skill
(`SKILL.md` + `references/` + `scripts/` + `assets/`), so every agent that
reads the agentskills.io format can load it. `install.sh` / `install.ps1` at
the repository root copy it into all three agents below in one go:

```bash
git clone https://github.com/offensiveadhd/codeql-mrva
bash codeql-mrva/install.sh                                  # all detected agents, user scope
bash codeql-mrva/install.sh --agents claude,codex --link     # symlink instead of copy
pwsh codeql-mrva/install.ps1 -Agents hermes                  # native Windows
powershell -ExecutionPolicy Bypass -File codeql-mrva\install.ps1   # when scripts are disabled by policy
```

## Claude Code

Plugin (the repository is its own marketplace):

```bash
claude plugin marketplace add offensiveadhd/codeql-mrva
claude plugin install codeql-mrva@codeql-mrva
```

Or copy the skill folder:

```bash
git clone https://github.com/offensiveadhd/codeql-mrva
cp -R codeql-mrva/skills/codeql-mrva ~/.claude/skills/          # personal, every project
cp -R codeql-mrva/skills/codeql-mrva .claude/skills/            # this repository only, commit it
```

Or with the skills CLI:

```bash
npx skills add offensiveadhd/codeql-mrva -a claude-code -g
```

Invoke with `/codeql-mrva ...` or let it trigger on MRVA / variant analysis
requests. New top-level skill directories mid-session need `/reload-skills`.

## Hermes Agent

From the GitHub path (Hermes fetches `SKILL.md` and every `references/`,
`scripts/` and `assets/` file it mentions, scans them, and asks before
installing):

```bash
hermes skills inspect offensiveadhd/codeql-mrva/skills/codeql-mrva
hermes skills install offensiveadhd/codeql-mrva/skills/codeql-mrva --category security
hermes skills list
```

Or copy into the skills directory (the source of truth for Hermes):

```bash
cp -R codeql-mrva/skills/codeql-mrva ~/.hermes/skills/security/               # Linux / macOS / WSL
```

```powershell
Copy-Item -Recurse codeql-mrva\skills\codeql-mrva "$env:LOCALAPPDATA\hermes\skills\security\"   # native Windows
```

**Profiles.** A Hermes profile (`hermes -p NAME`) has its own skills tree at
`<hermes home>/profiles/NAME/skills/`, and a skill in the base directory is
not visible there (`skill_view("codeql-mrva")` answers "not found"). Install
per profile: `hermes -p NAME skills install offensiveadhd/codeql-mrva/skills/codeql-mrva`,
copy into `profiles/NAME/skills/security/`, or run the installers with
`--hermes-profile default,NAME` (bash) / `-HermesProfile default,NAME`
(PowerShell); `all` covers the base directory and every profile.

Project-local: `./.hermes/skills/codeql-mrva` or `./.agents/skills/codeql-mrva`,
then `hermes skills trust` in that checkout. Invoke with
`/codeql-mrva <request>` in the CLI or any messaging platform; the agent can
also load it itself through `skill_view("codeql-mrva")`. `npx skills add
offensiveadhd/codeql-mrva -a hermes-agent -g` also works.

## Codex (CLI and IDE extension)

Codex reads `$HOME/.agents/skills` (user), `$REPO_ROOT/.agents/skills` and
`$CWD/.agents/skills` (project), and `/etc/codex/skills` (admin).

```bash
npx skills add offensiveadhd/codeql-mrva -a codex -g
# or
git clone https://github.com/offensiveadhd/codeql-mrva
mkdir -p ~/.agents/skills && cp -R codeql-mrva/skills/codeql-mrva ~/.agents/skills/
```

Or as a plugin: the repository carries `.codex-plugin/plugin.json` and
`.agents/plugins/marketplace.json`, so

```bash
codex plugin marketplace add offensiveadhd/codeql-mrva
```

then open the Plugins directory, pick the `tikotiktok` marketplace and
install `codeql-mrva`. Invoke with `$codeql-mrva` (or `/skills` to browse);
restart Codex if a freshly copied skill does not appear.

## Every agent at once

```bash
npx skills add offensiveadhd/codeql-mrva --agent '*' -g -y
```

## What the skill's commands need on the machine

| Tool | Install | Used for |
|---|---|---|
| GitHub CLI, signed in with `repo` scope | https://cli.github.com, `gh auth login` | every remote call the scripts make |
| CodeQL CLI | `gh extension install github/gh-codeql` (downloads ~400 MB on first use), then `gh codeql install-stub <dir>` on Linux/macOS; on Windows add `%LOCALAPPDATA%\GitHub CLI\extensions\gh-codeql\dist\release\v<version>` to `PATH` (see `references/gh-mrva-cli.md`); or a `github/codeql-cli-binaries` release on `PATH` | local tests, `gh mrva` pack bundling |
| gh-mrva | `gh extension install GitHubSecurityLab/gh-mrva` | CLI runs |
| VS Code + `GitHub.vscode-codeql` | VS Code Marketplace | interactive runs |
| Python 3.9+ | python.org / winget / brew | the three helper scripts (standard library only) |
| `mrva` (Trail of Bits), optional | `python -m pip install mrva` | fully local variant analysis |
