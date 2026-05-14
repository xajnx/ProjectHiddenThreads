# Version 0.2

## Summary

Upgraded ingestion pipeline to explicit three-phase flow:

- Discovery (crawl and index links)
- Verification (classify new/unchanged/modified/blocked/unknown)
- Acquisition (download only new/modified and permitted assets)

## Key Enhancements

- Pagination-aware discovery support
- Static script URL extraction
- Terminal blocked-state handling for 401/403
- JSON index output at data/metadata/assets_index.json
- Markdown and HTML reports in data/reports
- Crawl session counters for downloaded and blocked assets
