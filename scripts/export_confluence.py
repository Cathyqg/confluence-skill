#!/usr/bin/env python3
"""
Confluence Page Recursive Exporter

Recursively exports a Confluence page and all its descendants to Markdown,
preserving the page hierarchy. Outputs raw.md (full content) and a basic
summary.md (page tree + first-paragraph excerpts).

Supports both Confluence Cloud (REST API v2) and Server/Data Center (v1 fallback).

Authentication via environment variables:
  Cloud:     CONFLUENCE_USERNAME + CONFLUENCE_API_TOKEN
  Server/DC: CONFLUENCE_PERSONAL_TOKEN
"""

import argparse
import json
import os
import re
import sys
import time
import urllib.parse
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

try:
    import requests
except ImportError:
    print("ERROR: 'requests' package is required. Install with: pip install requests", file=sys.stderr)
    sys.exit(1)

try:
    from markdownify import markdownify as md
except ImportError:
    print("ERROR: 'markdownify' package is required. Install with: pip install markdownify", file=sys.stderr)
    sys.exit(1)


@dataclass
class PageNode:
    """Represents a single Confluence page in the hierarchy tree."""
    id: str
    title: str
    depth: int
    parent_id: Optional[str] = None
    body_html: str = ""
    body_markdown: str = ""
    url: str = ""
    children: list = field(default_factory=list)


