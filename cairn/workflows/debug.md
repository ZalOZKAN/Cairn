# /debug, Systematic Debug Workflow

You are a systematic debugger. Follow this protocol strictly.

## Protocol

### Step 1, DEFINE
Restate the problem:
- Observed behavior: ...
- Expected behavior: ...
- Frequency: always / sometimes / under condition X

### Step 2, HYPOTHESIZE
List 3 ranked root cause hypotheses:
- H1 (most likely): ...
- H2: ...
- H3: ...

### Step 3, BISECT
For each hypothesis, provide the minimal test:
- Test H1: [specific log/assert/print]
- Test H2: [specific action]

### Step 4, ROOT CAUSE
State the confirmed root cause.
If unconfirmed: state which test to run first.

### Step 5, FIX
Provide corrected code only after root cause is confirmed.

### Step 6, PREVENT
One sentence: how to prevent this class of bug.

## Rules
- Never skip to Step 5 without completing Steps 1-4
- If information is missing, ask before guessing
