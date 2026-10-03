# 在线硬件查询插件

AstrBot 在线硬件产品查询工具，支持质保查询、NAND 物料/颗粒识别等功能，支持文本和图片识别 SN。
图片识别使用插件单独配置的视觉模型 provider，不读取 AstrBot 普通对话上下文，也不会自动回落到全局图片转述模型。

## 当前支持功能

### 硬盘图片分析（试验功能，默认关闭）

启用 `hardware_inspection_enabled` 并配置独立 API 后，发送 `查硬盘` 加同一块机械硬盘的 1–3 张照片，也可引用图片。群聊仍需 @机器人。

插件将原图和同一条鉴定提示词直接提交给 Gemini 中转，并在同一次请求中声明原生搜索工具；模型按详细字段完成检查，插件最后只提取两行纯文本的“结论”和“疑点与依据”发送。该流程不调用质保接口，不使用前置 OCR，不读普通会话上下文，失败也不自动切换接口或普通视觉模型。

API 地址填写完整 HTTPS 端点，插件根据 URL 自动选择请求格式和搜索工具，无需另选接口类型：

- Responses：`https://example.com/v1/responses`，声明 `tools: [{"type": "google_search"}]`。
- Chat Completions：`https://example.com/v1/chat/completions`，声明 `tools: [{"type": "web_search"}]`。

两种接口使用同一条鉴别提示词，不额外设置温度或输出 token 上限。旧版仅填到 `/v1` 的地址仍按 Responses 处理。Chat 使用中转的原生搜索扩展，不是等待插件执行的自定义 `function`；这些工具名称是测试中转的协议，不代表所有 OpenAI 兼容服务都支持，也不是 Google 原生 `generateContent` 地址。

测试中已验证：Responses 使用 `google_search`，Chat 使用 `web_search`，图片与搜索可在同一请求中使用。具体中转和模型需自行确认支持情况；出现来源链接也不保证分析依据正确。

2026-10-03，同一提示词和同批 40 张本地标注图片（假 24、真 16）的评测：Responses 两轮严格标签一致为 33/40（82.5%）与 34/40（85.0%），假盘均识别 24/24，真盘分别 9/16 与 10/16 判为未发现明显矛盾，每轮误报 1 张；第二轮有 1 次请求未完成。Chat 在不额外设置温度和输出上限的完整重测中严格一致 33/40（82.5%），假盘识别 23/24，真盘 10/16 未发现矛盾、4/16 证据不足、1/16 误报，另有 1 次截断；平均耗时 32.15 秒。此前 Chat 探测参数为温度 0、输出上限 2400 时，33/40 次响应截断，严格一致仅 31/40，该批不作为接入通过依据。

鉴于假盘召回，本次仍推荐 Responses，Chat 作为可选端点。这些结果仅为该批图片与该中转的测试表现，不是实物鉴定准确率保证。服务端返回不完整时插件直接提示失败，不采用部分报告，也不自动重试或切换接口。

| 配置项 | 默认值 | 说明 |
| --- | --- | --- |
| hardware_inspection_enabled | false | 启用独立命令 `查硬盘` |
| hardware_api_base | 空 | 完整 HTTPS 端点，以 `/responses` 或 `/chat/completions` 结尾；自动识别接口类型 |
| hardware_api_key | 空 | 独立 API 密钥 |
| hardware_model | 空 | 支持图片与搜索的 Gemini 模型名 |
| hardware_timeout_seconds | 180 | 包含图片准备的最长等待秒数，可在插件配置中自定义；不自动重试 |

照片需为 JPEG、PNG、WebP 或 GIF，单张不超过 20 MiB。一次仅分析一块盘，不维护跨消息补图会话。`查硬盘` 必须严格等于命令本身；带其他文字的普通聊天会照常交给普通 LLM。资料不足时会给出有限分析，接口失败时直接提示失败。

图片只在内存中读取并发送给配置的接口；插件正式使用时不主动把用户图片或分析报告写入本地文件。请求会声明 `store: false`，但网关和模型服务的远端日志策略不在插件控制内。测试阶段的图片结果另由本地评测脚本按测试用途保存。
“未发现明显矛盾”不等于证明原装或全新；“发现可疑矛盾”是风险提示。本功能仍为试验功能，默认关闭。

### 图片卡片输出（t2i，默认关闭）

启用 `t2i_enabled` 后，质保查询、NAND 物料查询和硬盘图片分析的结果改用图片卡片发送，底层是 AstrBot 的 `html_render`，模板文件为 `templates/hardware_report.html`。卡片只展示状态徽标、关键字段和“结果说明/疑点与依据”；渲染失败自动回退纯文本，不影响原有输出内容。
### 质保查询

查询硬件产品保修状态，支持文本输入SN或从产品标签图片识别SN。

#### 支持品牌

| 品牌 | 别名 | 说明 |
|------|------|------|
| 西部数据 | 西数、WD | 直接查询 |
| 希捷 | 希捷、Seagate | 人机验证过不去，返回官网链接 |
| 东芝 | 东芝、Toshiba | 直接查询 |
| 闪迪 | 闪迪、sandisk | 直接查询 |
| 致态 | YMTC、长江存储 | 验证码自动OCR识别 |

### NAND 物料/颗粒查询

