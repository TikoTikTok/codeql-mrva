#!/usr/bin/env bash
# Create (or finish setting up) an MRVA controller repository.
#
#   mrva_controller.sh OWNER/NAME [--private|--public] [--runner-labels '["self-hosted","linux"]'] [--description TEXT]
#
# A controller repository hosts the dynamic GitHub Actions workflows that MRVA
# dispatches. It may be empty of code but must have at least one commit, so this
# script creates it with a README. Public controller: public targets only, free.
# Private controller: private/internal targets allowed, Actions minutes billed.
set -euo pipefail

usage() { sed -n '2,10p' "$0" | sed 's/^# \{0,1\}//'; }

NWO=""
VIS="--public"
LABELS=""
DESC="MRVA controller: hosts the dynamic workflows of CodeQL multi-repository variant analysis. No code lives here."

while (($#)); do
  case "$1" in
    --private) VIS="--private" ;;
    --public) VIS="--public" ;;
    --runner-labels) LABELS="${2:-}"; shift ;;
    --description) DESC="${2:-}"; shift ;;
    -h|--help) usage; exit 0 ;;
    -*) echo "unknown option: $1" >&2; usage >&2; exit 2 ;;
    *) NWO="$1" ;;
  esac
  shift
done

if [[ ! "$NWO" =~ ^[A-Za-z0-9-]+/[A-Za-z0-9._-]+$ ]]; then
  echo "error: give the controller as OWNER/NAME" >&2
  usage >&2
  exit 2
fi
command -v gh >/dev/null 2>&1 || { echo "error: gh (GitHub CLI) not found: https://cli.github.com" >&2; exit 1; }
gh auth status >/dev/null 2>&1 || { echo "error: not signed in; run: gh auth login" >&2; exit 1; }

if gh repo view "$NWO" --json name >/dev/null 2>&1; then
  echo "repository $NWO already exists"
else
  gh repo create "$NWO" "$VIS" --add-readme --description "$DESC" >/dev/null
  echo "created $NWO (${VIS#--})"
fi

# MRVA fails on an empty repository ("does not have a branch"): make sure one commit exists.
if ! gh api "repos/$NWO/commits?per_page=1" >/dev/null 2>&1; then
  echo "repository has no commits; adding a README"
  CONTENT=$(printf '# MRVA controller\n\nThis repository only hosts the dynamic GitHub Actions workflows that CodeQL multi-repository variant analysis dispatches. No code lives here.\n' | base64 | tr -d '\n')
  gh api -X PUT "repos/$NWO/contents/README.md" -f message="MRVA controller" -f content="$CONTENT" >/dev/null
fi

if [[ -n "$LABELS" ]]; then
  gh variable set MRVA_RUNNER_OS --repo "$NWO" --body "$LABELS"
  echo "set Actions variable MRVA_RUNNER_OS=$LABELS"
fi

BRANCH=$(gh repo view "$NWO" --json defaultBranchRef --jq '.defaultBranchRef.name')
VISIBILITY=$(gh repo view "$NWO" --json visibility --jq '.visibility')

cat <<MSG
Controller ready: $NWO (visibility: $VISIBILITY, default branch: $BRANCH)
  VS Code : "codeQL.variantAnalysis.controllerRepo": "$NWO"
  gh mrva : controller: $NWO        (in ~/.config/gh-mrva/config.yml)
  API     : POST repos/$NWO/code-scanning/codeql/variant-analyses
  logs    : https://github.com/$NWO/actions
Rule: public controller -> public targets only (free); private controller -> private/internal targets, Actions minutes billed.
MSG
