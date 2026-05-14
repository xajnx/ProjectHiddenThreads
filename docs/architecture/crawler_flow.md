# Crawler Flow

1. Discovery phase crawls listing pages, including pagination templates.
2. Links are normalized and deduplicated.
3. All discovered links are indexed in `assets` (including non-downloadable references).
4. Verification phase uses HEAD metadata to classify states:
   - new
   - unchanged
   - modified
   - blocked
   - unknown
5. Acquisition phase downloads only `new` and `modified` assets when access is permitted.
6. Reports and JSON registry are generated after each run.
