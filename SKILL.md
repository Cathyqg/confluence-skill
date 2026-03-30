---
name: confluence-export
description: >-
  Recursively export a Confluence page and all its child/descendant pages to
  Markdown files preserving the original hierarchy. Generates raw.md (full
  content) and summary.md (AI-enhanced summaries). Use when the user wants to
  export, download, dump, or back up Confluence pages to Markdown, or when
  they provide a Confluence page URL and ask for its content tree, or when
  they ask to summarize previously exported Confluence content.
---

# Confluence Page Export

Export a Confluence page and all its descendants to Markdown, preserving the
page hierarchy. Produces `raw.md` (full content) and `summary.md` (intelligent
summaries per page).

## Prerequisites

1. Python 3.8+ installed
2. Install dependencies:

```bash
pip install -r scripts/requirements.txt
```

3. Set authentication environment variables:

**Cloud** (`xxx.atlassian.net`):
```bash
export CONFLUENCE_USERNAME="your.email@company.com"
export CONFLUENCE_API_TOKEN="your_api_token"
```

**Server / Data Center**:
```bash
export CONFLUENCE_PERSONAL_TOKEN="your_personal_access_token"
```

Get a Cloud API token at: https://id.atlassian.com/manage-profile/security/api-tokens

## Determine Which Workflow to Use

Before starting, check whether the user wants a **new export** or to **work with
existing exports** (e.g. generate summaries from previously exported content).

**Check for existing exports** by reading `.confluence-exports.json` in the
workspace root. This manifest file is automatically maintained by the export
script and records every export with its output location.

```bash
cat .confluence-exports.json
```

- If the manifest exists and contains entries, **show the user** what has been
  exported before. If they want summaries or further processing of an existing
  export, skip to **Workflow B**.
- If no manifest exists, or the user provides a new URL, proceed with **Workflow A**.

---

## Workflow A: Fresh Export

### Step 1: Get the Page URL or ID

Ask the user for the Confluence page URL. Accepted formats:
- `https://xxx.atlassian.net/wiki/spaces/SPACE/pages/PAGE_ID/title`
- A numeric page ID directly

### Step 2: Verify Environment

Confirm the auth environment variables are set:

```bash
echo "Username: $CONFLUENCE_USERNAME"
echo "Token set: $([ -n "$CONFLUENCE_API_TOKEN" ] && echo yes || echo no)"
```

On Windows PowerShell:
```powershell
echo "Username: $env:CONFLUENCE_USERNAME"
echo "Token set: $(if($env:CONFLUENCE_API_TOKEN){'yes'}else{'no'})"
```

### Step 3: Run the Export Script

```bash
python scripts/export_confluence.py --url "<PAGE_URL>"
```

Key flags:
- `--url <URL>`: Confluence page URL (auto-detects base URL and page ID)
- `--page-id <ID>` + `--base-url <URL>`: Alternative to `--url`
- `--output-dir <DIR>`: Override output location (default: `.confluence-exports/<page-id>/`)
- `--max-depth <N>`: Limit descendant depth (-1 = unlimited)
- `--dry-run`: Preview the page tree without fetching content
- `--list-only`: Output page list as JSON to stdout

**Recommended**: Run `--dry-run` first to verify the page tree before full export.

The script automatically:
- Writes output to `.confluence-exports/<page-id>/` in the workspace root
- Updates `.confluence-exports.json` manifest so future sessions can find the export

### Step 4: Generate AI Summary

After the script completes, it produces a basic `summary.md` with first-paragraph
excerpts. To create a higher-quality summary:

1. Read `raw.md` from the output directory
2. For each page section in raw.md, write a concise summary (2-4 sentences)
3. Preserve the page hierarchy in the summary
4. Write the enhanced summary to `summary.md`, overwriting the script's version

Summary format:

```markdown
# <Root Page Title> - Summary

## Page Hierarchy
- Root Page
  - Child Page A
    - Grandchild Page X
  - Child Page B

---

## Page Summaries

### Root Page
<2-4 sentence summary of the root page content>

### Child Page A
<2-4 sentence summary>

### Grandchild Page X
<2-4 sentence summary>
```

### Step 5: Report Results

Tell the user:
- How many pages were exported
- Where the output files are located
- Offer to read or further process specific sections

---

## Workflow B: Summarize Existing Export

Use this when the user asks to generate summaries from a previous export, or when
`.confluence-exports.json` shows an export already exists for the requested page.

### Step 1: Find the Export

Read `.confluence-exports.json` from the workspace root:

```bash
cat .confluence-exports.json
```

The manifest contains entries like:
```json
{
  "exports": [
    {
      "page_id": "12345678",
      "title": "Design",
      "output_dir": ".confluence-exports/12345678",
      "output_dir_absolute": "/full/path/.confluence-exports/12345678",
      "total_pages": 9,
      "url": "https://xxx.atlassian.net/wiki/spaces/ARCH/pages/12345678/Design",
      "exported_at": "2026-03-30T12:00:00Z"
    }
  ]
}
```

If multiple exports exist, ask the user which one to summarize.

### Step 2: Read raw.md

Read `raw.md` from the export directory found in Step 1. If the file is very
large, read it in sections (each page section is separated by `---`).

### Step 3: Generate AI Summary

For each page section in raw.md:
1. Write a concise summary (2-4 sentences) capturing key points
2. Preserve the page hierarchy in heading levels
3. Write the enhanced summary to `summary.md` in the same directory

### Step 4: Report Results

Tell the user what was summarized and where the file is located.

---

## Output Files

| File | Description |
|------|-------------|
| `raw.md` | Full content of all pages, headings reflect hierarchy depth |
| `summary.md` | Page tree + per-page summaries |
| `pages.json` | Structured list of all page IDs, titles, URLs, and depths |
| `.confluence-exports.json` | Workspace-level manifest of all exports (in workspace root) |

## Default Output Location

Exports go to `.confluence-exports/<page-id>/` relative to the workspace root.
The workspace root is auto-detected by looking for `.git`, `.cursor`, `.vscode`,
`package.json`, or `pyproject.toml` markers. Override with `--output-dir`.

## Troubleshooting

- **401 Unauthorized**: Check environment variables are set correctly
- **403 Forbidden**: User lacks permission to view the page/space
- **429 Rate Limited**: Script auto-retries; increase `--rate-limit` if persistent
- **Empty content**: Some pages may use macros that don't convert cleanly to Markdown
- **Server/DC v2 API not available**: Script auto-falls back to v1 API
- **Can't find previous export**: Check `.confluence-exports.json` in workspace root

For API reference details, see [references/confluence-api.md](references/confluence-api.md).
