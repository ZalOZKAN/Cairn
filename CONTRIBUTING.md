# Contributing to Cairn

Want to contribute to Cairn, great. This guide walks you through the process.

---

## Quick start

```bash
git clone https://github.com/yourusername/cairn
cd cairn
pip install -e ".[dev]"
pytest tests/   # confirm the tests pass
```

---

## What kinds of contributions are welcome?

**Wanted:**
- Bug fixes
- New skills (`skills/<skill-name>/SKILL.md`)
- New workflows (`workflows/<name>.md`)
- Test coverage improvements
- Documentation improvements

**Let's discuss first:**
- New CLI commands
- Architectural changes
- New dependencies
- Breaking changes

---

## Contribution flow

### 1. Fork and branch
```bash
git fork https://github.com/yourusername/cairn
git checkout -b feat/my-feature    # for a feature
git checkout -b fix/bug-description # for a bug fix
```

**Branch naming:**
- `feat/`, new feature
- `fix/`, bug fix
- `docs/`, documentation only
- `test/`, adding tests
- `chore/`, dependency updates, etc.

### 2. Write code

**Required:**
- Python 3.10+ compatible
- Use type hints (`def foo(x: str) -> int:`)
- Add a test for new behavior
- `ruff check` must pass
- Never hardcode an API key

**Recommended:**
- Docstring per function
- Inline comment for non-obvious logic
- `"""description"""` at the top of every module

### 3. Run tests

```bash
# Run the suite
pytest tests/ -v

# Run a single test
pytest tests/test_core.py::test_router_classify_simple -v

# Coverage
pytest tests/ --cov=cairn --cov-report=term-missing
```

Minimum: at least one test for code you changed.

### 4. Commit

**Commit message format (Conventional Commits):**
```
feat(memory): add temporal validity invalidation
fix(memory): handle empty recall result
docs(readme): update quick start section
test(security): add injection pattern tests
chore(deps): update litellm to 1.35
```

Format: `type(scope): description`

Types: `feat` `fix` `docs` `test` `chore` `refactor` `perf`

### 5. Open a PR

- Fill in the PR template (don't leave it blank)
- Merges only after CI passes
- At least one maintainer review required
- No direct pushes to `main`, always a PR

---

## Adding a skill (easiest contribution)

You don't need to know Python to add a new skill:

```bash
mkdir -p skills/my-skill
cat > skills/my-skill/SKILL.md << 'EOF'
# @my-skill, Skill description

You are an expert in...

## What you do
...

## Output format
...
EOF
```

Then add an alias in `cairn/skills/registry.py`:
```python
SKILL_ALIASES: dict[str, str] = {
    ...
    "@ms": "my-skill",
}
```

Open a PR; once CI passes it gets merged.

---

## Security

**If you find a vulnerability:**
- Do NOT open a public GitHub issue
- Email `security@yourdomain.com`
- Or use GitHub's private security advisory feature
- Details: [SECURITY.md](SECURITY.md)

**When writing code:**
- Always validate user input through `SecurityGuard.check_prompt()`
- Check file paths with `SecurityGuard.sanitize_file_path()`
- Isolate external content with `SecurityGuard.sanitize_for_llm()`
- Never log API keys, use `mask_secrets()`

---

## Code review criteria

For a PR to be merged:

| Criterion | Description |
|---|---|
| CI passes | `pytest` and `ruff` are green |
| Tests present | A test exists for the new code |
| Security | No hardcoded API keys; input is validated |
| No breaking change | Or if there is one, it's noted in CHANGELOG |
| Dependencies | Fewer new dependencies is better |

---

## Communication

- GitHub Discussions, ask a question, share an idea
- GitHub Issues, bug reports, feature requests
- Don't DM `main`, open an issue or discussion

---

Thanks in advance for your contribution.
