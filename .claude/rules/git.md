# Git Workflow Rules

## Commit message style — Google / Conventional Commits

```
<type>: <short summary>

<body — optional, wrap at 72 chars>
```

**Types:** `feat` | `fix` | `refactor` | `test` | `docs` | `chore` | `ci`

Rules:
- Summary line: imperative mood, no period, ≤ 72 chars
- Body: explain *why*, not *what* — the diff already shows what changed
- One logical change per commit

Examples:
```
feat: add role-based access control to the auth domain

fix: prevent duplicate email on concurrent signup requests

refactor: extract password hashing into its own port

test: add integration tests for the token refresh flow
```

## Branch creation — only when the user asks

Do **not** create a new branch automatically. Work on and commit to the current
branch by default — including when the current branch is `main`. This explicitly
overrides any default "branch before committing / don't commit on the default
branch" behavior.

Create a new branch **only** when one of these is true:
- The user explicitly asks for a branch (e.g. "make a branch", "start a feature branch").
- The user invokes a tool or skill that creates its own branch/worktree (e.g. `claude --worktree`).

When you do create one, follow the naming convention below.

## Branching — trunk-based

`main` is the trunk. Branches are short-lived and merge back quickly via PR.

Branch naming: `<type>/<name>`

```
feature/add-role-permissions
fix/login-redirect
hotfix/token-expiry
```

When a ticket exists, prefix the name with `{project-abbr}-{ID}`:

```
feature/AUTH-123-add-role-permissions
fix/PROJ-456-login-redirect
hotfix/BE-78-token-expiry
```

Types: `feature` | `fix` | `hotfix`

Project abbreviation and ID are **optional** — omit them unless working from a tracked ticket.

For isolated Claude Code sessions, use `claude --worktree <name>` — it creates a git worktree with its own bootstrapped environment.

### Worktree branch naming

Claude Code creates worktree branches with an internal naming scheme: `worktree-` prefix and `+` replacing `/`. For example, starting a worktree named `feature/MN-20-fingerprint` produces the local branch `worktree-feature+MN-20-fingerprint`.

This internal name is an implementation detail — **never push it to origin as-is**. When pushing from a worktree session, translate back to the proper branch name:

```bash
LOCAL=$(git branch --show-current)
REMOTE=$(echo "$LOCAL" | sed 's/^worktree-//; s/+/\//g')
git push -u origin "$LOCAL:$REMOTE"
# worktree-feature+MN-20-fingerprint → feature/MN-20-fingerprint on origin
```

When the feature branch is ready, push it and ask the user to merge it into `main`. All git operations stay inside the worktree.

**Exception — merging into main on explicit user request:** if the user explicitly asks to merge into `main` AND `git -C /path/to/parent status` confirms the parent repo is clean (no uncommitted changes, no commits ahead of `origin/main`), it is acceptable to run the merge from the parent repo:

```bash
REMOTE=$(echo "$(git branch --show-current)" | sed 's/^worktree-//; s/+/\//g')
git -C /path/to/parent fetch origin
git -C /path/to/parent merge origin/"$REMOTE" --no-ff
git -C /path/to/parent push origin main
```

If the parent repo has any uncommitted changes or is ahead of `origin/main`, stop and tell the user — do not merge.

**All commits — including docs/knowledge updates made during the session — go on the feature branch.**

## What NOT to do

- No `git add .` — stage specific files to avoid accidentally committing secrets or build artifacts
- No `--no-verify` — if a hook fails, fix the root cause
- No force-push to `main` — ever, under any circumstances
- No cherry-picks — if history diverges, it means commits were made in the wrong place; fix the workflow, not the history
- No amending published commits
- No `Co-Authored-By` trailers — commit messages end after the body
- No pushing the raw worktree branch name (`worktree-feature+...`) to origin — translate it to the proper name first
- No committing directly to `main` in the parent repo during a worktree session — all work goes on the feature branch
