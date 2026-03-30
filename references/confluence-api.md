# Confluence REST API Quick Reference

## Authentication

### Cloud (xxx.atlassian.net)

HTTP Basic Auth with `email:api_token` (base64 encoded):

```
Authorization: Basic <base64(email:api_token)>
```

### Server / Data Center

Bearer token with Personal Access Token:

```
Authorization: Bearer <personal_access_token>
```

## REST API v2 Endpoints (Cloud)

Base path: `/wiki/api/v2`

### Get Page by ID

```
GET /wiki/api/v2/pages/{id}?body-format=storage
```

Query params:
- `body-format`: `storage` (HTML-like) | `atlas_doc_format` (ADF JSON)
- `include-labels`: boolean
- `include-versions`: boolean

Response includes `body.storage.value` with HTML content.

### Get Page Descendants

```
GET /wiki/api/v2/pages/{id}/descendants
```

Query params:
- `limit`: int (default 25, max 250)
- `depth`: int (limit depth of descendants)
- `cursor`: string (pagination cursor)

Response (flat list, top-to-bottom order):

```json
{
  "results": [
    {
      "id": "12345",
      "status": "current",
      "title": "Page Title",
      "type": "page",
      "parentId": "67890",
      "depth": 1,
      "childPosition": 0
    }
  ],
  "_links": {
    "next": "/wiki/api/v2/pages/{id}/descendants?cursor=xxx",
    "base": "https://xxx.atlassian.net"
  }
}
```

### Get Child Pages

```
GET /wiki/api/v2/pages/{id}/children
```

Query params:
- `limit`: int
- `cursor`: string
- `sort`: `created-date` | `-created-date` | `id` | `-id` | `title` | `-title`

Returns only direct child **pages** (not other types).

## REST API v1 Endpoints (Server/DC Fallback)

Base path: `/rest/api`

### Get Page Content

```
GET /rest/api/content/{id}?expand=body.storage,ancestors
```

### Get Child Pages

```
GET /rest/api/content/{id}/child/page?limit=50&expand=body.storage
```

### Search with CQL

```
GET /rest/api/content/search?cql=ancestor={pageId}&limit=50
```

## Pagination

v2 uses **cursor-based** pagination:
1. Send initial request with `limit`
2. Check `_links.next` in response
3. Extract `cursor` param from next URL
4. Repeat until no `next` link

v1 uses **offset-based** pagination:
1. Send initial request with `limit` and `start=0`
2. Check `size` and `totalSize` in response
3. Increment `start` by `size`
4. Repeat until `start >= totalSize`

## Rate Limiting

- Cloud: ~100 requests/minute per user
- Server/DC: Depends on configuration
- 429 response includes `Retry-After` header (seconds)

## Confluence Storage Format

Page bodies in `storage` format use HTML with Confluence-specific XML macros:

| Element | Description |
|---------|-------------|
| `<ac:structured-macro ac:name="code">` | Code block |
| `<ac:structured-macro ac:name="info">` | Info panel |
| `<ac:structured-macro ac:name="warning">` | Warning panel |
| `<ac:structured-macro ac:name="note">` | Note panel |
| `<ac:emoticon>` | Emoji |
| `<ac:image>` | Image embed |
| `<ri:attachment>` | Attachment reference |

The export script strips these macros and converts standard HTML to Markdown
using `markdownify`.
