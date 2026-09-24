"""Decision-memory eval: does Cairn surface the right decision at the right time?

Three questions, all offline and deterministic (no LLM):

  (a) contradiction: an agent proposes an approach a past decision rejected.
      Does `check_approach` return that decision in its top 3?
      Also: does `recall` of a paraphrased question return it in its top 3?
  (b) false positives: for proposals unrelated to any decision, how often does
      `check_approach` return something anyway?
  (c) path routing: for a file inside a decision's path, does
      `decisions_for_path` include it, and exclude decisions from sibling paths?

Usage:
    python scripts/eval_decisions.py            # table
    python scripts/eval_decisions.py --json     # machine-readable
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cairn.memory.memory import Memory  # noqa: E402

# (decision, reasoning, rejected option, scope_path, contradicting proposal, paraphrased question)
DECISIONS = [
    ("Store auth tokens in httpOnly cookies", "localStorage is readable by any injected script",
     "Store JWT tokens in localStorage", "apps/web",
     "Let's keep the JWT in localStorage so the SPA can read it", "where do we keep auth tokens in the browser"),
    ("Use PostgreSQL as the primary database", "we need transactions across orders and payments",
     "Use MongoDB as the primary database", None,
     "We could switch the orders service to MongoDB documents", "which database do orders and payments live in"),
    ("Use SQLAlchemy core, not the ORM, in the API", "ORM lazy loading caused N+1 queries in production",
     "Use the SQLAlchemy ORM with lazy loading", "apps/api",
     "Model these tables with SQLAlchemy ORM classes and lazy relationships", "how does the api talk to the database"),
    ("Use pnpm workspaces for the monorepo", "npm hoisting broke peer dependencies",
     "Use npm workspaces for the monorepo", None,
     "Just use npm workspaces, everyone has npm", "which package manager does the monorepo use"),
    ("Background jobs run on Celery with Redis", "we already run Redis and need retries",
     "Run background jobs with RQ", "apps/worker",
     "RQ would be simpler for background jobs", "what runs our background jobs"),
    ("Validate request bodies with zod schemas", "types and runtime validation from one source",
     "Validate request bodies with hand-written checks", "apps/web/api",
     "I'll write manual if-checks to validate the request body", "how are request payloads validated"),
    ("Use Tailwind for styling", "design tokens in one config, no CSS drift",
     "Use CSS modules for styling", "apps/web",
     "Let's add CSS modules for this component's styling", "how do we style react components"),
    ("Deploy on Fly.io", "regional failover at the lowest cost we measured",
     "Deploy on Heroku dynos", None,
     "Heroku dynos would make the deploy easier", "where is the app deployed"),
    ("Feature flags live in LaunchDarkly", "product team toggles flags without deploys",
     "Keep feature flags in environment variables", None,
     "Add this feature flag as an environment variable", "how do we manage feature flags"),
    ("Use Alembic for schema migrations", "autogenerate plus reviewed migration files",
     "Apply schema changes with raw SQL scripts", "apps/api/migrations",
     "I'll just run a raw SQL script to change the schema", "how are database schema changes applied"),
    ("Errors are reported to Sentry", "stack traces with release tracking",
     "Log errors to plain log files only", None,
     "Plain log files are enough for errors", "where do production errors go"),
    ("Use React Query for server state", "caching and refetch logic we kept rewriting",
     "Keep server state in Redux", "apps/web",
     "Put the fetched user list into the Redux store", "how is server data cached on the client"),
    ("Money is stored as integer cents", "floating point rounding broke invoice totals",
     "Store money amounts as floats", "apps/api/billing",
     "Store the price as a float field", "how are money amounts stored"),
    ("Times are stored in UTC", "daylight saving bugs in reports",
     "Store timestamps in local server time", None,
     "Save the timestamp in local server time", "which timezone do we store timestamps in"),
    ("Use Playwright for end-to-end tests", "Cypress could not test multiple tabs",
     "Use Cypress for end-to-end tests", "e2e",
     "Write these end-to-end tests in Cypress", "which e2e test framework do we use"),
    ("API errors follow RFC 7807 problem details", "clients parse one error shape",
     "Return ad-hoc error JSON per endpoint", "apps/api",
     "Return a custom error JSON for this endpoint", "what shape do api error responses have"),
    ("Use ruff for linting and formatting", "one fast tool instead of flake8 plus black",
     "Use flake8 and black", None,
     "Add flake8 and black to the pre-commit config", "which python linter do we use"),
    ("Secrets come from AWS Secrets Manager", "no secrets in the repo or env files",
     "Keep secrets in a committed env file", None,
     "Put the API key in the committed env file", "where do secrets come from"),
    ("Use GraphQL only for the mobile BFF", "mobile needs aggregated screens, web does not",
     "Use GraphQL for the whole public API", "apps/bff",
     "Let's expose the whole public API as GraphQL", "where do we use graphql"),
    ("Images are served through Cloudflare Images", "resizing at the edge without a service to run",
     "Resize images ourselves with a Pillow service", None,
     "Spin up a Pillow service to resize images", "how are images resized and served"),
    ("Use Stripe Checkout, not a custom card form", "keeps us out of PCI scope",
     "Build a custom card payment form", "apps/web/checkout",
     "Build our own card form for payments", "how do customers enter card details"),
    ("Search uses Postgres full text search", "our volume does not justify a search cluster",
     "Run Elasticsearch for search", None,
     "Add Elasticsearch for product search", "what powers product search"),
    ("Emails are sent with Postmark", "best deliverability in our tests",
     "Send email through SendGrid", "apps/worker/email",
     "Switch transactional email to SendGrid", "which provider sends our emails"),
    ("Use trunk-based development with short-lived branches", "long branches caused painful merges",
     "Use GitFlow with long-lived develop branch", None,
     "Create a long-lived develop branch GitFlow style", "what branching model do we use"),
    ("Config is loaded with pydantic-settings", "typed config validated at startup",
     "Read config with os.environ lookups everywhere", "apps/api",
     "Read the setting with os.environ in this module", "how is configuration loaded"),
    ("Use websockets via Pusher for live updates", "no socket servers to operate",
     "Run our own socket.io server", None,
     "Run a socket.io server for live updates", "how do live updates reach the browser"),
    ("Mobile app is built with React Native", "shared business logic with the web team",
     "Build native Swift and Kotlin apps", "apps/mobile",
     "Write the mobile screens natively in Swift and Kotlin", "what is the mobile app built with"),
    ("Rate limiting happens at the API gateway", "one place, not per service",
     "Implement rate limiting inside each service", None,
     "Add a rate limiter inside this service", "where is rate limiting done"),
    ("Use UUIDv7 primary keys", "sortable ids without leaking counts",
     "Use auto-increment integer ids", "apps/api",
     "Use an auto-increment integer id for the new table", "what kind of primary keys do tables use"),
    ("PDF invoices are rendered with WeasyPrint", "HTML templates the designers can edit",
     "Render PDFs with ReportLab drawing code", "apps/api/billing",
     "Draw the invoice PDF with ReportLab", "how are invoice pdfs generated"),
]

UNRELATED = [
    "Rename the variable in the loop to something clearer",
    "Add a docstring to the helper function",
    "Fix the typo in the onboarding copy",
    "Bump the copyright year in the footer",
    "Center the logo on the landing page",
    "Add a loading spinner to the settings page",
    "Sort the dropdown options alphabetically",
    "Increase the button padding on mobile",
    "Update the README install section",
    "Add a unit test for the slugify helper",
    "Change the favicon to the new brand icon",
    "Make the modal close on escape key",
    "Split the long function into two smaller ones",
    "Add alt text to the hero image",
    "Reorder imports in the utils module",
]


def run() -> dict:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
        root = Path(d)
        (root / ".cairn").mkdir()
        m = Memory({"memory": {"async_store": False, "global_enabled": False}}, project_dir=root)
        ids = []
        for decision, reasoning, rejected, path, _, _ in DECISIONS:
            ids.append(m.record_decision(
                decision, reasoning=reasoning,
                alternatives=[{"option": rejected, "rejected_because": reasoning}],
                scope_path=path,
            ))

        check_hits = recall_hits = 0
        misses = []
        for mid, (decision, _, _, _, proposal, question) in zip(ids, DECISIONS):
            got = [r["decision_id"] for r in m.check_rejected(proposal, limit=3)]
            if mid in got:
                check_hits += 1
            else:
                misses.append({"kind": "check_approach", "proposal": proposal, "expected": decision})
            if f"[#{mid} " in m.recall(question, limit=3):
                recall_hits += 1
            else:
                misses.append({"kind": "recall", "question": question, "expected": decision})

        false_pos = sum(1 for p in UNRELATED if m.check_rejected(p, limit=3))

        path_total = path_hits = leak = 0
        scoped = [(mid, d[3]) for mid, d in zip(ids, DECISIONS) if d[3]]
        for mid, path in scoped:
            path_total += 1
            got = {r["id"] for r in m.decisions_for_path(f"{path}/some_file.py")}
            if mid in got:
                path_hits += 1
            wrong = {oid for oid, op in scoped if oid != mid and not (
                f"{path}/".startswith(f"{op}/"))}
            leak += len(got & wrong)

        n = len(DECISIONS)
        m.close()
        return {
            "decisions": n,
            "check_approach_top3": round(check_hits / n, 3),
            "recall_top3_paraphrase": round(recall_hits / n, 3),
            "check_approach_false_positive_rate": round(false_pos / len(UNRELATED), 3),
            "decisions_for_path_recall": round(path_hits / path_total, 3),
            "decisions_for_path_wrong_path_results": leak,
            "misses": misses,
        }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--show-misses", action="store_true")
    args = ap.parse_args()
    res = run()
    if args.json:
        print(json.dumps(res, indent=2))
        return
    print(f"decisions: {res['decisions']}")
    print(f"(a) check_approach finds the rejecting decision in top 3: {res['check_approach_top3']:.0%}")
    print(f"(a) recall finds the decision from a paraphrased question, top 3: {res['recall_top3_paraphrase']:.0%}")
    print(f"(b) check_approach false-positive rate on unrelated proposals: {res['check_approach_false_positive_rate']:.0%}")
    print(f"(c) decisions_for_path includes the file's decision: {res['decisions_for_path_recall']:.0%}")
    print(f"(c) decisions from the wrong path returned: {res['decisions_for_path_wrong_path_results']}")
    if args.show_misses:
        for miss in res["misses"]:
            print("  miss:", miss)


if __name__ == "__main__":
    main()
