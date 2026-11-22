# AGENTS.md

Agent context. This file is read automatically by the sandbox at session start.

## Project Overview
**Primary languages:** Python
**Package managers:** pip, uv

## Build, Test, and Lint Commands
**Install dependencies:**
```
make sync
```
**Lint / format:**
```
make lint-all
```

## Directory Structure
**Top-level directories:** `src`, `tests`
**Source roots:** `src`
**Test roots:** `tests`
**Most-changed directories:** `README.md`, `Makefile`, `pyproject.toml`, `src`, `.gitignore`

## Sandbox Awareness
The sandbox has network access disabled by default during code execution.
- Do not make HTTP requests in tests — mock all external calls.
- Do not rely on system-installed tools; prefer project-local binaries (`./node_modules/.bin/`, virtualenv, etc.).
- The sandbox resets between sessions — do not rely on persistent state outside the repo.
- Python dependencies: install into a virtualenv. The sandbox may not have all packages pre-installed.

## Conventions Inferred from Codebase
**Commit prefix style:** `feat`, `bump`, `docs`, `refactor` (follow this pattern)
**Common file extensions:** `.py`, `[no_ext]`, `.md`, `.toml`, `.lock`
- Python: use type hints on all public functions
- Python: snake_case for functions/variables, PascalCase for classes

## apply_patch Usage
When modifying files, prefer `apply_patch` over full-file writes for surgical changes:
- Keep patches small and focused on the minimal diff.
- After applying a patch, run the test command to verify correctness.
- Use `apply_patch` with `create` action for new files, `modify` for existing ones.
- For large structural refactors, consider writing the full new file instead of a complex patch.

## Approval Gates
Before marking a task complete, verify ALL of the following:
- [ ] Tests pass `make tests-all`
- [ ] Typing clean `make typing-all`
- [ ] Secrets check clean `make secrets-all`
- [ ] Dead code detection clean `make vulture-all`
- [ ] Vulnerabilities clean `make bandit-all`
- [ ] Linting clean: `make lint-all`
- [ ] No unintended files modified (check `git diff --stat`)
- [ ] New code has corresponding tests (if behavior change)
- [ ] Commit message follows the project prefix convention

## Anti-Patterns to Avoid
- **Do not** make network calls in the sandbox — all external calls must be mocked.
- **Do not** commit secrets, API keys, or credentials.
- **Do not** modify test fixtures or golden files to make tests pass without understanding why.
- **Do not** introduce new dependencies without updating the lock file.
- **Do not** skip linting or type-check steps — they are part of the approval gate.
