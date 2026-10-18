# Changelog

本文件记录 maibot-fetch-url-plugin 的版本变更。

格式基于 [Keep a Changelog](https://keepachangelog.com/zh-CN/)，
版本号遵循 [Semantic Versioning](https://semver.org/lang/zh-CN/)。

## [0.5.0] - 2026-10-17

### 主要功能

- 正文读取：百度百科词条与 KKBOX 歌曲原页使用定向匿名 Chromium，保留原 URL 身份与既有 Markdown、分页、缓存和接口。
- 请求安全：保留 TLS 校验与沙箱，增加回环认证出站隧道、资源白名单和公网地址固定；系统 Fake-IP 仅沿用已有授权与透明代理路径，其他私网仍拒绝。不读取个人浏览器档案或配置登录 Cookie。
- 请求范围：限制 HTTP(S) 资源与非必要媒体，阻止 WebSocket、Service Worker 和子框架导航，保留原网站的正常验证脚本。

### 细节

- 匿名会话仅保留在内存，串行读取、同站请求间隔与风控冷却减少重复访问；浏览器版本与 UA 一致。
- 验证最终文档，允许脚本验证后正常返回正文；验证码、拒绝页和空壳不进入成功缓存，不重试其他后端掩盖错误。
- 浏览器路径和代理策略纳入缓存键；其他图片、PDF、JSON、Tool/API/LRS 行为保持不变。

## [0.4.2] - 2026-10-06

### 修复

- `fetch_url` 工具、公开 API 与 LRS `invoke_procedure` 声明 Host RPC 超时（默认 195 秒）。未声明时 Host 对 `plugin.invoke_tool` 只等 60 秒；jina 超时回退后再做 LLM 总结会被掐成 `[E_TIMEOUT]`

## [0.4.1] - 2026-09-20

### 修复

- 总结与 VLM 描述的 LLM 调用改为按 Host 任务名走 `task_name`，具体模型走 `model_name`。SDK 2.8.1 会默认附带 `task_name="utils"`，若再把 `utils`/`planner` 放进 `model`，Host 会当成具体模型名并报「未找到名为 … 的模型」

## [0.4.0] - 2026-08-06

### 新增

- 可选 LRS Procedure provider：公开 `@API describe_procedures@1`（`lunagentic_extension=procedures`）与 `@API invoke_procedure@1`
- Procedure ID `fetch_url.fetch`：复用现有抓取管线；LRS 调用固定 `return_image=false`（只返回文字描述，不回传图片 binary）
- 提供方配置、Jina/VLM 密钥仍完全由本插件管理；不把 LRS 写成硬依赖

### 保持

- 原有 `@Tool fetch_url` 与公开 `@API fetch_url@1` 行为与参数兼容

## [0.3.0] - 2026-08-01

### 新增

- 公开 `@API fetch_url`（`com.0-hz.fetch-url.fetch_url`）：参数对齐工具，供其他插件调用
- API 图片默认 `return_image=false`：经 alt_text/VLM 返回文字描述；VLM 不可用或失败时返回 metadata-only；`return_image=true` 时与工具相同回传图片

## [0.2.9] - 2026-07-20

### 新增

- `[llm] rpc_timeout_ms`：本插件所有 `llm.generate`（内容总结与 VLM 图片描述）的 cap.call RPC 超时（毫秒），默认 120000（120 秒）；`config_version` 升至 1.9.0

## [0.2.8] - 2026-07-12

### 变更

- `alt_text.max_images` 默认改为 `0`（关闭自动 VLM alt，缩短单次工具调用耗时；`config_version` 1.8.0）
- 网页 Markdown 中每张图片的 alt 追加提示：`You can call fetch_url again to obtain this image.`

## [0.2.7] - 2026-07-11

### 修复

- 持久化配置前去除 `None`；WebUI 清空可选字段时按默认值处理

## [0.2.6] - 2026-07-10

### 变更

- 默认 summarize prompt 以具名 AI 生命体的「网页抓取」模块自居，并标注性格与表达风格

## [0.2.5] - 2026-06-14

### 新增

- 内存抓取缓存（30 分钟 TTL，128 条）
- 返回页面元数据与格式化 JSON 响应
- jina 不可用时回退到本地 `pypdf` 解析 PDF

## [0.2.4] - 2026-06-14

### 修复

- Content-Type 缺失时读取足够字节以识别 RIFF/WEBP
- VLM alt 替换仅在截断 / 摘要决策之后应用，保证 `returned_range` 与原始 markdown 偏移一致

## [0.2.3] - 2026-06-13

### 变更

- 未设置的配置字段跟随代码默认值；从 1.5.0 迁移时保留用户自定义（`config_version` 1.6.0）

## [0.2.2] - 2026-06-13

### 修复

- 将配置迁移内联进 `plugin.py`（Host 仅通过 importlib 加载该文件，独立迁移模块运行时不可导入）

## [0.2.1] - 2026-06-13

### 修复

- 拆分入站直抓与 VLM 图片路径：可接受格式且在尺寸限制内时直通，避免 v0.2.0 过度压缩
- 增加配置升级迁移

## [0.2.0] - 2026-06-13

### 变更

- 多遍图片压缩改为基于探测的单遍质量 / 缩放估算，降低 CPU
- 增加动图策略（`keep_animated` / `skip` / `first_frame`）

## [0.1.0] - 2026-06-11

### 新增

- 首次发布：`fetch_url` 工具
- 添加 MIT LICENSE；默认 `max_content_length` 提至 8192
