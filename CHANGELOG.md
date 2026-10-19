# Changelog

## 1.1.0 — 2026-10-19

Host-agnostic packaging and repository files.

### Compatibility

- Documented how to locate the script and inject the key in DSH, OpenClaw, Hermes, and any other host that loads `SKILL.md`, including `python` versus `python3` and OpenClaw's `{baseDir}`.
- The script now also reads `DEEPSEEK_API_KEY` / `DEEPSEEK_BASE_URL` from the skill's own `.env`, after the project chain, so a skill installed outside the project root still finds its configuration.

### Docs

- Both READMEs gained a supported-hosts table, the `.env.example` quick start, and a warning against pasting keys into chat: DSH session logs are replayable, and other hosts may persist conversations.

### Repository

- Added `.env.example`, `CHANGELOG.md`, `.gitattributes`, and `agents/openai.yaml`.
- Added a test asserting the skill directory is the last `.env` candidate.

## 1.0.0 — first published version

- `search` and `fetch` subcommands over `POST /v1/tools/search` and `POST /v1/tools/fetch`.
- Text and `--json` output, `--content` / `--max-chars` / `--content-chars` truncation with explicit markers.
- Automatic retries for timeouts, network errors, 408, 429, and 5xx, honoring `Retry-After`.
- Typed usage errors (exit 2) before any billed request, and API errors (exit 1) with the provider message.
- Behavioral test suite against a local mock of both endpoints; CI matrix over ubuntu/windows and Python 3.9/3.11/3.13.