基于 [iTXTech fdnext](https://github.com/iTXTech/fdnext) 解析引擎，支持查询 NAND Flash 颗粒的详细物料信息，包括容量、制程、Die 信息、电压、接口类型等。

#### 支持查询类型

- **Part Number (PN) 解码**：输入物料型号（如 `MT29F64G08CBABA`），解析厂商、容量、Cell 类型、制程等
- **Flash ID 解码**：输入 NAND Flash ID（如 `2C64444BA900`），识别对应颗粒型号和参数

#### 支持厂商

覆盖 Samsung、SK hynix、SanDisk/WD、KIOXIA、Micron、YMTC 等主流 NAND 厂商。

## 使用方法

群聊中必须 @ 机器人，并且清洗后的消息必须严格符合 `关键词 空格 参数` 才会触发插件；否则放行给普通 LLM，避免误拦截正常聊天。

### 质保查询 - 文本查询

格式：`查质保 品牌 SN` 或 `查保修 品牌 SN`

示例：
- `查质保 西数 WXA1A12345678`
- `查质保 希捷 ZR1A12345678`
- `查保修 东芝 X5U2A02E12345`

### 质保查询 - 图片查询

发送或引用产品标签图片，带上查询命令和品牌：
- `查质保 西数` + 图片
- `查质保 希捷` + 图片
- `查质保 致态` + 图片

插件会通过主视觉模型从图片中提取 SN；主模型失败或超时后，按配置顺序切换备用视觉模型，再查询对应品牌。

### NAND 物料查询 - 文本查询

格式：`查颗粒 型号` 或 `查物料 查询文本`

示例：
- `查颗粒 MT29F64G08CBABA` — 查询美光 8GB MLC NAND 颗粒详情
- `查物料 2C64444BA900` — 通过 Flash ID 查询颗粒信息
- `查flash id 2C,64,44,4B,A9,00` — 支持逗号/空格分隔的 Flash ID
- `查pn MT29F1T08EQLCEB2` — 查询美光 128GB QLC 颗粒
- `查型号 K9ABGD8U0D` — 查询三星颗粒

### NAND 物料查询 - 图片查询

发送或引用 NAND 芯片照片，带上查询命令：
- `查颗粒` + 图片
- `查物料` + 图片

插件会通过配置的视觉模型 provider 从芯片丝印中自动识别型号（PN / Flash ID），然后查询。
关闭 `image_recognition_enabled` 可关闭图片识别。

## 配置项

| 配置项 | 类型 | 默认值 | 说明 |
|--------|------|--------|------|
| default_region | string | CN | 默认地区代码 |
| cache_ttl_seconds | int | 604800 | 缓存时间（秒），默认一周 |
| timeout_seconds | int | 15 | 请求超时（秒） |
| llm_summary | bool | false | 启用模型总结（费token） |
| trigger_keywords | list | ["查质保", "查保修"] | 质保查询命令关键词；需 @机器人 且严格匹配“关键词 空格 品牌 空格 SN/图片说明” |
| nand_query_enabled | bool | true | 启用 NAND 物料查询功能 |
| nand_api_base | string | https://fdnext.itxtech.org | fdnext API 地址，可填自建实例 |
| nand_trigger_keywords | list | ["查颗粒", "查物料", ...] | NAND 查询命令关键词；需 @机器人。文本查询需“关键词 空格 参数”，带图/引用图可只发关键词 |
| image_recognition_enabled | bool | true | 启用图片识别；关闭后不从产品标签图或 NAND 芯片图中提取 SN/PN |
| vision_provider_id | string | 空 | 插件图片识别的主视觉模型；不会使用 AstrBot 全局图片转述或普通对话模型 |
| vision_fallback_provider_ids | list | [] | 主模型失败或超时后的备用视觉模型，可添加多个，列表顺序就是回落顺序 |
| vision_timeout_seconds | int | 60 | 单个视觉模型最长等待时间，单位为秒；填 0 不设置插件侧超时 |
| image_prompt | string | 空 | 质保标签图片识别提示词；留空使用插件内置默认提示词 |
| nand_image_prompt | string | 空 | NAND颗粒丝印识别提示词；留空使用内置颗粒提示词，不影响质保SN识别 |
| fail_message | string | 看不清图，别用锁泥相机拍 | 图片识别失败回复；留空则不回复 |
| min_sn_len | int | 8 | SN最短长度 |
| max_sn_len | int | 32 | SN最长长度 |
| brand_aliases | object | 见配置 | 品牌别名，大小写不敏感 |

## 依赖说明

插件加载时会自动安装 `requirements.txt` 中的依赖。

图片识别依赖 AstrBot Chat provider，可在插件配置页分别选择主模型和多个备用模型。插件调用每个模型时使用空上下文，因此不会把图片识别请求写入 AstrBot 普通对话。

## 扩展品牌

1. 新建 `providers/xxx.py`，实现 `WarrantyProvider` 接口
2. 在 `providers/__init__.py` 注册
3. 在 `_conf_schema.json` 添加别名配置

## 架构

```
providers/
├── base.py           # Provider 接口（质保查询）
├── western_digital.py # 西部数据
├── seagate.py        # 希捷
├── toshiba.py        # 东芝
├── sandisk.py        # 闪迪
├── ymtc.py           # 致态
└── nand.py           # NAND 物料查询（fdnext 引擎）
main.py               # 主逻辑：触发解析、SN识别、缓存、格式化
vision_client.py      # 插件独立视觉调用与有序备用 provider 回落
hardware_inspection.py # 独立硬盘图片+搜索请求与报告解析，无 AstrBot 依赖
models.py             # 数据模型
cache.py              # JSON 文件缓存
```

## 说明

各品牌查询逻辑隔离在各自 Provider 中，接口变化时只需修改对应 Provider，不影响主插件。

NAND 物料查询基于 iTXTech fdnext 云端 API（`https://fdnext.itxtech.org`），也可对接自部署的 fdnext 实例。
数据来源：[FlashMaster](https://fm.itxtech.org) / [fdnext](https://github.com/iTXTech/fdnext)
