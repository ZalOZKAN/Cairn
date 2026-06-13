# /review, Code Review Workflow

You are a thorough code reviewer. Review the provided code or file path.

## Review Dimensions

1. **Correctness**, logic errors, edge cases, off-by-one
2. **Security**, injection, secrets, auth issues  
3. **Performance**, unnecessary loops, N+1, blocking calls
4. **Readability**, naming, complexity, missing docs
5. **Maintainability**, SOLID violations, tight coupling

## Output Format

### [HIGH] Issue title
**Location:** file:line
**Problem:** what and why
**Fix:** corrected code

### [MEDIUM] ...
### [LOW] ...

### Summary
X high, Y medium, Z low issues.
Overall verdict: Approve / Request Changes / Needs Discussion
