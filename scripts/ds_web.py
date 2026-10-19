#!/usr/bin/env python3
"""Search the web and fetch page Markdown through DeepSeek's Search and Fetch APIs.

Both endpoints are read-only and Bearer-authenticated with a DeepSeek API key:

    POST {base}/v1/tools/search   query -> ranked results with summaries
    POST {base}/v1/tools/fetch    url   -> page title and Markdown body

Usage:
    python ds_web.py search "DeepSeek V4 模型 发布" --count 5
    python ds_web.py search 'DeepSeek site:github.com OR site:huggingface.co lang:zh after:2025'
    python ds_web.py search "release notes" --content --content-chars 2000 --ori-question "What changed?"
    python ds_web.py fetch https://example.com/article
    python ds_web.py fetch https://a.example https://b.example --max-chars 4000

The API key is read from --api-key, then $DEEPSEEK_API_KEY, then the nearest `.env`
walking up from the current directory. The base URL is read from --base-url, then
$DEEPSEEK_BASE_URL, and otherwise defaults to https://api.deepseek.com.

Exit codes: 0 success (including a search with zero results), 1 API or network
failure, 2 usage or configuration error. Retries after a timeout, a network error,
408, 429, or 5xx are automatic and honor Retry-After; other 4xx responses are not
retried because they require a different request.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

DEFAULT_BASE_URL = "https://api.deepseek.com"
SEARCH_PATH = "/v1/tools/search"
FETCH_PATH = "/v1/tools/fetch"
MAX_QUERY_CHARS = 128
MAX_URL_CHARS = 8192
MAX_RESULTS = 50
RETRY_AFTER_CAP_SECONDS = 30.0


class UsageError(Exception):
    """A request the API would reject, caught before spending a billed call."""


class ApiError(Exception):
    """A failed API request whose status and message are already user-readable."""


@dataclass(frozen=True)
class Settings:
    """Resolved endpoint credentials and transport policy for one CLI run."""

    api_key: str
    base_url: str
    timeout: float
    retries: int
    as_json: bool


def parse_env_file(path: Path) -> dict[str, str]:
    """Read simple KEY=VALUE lines, skipping comments and blank lines."""
    values: dict[str, str] = {}
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return values
    for line in text.splitlines():
        entry = line.strip()
        if not entry or entry.startswith("#") or "=" not in entry:
            continue
        name, _, raw = entry.partition("=")
        name = name.strip()
        if name.startswith("export "):
            name = name[len("export "):].strip()
        if name and name not in values:
            values[name] = raw.strip().strip("'\"")
    return values


def resolve_setting(name: str, explicit: str) -> str:
    """Resolve an explicit flag, then the environment, then the nearest `.env`."""
    if explicit:
        return explicit
    from_environment = os.environ.get(name)
    if from_environment:
        return from_environment
    start = Path.cwd()
    for directory in (start, *start.parents):
        candidate = directory / ".env"
        if candidate.is_file():
            value = parse_env_file(candidate).get(name)
            if value:
                return value
    return ""


def api_root(base_url: str) -> str:
    """Normalize a base URL to the host root, tolerating a trailing `/v1`."""
    root = base_url.strip().rstrip("/")
    if not root:
        return DEFAULT_BASE_URL
    if not root.startswith(("http://", "https://")):
        raise UsageError(f"--base-url must start with http:// or https://, got: {base_url}")
    if root.endswith("/v1"):
        root = root[: -len("/v1")]
    return root


def retry_delay(attempt: int, retry_after: str | None) -> float:
    """Backoff for one failed attempt: Retry-After when present, else 1s, 2s, 4s..."""
    if retry_after:
        try:
            seconds = float(retry_after)
        except ValueError:
            seconds = 0.0
        if seconds > 0:
            return min(seconds, RETRY_AFTER_CAP_SECONDS)
    return min(2.0 ** (attempt - 1), RETRY_AFTER_CAP_SECONDS)


def error_message(error: urllib.error.HTTPError) -> tuple[str, str | None]:
    """Decode an error body into `(message, Retry-After)`; bodies are OpenAI-shaped."""
    retry_after = error.headers.get("Retry-After") if error.headers else None
    try:
        raw = error.read().decode("utf-8", errors="replace")
    except OSError:
        # The body stream is already broken; the HTTP status and reason still explain the failure.
        return str(error.reason), retry_after
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        text = raw.strip()[:500]
        return (text or str(error.reason)), retry_after
    detail = parsed.get("error") if isinstance(parsed, dict) else None
    if isinstance(detail, dict):
        message = str(detail.get("message") or "").strip()
        code = detail.get("code")
        if code and str(code) not in message:
            return f"{message or 'request failed'} (code: {code})", retry_after
        return (message or str(error.reason)), retry_after
    text = raw.strip()[:500]
    return (text or str(error.reason)), retry_after


def is_retryable_status(status: int) -> bool:
    """Retry what the guide marks transient: 408, 429, and every 5xx."""
    return status in (408, 429) or 500 <= status <= 599


def post(path: str, payload: dict[str, object], settings: Settings) -> dict[str, object]:
    """Send one JSON POST and return its parsed response, retrying transient failures."""
    url = settings.base_url + path
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    headers = {
        "Authorization": f"Bearer {settings.api_key}",
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": "deepseek-web-search/1.0",
    }
    attempt = 0
    while True:
        attempt += 1
        request = urllib.request.Request(url, data=body, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=settings.timeout) as response:
                text = response.read().decode("utf-8")
            return json.loads(text)
        except urllib.error.HTTPError as error:
            message, retry_after = error_message(error)
            if is_retryable_status(error.code) and attempt <= settings.retries:
                time.sleep(retry_delay(attempt, retry_after))
                continue
            raise ApiError(f"HTTP {error.code} from {path}: {message}") from error
        except OSError as error:
            # HTTPError, and therefore this status branch, is checked above; here an
            # OSError is a transport failure such as a refused connection or a reset.
            if attempt <= settings.retries:
                time.sleep(retry_delay(attempt, None))
                continue
            reason = getattr(error, "reason", error)
            raise ApiError(f"network error calling {path}: {reason}") from error
        except ValueError as error:
            raise ApiError(f"unreadable response from {path}: {error}") from error


def truncate(text: str, limit: int) -> str:
    """Cap text for one context budget; a non-positive limit disables the cap."""
    if limit <= 0 or len(text) <= limit:
        return text
    return f"{text[:limit]} …[truncated {len(text) - limit} characters]"


def format_score(value: object) -> str:
    """Render rerank_score with two decimals so ordering differences stay visible."""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return f"{value:.2f}"
    return "?"


def format_date(value: object) -> str:
    """Render the RFC 3339 publication date as its calendar day."""
    text = str(value) if value else ""
    if not text:
        return "unknown"
    return text.split("T", 1)[0]


def build_search_payload(args: argparse.Namespace) -> dict[str, object]:
    """Validate CLI search input and map it onto the documented request fields."""
    query = args.query.strip()
    if not query:
        raise UsageError("search query must not be blank")
    if len(query) > MAX_QUERY_CHARS:
        raise UsageError(
            f"search query is {len(query)} characters and the API accepts at most {MAX_QUERY_CHARS}; "
            "shorten it and use site:/lang:/after:/before: operators to narrow the results",
        )
    if not 1 <= args.count <= MAX_RESULTS:
        raise UsageError(f"--count must be between 1 and {MAX_RESULTS}, got {args.count}")
    payload: dict[str, object] = {
        "query": query,
        "count": args.count,
        "need_content": bool(args.content),
    }
    original = (args.ori_question or "").strip()
    if original:
        if len(original) > MAX_QUERY_CHARS:
            raise UsageError(f"--ori-question is {len(original)} characters and the API accepts at most {MAX_QUERY_CHARS}")
        payload["ori_question"] = original
    localization = (args.localization or "").strip()
    if localization:
        payload["localization"] = localization
    return payload


def validate_url(url: str) -> str:
    """Reject URLs the API documents as invalid before spending a request."""
    target = url.strip()
    if len(target) > MAX_URL_CHARS:
        raise UsageError(f"URL is {len(target)} characters and the API accepts at most {MAX_URL_CHARS}")
    try:
        parts = urllib.parse.urlsplit(target)
    except ValueError as error:
        raise UsageError(f"not a parseable URL: {url}") from error
    if parts.scheme not in ("http", "https"):
        raise UsageError(f"URL must be HTTP or HTTPS: {url}")
    if not parts.hostname:
        raise UsageError(f"URL has no host: {url}")
    if parts.username or parts.password:
        raise UsageError(f"URL must not carry a username or password: {url}")
    return target


def render_search(payload: dict[str, object], response: dict[str, object], content_chars: int) -> str:
    """Render ranked results as the model-facing text view; summaries are the payload."""
    results = response.get("results")
    if not isinstance(results, list):
        results = []
    lines = [f"query: {payload['query']}", f"count: {payload['count']}", f"results: {len(results)}"]
    if not results:
        lines.append("no results matched; this request is not billed. Widen the query or drop restrictive operators.")
        return "\n".join(lines)
    for index, result in enumerate(results, start=1):
        if not isinstance(result, dict):
            continue
        lines.append("")
        lines.append(f"[{index}] {result.get('title') or '(untitled)'}")
        lines.append(f"    url: {result.get('url') or ''}")
        lines.append(
            "    score:"
            f" {format_score(result.get('rerank_score'))}"
            f" | authority: {result.get('authority', -1)}"
            f" | date: {format_date(result.get('date'))}"
            f" | site: {result.get('site_name') or 'unknown'}",
        )
        summary = str(result.get("summary") or result.get("snippet") or "").strip()
        if summary:
            lines.append(f"    summary: {summary}")
        content = str(result.get("content") or "").strip()
        if content:
            lines.append(f"    content: {truncate(content, content_chars)}")
    return "\n".join(lines)


def render_fetch(url: str, title: str, content: str, limit: int) -> str:
    """Render one fetched page as title plus Markdown body for model reading."""
    lines = [f"url: {url}"]
    if title:
        lines.append(f"title: {title}")
    lines.append(f"chars: {len(content)}")
    if not content.strip():
        lines.append("content: (empty; the page produced no extractable Markdown)")
        return "\n".join(lines)
    lines.append("content:")
    lines.append(truncate(content, limit))
    return "\n".join(lines)


def build_settings(args: argparse.Namespace) -> Settings:
    """Resolve credentials and transport options, failing before any billed request."""
    api_key = resolve_setting("DEEPSEEK_API_KEY", args.api_key).strip()
    if not api_key:
        raise UsageError(
            "no API key: pass --api-key, set DEEPSEEK_API_KEY, or add DEEPSEEK_API_KEY to .env "
            "(create one at https://platform.deepseek.com/api_keys)",
        )
    base_url = api_root(resolve_setting("DEEPSEEK_BASE_URL", args.base_url))
    if args.retries < 0:
        raise UsageError("--retries must be 0 or greater")
    if args.timeout <= 0:
        raise UsageError("--timeout must be greater than 0")
    return Settings(
        api_key=api_key,
        base_url=base_url,
        timeout=args.timeout,
        retries=args.retries,
        as_json=bool(args.json),
    )


def run_search(args: argparse.Namespace, settings: Settings) -> None:
    """Run one Search request and print the text view or raw JSON."""
    payload = build_search_payload(args)
    response = post(SEARCH_PATH, payload, settings)
    if settings.as_json:
        print(json.dumps(response, ensure_ascii=False, indent=2))
        return
    print(render_search(payload, response, args.content_chars))


def run_fetch(args: argparse.Namespace, settings: Settings) -> None:
    """Run one Fetch request per URL and print pages separated by blank lines."""
    targets = [validate_url(url) for url in args.urls]
    pages = []
    for target in targets:
        response = post(FETCH_PATH, {"url": target}, settings)
        pages.append(
            {
                "url": target,
                "title": str(response.get("title") or ""),
                "content": str(response.get("content") or ""),
            },
        )
    if settings.as_json:
        print(json.dumps(pages[0] if len(pages) == 1 else pages, ensure_ascii=False, indent=2))
        return
    blocks = [render_fetch(page["url"], page["title"], page["content"], args.max_chars) for page in pages]
    print("\n\n".join(blocks))


def build_parser() -> argparse.ArgumentParser:
    """Build the CLI; `search` and `fetch` share transport flags."""
    parser = argparse.ArgumentParser(
        prog="ds_web.py",
        description="Search the web and fetch page Markdown through the DeepSeek Search and Fetch APIs.",
    )
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--api-key", default="", help="DeepSeek API key; defaults to $DEEPSEEK_API_KEY or the nearest .env")
    common.add_argument(
        "--base-url",
        default="",
        help=f"API base URL; defaults to $DEEPSEEK_BASE_URL or {DEFAULT_BASE_URL}",
    )
    common.add_argument("--timeout", type=float, default=30.0, help="per-request timeout in seconds (default: 30)")
    common.add_argument("--retries", type=int, default=3, help="retries after a timeout, network error, 408, 429, or 5xx (default: 3)")
    common.add_argument("--json", action="store_true", help="print the API JSON payload instead of the text view")

    subparsers = parser.add_subparsers(dest="command", required=True)

    search = subparsers.add_parser("search", parents=[common], help="search the web and print ranked results")
    search.add_argument("query", help='query text; supports site:, -site:, -word, -"phrase", "phrase", lang:, after:, before:')
    search.add_argument("--count", type=int, default=10, help="results to return, 1-50 (default: 10)")
    search.add_argument("--content", action="store_true", help="include page bodies in this response")
    search.add_argument("--ori-question", default="", help="the user's original question, 1-128 characters")
    search.add_argument("--localization", default="", help="IP address used to prefer localized results")
    search.add_argument("--content-chars", type=int, default=3000, help="text-mode cap per page body (default: 3000; 0 disables)")

    fetch = subparsers.add_parser("fetch", parents=[common], help="fetch one or more pages as Markdown")
    fetch.add_argument("urls", nargs="+", help="HTTP/HTTPS URLs, one API request each")
    fetch.add_argument("--max-chars", type=int, default=20000, help="text-mode cap per page (default: 20000; 0 disables)")

    return parser


def main(argv: list[str] | None = None) -> int:
    """Parse arguments, execute one command, and map failures onto exit codes."""
    args = build_parser().parse_args(argv)
    try:
        settings = build_settings(args)
        if args.command == "search":
            run_search(args, settings)
        else:
            run_fetch(args, settings)
    except UsageError as error:
        print(f"deepseek-web-search: {error}", file=sys.stderr)
        return 2
    except ApiError as error:
        print(f"deepseek-web-search: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    # Windows consoles default to a legacy code page; search text is usually UTF-8.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
