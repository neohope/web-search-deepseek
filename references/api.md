# Search and Fetch API reference

Source: [Search and Fetch guide](https://api-docs.deepseek.com/zh-cn/guides/search), [Search API](https://api-docs.deepseek.com/zh-cn/api/search), [Fetch API](https://api-docs.deepseek.com/zh-cn/api/fetch). Read the guide pages again before relying on limits or prices; this page records the schema the CLI depends on.

## Endpoints and authentication

| API | Endpoint |
|---|---|
| Search | `POST https://api.deepseek.com/v1/tools/search` |
| Fetch | `POST https://api.deepseek.com/v1/tools/fetch` |

Both require `Authorization: Bearer <DEEPSEEK_API_KEY>` and `Content-Type: application/json`. They are read-only: no state is created by either call. Unknown request fields are ignored.

## Search request

| Field | Type | Required | Default | Notes |
|---|---|---|---|---|
| `query` | string | yes | — | 1–128 Unicode characters, not all whitespace; supports the `site:`, `-site:`, `-`, `"..."`, `lang:`, `after:`, and `before:` syntax documented in [the skill](../SKILL.md) |
| `count` | integer | no | `10` | 1–50 results |
| `ori_question` | string, nullable | no | — | The user's original question, 1–128 characters; `null` means absent |
| `localization` | string | no | `""` | IP address used to prefer localized results |
| `need_content` | boolean | no | `false` | `true` fills `content` with the page body; `false` leaves it empty |

## Search response

HTTP 200 with `results`, an array that is empty when nothing matched.

| Field | Type | Notes |
|---|---|---|
| `rerank_score` | number | 0–3; at least 1.5 marks a high-quality result |
| `title` | string | Result title |
| `url` | string | Result link |
| `snippet` | string | Short summary, usually 100–200 characters, meant for display |
| `summary` | string | Long summary, usually 600–800 characters, meant for a model to read |
| `content` | string | Page body; empty when `need_content` is `false` or the body is missing |
| `site_name` | string, nullable | `null` when unknown |
| `site_icon` | string, nullable | Favicon URL, `null` when unknown |
| `date` | string, nullable | Publication time as UTC RFC 3339, `null` when unknown |
| `authority` | integer | 0–4, `-1` unknown; at least 3 is highly authoritative |

## Fetch request

| Field | Type | Required | Notes |
|---|---|---|---|
| `url` | string | yes | At most 8192 characters; HTTP/HTTPS with a host; no username or password |

## Fetch response

| Field | Type | Notes |
|---|---|---|
| `title` | string | Page title; empty string when missing |
| `content` | string | Page body converted to Markdown |

Results depend on the target site's availability and access policy, so the same URL can succeed today and fail later.

## Errors

Errors use the OpenAI-compatible envelope with `param` always `null`:

```json
{"error": {"message": "'count' must be between 1 and 50", "type": "invalid_request_error", "param": null, "code": "invalid_request_error"}}
```

| Status | Meaning | Retry |
|---|---|---|
| 400 | Malformed headers or body, including invalid JSON or field values | No |
| 401 | Missing or invalid API key | No |
| 402 | Insufficient account balance | No, top up first |
| 403 | Fetch refused by security policy | No |
| 404 | Fetch found no usable page content: unreachable URL, blocked by site policy, target error, unsupported content type, or no extractable Markdown | No |
| 408 | Reading the request body timed out | Yes |
| 413 | Request body over the size limit | No |
| 429 | QPS limit exceeded | Yes |
| 500 | Server error | Yes |
| 504 | Downstream timeout | Yes |

## Billing and rate limits

- Search and Fetch bill per request, not per token or per returned result: ¥5 per 1,000 successful requests each.
- Only an HTTP 200 that yields output is billed — Search with a non-empty `results`, Fetch with a page body. Failed requests, empty results, and timeouts are free.
- `count` changes how many results come back, not the price.
- Server-side Search/Fetch calls made through DeepSeek Harness, Claude Code, or Codex are unbilled until 2026-11-01 00:00 Beijing time.
- Per account, Search allows 100 QPS and Fetch 25 QPS, limited independently of each other and of model concurrency. Fetch additionally limits requests per target domain.
