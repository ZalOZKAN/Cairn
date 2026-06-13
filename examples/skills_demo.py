"""
Cairn, skills usage examples.

Cairn loads skills and workflows as markdown, it does not execute them.
The IDE (Claude Code, Cursor, ...) takes the markdown and runs the LLM.

Run:
  pip install cairn
  python examples/skills_demo.py
"""
from __future__ import annotations

import sys

from cairn import Project

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def main() -> None:
    project = Project()

    print("=" * 50)
    print("List installed skills")
    print("=" * 50)
    skills = project.list_skills()
    for s in skills[:8]:
        print(f"  {s.get('alias') or s.get('name')}")

    print("\n" + "=" * 50)
    print("Load the @cr (code-review) skill prompt")
    print("=" * 50)
    cr_prompt = project.get_skill("@cr")
    if cr_prompt:
        print(cr_prompt[:600] + "..." if len(cr_prompt) > 600 else cr_prompt)
    else:
        print("  Skill '@cr' not found.")

    print("\n" + "=" * 50)
    print("Load the /plan workflow prompt")
    print("=" * 50)
    plan_prompt = project.get_workflow("/plan")
    if plan_prompt:
        print(plan_prompt[:600] + "..." if len(plan_prompt) > 600 else plan_prompt)
    else:
        print("  Workflow '/plan' not found.")


if __name__ == "__main__":
    main()
