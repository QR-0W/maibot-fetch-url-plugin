# MaiBot 网页抓取插件 (Fetch URL)

为麦麦提供 `fetch_url` 工具：抓取任意 URL 并智能分流处理。

- **网页 / PDF** → 优先通过 [jina.ai Reader](https://jina.ai/reader/) 转为 Markdown（PDF 也支持）；jina 失败或关闭时回退到本地抓取 + markdownify，PDF 另支持 pypdf 本地提取文本
- **图片** → 下载后按配置自动转码 / 压缩，直接以图像形式回传给麦麦观察
- **抓取缓存** → 原始抓取结果内存缓存（默认 30 分钟 TTL、128 条上限），分页 / 总结在缓存命中后仍按参数执行
- **JSON API** → `application/json` 响应自动格式化为 Markdown 代码块
- **页面元信息** → 返回标题、摘要、发布时间、最终 URL 等
- **超长内容** → 默认调用 LLM（注入麦麦人设）总结，或按需截断；支持 `start_char` / `end_char` 分页读取原文
- **网页图片** → 自动用 VLM 为页面中最大的几张图片生成中文描述并替换 alt 文本，描述结果持久缓存

## 功能特性

### 内容类型自动判定

对目标 URL 发起流式请求，根据响应头 `Content-Type`（辅以魔数嗅探）判定内容类型：


| 类型        | 处理方式                                      |
| --------- | ----------------------------------------- |
| `image/`* | 下载 → 校验 → 可接受格式原样回传 / 不支持格式转码 → 以 `content_items` 回传 |
| PDF       | jina Reader 转 Markdown；jina 不可用时回退 pypdf 本地提取文本      |
| HTML / 文本 | jina Reader（默认）或本地 markdownify            |


### 直接抓取图片（入站 content_items）

- 格式在 `acceptable_formats` 内 **且** 体积 ≤ `max_image_size`（默认 16 MB）**且** 最长边 ≤ `max_dimension`（默认 4096 px）→ 原样回传
- 超过 `max_image_size` 或 `max_dimension` → **无论格式** 均按 `convert_format`（默认 webp）、`quality_start` 预处理编码，以 `max_image_size` 为体积目标，并预缩到 `max_dimension`
- 不支持格式（如 BMP）→ 同上，始终预处理编码

### VLM alt 图片压缩（`[alt_text.image]`，仅网页内嵌图送 VLM 描述）

- 始终经体积估算单次压缩管道转码（默认 WebP、目标 1 MB、最长边 2048 px）
- 动图由 `animated_policy`（默认 `keep_animated`）控制

### 超长内容窗口与总结

- 文档超过 `max_content_length`（默认 8192 字符）时：
  - `llm_summarize = true`（默认）→ 调用 LLM 总结，提示词注入麦麦的昵称 / 人格 / 表达风格
  - 关闭总结或工具调用时指定 `on_exceed=truncate` → 截断返回原文
- 麦麦可通过 `start_char` / `end_char` 指定窗口分段读取未删改原文；自定义窗口若仍超限，则只总结该窗口
- 返回结果始终标注文档总长、本次窗口范围以及内容被总结 / 截断的事实

### VLM 图片描述（alt 文本替换）

替换优先级：**VLM 描述 > jina 生成的 alt（需配置 `jina.api_key` 且开启 `with_generated_alt`）> 网页原始 alt**。

- 页面图片多于 `max_images`（默认 **0**，即关闭）时，按 HEAD `Content-Length` 排序优先描述**最大**的几张（跳过最短边小于 `min_dimension` 的图标 / Logo）
- `alt_text.prompt` 留空时使用内置中文描述提示词，配置页以灰色占位内容显示；填写后覆盖默认值，不会自动开启图片描述。
- 关闭 VLM 时仍保留 jina / 原始 alt，并为每张图的 alt 追加提示：`You can call fetch_url again to obtain this image.`（可再调 `fetch_url` 拉取原图）
- 送入 VLM 前**始终**经体积估算单次压缩管道转码（由独立的 `[alt_text.image]` 配置，默认 WebP、目标 1 MB、最长边 2048 px）
- 描述结果以图片内容 sha256 为键持久缓存在 `data/alt_text_cache.json`（LRU，默认 1024 条），重复抓取不再消耗 VLM 调用
- VLM 任务未配置时自动跳过，保留 jina / 原始 alt
- 直接抓取的单张图片无需此机制——MaiBot 主程序会自动为工具回传的图片生成并缓存描述

### 按响应内容决定是否执行脚本

所有域名共用同一套读取流程。先用流式 HTTP 请求探测响应头和魔数，图片、PDF、JSON 继续使用原有专用处理。已有有效正文的 HTML 直接转换为 Markdown；开启 `jina.enabled` 时保留原有 Jina 优先策略。

HTML 只有脚本空壳、缺少正文，或明确要求自动脚本校验时，才触发一次通用 Chromium 渲染。判断依据是响应和正文结构，不是网站域名，也不要求页面具有 `h1`。渲染会在预算内等待正文出现，导航和页脚不作为正文就绪的依据。验证码、登录要求、限流、明确拒绝访问和安全策略拦截会直接报错，不循环切换后端或代理。

每次渲染使用新的匿名上下文，不读取个人浏览器档案或配置中的 Cookie；页面签发的临时会话在本次读取结束后销毁。浏览器串行运行，每次读取后关闭，不按网站积累实例。保持 TLS 证书校验和浏览器沙箱，阻止子框架导航、弹窗、WebSocket、Service Worker，以及图片、媒体和字体加载；HTTPS 的 XHR/fetch POST 可用于页面数据查询和自动脚本校验，不操作页面表单。

浏览器出站经过认证的本机代理防护，支持标准 HTTP 80 / HTTPS 443。普通 DNS 结果必须全部为公网地址，并固定连接已校验 IP；重定向和 Worker 的连接同样受约束。真实私网、混合公网/私网结果和混合 Fake-IP 结果仍会拒绝。

系统 DNS 全部返回 `198.18.0.0/15` Fake-IP 时，只有已有 `fetch.allow_private_networks=true` 授权才允许使用系统透明代理或已配置的本机回环代理。经本机显式代理时，把已校验的原主机名交给该代理解析，不把数值 Fake-IP 当作实际网站地址，也不转交远程或局域网代理。此例外依赖本机代理完成真实解析，本插件无法独立验证它最终连接的真实 IP。不修改系统 DNS、代理或证书设置，不添加外部 DNS 服务。

通用渲染使用 `rendering.enabled=true`、`rendering.timeout=30`（秒，范围 5–45，包含排队、启动、加载和验证）及 `rendering.proxy_mode="inherit"`。`inherit` 沿用 `fetch.proxy`，`direct` 只是不使用显式代理，仍保留系统网络。未安装 Chromium 时需要在对应环境安装浏览器，插件不会自动下载。

旧 `[browser]` 节仅保留配置兼容，不在可视化界面显示，旧开关及站点代理策略不再影响读取。新缓存区分通用渲染开关、代理策略与 Jina 设置，不复用旧站点浏览器结果。图片、PDF、分页、总结和 Tool/API/LRS 接口继续沿用既有实现。此机制提高动态网页的覆盖范围，不承诺绕过人工验证、登录或地区/IP 限制。

## 安装

1. 将本插件目录放入 MaiBot 的 `plugins/` 目录：
  ```bash
   cd <MaiBot 根目录>/plugins
   git clone https://github.com/yufei-pan/maibot-fetch-url-plugin.git
  ```
2. 重启 MaiBot（依赖 `httpx`、`markdownify`、`beautifulsoup4`、`pillow` 会按 `_manifest.json` 声明自动安装）
3. 在 WebUI 插件管理中确认插件已加载并启用

## 配置

WebUI 按功能展示四张卡片，每个分组有独立标题和分隔线：

| 卡片 | 分组 |
| --- | --- |
| 工具开关与网站连接 | 工具开关与可见性、连接、网站访问凭据（可选） |
| 网页读取与正文处理 | Jina 正文读取、脚本网页自动渲染、正文长度与超长处理、总结模型和提示词、共享模型调用超时 |
| 图片返回与内容描述 | 直接返回图片时的转码与压缩、网页图片转文字、发送给图片描述模型前的压缩 |
| 读取结果与图片描述缓存 | 抓取结果内存缓存、图片描述持久化缓存 |

连接分组统一管理代理、请求超时、下载上限、User-Agent 和内网访问权限。可见字段均有中文名称和用途说明；例如 `fetch.proxy` 显示为「代理」，但 TOML 键名不变。网站访问凭据用于目标网站，不是插件登录；经 Jina 读取时，匹配的 Cookie 会转发给 Jina，本地浏览器读取不使用这些 Cookie。

按域名设置 Cookie 时，界面使用「域名 / Cookie」两列，支持添加、修改和删除。域名只填主机名，不含协议或路径；Cookie 默认隐藏，可手动显示。保存仍使用 `fetch.domain_cookies` 映射，无需改写 TOML 结构。

原配置路径保持不变，通用渲染新增 `rendering` 节，旧 `browser` 节仅兼容保留。`alt_text.image` 仍使用原有嵌套对象；`plugin.config_version` 不在界面展示，无需迁移原有配置键。常用项：


| 配置项                                      | 默认值               | 说明                             |
| ---------------------------------------- | ----------------- | ------------------------------ |
| `rendering.enabled`                    | true              | HTML 需要脚本时尝试一次匿名渲染 |
| `rendering.timeout`                    | 30                | 单次渲染预算，单位秒，范围 5–45 |
| `rendering.proxy_mode`                 | inherit           | 沿用 `fetch.proxy`；可选 `direct`，所有网站使用同一策略 |
| `plugin.always_visible_for_planner`      | false             | 让 `fetch_url` 始终对 Planner 可见（无需 `tool_search`）；**修改后需重新加载插件** |
| `fetch.timeout`                          | 15                | 直接抓取超时（秒）                      |
| `fetch.user_agent`                       | Chrome 风格 UA    | 抓取时使用的 User-Agent              |
| `fetch.proxy`                            | （空）               | 代理地址，如 `http://127.0.0.1:7890` |
| `fetch.cookies` / `fetch.domain_cookies` | （空）               | 全局 / 按域名 Cookie                |
| `fetch.allow_private_networks`           | false             | 是否允许抓取内网地址（SSRF 防护）            |
| `jina.enabled`                           | true              | 优先使用 jina Reader               |
| `jina.api_key`                           | （空）               | jina API Key（可选，不填有频率限制）       |
| `jina.timeout`                           | 30                | jina 超时（秒），超时回退本地抓取            |
| `jina.engine`                            | browser           | jina 渲染引擎（质量最好）                |
| `image.acceptable_formats`               | jpeg/png/gif/webp | 可原样回传的格式（须同时 ≤ max_image_size）   |
| `image.convert_format`                   | webp              | 预处理编码目标格式                         |
| `image.max_image_size`                   | 16 MB             | 原样回传上限；超过则一律预处理并以之为压缩目标       |
| `image.max_dimension`                    | 4096              | 原样回传最长边上限；超过则预处理并预缩到该尺寸         |
| `content.max_content_length`             | 8192              | 单次返回文本上限（字符）                   |
| `content.llm_summarize`                  | true              | 超长内容是否 LLM 总结                  |
| `llm.model`                              | planner           | 总结用的模型**任务名**（非原始模型 ID）        |
| `llm.rpc_timeout_ms`                     | 120000            | 总结 / VLM `llm.generate` 的 cap.call RPC 超时（毫秒） |
| `alt_text.max_images`                    | 0                 | 每次抓取最多 VLM 描述的图片数（0 关闭，默认关闭） |
| `alt_text.cache_size`                    | 1024              | 持久描述缓存条目上限                     |
| `alt_text.image.convert_format`          | webp              | VLM 输入转码目标格式                     |
| `alt_text.image.target_image_size`       | 1 MB              | 体积估算目标（始终走估算算法）                |
| `alt_text.image.max_dimension`           | 2048              | VLM 输入最长边上限                       |
| `alt_text.image.max_quality`             | 80                | webp/jpeg 最高质量                       |
| `alt_text.image.min_quality`             | 10                | webp/jpeg 最低质量                       |
| `alt_text.image.animated_policy`         | keep_animated     | VLM 动图策略                           |
| `cache.enabled`                          | true              | 是否缓存抓取原始结果                     |
| `cache.ttl_seconds`                      | 1800              | 抓取缓存 TTL（秒）                      |
| `cache.max_entries`                      | 128               | 抓取缓存条目上限                         |

`fetch_url` 向 Host 声明的整次调用超时是 jina 超时 + 本地抓取超时 + 上述 LLM 超时 + 30 秒余量（默认 195 秒）。Host 对未声明超时的工具只等 60 秒。

> **安全提醒**：`fetch.cookies`、`fetch.domain_cookies`、`jina.api_key` 属于敏感信息，请勿提交到版本库。
> 另外 jina 在转发 Cookie（`X-Set-Cookie`）时会绕过其缓存，请求可能稍慢。

### Planner 常显（可选）

默认情况下 `fetch_url` 在 **deferred** 工具池里，麦麦需要先通过 `tool_search` 发现它才能调用。若希望 Planner 每轮都能直接看到并调用 `fetch_url`，在配置中开启：

```toml
[plugin]
always_visible_for_planner = true
```

开启后等同于声明 `core_tool=True`。请谨慎使用——常显工具会增加 Planner 的选择成本。修改此项后，需要在 WebUI 中**禁用并重新启用插件**（或重启 MaiBot）才能生效；仅保存配置不会更新已注册的工具元数据。

## 工具用法（麦麦视角）

```text
fetch_url(url, start_char=0, end_char=-1, on_exceed="summarize", summary_focus="")
```

- 抓取整页：`fetch_url(url="https://example.com/article")`
- 内容太长被总结后，分页读原文：`fetch_url(url=..., start_char=0, end_char=7000)`，下一页 `start_char=7000`
- 强制要原文不要摘要：`fetch_url(url=..., on_exceed="truncate")`
- 定向总结：`fetch_url(url=..., summary_focus="价格和发布时间")`
- 抓图片：`fetch_url(url="https://example.com/photo.png")` → 图片直接出现在上下文中

## 插件间 API（其他插件调用）

本插件暴露公开 API `fetch_url`（完整名：`com.0-hz.fetch-url.fetch_url`）。

```python
result = await self.ctx.api.call(
    "com.0-hz.fetch-url.fetch_url",
    url="https://example.com/page",
    start_char=0,
    end_char=-1,
    on_exceed="summarize",
    summary_focus="",
    return_image=False,
)
# SDK 已解包 Host 的 {success, result}，result 即 API 处理函数返回的字典
```

参数与工具大体一致，额外：

| 参数 | 默认 | 说明 |
|------|------|------|
| `return_image` | `false` | 仅 API。`false`：图片走 VLM/alt_text 文字描述（失败则 metadata-only）；`true`：与工具相同，经 `content_items` 回传图片字节 |

API **不会**向 Maisaka 上下文追加内容；调用方只使用返回值。SDK 成功调用时直接返回处理函数字典，形如
`{success, content, ...}`，无需再读取 `result["result"]`。失败有两种形态：

- Host 级调用失败可能返回 `{success: False, error: ...}`，不含 `content`；
- 抓取处理函数的软失败返回 `{success: False, content: "抓取失败：…"}`。

API 对单张图片的文字描述会调用 VLM，不受 `alt_text.max_images` 限制；该配置只控制网页内嵌图片的 alt 文本替换数量。

## Lunagentic Research Swarm（可选集成）

本插件是 [麦麦深度调查组（LRS）](https://github.com/yufei-pan/maibot-lunagentic-research-swarm) 的**推荐**网页全文抓取 Procedure 提供方，**不是**硬依赖：

- 同时安装并启用两个插件后，LRS 会扫描带 `lunagentic_extension=procedures` 的公开 API，自动发现本插件的 `describe_procedures@1` / `invoke_procedure@1`。
- **不需要**在 LRS 配置中复制 Jina key、Cookie、代理等；密钥与抓取策略仍完全由本插件 `config.toml` 管理。
- Procedure ID：`fetch_url.fetch`。参数与工具对齐：`url`（必填）、`start_char` / `end_char`、`on_exceed`（`summarize`|`truncate`）、`summary_focus`。
- LRS 调用固定 `return_image=false`：图片只返回文字描述/元数据，不把 base64 图片塞进 Procedure payload；普通 MaiBot `fetch_url` Tool 仍可按原样回传图片。
- 结果带 provenance metadata（请求 URL / 最终 URL / provider / cached），**不**承诺对网页内容真实性背书。
- 卸载或禁用本插件后，LRS 本身继续运行；对新调用返回结构化 `procedure_unavailable`，`/swarm health` 可显示缺少推荐集成。

## 测试

```bash
# 离线冒烟测试（不需要 MaiBot Host）
cd maibot-fetch-url-plugin
PYTHONPATH=<maibot-plugin-sdk 路径> python tests/smoke_test.py
```

本地加载测试：将插件放入 `MaiBot/plugins/` 后启动 MaiBot，观察日志中的
`fetch_url 插件已加载`；随后在聊天中让麦麦抓取一个网页 / 图片 / PDF 验证各路径。

## 常见问题

- **抓取内网地址被拦截**：默认开启 SSRF 防护，确有需要时打开 `fetch.allow_private_networks`
- **PDF 抓取失败**：优先确认 `jina.enabled = true` 且 jina 可达；jina 不可用时插件会尝试 pypdf 本地提取（扫描件 / 图片型 PDF 仍可能需要 jina）
- **图片描述没有生效**：确认 Host 已配置 `vlm` 模型任务（与麦麦收图识别共用），且 `alt_text.max_images > 0`
- **总结质量不佳**：可调整 `llm.model`（如换成 `replyer`）或自定义 `llm.summarize_prompt_template`
- **需要登录的页面**：在 `fetch.domain_cookies` 中为对应域名配置 Cookie

## License

MIT