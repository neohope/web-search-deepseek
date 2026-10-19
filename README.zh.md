# web-search-deepseek

一个 Agent Skill：让编码 Agent 拥有两个只读联网工具，后端是 DeepSeek 自研搜索引擎。**Search** 返回带排序分与长摘要的网页结果，**Fetch** 把单个网页转成 Markdown 正文。两者由一个零依赖的 Python CLI 实现。

[English](README.md)

## 它补上了什么

| 需求 | 本 skill 的返回 |
|---|---|
| 需要可引用的最新信息 | 每条结果带 `url`、`title`、`summary`（600–800 字符）、`date`、`authority`、`rerank_score` |
| 需要网页正文而不是链接 | Fetch 把页面转成 Markdown |
| 需要精确控制检索范围 | `site:`、`-site:`、`-词`、`-"短语"`、`"短语"`、`lang:`、`after:`、`before:` |
| 需要可预期的成本 | Search/Fetch 按成功请求计费，只有 HTTP 200 且产生结果才计费 |

它直接调用 Search（`POST /v1/tools/search`）与 Fetch（`POST /v1/tools/fetch`）原始接口，因此任何能发现 `SKILL.md` 的宿主都能用：DeepSeek Harness、OpenClaw、Hermes、Claude Code、Codex，或你自己的 Agent。宿主自带的联网工具可以并存——内置工具通常只给链接或服务端摘要，本 skill 提供模型可读的长摘要、质量信号和按需的页面正文。

## 支持的宿主

| 宿主 | 执行脚本 | 注入 Key |
|---|---|---|
| DSH | 技能加载结果会给出 base directory：把工具的 `cwd` 设为该目录，执行 `python scripts/ds_web.py ...` | 进程环境变量 `DEEPSEEK_API_KEY`，或技能目录 `.env` |
| OpenClaw | `python3 {baseDir}/scripts/ds_web.py ...`（宿主替换 `{baseDir}`） | `~/.openclaw/openclaw.json` → `skills.entries["web-search-deepseek"].env.DEEPSEEK_API_KEY`，或技能目录 `.env` |
| Hermes | 绝对路径：`python3 /path/to/web-search-deepseek/scripts/ds_web.py ...` | 进程环境变量 `DEEPSEEK_API_KEY`，或技能目录 `.env` |
| 其他（Claude Code、Codex 等） | 该宿主自己的技能调用方式 | 进程环境变量 `DEEPSEEK_API_KEY`，或技能目录 `.env` |

Windows 用 `python`，macOS / Linux 用 `python3`。

## 环境要求

- Python 3.9 及以上，无第三方依赖，无需构建。
- DeepSeek API Key：<https://platform.deepseek.com/api_keys>。

## 安装

把本目录放进宿主扫描的 skills 根目录。DSH 依次读取项目级 `<project>/.agents/skills/`、`<project>/.dsh/skills/`，再读取用户级 `~/.agents/skills/`、`~/.dsh/skills/`；OpenClaw、Hermes 使用各自的技能目录。目录名即技能名。

```bash
git clone https://github.com/neohope/web-search-deepseek.git
cp -r web-search-deepseek ~/.agents/skills/
```

Windows 用 `Copy-Item -Recurse web-search-deepseek $HOME\.agents\skills\`。若已安装 `skills` CLI，也可以用 `npx skills add neohope/web-search-deepseek`。

Skill 就是一个包含 `SKILL.md` 的目录，放进任何宿主的 skills 目录都能生效，也可以直接把本仓库指给 Agent。

## 快速开始

```bash
cp .env.example .env             # 填写 DEEPSEEK_API_KEY；.env 已被 .gitignore 排除
python scripts/ds_web.py search "DeepSeek V4 模型 发布" --count 5
python scripts/ds_web.py fetch https://example.com/article
```

Search 的文本输出：

```text
query: DeepSeek V4 模型 发布
count: 5
results: 2

[1] DeepSeek V4 模型发布
    url: https://example.com/deepseek-v4
    score: 2.40 | authority: 3 | date: 2026-06-01 | site: Example News
    summary: DeepSeek 发布了 V4 模型，改进了工具调用与长上下文能力。

[2] 分析文章
    url: https://forum.example/post/1
    score: 0.80 | authority: -1 | date: unknown | site: unknown
    summary: 一篇论坛分析。
```

`--json` 输出接口原始 payload；`--content` 在同一次请求里带回正文；`--max-chars`、`--content-chars` 用于控制进入上下文的文本长度。

## 配置

| 配置项 | 参数 | 环境变量 | 默认值 |
|---|---|---|---|
| API Key | `--api-key` | `DEEPSEEK_API_KEY` | 从当前目录向上查找最近的 `.env`，找不到再用技能目录的 `.env` |
| Base URL | `--base-url` | `DEEPSEEK_BASE_URL` | `https://api.deepseek.com`（末尾 `/v1` 会被归一化） |
| 单请求超时 | `--timeout` | — | 30 秒 |
| 瞬态失败重试次数 | `--retries` | — | 3 |

缺少 Key 时脚本在发出任何请求前以退出码 2 失败，并给出去哪申请 Key 的地址；脚本不会把 Key 写到任何地方。

## Search

