#!/usr/bin/env bash
#
# Stage everything, commit, rebase on origin, push. One command.
#
# This exists because the multi-line "run these four git commands" handoff
# failed the way that kind of handoff always fails: the `git add` list was
# written out by hand each time, and twice it was missing a path. Nobody
# noticed, because a commit that omits a directory looks exactly like a
# commit that does not. `assets/` sat untracked through two rounds, which
# would have had CI rendering cards in whatever sans the runner happened
# to have.
#
# `git add -A` and a correct .gitignore cannot omit a path. That is the
# whole idea.
#
#   ./scripts/ship.sh "what changed"
#
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"

msg="${1:-}"
if [ -z "$msg" ]; then
  echo "usage: scripts/ship.sh \"commit message\"" >&2
  exit 64
fi

# Locks left behind by the file bridge, which can write into .git but is not
# permitted to unlink. Harmless once removed; fatal to the next git command
# if they are not.
rm -f .git/index.lock .git/HEAD.lock .git/objects/maintenance.lock
find .git/objects -name 'tmp_obj_*' -delete 2>/dev/null || true

if [ -d .git/rebase-merge ] || [ -d .git/rebase-apply ]; then
  echo "A rebase is already in progress. Finish or abort it first:" >&2
  echo "  git rebase --continue   # or --abort" >&2
  exit 1
fi

# Files git is tracking that .gitignore says to ignore. An ignore rule has
# no effect on a file already in the index, so __pycache__ rode along in
# every commit with a rule sitting right there claiming to exclude it, and
# the only symptom was conflicts on files nobody wrote. Untracking is a
# one-time correction; doing it here makes it automatic, so adding a rule
# and shipping once is enough to be rid of the strays.
strays="$(git ls-files -i -c --exclude-standard)"
if [ -n "$strays" ]; then
  echo "Untracking files .gitignore excludes:"
  echo "$strays" | sed 's/^/  /'
  printf '%s\n' "$strays" | tr '\n' '\0' | xargs -0 git rm -r --cached -q --
fi

git add -A
if git diff --cached --quiet; then
  echo "Nothing to commit."
else
  echo "Committing:"
  git diff --cached --name-status | sed 's/^/  /'
  git commit -q -m "$msg"
fi

echo
echo "Rebasing on origin/main..."
git pull --rebase --quiet

echo "Pushing..."
git push --quiet

echo
echo "Pushed. origin/main is now:"
git log --oneline -1 origin/main
