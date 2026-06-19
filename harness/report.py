"""Final report generation (spec §19, §24.14).

A run is incomplete without a final report. The report MUST contain the 13
mandatory sections of §24.14 and make explicit what is known, what failed, what
was negative, what is only technically valid, and what cannot be concluded.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .events import EventStore

# The 13 mandatory sections (spec §24.14). Order is part of the contract; tests
# assert every one of these headings is present.
MANDATORY_SECTIONS = [
    "1. What was executed",
    "2. What was NOT executed",
    "3. What failed",
    "4. What was negative",
    "5. What was inconclusive",
    "6. What was technically valid",
    "7. What was biologically interpretable",
    "8. What CANNOT be concluded",
    "9. Resources used",
    "10. Software / versions used",
    "11. Data included / excluded",
    "12. Remaining risks",
    "13. Recommended next actions",
]


class ReportGenerator:
    def __init__(self, run_dir: str | Path) -> None:
        self.run_dir = Path(run_dir)

    def _md_list(self, items: list[Any]) -> str:
        if not items:
            return "_none_\n"
        return "".join(f"- {i}\n" for i in items)

    def generate(self, report: dict[str, Any]) -> dict[str, str]:
        """Write report.md + report.json. ``report`` carries the section payloads."""
        sections = report.get("sections", {})
        lines: list[str] = []
        lines.append(f"# Run report — {report.get('run_id', 'unknown')}\n")
        lines.append("## Executive summary\n")
        lines.append(report.get("summary", "_no summary provided_") + "\n")
        lines.append(f"\n**Scientific question:** {report.get('scientific_question', 'n/a')}\n")

        for heading in MANDATORY_SECTIONS:
            lines.append(f"\n## {heading}\n")
            payload = sections.get(heading, [])
            if isinstance(payload, str):
                lines.append(payload + "\n")
            else:
                lines.append(self._md_list(list(payload)))

        md_path = self.run_dir / "report.md"
        json_path = self.run_dir / "report.json"
        md_path.write_text("".join(lines), encoding="utf-8")
        json_path.write_text(json.dumps(report, indent=2, sort_keys=True, default=str), encoding="utf-8")
        return {"markdown": str(md_path), "json": str(json_path)}

    @staticmethod
    def empty_sections() -> dict[str, list]:
        return {h: [] for h in MANDATORY_SECTIONS}