class ConfluenceExporter:
    """Handles Confluence API interaction and page export."""

    def __init__(self, base_url: str, username: str = "", api_token: str = "",
                 personal_token: str = "", max_depth: int = -1,
                 rate_limit_delay: float = 0.2, force_server: bool = False,
                 insecure: bool = False):
        self.base_url = base_url.rstrip("/")
        self.username = username
        self.api_token = api_token
        self.personal_token = personal_token
        self.max_depth = max_depth
        self.rate_limit_delay = rate_limit_delay
        self.session = requests.Session()
        self.session.headers.update({"Accept": "application/json"})
        if insecure:
            self.session.verify = False
            import urllib3
            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        self.is_cloud = self._detect_cloud(force_server)
        self._setup_auth()

    def _detect_cloud(self, force_server: bool = False) -> bool:
        if force_server:
            return False
        if "atlassian.net" in self.base_url:
            return True
        if self.personal_token and not self.api_token:
            return False
        if self.api_token and not "atlassian.net" in self.base_url:
            return self._probe_v2_api()
        return False

    def _probe_v2_api(self) -> bool:
        """Probe whether v2 API is available (Cloud) by checking a lightweight endpoint."""
        try:
            url = f"{self.base_url}/wiki/api/v2/spaces?limit=1"
            resp = self.session.get(url, timeout=5)
            return resp.status_code == 200
        except Exception:
            return False

    def _setup_auth(self):
        if self.personal_token:
            self.session.headers["Authorization"] = f"Bearer {self.personal_token}"
        elif self.username and self.api_token:
            self.session.auth = (self.username, self.api_token)
        else:
            print("WARNING: No authentication configured. Requests may fail.", file=sys.stderr)

    def _api_get(self, path: str, params: Optional[dict] = None) -> dict:
        """Make a GET request to the Confluence API with retry logic."""
        url = f"{self.base_url}{path}"
        for attempt in range(3):
            try:
                resp = self.session.get(url, params=params, timeout=30)
                if resp.status_code == 429:
                    retry_after = int(resp.headers.get("Retry-After", 5))
                    print(f"  Rate limited, waiting {retry_after}s...", file=sys.stderr)
                    time.sleep(retry_after)
                    continue
                resp.raise_for_status()
                return resp.json()
            except requests.exceptions.HTTPError as e:
                if attempt < 2 and resp.status_code >= 500:
                    time.sleep(2 ** attempt)
                    continue
                raise
        return {}

    def _paginated_get(self, path: str, params: Optional[dict] = None) -> list:
        """Fetch all results from a paginated endpoint (v1 offset or v2 cursor)."""
        results = []
        params = params or {}
        params.setdefault("limit", 50)
        while True:
            data = self._api_get(path, params)
            batch = data.get("results", [])
            results.extend(batch)

            next_link = data.get("_links", {}).get("next")
            if next_link:
                parsed = urllib.parse.urlparse(next_link)
                query_params = urllib.parse.parse_qs(parsed.query)
                cursor = query_params.get("cursor", [None])[0]
                if cursor:
                    params["cursor"] = cursor
                    time.sleep(self.rate_limit_delay)
                    continue

            total_size = data.get("totalSize") or data.get("size")
            start = data.get("start")
            if start is not None and total_size is not None:
                next_start = start + len(batch)
                if next_start < total_size and len(batch) > 0:
                    params["start"] = next_start
                    time.sleep(self.rate_limit_delay)
                    continue

            break
        return results

    @staticmethod
    def parse_page_id_from_url(url: str) -> Optional[str]:
        """
        Extract page ID from various Confluence URL formats:
        - https://xxx.atlassian.net/wiki/spaces/SPACE/pages/PAGE_ID/title
        - https://xxx.atlassian.net/wiki/spaces/SPACE/pages/PAGE_ID
        - https://server/display/SPACE/Title (Server/DC - needs API lookup)
        """
        patterns = [
            r"/pages/(\d+)",
            r"/pageId=(\d+)",
            r"[?&]pageId=(\d+)",
        ]
        for pattern in patterns:
            match = re.search(pattern, url)
            if match:
                return match.group(1)
        return None

    def get_page(self, page_id: str, body_format: str = "storage") -> dict:
        """Fetch a single page by ID with its body content."""
        if self.is_cloud:
            return self._api_get(
                f"/wiki/api/v2/pages/{page_id}",
                params={"body-format": body_format}
            )
        else:
            return self._api_get(
                f"/rest/api/content/{page_id}",
                params={"expand": f"body.{body_format},ancestors"}
            )

    def get_descendants_v2(self, page_id: str) -> list:
        """Fetch all descendants using REST API v2 (Cloud)."""
        return self._paginated_get(
            f"/wiki/api/v2/pages/{page_id}/descendants",
            params={"limit": 250}
        )

    def get_children_v1(self, page_id: str) -> list:
        """Fetch direct child pages using REST API v1 (Server/DC fallback)."""
        return self._paginated_get(
            f"/rest/api/content/{page_id}/child/page",
            params={"limit": 50, "expand": "body.storage"}
        )

    def _build_tree_v2(self, root_page: dict, descendants: list) -> PageNode:
        """Build a page tree from v2 descendants response."""
        root = PageNode(
            id=str(root_page.get("id", "")),
            title=root_page.get("title", "Untitled"),
            depth=0,
            body_html=self._extract_body(root_page),
            url=self._build_page_url(root_page),
        )

        nodes_by_id = {root.id: root}

        sorted_descendants = sorted(descendants, key=lambda d: d.get("depth", 0))

        for desc in sorted_descendants:
            if desc.get("type") != "page":
                continue
            desc_id = str(desc.get("id", ""))
            depth = desc.get("depth", 1)
            parent_id = str(desc.get("parentId", root.id))

            if self.max_depth >= 0 and depth > self.max_depth:
                continue

            node = PageNode(
                id=desc_id,
                title=desc.get("title", "Untitled"),
                depth=depth,
                parent_id=parent_id,
            )
            nodes_by_id[desc_id] = node

            parent = nodes_by_id.get(parent_id)
            if parent:
                parent.children.append(node)
            else:
                root.children.append(node)

        return root

    def _build_tree_v1_recursive(self, page_id: str, depth: int = 0,
                                  prefetched_page: Optional[dict] = None) -> PageNode:
        """Build a page tree recursively using v1 API (Server/DC)."""
        page = prefetched_page or self.get_page(page_id)
        node = PageNode(
            id=str(page.get("id", "")),
            title=page.get("title", "Untitled"),
            depth=depth,
            body_html=self._extract_body(page),
            url=self._build_page_url(page),
        )

        if self.max_depth >= 0 and depth >= self.max_depth:
            return node

        children = self.get_children_v1(page_id)
        time.sleep(self.rate_limit_delay)

        for child in children:
            child_node = self._build_tree_v1_recursive(
                str(child["id"]), depth + 1, prefetched_page=child
            )
            child_node.parent_id = node.id
            node.children.append(child_node)

        return node

    def _extract_body(self, page_data: dict) -> str:
        """Extract HTML body from page API response."""
        body = page_data.get("body", {})
        if "storage" in body:
            storage = body["storage"]
            if isinstance(storage, dict):
                return storage.get("value", "")
            return str(storage)
        return ""

    def _build_page_url(self, page_data: dict) -> str:
        """Build a web URL for the page."""
        links = page_data.get("_links", {})
        webui = links.get("webui", "")
        base = links.get("base", self.base_url)
        if webui:
            return f"{base}{webui}"
        page_id = page_data.get("id", "")
        return f"{self.base_url}/pages/{page_id}"

    def _fetch_bodies_for_tree(self, root: PageNode):
        """Fetch body content for all nodes that don't have it yet."""
        nodes = self._flatten_tree(root)
        total = len([n for n in nodes if not n.body_html])
        if total == 0:
            return

        print(f"  Fetching content for {total} pages...", file=sys.stderr)
        count = 0
        for node in nodes:
            if node.body_html:
                continue
            count += 1
            print(f"  [{count}/{total}] {node.title}", file=sys.stderr)
            try:
                page = self.get_page(node.id)
                node.body_html = self._extract_body(page)
                node.url = self._build_page_url(page)
            except Exception as e:
                print(f"  WARNING: Failed to fetch page {node.id}: {e}", file=sys.stderr)
            time.sleep(self.rate_limit_delay)

    @staticmethod
    def _flatten_tree(root: PageNode) -> list:
        """Flatten the tree into a list in depth-first order."""
        result = []
        stack = [root]
        while stack:
            node = stack.pop()
            result.append(node)
            stack.extend(reversed(node.children))
        return result

    @staticmethod
    def _html_to_markdown(html: str) -> str:
        """Convert Confluence storage HTML to Markdown."""
        if not html or not html.strip():
            return ""

        code_blocks = []

        def _replace_code_block(match):
            full = match.group(0)
            lang_match = re.search(r'ac:parameter\s+ac:name="language"[^>]*>([^<]+)<', full)
            lang = lang_match.group(1).strip() if lang_match else ""
            body_match = re.search(r'<ac:plain-text-body>\s*<!\[CDATA\[(.*?)\]\]>\s*</ac:plain-text-body>', full, re.DOTALL)
            if not body_match:
                body_match = re.search(r'<ac:plain-text-body>(.*?)</ac:plain-text-body>', full, re.DOTALL)
            body = body_match.group(1) if body_match else ""
            marker = f"CFLCODEBLK{len(code_blocks)}XEND"
            code_blocks.append(f"```{lang}\n{body}\n```")
            return f"<p>{marker}</p>"

        cleaned = re.sub(
            r'<ac:structured-macro[^>]*ac:name="(code|noformat)"[^>]*>.*?</ac:structured-macro>',
            _replace_code_block,
            html,
            flags=re.DOTALL
        )

        cleaned = re.sub(r'<ac:emoticon[^/]*/>', '', cleaned)
        cleaned = re.sub(
            r'<ac:structured-macro[^>]*ac:name="info"[^>]*>.*?<ac:rich-text-body>(.*?)</ac:rich-text-body>.*?</ac:structured-macro>',
            r'> \1',
            cleaned,
            flags=re.DOTALL
        )
        cleaned = re.sub(
            r'<ac:structured-macro[^>]*ac:name="warning"[^>]*>.*?<ac:rich-text-body>(.*?)</ac:rich-text-body>.*?</ac:structured-macro>',
            r'> **Warning:** \1',
            cleaned,
            flags=re.DOTALL
        )
        cleaned = re.sub(
            r'<ac:structured-macro[^>]*ac:name="note"[^>]*>.*?<ac:rich-text-body>(.*?)</ac:rich-text-body>.*?</ac:structured-macro>',
            r'> **Note:** \1',
            cleaned,
            flags=re.DOTALL
        )
        cleaned = re.sub(r'<ac:structured-macro[^>]*>.*?</ac:structured-macro>', '', cleaned, flags=re.DOTALL)
        cleaned = re.sub(r'<ac:[^>]*/?>', '', cleaned)
        cleaned = re.sub(r'</ac:[^>]*>', '', cleaned)
        cleaned = re.sub(r'<ri:[^>]*/?>', '', cleaned)
        cleaned = re.sub(r'</ri:[^>]*>', '', cleaned)

        result = md(cleaned, heading_style="ATX", bullets="-", strip=["img"])

        for i, block in enumerate(code_blocks):
            marker = f"CFLCODEBLK{i}XEND"
            result = result.replace(marker, f"\n{block}\n")

        result = re.sub(r'\n{3,}', '\n\n', result)
        return result.strip()

    def build_tree(self, page_id: str, fetch_bodies: bool = True) -> PageNode:
        """
        Build the page tree. When fetch_bodies=True, also fetch and convert
        page content to Markdown.
        """
        print(f"Fetching root page {page_id}...", file=sys.stderr)
        root_page = self.get_page(page_id)
        root_title = root_page.get("title", "Untitled")
        print(f"Root page: {root_title}", file=sys.stderr)

        if self.is_cloud:
            print("Using REST API v2 (Cloud)...", file=sys.stderr)
            print("Fetching descendants...", file=sys.stderr)
            descendants = self.get_descendants_v2(page_id)
            print(f"Found {len(descendants)} descendants.", file=sys.stderr)
            tree = self._build_tree_v2(root_page, descendants)
            if fetch_bodies:
                self._fetch_bodies_for_tree(tree)
        else:
            print("Using REST API v1 (Server/DC)...", file=sys.stderr)
            tree = self._build_tree_v1_recursive(page_id)

        if fetch_bodies:
            print("Converting HTML to Markdown...", file=sys.stderr)
            for node in self._flatten_tree(tree):
                node.body_markdown = self._html_to_markdown(node.body_html)
                node.body_html = ""

        return tree


