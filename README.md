# 在线硬件查询插件

AstrBot 在线硬件产品查询工具，支持质保查询、NAND 物料/颗粒识别等功能，支持文本和图片识别SN。
图片识别使用 AstrBot 视觉模型 provider；留空则自动使用全局 `default_image_caption_provider_id`。

## 当前支持功能

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
- `质保 西数 WXA1A12345678`
- `质保 希捷 ZR1A12345678`
- `质保 东芝 X5U2A02E12345`

### 质保查询 - 图片查询

发送或引用产品标签图片，带上品牌关键词：
- `质保 西数` + 图片
- `质保 希捷` + 图片
- `质保 致态` + 图片

插件会自动从图片中提取SN，然后查询对应品牌。

### NAND 物料查询 - 文本查询

格式：`查颗粒 型号` 或 `查物料 查询文本`

示例：
- `颗粒 MT29F64G08CBABA` — 查询美光 8GB MLC NAND 颗粒详情
- `物料 2C64444BA900` — 通过 Flash ID 查询颗粒信息
- `flash id 2C,64,44,4B,A9,00` — 支持逗号/空格分隔的 Flash ID
- `pn查询 MT29F1T08EQLCEB2` — 查询美光 128GB QLC 颗粒
- `型号查询 K9ABGD8U0D` — 查询三星颗粒

### NAND 物料查询 - 图片查询

发送或引用 NAND 芯片照片，带上颗粒关键词：
- `颗粒` + 图片
- `物料` + 图片

插件会通过 LLM 或本地 OCR 从芯片丝印中自动识别型号（PN / Flash ID），然后查询。
图片识别模式复用 `image_sn_mode` 配置项（llm / off）。

## 配置项

| 配置项 | 类型 | 默认值 | 说明 |
|--------|------|--------|------|
| enabled | bool | true | 启用插件 |
| default_region | string | CN | 默认地区代码 |
| cache_ttl_seconds | int | 604800 | 缓存时间（秒），默认一周 |
| timeout_seconds | int | 15 | 请求超时（秒） |
| llm_summary | bool | false | 启用模型总结（费token） |
| trigger_keywords | list | ["查质保", "查保修"] | 质保查询命令关键词；需 @机器人 且严格匹配“关键词 空格 品牌 空格 SN/图片说明” |
| nand_query_enabled | bool | true | 启用 NAND 物料查询功能 |
| nand_api_base | string | https://fdnext.itxtech.org | fdnext API 地址，可填自建实例 |
| nand_trigger_keywords | list | ["查颗粒", "查物料", ...] | NAND 查询命令关键词；需 @机器人。文本查询需“关键词 空格 参数”，带图/引用图可只发关键词 |
| image_sn_mode | string | llm | 图片SN识别模式：llm/off |
| min_sn_len | int | 8 | SN最短长度 |
| max_sn_len | int | 32 | SN最长长度 |
| brand_aliases | object | 见配置 | 品牌别名，大小写不敏感 |

## 依赖说明

插件加载时会自动安装 `requirements.txt` 中的依赖。

图片识别依赖 AstrBot 视觉模型 provider，不再需要额外安装本地 OCR。

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
models.py             # 数据模型
cache.py              # JSON 文件缓存
```

## 说明

各品牌查询逻辑隔离在各自 Provider 中，接口变化时只需修改对应 Provider，不影响主插件。

NAND 物料查询基于 iTXTech fdnext 云端 API（`https://fdnext.itxtech.org`），也可对接自部署的 fdnext 实例。
数据来源：[FlashMaster](https://fm.itxtech.org) / [fdnext](https://github.com/iTXTech/fdnext)