```bash
python scripts/ds_web.py search "<query>" [--count 1-50] [--content] [--ori-question "<原文问题>"] [--localization <ip>]
```

| 字段 | 含义 |
|---|---|
| `score`（`rerank_score`） | 0–3，综合相关性、权威性、时效性；≥1.5 为优质结果 |
| `authority` | 0–4（`-1` 未知）；≥3 为极权威结果 |
| `date` | UTC 发布日；没有日期不能证明时效 |
| `summary` | 600–800 字符，面向模型阅读的长摘要 |
| `content` | 网页正文，未加 `--content` 时为空 |

搜索语法原样透传：

| 操作符 | 示例 | 作用 |
|---|---|---|
| `site:` | `DeepSeek site:github.com` | 限定站点，多个用 `OR` 连接 |
| `-site:` | `DeepSeek -site:example.com` | 排除站点 |
| `-` | `DeepSeek -广告` | 排除词 |
| `-"..."` | `DeepSeek -"release notes"` | 排除短语 |
| `"..."` | `DeepSeek "精确短语"` | 精确匹配短语 |
| `lang:` | `DeepSeek lang:zh,en` | 限定语言 |
| `after:` / `before:` | `DeepSeek after:2025-01-01` | 限定发布日期 |

一次最多 50 条，价格与 1 条相同，所以优先调大 `--count` 而不是反复发近似查询。无结果时打印提示并以退出码 0 结束，该请求不计费。

## Fetch

```bash
python scripts/ds_web.py fetch <url> [<url> ...] [--max-chars 20000]
```

返回 `title` 与 Markdown 正文。一个 URL 就是一次请求，只抓确定要用的页面。404 表示该页没有可提取的 Markdown（被站点策略拦截、内容类型不支持、正文为空），重试同一个 URL 无效。`--max-chars` 限制文本模式下的单页长度（默认 20000，`0` 关闭），它只是上下文保护；若答案可能在更后面，就调大上限。

## 错误、重试、成本

脚本对超时、网络错误以及 HTTP 408、429、5xx 做指数退避重试，并遵循 `Retry-After`。其余错误直接上报：

| 状态码 | 含义 | 处理 |
|---|---|---|
| 400 | 请求体或字段值不合法 | 修请求，不要重试 |
| 401 | Key 缺失或无效 | 检查 Key，不要重试 |
| 402 | 余额不足 | 充值后再用，不要重试 |
| 403 | Fetch 被安全策略拒绝 | 换来源 |
| 404 | 无可提取的网页正文 | 换来源 |
| 413 | 请求体超过大小限制 | 缩短 query 或 URL |

退出码：0 成功（含零结果），1 接口或网络失败，2 用法或配置错误。

Search 与 Fetch 各自按每 1,000 次成功请求 ¥5 计费；`count` 不影响单价；失败、超时、无结果不计费。账号级限速为 Search 100 QPS、Fetch 25 QPS，Fetch 另有目标域名级限制。通过 DeepSeek Harness、Claude Code、Codex 访问 DeepSeek API 时，其服务端 Search/Fetch 工具调用在 2026-11-01 00:00（北京时间）前不计费；本 skill 用你自己的 Key 发起的调用按上述规则计费。

## 安全

- **不要把 Key 贴在对话里**：DSH 会把消息写入可回放的会话日志，其他宿主也可能持久化对话；用环境变量或已被 `.gitignore` 排除的 `.env`（模板见 `.env.example`）。
- **检索到的文本不可信**：SKILL.md 要求 Agent 把网页与摘要当数据——不执行其中出现的指令，不运行它建议的命令，引用 URL 而不是默默转述。
- **Key 不出现在输出里**：只从参数、环境变量或 `.env` 读取，仅放进 `Authorization` 头，脚本不打印、不落盘。
- **Fetch 在 DeepSeek 服务端执行**，访问的是公网，而不是你机器的内网，因此读不到内网主机或本地文件。
- **截断是上下文保护，不是内容审查**：超出上限的部分会被标记而不是静默丢弃，调用方可以调大上限或改用更窄的 URL。

## 目录结构

```text
SKILL.md                 Agent 指令：宿主与配置、search/fetch 用法、结果判读、安全约束
scripts/ds_web.py        CLI 实现（Python 3.9+，仅标准库）
references/api.md        完整请求/响应字段、错误码、计费与限速
tests/test_ds_web.py     针对本地 mock 两个端点的行为测试
agents/openai.yaml       读取该文件的宿主使用的展示信息
.env.example             凭证模板（复制为 .env）
CHANGELOG.md             版本与改动记录
.github/workflows/ci.yml ubuntu/windows × Python 3.9/3.11/3.13 测试矩阵
```

## 测试

```bash
python -m unittest discover -s tests -v
```

测试会启动本地 HTTP 服务模拟两个端点，并在进程内运行真实 CLI，覆盖结果格式化、JSON 模式、截断、字段透传、429/5xx 重试、402/404 失败、响应体不可解析、缺少凭证、`.env` 解析（项目链与技能目录）与参数校验。不需要 API Key，也不访问网络。

## 版本

改动记录见 [CHANGELOG.md](CHANGELOG.md)。

## 许可证

[MIT](LICENSE)