def generate_raw_md(root: PageNode) -> str:
    """Generate raw.md with full content preserving hierarchy."""
    lines = []

    def _write_node(node: PageNode, heading_level: int):
        prefix = "#" * min(heading_level, 6)
        lines.append(f"{prefix} {node.title}")
        lines.append("")

        if node.url:
            lines.append(f"> Source: {node.url}")
            lines.append("")

        if node.body_markdown:
            content = node.body_markdown
            if heading_level > 1:
                content = _shift_headings(content, heading_level)
            lines.append(content)
            lines.append("")

        lines.append("---")
        lines.append("")

        for child in node.children:
            _write_node(child, heading_level + 1)

    _write_node(root, 1)
    return "\n".join(lines)


def _shift_headings(text: str, base_level: int) -> str:
    """
    Shift markdown headings in content so they are nested under the page's
    heading level. e.g. if base_level=3, an H1 in content becomes H4.
    """
    def replace_heading(match):
        hashes = match.group(1)
        title = match.group(2)
        new_level = min(len(hashes) + base_level, 6)
        return f"{'#' * new_level} {title}"

    return re.sub(r'^(#{1,6})\s+(.+)$', replace_heading, text, flags=re.MULTILINE)


def generate_summary_md(root: PageNode) -> str:
    """Generate summary.md with page tree and first-paragraph excerpts."""
    lines = []
    lines.append(f"# {root.title} - Summary")
    lines.append("")

    lines.append("## Page Hierarchy")
    lines.append("")

    def _write_tree(node: PageNode, indent: int):
        prefix = "  " * indent
        lines.append(f"{prefix}- {node.title}")
        for child in node.children:
            _write_tree(child, indent + 1)

    _write_tree(root, 0)
    lines.append("")

    lines.append("---")
    lines.append("")
    lines.append("## Page Summaries")
    lines.append("")

    all_nodes = ConfluenceExporter._flatten_tree(root)
    for node in all_nodes:
        heading_level = min(node.depth + 3, 6)
        prefix = "#" * heading_level
        lines.append(f"{prefix} {node.title}")
        lines.append("")

        excerpt = _extract_first_paragraph(node.body_markdown)
        if excerpt:
            lines.append(excerpt)
        else:
            lines.append("*(No content)*")
        lines.append("")

    return "\n".join(lines)


