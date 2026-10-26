#!/usr/bin/env bash
# Install the codeql-mrva skill into Claude Code, Hermes Agent and/or Codex.
#
#   install.sh [--agents claude,hermes,codex] [--hermes-profile default,NAME|all] [--link] [--project] [--uninstall]
#
#   --agents          comma-separated subset (default: every agent detected on this machine,
#                     or all three when none is detected)
#   --hermes-profile  which Hermes profiles get the skill: "default" (the base skills directory),
#                     profile names (their own skills directory under profiles/NAME/skills),
#                     or "all" for the base directory plus every profile. Default: default
#   --link            symlink the skill folder instead of copying (git pull then updates every agent)
#   --project         install into the current directory's project-level skill folders instead of
#                     the user-level ones (.claude/skills, .hermes/skills, .agents/skills)
#   --uninstall       remove the skill from the selected agents
#
# User-level targets:
#   Claude Code : ~/.claude/skills/codeql-mrva
#   Hermes      : <hermes home>/skills/security/codeql-mrva                 (profile "default")
#                 <hermes home>/profiles/NAME/skills/security/codeql-mrva   (profile NAME)
#                 hermes home = $HERMES_HOME, else %LOCALAPPDATA%\hermes on Windows, else ~/.hermes
#   Codex       : ~/.agents/skills/codeql-mrva
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
NAME="codeql-mrva"
SRC="$HERE/skills/$NAME"
AGENTS=""
HERMES_PROFILES="default"
LINK=0
PROJECT=0
UNINSTALL=0

usage() { sed -n '2,22p' "$0" | sed 's/^# \{0,1\}//'; }

while (($#)); do
  case "$1" in
    --agents) AGENTS="${2:-}"; shift ;;
    --agents=*) AGENTS="${1#*=}" ;;
    --hermes-profile) HERMES_PROFILES="${2:-}"; shift ;;
    --hermes-profile=*) HERMES_PROFILES="${1#*=}" ;;
    --link) LINK=1 ;;
    --project) PROJECT=1 ;;
    --uninstall) UNINSTALL=1 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "unknown option: $1" >&2; usage >&2; exit 2 ;;
  esac
  shift
done

[[ -f "$SRC/SKILL.md" ]] || { echo "error: $SRC/SKILL.md not found; run from a clone of the repository" >&2; exit 1; }

hermes_home() {
  if [[ -n "${HERMES_HOME:-}" ]]; then echo "$HERMES_HOME"; return; fi
  if [[ -n "${LOCALAPPDATA:-}" && -d "$LOCALAPPDATA/hermes" ]]; then echo "$LOCALAPPDATA/hermes"; return; fi
  echo "$HOME/.hermes"
}

detect() {
  local found=""
  if command -v claude >/dev/null 2>&1 || [[ -d "$HOME/.claude" ]]; then found="$found,claude"; fi
  if command -v hermes >/dev/null 2>&1 || [[ -d "$(hermes_home)" ]]; then found="$found,hermes"; fi
  if command -v codex >/dev/null 2>&1 || [[ -d "$HOME/.codex" || -d "$HOME/.agents" ]]; then found="$found,codex"; fi
  found="${found#,}"
  echo "${found:-claude,hermes,codex}"
}

# one target path per line
targets_for() {
  case "$1" in
    claude) if ((PROJECT)); then echo "$PWD/.claude/skills/$NAME"; else echo "$HOME/.claude/skills/$NAME"; fi ;;
    codex)  if ((PROJECT)); then echo "$PWD/.agents/skills/$NAME"; else echo "$HOME/.agents/skills/$NAME"; fi ;;
    hermes)
      if ((PROJECT)); then echo "$PWD/.hermes/skills/$NAME"; return; fi
      local home p d profs
      home="$(hermes_home)"
      IFS=',' read -r -a profs <<<"$HERMES_PROFILES"
      for p in "${profs[@]}"; do
        p="${p// /}"
        case "$p" in
          "") ;;
          default) echo "$home/skills/security/$NAME" ;;
          all)
            echo "$home/skills/security/$NAME"
            for d in "$home"/profiles/*/; do [[ -d "$d" ]] && echo "${d%/}/skills/security/$NAME"; done ;;
          *)
            [[ -d "$home/profiles/$p" ]] || echo "warning: Hermes profile '$p' not found under $home/profiles; creating its skills directory anyway" >&2
            echo "$home/profiles/$p/skills/security/$NAME" ;;
        esac
      done ;;
    *) echo "error: unknown agent '$1' (claude, hermes, codex)" >&2; exit 2 ;;
  esac
}

remove_existing() {
  local t="$1"
  if [[ -L "$t" ]]; then rm -f "$t"
  elif [[ -d "$t" ]]; then
    [[ -f "$t/SKILL.md" ]] || { echo "error: $t exists and is not a skill folder; not touching it" >&2; exit 1; }
    rm -rf "$t"
  fi
}

[[ -n "$AGENTS" ]] || AGENTS="$(detect)"
IFS=',' read -r -a LIST <<<"$AGENTS"

for agent in "${LIST[@]}"; do
  agent="${agent// /}"
  [[ -n "$agent" ]] || continue
  while IFS= read -r t; do
    [[ -n "$t" ]] || continue
    if ((UNINSTALL)); then
      if [[ -e "$t" || -L "$t" ]]; then remove_existing "$t"; echo "$agent: removed $t"; else echo "$agent: nothing at $t"; fi
      continue
    fi
    mkdir -p "$(dirname "$t")"
    remove_existing "$t"
    if ((LINK)); then
      if ln -s "$SRC" "$t" 2>/dev/null; then
        echo "$agent: linked $t -> $SRC"
      else
        cp -R "$SRC" "$t"
        echo "$agent: symlink not permitted here, copied to $t"
      fi
    else
      cp -R "$SRC" "$t"
      echo "$agent: copied to $t"
    fi
  done < <(targets_for "$agent")
done

((UNINSTALL)) && exit 0
echo
echo "Next:"
echo "  Claude Code : /codeql-mrva ...   (run /reload-skills in an open session)"
echo "  Hermes      : /codeql-mrva ...   (project installs need: hermes skills trust; other profiles: --hermes-profile NAME)"
echo "  Codex       : \$codeql-mrva ...   (restart Codex if it does not appear)"
