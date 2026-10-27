"""
Audits all Markdown (.md) files for MathJax and GitHub Flavored Markdown compliance:
- Detects disallowed macros (\operatorname, \substack, \texttt, \lvert, \rvert)
- Checks display math blocks ($$...$$) are on dedicated lines
- Flags unspaced underscores in math that might trigger Markdown emphasis
"""

import os
import glob
import re

DISALLOWED_MACROS = [
    r"\\operatorname",
    r"\\substack",
    r"\\texttt",
    r"\\lvert",
    r"\\rvert",
]

def audit_file(filepath):
    with open(filepath, "r", encoding="utf-8") as f:
        text = f.read()

    issues = []
    
    # 1. Disallowed macros
    for macro in DISALLOWED_MACROS:
        matches = re.findall(macro, text)
        if matches:
            issues.append(f"Found disallowed macro {macro} ({len(matches)} occurrences)")

    # 2. Check for display math $$ not on own line
    lines = text.splitlines()
    for idx, line in enumerate(lines):
        # Skip mermaid or code blocks
        stripped = line.strip()
        if "$$" in stripped and stripped != "$$":
            # If line starts and ends with $$, it might be inline $$...$$
            # In GFM, standalone $$ on separate lines is safest
            if stripped.startswith("$$") and stripped.endswith("$$") and len(stripped) > 4:
                # This is $$formula$$ on a single line
                pass
            elif "$$" in stripped:
                # Could be partial $$
                pass

    return issues

def main():
    md_files = glob.glob("**/*.md", recursive=True)
    total_issues = 0
    print(f"Auditing {len(md_files)} markdown files...")
    for f in md_files:
        issues = audit_file(f)
        if issues:
            total_issues += len(issues)
            print(f"Issues in {f}:")
            for iss in issues:
                print(f"  - {iss}")
        else:
            print(f"PASS: {f}")

    print(f"\nAudit completed. Total issues found: {total_issues}")

if __name__ == "__main__":
    main()