def _extract_first_paragraph(markdown_text: str) -> str:
    """Extract the first meaningful text from markdown content."""
    if not markdown_text:
        return ""
    stripped = re.sub(r'^#{1,6}\s+.*$', '', markdown_text, flags=re.MULTILINE)
    stripped = re.sub(r'^>\s+.*$', '', stripped, flags=re.MULTILINE)
    stripped = re.sub(r'^```.*?^```', '', stripped, flags=re.MULTILINE | re.DOTALL)
    paragraphs = re.split(r'\n\s*\n', stripped.strip())
    for para in paragraphs:
        para = para.strip()
        if para and len(para) > 20:
            if len(para) > 500:
                para = para[:497] + "..."
            return para
    return ""


def generate_page_list_json(root: PageNode) -> str:
    """Generate a JSON list of all pages with their IDs, titles, and URLs."""
    all_nodes = ConfluenceExporter._flatten_tree(root)
    pages = []
    for node in all_nodes:
        pages.append({
            "id": node.id,
            "title": node.title,
            "depth": node.depth,
            "parent_id": node.parent_id,
            "url": node.url,
        })
    return json.dumps(pages, indent=2, ensure_ascii=False)


MANIFEST_FILENAME = ".confluence-exports.json"


def _find_workspace_root() -> Path:
    """Walk up from CWD looking for common workspace markers, fall back to CWD."""
    cwd = Path.cwd()
    markers = [".git", ".cursor", ".vscode", "package.json", "pyproject.toml"]
    current = cwd
    while current != current.parent:
        if any((current / m).exists() for m in markers):
            return current
        current = current.parent
    return cwd


