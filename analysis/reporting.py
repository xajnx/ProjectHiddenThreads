from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from database.db import Database


@dataclass(slots=True)
class ReportOutputs:
    markdown_path: Path
    html_path: Path
    json_index_path: Path


class ReportGenerator:
    def __init__(self, db: Database, project_root: Path) -> None:
        self.db = db
        self.project_root = project_root

    def generate(self) -> ReportOutputs:
        now = datetime.now(timezone.utc)
        date_stamp = now.strftime("%Y%m%d")

        report_dir = self.project_root / "data" / "reports"
        metadata_dir = self.project_root / "data" / "metadata"
        report_dir.mkdir(parents=True, exist_ok=True)
        metadata_dir.mkdir(parents=True, exist_ok=True)

        assets = self.db.list_all_assets()
        stats = self.db.summarize_asset_status_counts()

        new_assets = [row for row in assets if str(row["verification_state"]) == "new"]
        modified_assets = [
            row for row in assets if str(row["verification_state"]) == "modified"
        ]
        blocked_assets = [
            row
            for row in assets
            if str(row["status"]) == "blocked"
            or str(row["download_status"]) == "blocked"
        ]

        markdown_path = report_dir / f"crawl_report_{date_stamp}.md"
        html_path = report_dir / f"crawl_report_{date_stamp}.html"
        json_index_path = metadata_dir / "assets_index.json"

        markdown_path.write_text(
            self._markdown_report(stats, new_assets, modified_assets, blocked_assets),
            encoding="utf-8",
        )
        html_path.write_text(
            self._html_report(stats, new_assets, modified_assets, blocked_assets),
            encoding="utf-8",
        )
        json_index_path.write_text(
            json.dumps([dict(row) for row in assets], indent=2),
            encoding="utf-8",
        )

        return ReportOutputs(
            markdown_path=markdown_path,
            html_path=html_path,
            json_index_path=json_index_path,
        )

    @staticmethod
    def _markdown_report(stats, new_assets, modified_assets, blocked_assets) -> str:
        lines = [
            "# Crawl Report",
            "",
            "## Crawl Statistics",
            "",
            f"- Total assets tracked: {sum(stats.values())}",
        ]
        for key in sorted(stats.keys()):
            lines.append(f"- {key}: {stats[key]}")

        lines.extend(["", "## Newly Discovered Assets", ""])
        lines.extend(_render_asset_lines(new_assets))

        lines.extend(["", "## Modified Assets", ""])
        lines.extend(_render_asset_lines(modified_assets))

        lines.extend(["", "## Blocked Assets", ""])
        lines.extend(_render_asset_lines(blocked_assets))

        return "\n".join(lines) + "\n"

    @staticmethod
    def _html_report(stats, new_assets, modified_assets, blocked_assets) -> str:
        return f"""
<!doctype html>
<html lang=\"en\">
  <head>
    <meta charset=\"utf-8\" />
    <title>ProjectHiddenThreads Crawl Report</title>
    <style>
      body {{ font-family: 'Segoe UI', sans-serif; margin: 24px; color: #1d2430; }}
      table {{ width: 100%; border-collapse: collapse; margin-top: 12px; }}
      th, td {{ border: 1px solid #d9dde3; padding: 8px; text-align: left; }}
      th {{ background: #f5f7fa; }}
      h2 {{ margin-top: 28px; }}
    </style>
  </head>
  <body>
    <h1>Crawl Report</h1>
    <h2>Crawl Statistics</h2>
    <ul>
      <li>Total assets tracked: {sum(stats.values())}</li>
      {''.join(f'<li>{key}: {stats[key]}</li>' for key in sorted(stats.keys()))}
    </ul>
    {render_html_table('Newly Discovered Assets', new_assets)}
    {render_html_table('Modified Assets', modified_assets)}
    {render_html_table('Blocked Assets', blocked_assets)}
  </body>
</html>
""".strip() + "\n"


def _render_asset_lines(rows) -> list[str]:
    if not rows:
        return ["- None"]
    return [f"- {row['url']} ({row['status']})" for row in rows[:200]]


def render_html_table(title: str, rows) -> str:
    if not rows:
        return f"<h2>{title}</h2><p>None</p>"
    body = "".join(
        f"<tr><td>{row['id']}</td><td>{row['url']}</td><td>{row['status']}</td><td>{row['verification_state']}</td></tr>"
        for row in rows[:300]
    )
    return (
        f"<h2>{title}</h2>"
        "<table><thead><tr><th>ID</th><th>URL</th><th>Status</th><th>Verification</th></tr></thead>"
        f"<tbody>{body}</tbody></table>"
    )
