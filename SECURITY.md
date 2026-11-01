# Security Policy

## Supported versions

| Version | Security support |
|---|---|
| 0.5.x | ✅ Active   |
| < 0.5 | ❌ Unsupported |

---

## Reporting a vulnerability

**Do NOT open a public GitHub issue** for security problems. Instead:

Use GitHub's **Security Advisory** feature:
   `github.com/ZalOZKAN/Cairn/security/advisories/new`

**Include in your report:**
- Type of vulnerability (injection, authentication, etc.)
- Affected file / line
- A reproducible example (WITHOUT real API keys)
- Estimated impact

**Response time:** acknowledgement within 48 hours, patch within 7 days.

---

## Cairn's security model

### Threat model

Cairn is a developer tool, not a public-facing web service. The threat
model reflects that.

**Defended against:**
- Prompt-injection attempts (SecurityGuard)
- API keys leaking into logs (mask_secrets)
- Path traversal (sanitize_file_path)
- External-content injection (sanitize_for_llm)

**Out of scope (user's responsibility):**
- Security of the user's own API keys
- Security of the LLM provider being called
- Security of any code the user executes

### Security features

**SecurityGuard (`cairn/security/guard.py`):**
- Prompt-injection pattern detection
- Input length limit (50K characters)
- File-path traversal protection
- API-key masking
- Threat logging (`~/.cairn/security.log`)

**Config security:**
- API keys are never written to a config file
- They are only read from environment variables
- `.gitignore` blocks `*.env` and `*.key`

**Dependency security:**
- CI runs `pip-audit`
- `pyproject.toml` pins minimum versions

---

## Security recommendations for users

```bash
# Never put an API key in your code.
# Correct:
export GROQ_API_KEY=your_key

# Wrong (never do this):
# echo "GROQ_API_KEY=your_key" >> config.yaml
```

```bash
# Do not commit .env files.
echo ".env" >> .gitignore
echo "*.key" >> .gitignore
```

```bash
# Periodically check the security log.
cat ~/.cairn/security.log
```

---

## Known limitations

- Cairn does not validate LLM provider output.
- The security of MCP tools is the MCP server's responsibility.
- Subagent output is not automatically treated as trusted.