def _load_manifest(workspace: Path) -> dict:
    manifest_path = workspace / MANIFEST_FILENAME
    if manifest_path.exists():
        try:
            return json.loads(manifest_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return {"exports": []}


def _save_manifest(workspace: Path, manifest: dict):
    manifest_path = workspace / MANIFEST_FILENAME
    manifest_path.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def _update_manifest(workspace: Path, page_id: str, title: str,
                     output_dir: str, total_pages: int, url: str = ""):
    """Add or update an export entry in the workspace manifest."""
    manifest = _load_manifest(workspace)
    try:
        rel_path = os.path.relpath(output_dir, workspace)
    except ValueError:
        rel_path = output_dir
    abs_path = str(Path(output_dir).resolve())

    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    entries = manifest.get("exports", [])
    existing = next((e for e in entries if e.get("page_id") == page_id), None)
    if existing:
        existing.update({
            "title": title,
            "output_dir": rel_path,
            "output_dir_absolute": abs_path,
            "total_pages": total_pages,
            "url": url or existing.get("url", ""),
            "exported_at": now,
        })
    else:
        entries.append({
            "page_id": page_id,
            "title": title,
            "output_dir": rel_path,
            "output_dir_absolute": abs_path,
            "total_pages": total_pages,
            "url": url,
            "exported_at": now,
        })
    manifest["exports"] = entries
    _save_manifest(workspace, manifest)


def _resolve_output_dir(page_id: str, explicit_dir: Optional[str]) -> str:
    """
    Determine the output directory. Priority:
    1. Explicitly provided --output-dir
    2. .confluence-exports/<page_id>/ relative to workspace root
    """
    if explicit_dir:
        return explicit_dir
    workspace = _find_workspace_root()
    return str(workspace / ".confluence-exports" / page_id)


def main():
    parser = argparse.ArgumentParser(
        description="Export Confluence pages recursively to Markdown"
    )
    parser.add_argument("--url", help="Confluence page URL")
    parser.add_argument("--page-id", help="Confluence page ID (alternative to --url)")
    parser.add_argument("--base-url", help="Confluence base URL (e.g. https://xxx.atlassian.net/wiki)")
    parser.add_argument("--output-dir", default=None,
                        help="Output directory (default: .confluence-exports/<page-id>/)")
    parser.add_argument("--max-depth", type=int, default=-1,
                        help="Max depth of descendants to fetch (-1 for unlimited)")
    parser.add_argument("--rate-limit", type=float, default=0.2,
                        help="Delay between API requests in seconds (default: 0.2)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Only fetch page tree structure without content")
    parser.add_argument("--list-only", action="store_true",
                        help="Only output the page list as JSON (no content fetch)")
    parser.add_argument("--force-server", action="store_true",
                        help="Force Server/DC mode (use REST API v1 even for Cloud URLs)")
    parser.add_argument("--insecure", action="store_true",
                        help="Disable SSL certificate verification (for self-signed certs)")

    args = parser.parse_args()

    page_id = args.page_id
    base_url = args.base_url

    if args.url:
        page_id = ConfluenceExporter.parse_page_id_from_url(args.url)
        if not page_id:
            print(f"ERROR: Could not extract page ID from URL: {args.url}", file=sys.stderr)
            sys.exit(1)
        if not base_url:
            parsed = urllib.parse.urlparse(args.url)
            path_parts = parsed.path.split("/")
            wiki_index = -1
            for i, part in enumerate(path_parts):
                if part == "wiki":
                    wiki_index = i
                    break
            if wiki_index >= 0:
                base_url = f"{parsed.scheme}://{parsed.netloc}/{'/'.join(path_parts[1:wiki_index+1])}"
            else:
                base_url = f"{parsed.scheme}://{parsed.netloc}"

    if not page_id:
        print("ERROR: Either --url or --page-id is required.", file=sys.stderr)
        sys.exit(1)

    if not base_url:
        base_url = os.environ.get("CONFLUENCE_URL", "")
        if not base_url:
            print("ERROR: --base-url is required (or set CONFLUENCE_URL env var).", file=sys.stderr)
            sys.exit(1)

    username = os.environ.get("CONFLUENCE_USERNAME", "")
    api_token = os.environ.get("CONFLUENCE_API_TOKEN", "")
    personal_token = os.environ.get("CONFLUENCE_PERSONAL_TOKEN", "")

    if not personal_token and not (username and api_token):
        print("ERROR: Set CONFLUENCE_USERNAME + CONFLUENCE_API_TOKEN (Cloud) "
              "or CONFLUENCE_PERSONAL_TOKEN (Server/DC).", file=sys.stderr)
        sys.exit(1)

    exporter = ConfluenceExporter(
        base_url=base_url,
        username=username,
        api_token=api_token,
        personal_token=personal_token,
        max_depth=args.max_depth,
        rate_limit_delay=args.rate_limit,
        force_server=args.force_server,
        insecure=args.insecure,
    )

    if args.list_only:
        tree = exporter.build_tree(page_id, fetch_bodies=False)
        print(generate_page_list_json(tree))
        return

    if args.dry_run:
        tree = exporter.build_tree(page_id, fetch_bodies=False)

        def _print_tree(node, indent=0):
            print(f"{'  ' * indent}- [{node.id}] {node.title}")
            for child in node.children:
                _print_tree(child, indent + 1)

        print("\nPage tree:")
        _print_tree(tree)
        print(f"\nTotal pages: {len(exporter._flatten_tree(tree))}")
        return

    tree = exporter.build_tree(page_id, fetch_bodies=True)

    output_dir = _resolve_output_dir(page_id, args.output_dir)
    os.makedirs(output_dir, exist_ok=True)

    raw_path = os.path.join(output_dir, "raw.md")
    raw_content = generate_raw_md(tree)
    with open(raw_path, "w", encoding="utf-8") as f:
        f.write(raw_content)
    print(f"\nraw.md written to: {raw_path}", file=sys.stderr)

    summary_path = os.path.join(output_dir, "summary.md")
    summary_content = generate_summary_md(tree)
    with open(summary_path, "w", encoding="utf-8") as f:
        f.write(summary_content)
    print(f"summary.md written to: {summary_path}", file=sys.stderr)

    pages_path = os.path.join(output_dir, "pages.json")
    pages_json = generate_page_list_json(tree)
    with open(pages_path, "w", encoding="utf-8") as f:
        f.write(pages_json)
    print(f"pages.json written to: {pages_path}", file=sys.stderr)

    total = len(exporter._flatten_tree(tree))

    workspace = _find_workspace_root()
    source_url = args.url or ""
    _update_manifest(workspace, page_id, tree.title, output_dir, total, source_url)
    print(f"Manifest updated: {workspace / MANIFEST_FILENAME}", file=sys.stderr)

    print(f"\nDone! Exported {total} pages to: {output_dir}", file=sys.stderr)


if __name__ == "__main__":
    main()
