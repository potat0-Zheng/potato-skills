---
name: po-xu-wang
description: 日常工作用的事实核查助手，适用于新闻媒体说法、商业数据统计和社交媒体/内部不确定信息，最终产出结构化核查报告。
model: deepseek-v4-pro
---

# 事实核查技能

## 目标
对用户提供的内容（链接、全文或详细描述）进行严谨的事实核查，区分观点与事实，给出结构化的可信度结论，并清晰说明推理过程。

---

## 运行模式

本技能支持三种运行模式，通过 `--mode` 参数切换：

| 模式 | 用法 | 输出结构 | 适用场景 |
|------|------|----------|----------|
| `verify`（默认） | `/破虚妄` 或 `/破虚妄 --mode verify` | 9 章节核查报告 | 真伪判断、事实核查 |
| `compile` | `/破虚妄 --mode compile` | 信息汇编报告 | 信息汇编、文件整理、多源材料汇总 |
| `timeline` | `/破虚妄 --mode timeline` | 事件时间线报告 | 事件脉络梳理、时间线重构 |

若用户指令中无法判断模式，默认使用 `verify`。若用户的需求明显是"把这些材料整理到一起"或"梳理这个事件的来龙去脉"，应自动选择 `compile` 或 `timeline`。

**联合态只走 `verify` + `facts.json`**（v2）：被三合一编排为第 ③ 阶段时，不进入 `compile` / `timeline`
模式——汇编与时间线是 ② 揽风云的职责，同时启用会产生"谁写汇编"的歧义与互相覆盖。这两个模式是独立态模式。

---

## 输入方式
- 优先接收网页链接或完整文本，由技能直接分析。
- 若链接无法访问或爬取失效，允许用户以详细的描述性文字复述待查内容。
- 用户可在对话中直接说"破虚妄"或"核查这段"，然后粘贴内容。
- 用户可在核查指令后添加 `--summary` 参数，仅输出核查结论与综合可信度评估（跳过中间证据展开）。
- 用户可在核查指令后添加 `--mode compile` 或 `--mode timeline` 切换报告模式。

---

## 技能路径

工具目录的绝对路径是 `C:\Users\郑懿宸\.dsh\skills\po-xu-wang\tools\`。

DSH 环境下每次 pwsh 调用都是全新进程，环境变量不跨调用持久，因此**工具调用一律写这个绝对路径**。
`${SKILL_DIR}` / `{SKILL_DIR}` 这类占位写法**已废止**，本文其他地方不再出现。

**编码注意事项**：
- 含中文的 Python 脚本**不支持**通过 `python -c` 内联执行（PowerShell 5.1 的 UTF-16 LE 编码会导致语法错误）。
- 正确做法：先用 Write 工具将脚本写入 .py 文件，再通过 PowerShell 执行该文件。
- .py 文件必须包含 `# -*- coding: utf-8 -*-` 声明。

---

## 工作流程（内部执行，无需逐条输出中间步骤）

### 阶段 0：网络可达性探活

在开始核查前，**先对本次真正要调用的端点各发一次最小真实请求**，将工具分为"可用"和"不可用（跳过）"两组。

#### 探活脚本模板

**端点清单必须来自工具源码，不得凭印象写域名**（与实现不符的域名表会给出"可用"的假结论）。
据源码列端点：`grep -o 'https\?://[a-zA-Z0-9.-]*' tools/*.py | sort -u`。

```python
# -*- coding: utf-8 -*-
import socket, sys

# 每项＝(host, port)；不要写工具不访问的域名。当前 china_sources.py 用以下四个端点。
targets = {
    "china_sources.piyao":    ("so.news.cn", 443),
    "china_sources.weibo":    ("s.weibo.com", 443),
    "china_sources.qcc":      ("www.qcc.com", 443),
    "china_sources.thepaper": ("www.thepaper.cn", 443),
    "gov_stats":              ("data.stats.gov.cn", 443),
    "world_bank":             ("api.worldbank.org", 443),
    "google_factcheck":       ("contentfactchecktools.googleapis.com", 443),
    "wayback":                ("archive.org", 443),
}

for name, (host, port) in targets.items():
    try:
        s = socket.create_connection((host, port), timeout=5)
        s.close()
        print(f"PROBE:{name}=TCP_OK")
    except Exception as e:
        print(f"PROBE:{name}=UNREACHABLE ({e})")
```

探活结论分三档，方法论说明中如实落档（不许静默跳过，跳过项写入合规账本）：

| 档 | 判据 | 处置 |
|----|------|------|
| 可用 | `TCP_OK` 且随后真实调用返回可解析内容 | 正常调度 |
| 凭证类 | `TCP_OK` 但调用返回空/被拦（微博、企查查常见） | 向用户索取登录态，**不得**记为"网络不可达" |
| 不可达 | `UNREACHABLE` | 先分权限类（沙箱/代理→升级重试）与环境类；确属网络环境才记"跳过"并给替代路径（存档站/远端主机） |

**`TCP_OK` ≠ 工具可用**：TCP 只证明端口通得了。

---

### 阶段 1：内容获取（四级抓取回退链）

按以下顺序尝试获取待核查内容，每一级失败后**自动降级且不可跳过**：

| 级别 | 方式 | 超时 | 失败处理 |
|------|------|------|----------|
| L1 | `pwsh` + `curl` 直接抓取（DSH 无 WebFetch 工具，"WebFetch 不可用"是历史写法） | 15 秒 | 被墙 → 先走 L1b；超时/格式不支持 → 直接 L2 |
| L1b | 存档站替代抓取（`web.archive.org` / `archive.is`，被墙时专用） | 30 秒 | 自动降级到 L2 |
| L2 | 搜索引擎摘要层核（未获取原文全文，见「信源可达性」§2） | — | 自动降级到 L3 |
| L3 | 提示用户粘贴原文内容 | — | 等待用户提供 |

#### L1 curl 命令模板

当 `curl` 返回 "unable to verify if domain is safe" 或超时时，按住上表降级：

```powershell
curl -s -L --max-time 15 -H "User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36" "<目标URL>" | Select-Object -First 500
```

若 curl 不可用（Windows 环境不一定预装），则直接降级到 L3。

#### 抓取结果标注

在报告的"方法论说明"中明确标注最终采用的获取方式：
- "L1 curl 成功获取"
- "L1 被墙 → L1b 存档站替代抓取成功"（见「信源可达性」章节）
- "L1/L1b 均失败 → L2 基于搜索引擎摘要（未获取原文全文）核查"
- "L1/L1b/L2 均失败 → L3 基于用户提供的文本核查"
- "L1 不可用（curl 缺失/权限受限）"：先区分权限类与凭证类并按阶段 0 处置，不得直接判跳过

---

### 阶段 2：内容拆解与类型标记

1. 从文本中提取**整体事实指向**（如：报道的核心主张是"甲杀害了乙"）。
2. 拆出所有可验证的**事实断言**（具体陈述），并将其与观点、评论、推测严格分离。
3. **为每条事实断言标注类型**，用于后续工具匹配：

| 类型标签 | 含义 | 匹配工具 |
|----------|------|----------|
| `statistical` | 涉及经济/社会统计数据 | gov_stats.py, world_bank.py |
| `web_existence` | 涉网页在某时间点是否存在/被修改 | wayback.py |
| `enterprise` | 涉及企业工商信息/高管任职 | china_sources.py (company_lookup) |
| `social_media` | 涉及微博等社交平台发帖 | china_sources.py (weibo_search) |
| `rumor` | 涉及已可能被辟谣的传言 | china_sources.py (piyao), google_factcheck.py |
| `general` | 通用事实（不属以上） | WebSearch 多源检索 |

4. **只登记可裁决真假的事实陈述**，并**一条只承载一个可独立裁决的主张**：
   - 四类主张**不进登记册**（评价性判断 / 法律与专业定性 / 因果推断 / 证明力评价）——其可核部分
     （"某人公开表示…"）仍可登记，但裁决对象是"该意见被如实转述"，不是"该法律结论成立"；
   - 一条 `claim` 内含两个以上可分别给出不同裁决词的子句，即属合并，须拆条（判据见 `CONTRACT-joint.md` §6）。
   把律师意见、当事人转述或四五件事合并成一条判「真实」，读者会读成"权威已作认定 / 四五件都核过了"。
5. **观点部分单独标记**，备后续辨析（这些内容在联合态归 `opinion_analysis`，不占事实断言位）。

---

### 阶段 3：工具匹配预判

根据阶段 2 中事实断言的类型分布，**只调度匹配的工具**，不相关工具直接跳过。

**匹配规则：**
- 所有断言 → WebSearch 多源检索（必执行）
- 有 `rumor` 标签 → 先调 piyao（国内）/ google_factcheck（境外），国内优先
- 有 `statistical` 标签 → gov_stats.py，若有矛盾再调 world_bank.py 交叉验证
- 有 `enterprise` 标签 → china_sources.py (company_lookup)
- 有 `social_media` 标签 → china_sources.py (weibo_search)
- 有 `web_existence` 标签 → wayback.py
- 中文语境内容 → china_sources.py 优先于 google_factcheck.py
- 英文/国际内容 → google_factcheck.py + world_bank.py 优先

#### 中文语境的 china_sources 处理

**按断言类型分流**（取代原先"必须至少调一次"与"不匹配就跳过"的自相矛盾）：

- 断言带 `rumor` / `social_media` / `enterprise` 标签，或需查"某说法在辟谣平台有无记录"
  → **必须调用** china_sources.py（`ChinaVerifyClient().verify()` 或对应子功能）；
- 断言属**事件经过类**（时间、地点、行为、双方主张、监控画面等）→ 辟谣平台与企查查
  **结构上无匹配**，此时不调用，在方法论说明中登记"结构上无匹配"即可，**无须逐条辩解**。

不允许无记录地静默跳过；也不允许把"结构上无匹配"写成"已核查未发现"。

---

### 阶段 4：溯源与比对

并行调度所有匹配且可达的工具（每个工具调用硬超时 **15 秒**，最多重试 **1 次**），具体调用方式见下方"内置工具"章节。

超时或失败的处理：
- 工具在 15 秒内无响应 → 标记为"超时"，报告中注明"工具 X 因网络原因未能调用"
- 工具返回错误 → 重试 1 次，仍失败则同上
- 成功返回 → 结果纳入证据池
- WebSearch 为兜底策略，当所有专用工具不可用时至少保证多源检索可用

然后：
- 对照固定权威来源（政府机构、官方媒体）。
- 对比不同来源的证据，识别数据矛盾、口径差异或断章取义等问题。
- 最后以内部可信源清单作为辅助印证（清单不在技能内维护，由用户按需提供或确认）。

---

### 阶段 5：综合判断

- 为每个事实断言给出核查结论：`真实` / `部分真实` / `失实` / `证据不足`——**只用这四个词**，
  不加括号后缀（如"真实（观点性判断）"：观点性主张本就不该登记为事实断言，见阶段 2 第 4 条）。
- **否定性主张**（"X 未发生 / 官方未回应 / 未见表态"类）默认裁决「证据不足」并注记"检索未见支持证据"；仅当存在权威渠道的明确否定声明时才可判「真实」——**检索不到 ≠ 确认未发生**。
  **且必须以否定句登记**：`claim` 写「截至 X 未见 Y」，禁止用肯定句登记（"某机关已作出通报"）
  而把"尚无"写进短标题——同一张卡里正文、标题、裁决三向相反，读者按标题读成"确认没有"、按正文读成"确认有了"。
- **引文类断言**须标注引文来源等级并尽量与多版本比对；证据仅到转载引文/摘要层的，不得单独支撑「真实」，
  应表述为「报道属实（原文未核）」或降级。**等级值的口径见 `CONTRACT-joint.md` §2，本处不重复列举**——
  该等级量的是"证据本身的形态"，不是"本次核验取得了什么"；"我这次没抓到原文全文"不构成降级理由
  （那是归档完整度问题，记在 `sources.json.fetched_file` 缺位上）。
- 对整体事实指向进行吻合度评估（如：整体真实，但细节有误）。
- 开展观点辨析：分析隐含前提、逻辑谬误、立场偏差等，用严谨的推理说明从证据到结论的推导过程（例如："虽然 A 数据真实，但 B 存在断章取义，因此结论不可靠"）。
- 评估综合可信度时，除真实性本身，还需考虑：
  - **证据充分性**：信息源数量、一致性、权威性
  - **信息覆盖完整性**：是否存在已知的未覆盖领域（如某类媒体缺失、某方立场未获取等），若有应明确列出

---

## 信源可达性：被墙外媒的应对策略

国际话题核查中，外媒报道是关键信源。**WebSearch 搜索阶段的节点在境外，不受本地网络限制**；被墙阻断的是 curl 抓取原文阶段（L1），以及 world_bank.py / google_factcheck.py 等需翻墙工具的 API 调用（阶段 0 探活已识别）。

以下策略与四级回退链（L1→L1b→L2→L3）互补，聚焦于"被墙但仍需获取内容"的场景：

### 1. 存档站注入回退链

L1 curl 因被墙而失败时（非超时/格式不支持），按回退链先走 **L1b 存档站**，不跳过：

- `https://web.archive.org/web/<原URL>` — 本技能内置 wayback.py 已封装此功能，但 wayback.py 设计用于"验证历史快照"，此处用作抓取替代入口
- `https://archive.is/<原URL>` — wayback.py 未命中时手动构造

存档站成功获取的，在方法论说明中标注为"L1 被墙 → L1b 存档站替代抓取"，视为等效于 L1 获取。存档站也失败，才降级到 L2（摘要层）。

### 2. 搜索引擎摘要作为独立证据层

WebSearch 返回的结果摘要往往已包含可核查的关键事实——数字、日期、主体、结论。同一事实通常被多个来源的摘要交叉覆盖，**摘要本身即可支撑初步核查结论**。

这既是回退链的 **L2**，也可以在阶段 2（内容拆解）之前就主动利用：先用摘要交叉确认事实轮廓，确有争议点再针对性地走存档站或 L1。已在注意事项中覆盖"基于搜索引擎摘要进行核查"的标注要求（摘要层证据按 `evidence_level=4` 登记，不得单独支撑「真实」）。

### 3. 多源交叉确认消化缺口

阶段 4（溯源与比对）已要求多源对比。某一外媒被墙且存档站未命中时，转向其他**可访问**的权威信源覆盖同一事实——路透社、美联社、半岛电视台、区域性媒体等。交叉确认本身就是缺口填补，不要对被墙的单一信源反复尝试。

### 4. RSS / API 绕过

对反复需要的外媒（路透社、美联社等），优先尝试其 RSS feed 或公开 API。内容与主站一致但不走主站域名渲染，被墙概率更低。若某外媒在多次核查中都成为关键信源，建议预置其 RSS/API 入口。

### 禁止项

- ❌ **使用外媒中文版替代英文原站**。纽约时报中文网、BBC 中文、FT 中文网、DW 中文、日经中文网等中文版本常生产与主站内容不一致的"特供"报道——或删减、或改写、或另起议题。这种不一致不是翻译偏差，而是面向不同受众的编辑策略。用特供版的事实去"核查"另一个事实，是在扭曲之上叠加扭曲。
- ❌ **对同一被墙 URL 反复重试**。L1 被墙 → 存档站 → L2，不走回头路。

---

## 输出规范

最终直接向用户呈现结构化报告，无需暴露过程步骤细节。

---

### 模式一：核查报告（`--mode verify`，默认）

报告必须包含以下固定部分（按顺序）：

1. **原始内容精要**（节选或压缩，说明文本的整体事实指向）
2. **事实断言清单**（每一条独立的可核查陈述，含类型标签）
3. **核查结论**（逐条标注：真实/部分真实/失实/证据不足，并简述理由）
4. **关键证据与来源**（列出支撑结论的核心依据，附链接或出处）
5. **证据矛盾标注**（若不同来源存在冲突，明确指出矛盾点）
6. **观点辨析与推理说明**（对观点部分进行逻辑推理，详细写出从证据到结论的推导链条）
7. **综合可信度评估**（包含真实性判断、证据充分性判断、信息覆盖完整性评估，如"信息不足，存疑"等）
8. **方法论说明**（简述本报告是如何得出当前结论的，例如参考了哪些类型的来源，为什么选择某些证据等。必须包含：实际采用的抓取方式、china_sources.py 调用情况、跳过工具的原因）
9. **核查时间戳**（记录本次核查时间，以及原始信息的发布时间，如可获取）

---

### 模式二：信息汇编报告（`--mode compile`）

适用于信息汇编、文件整理、多源材料汇总场景。报告结构：

1. **任务概述**（用户需求的一句话概括）
2. **核心内容**（按用户要求的主题组织，每个主题为一个章节，包含原文/摘要和来源标注）
3. **来源索引**（所有引用来源的列表，含名称、链接、类型标注：官方媒体/自媒体/个人/行业协会等）
4. **信息覆盖完整性声明**（列出已覆盖和未覆盖的领域/立场/来源类型）
5. **汇编时间戳**

---

### 模式三：事件时间线（`--mode timeline`）

适用于事件脉络梳理、时间线重构场景。报告结构：

1. **事件概述**（一句话概括）
2. **时间线**（按时间顺序排列的关键节点，每项含日期、事件描述、来源）
3. **各方反应**（按主体分类列出反应和表态）
4. **关键节点分析**（转折点识别和简要分析）
5. **来源索引**
6. **汇编时间戳**

---

### 摘要模式（`--summary`，可与任一模式叠加）

仅输出：
- 对应模式的精简版本
- 对于 `verify`：核查结论 + 综合可信度评估（一句话结论 + 核心局限说明）
- 对于 `compile`：核心内容精简 + 来源列表
- 对于 `timeline`：时间线精简 + 关键节点

### 联合模式（三合一：产出 `facts.json` 供 build_report 装配）

被 san-he-yi（三合一）编排为第 ③ 阶段时，核查结论不写 Word/HTML，而是把登记册写入 `data/{event_id}/facts.json`。

**字段表不在这里**——`facts.json` 的 schema 唯一权威是 `CONTRACT-joint.md §11`（与 ② 的工件同处）。
本技能不复制字段、不即兴加字段。

登记阶段的硬约束（判据一律在契约，本处不复述）：

| 事项 | 依据 |
|------|------|
| 断言颗粒度、"哪些主张不进登记册"（评价性 / 法律定性 / 因果推断 / 证明力评价） | §6 |
| 证据等级口径（量证据形态，不量"我这次抓到没抓到"）；证据行不得写抓取动作 | §2 |
| 裁决词只用 4 个；否定性主张必须以否定句登记 | §3 |
| `evidence[]` 只放支撑证据，"反证 / 检索未见"写进 `reason` | §11 · 门禁 G13 |
| 裁决必须回写叙事：`证据不足 / 失实` 的具名数字不得在第一、二、五章直陈 | §6 · 门禁 R5 |
| 口径冲突只用 §11 定死的那一种 `conflicts` 形态 | §11 · 门禁 G13 |
| verify 九章在联合态的去向（哪章映射到哪个字段、哪章不做） | §11 首段表格 |

装配端只消费 `claims`；编号按 `claims` 顺序（#1 起），`ref` 必须落在 `sources.json` 的编号域内。
**独立态**（未接入三合一）仍按上文九章节输出，不受本节影响。

---

### 导出 Word

用户说"导出 Word"/"生成 docx"时，使用 export_docx.py 将报告结构化为 .docx 文件。

**传统核查报告**使用 `export_report()`（固定9章节）：
```powershell
# 工具绝对路径：C:\Users\郑懿宸\.dsh\skills\po-xu-wang\tools
# 先写入脚本文件，再执行
python <脚本文件.py>
```

**灵活模式报告**使用 `export_report_v2()`（自定义章节列表）。章节类型：`heading`、`text`、`quote`、`table`、`timeline`、`keyvalue`。

详细用法见 `tools/export_docx.py` 文件末尾的示例代码。

---

## 使用示例

```
用户：check 请核查这篇报道：https://example.com/article123
技能：（按 verify 模式输出 9 章节报告）

用户：check --summary 这篇内容可信吗？[粘贴文本]
技能：（按摘要模式输出核查结论 + 可信度评估）

用户：check --mode compile 针对oppo文案事件，整理武汉大学的声明和相关媒体报道
技能：（按 compile 模式输出信息汇编报告）

用户：check --mode timeline 梳理OPPO母亲节文案事件的完整时间线
技能：（按 timeline 模式输出事件时间线）

用户：把报告导出 Word 到 E:\reports
技能：（调用 export_docx.py 生成 .docx）
```

---

## 注意事项

- 若用户仅提供复述内容而非原始链接，必须在报告的"方法论说明"中注明"基于用户描述进行核查，未直接获取原始链接"。
- 若 L1（curl）与 L1b（存档站）均失败，报告中须注明"基于搜索引擎摘要进行核查，未获取原文全文"。
- **被墙外媒**：不依赖本地网络直连外媒网站。具体策略见「信源可达性：被墙外媒的应对策略」章节。严格执行禁止项——尤其不使用外媒中文版替代英文原站。
- 始终保持证据优先，避免用大模型内部知识替代外部查证。
- 综合可信度评估必须包含对自身判断局限性的说明。
- **工具调用一律写绝对路径** `C:\Users\郑懿宸\.dsh\skills\po-xu-wang\tools\`——DSH 每次 pwsh 都是新进程，
  `$env:SKILL_DIR` 不跨调用持久（改为相对/占位写法是历史遗留，已废止）。
- **所有工具调用使用后台任务（run_in_background）并行执行**，15 秒后通过 `job_output` 取结果，超时则标记跳过。
  （DSH 取后台输出的工具是 `job_output`；"TaskOutput"是历史写法。）
- **中文新闻优先使用 china_sources.py 的国内核查源**，境外工具仅作为补充；调用与否按阶段 3 的
  「按断言类型分流」判据，事件经过类断言属默认豁免。
- 抓取内容时遵循四级回退链（L1→L1b→L2→L3），**不可跳过**，并明确标注实际使用的获取方式。
- **含中文的 Python 脚本不可通过 `python -c` 内联执行**，必须先 Write 到 .py 文件再执行。
- 阶段 0 网络探活**必须执行**（探真正要调的端点），探活结果影响后续全部工具调度决策。

---

## 内置工具

所有工具位于 `C:\Users\郑懿宸\.dsh\skills\po-xu-wang\tools\` 目录下。调用时统一通过 PowerShell 执行 Python 脚本文件（非内联）。

**注意**：由于 PowerShell 5.1 编码限制，所有含中文的 Python 调用必须先通过 Write 工具写入 .py 临时文件，再通过 PowerShell 执行该文件。

### 工具一览

| 工具文件 | 用途 | 适用断言类型 | 网络要求 |
|----------|------|-------------|----------|
| `china_sources.py` | 辟谣（so.news.cn）/微博/企查查/澎湃新闻 | rumor, social_media, enterprise（**纯事件经过类无匹配**，见阶段 3） | 国内可通；微博/企查查另需登录态 |
| `gov_stats.py` | 国家统计局公开数据 | statistical | 国内可通 |
| `world_bank.py` | 世行数据交叉验证 | statistical（二次验证） | 需翻墙 |
| `google_factcheck.py` | 已有核查记录检索 | rumor | 需翻墙 |
| `wayback.py` | 网页历史快照 | web_existence | 需翻墙 |
| `export_docx.py` | 报告导出 Word（支持传统+灵活模式） | — | 无需网络 |

> `wayback.py` 在正文里还兼任"被墙时的抓取替代入口"（L1b），这是**两种角色同一个工具**：
> 写报告时按实际用法标注（验证历史快照 / 替代抓取），不要因为工具名而漏标 L1b。

---

### 工具: china_sources.py（优先使用）

封装中国本土核查源，**大陆网络可直接访问**。

#### 调用方式

先写入脚本文件，再执行：

```python
# -*- coding: utf-8 -*-
import sys, json
sys.path.insert(0, r'C:\Users\郑懿宸\.dsh\skills\po-xu-wang\tools')
from china_sources import ChinaVerifyClient
client = ChinaVerifyClient()

# 辟谣搜索
print(json.dumps(client.piyao_search('某传言'), ensure_ascii=False))

# 微博搜索
print(json.dumps(client.weibo_search('关键词'), ensure_ascii=False))

# 企业工商查询
print(json.dumps(client.company_lookup('OPPO广东移动通信有限公司'), ensure_ascii=False))

# 企业高管任职验证
print(json.dumps(client.company_verify_executive('公司名', '高管名'), ensure_ascii=False))

# 澎湃新闻搜索
print(json.dumps(client.thepaper_search('话题'), ensure_ascii=False))

# 批量自动核实（中文语境强制推荐使用此方法）
print(json.dumps(client.verify('断言文本', claim_type='rumor'), ensure_ascii=False))
```

#### claim_type 参数

| 值 | 适用场景 | 自动调用源 |
|----|---------|-----------|
| `rumor` | 传言/辟谣类 | piyao + thepaper |
| `enterprise` | 企业/高管信息 | qcc.com |
| `social_media` | 社交平台发帖 | weibo.com |
| `general` | 通用 | piyao + thepaper + weibo |

#### 使用规则

- **中文新闻核查必须调用此工具**（强制检查点），因为其 API 端点在国内可通。
- `company_verify_executive()` 可精确验证"某人是某公司高管"类断言。
- 微博搜索结果反映公众讨论热度，可作为"引发大规模舆情"的间接证据。
- 若辟谣平台有匹配结果，直接在核查结论中引用并附原文链接。
- 若此工具跳过未调用，必须在方法论说明中记录原因。

---

### 工具: gov_stats.py

封装国家统计局公开 API (easyquery + V2.0)，用于核查中国经济统计数据。

#### 调用方式

```python
# -*- coding: utf-8 -*-
import sys, json
sys.path.insert(0, r'C:\Users\郑懿宸\.dsh\skills\po-xu-wang\tools')
from gov_stats import query, search
result = query('CPI', freq='monthly', start='202301', end='202512')
print(json.dumps(result, ensure_ascii=False))
```

#### 预设指标速查

| 名称 | 指标 | 频率 |
|------|------|------|
| CPI | 居民消费价格指数 | monthly |
| GDP | 国内生产总值(现价) | quarterly |
| GDP_index | GDP 指数(上年=100) | quarterly |
| unemployment | 城镇调查失业率 | monthly |
| retail_sales | 社会消费品零售总额 | monthly |
| PMI | 制造业采购经理指数 | monthly |
| money_supply | 货币供应量 M0/M1/M2 | monthly |
| PPI | 工业生产者出厂价格 | monthly |
| import_export | 进出口总额 | monthly |
| fixed_investment | 固定资产投资 | monthly |
| real_estate_investment | 房地产开发投资额 | monthly |
| population | 年末总人口 | annual |
| birth_rate | 人口出生率 | annual |

#### 使用规则

- 当待核查内容包含上述任一指标的具体数值时调用，优先使用相同时间段和口径。
- 发现的差异须在"证据矛盾标注"中明确列出。
- 若请求失败（网络问题等），在"方法论说明"中注明。

---

### 工具: world_bank.py

封装世界银行公开数据 API，用于跨国经济指标交叉验证。**需翻墙**，在探活通过后才调度。

#### 调用方式

```python
# -*- coding: utf-8 -*-
import sys, json
sys.path.insert(0, r'C:\Users\郑懿宸\.dsh\skills\po-xu-wang\tools')
from world_bank import cross_validate
result = cross_validate('GDP', nbs_value=18.1e12, year=2023)
print(json.dumps(result, ensure_ascii=False))
```

#### NBS → 世行指标对照

| NBS 指标 | 世行代码 | 世行名称 |
|----------|----------|----------|
| GDP | NY.GDP.MKTP.CD | GDP (现价美元) |
| GDP_index | NY.GDP.MKTP.KD.ZG | GDP 年增长率 |
| GDP_per_capita | NY.GDP.PCAP.CD | 人均 GDP |
| CPI_annual | FP.CPI.TOTL.ZG | CPI 通胀率 |
| unemployment | SL.UEM.TOTL.ZS | 失业率 (ILO) |
| population | SP.POP.TOTL | 人口总数 |
| birth_rate | SP.DYN.CBRT.IN | 出生率 |

#### 使用规则

- 仅在 NBS 数据与待查内容存在矛盾时调用，作为第三方参照。
- `cross_validate()` 返回偏离百分比：<3% 一致，3-10% 小幅偏离（可能口径差异），>10% 需重点核查。
- 世行数据可能与 NBS 存在汇率换算和口径差异，报告中需注明。

---

### 工具: google_factcheck.py

封装 Google Fact Check Tools API。**需翻墙**，在探活通过后才调度。

#### 调用方式

```python
# -*- coding: utf-8 -*-
import sys, json
sys.path.insert(0, r'C:\Users\郑懿宸\.dsh\skills\po-xu-wang\tools')
from google_factcheck import make_report
report = make_report('某声称内容')
print(report['summary'])
```

#### 使用规则

- 仅在网络可达且核查内容为英文/国际话题时优先使用；中文内容优先使用 china_sources.py 的辟谣平台。
- `make_report()` 聚合多家核查机构评级，判断共识方向 (True/False/Mixed)。
- 共识为 "False" 且多家权威机构一致时，可直接作为事实依据。

---

### 工具: wayback.py

封装 Internet Archive Wayback Machine API。**需翻墙**，在探活通过后才调度。

#### 调用方式

```python
# -*- coding: utf-8 -*-
import sys, json
sys.path.insert(0, r'C:\Users\郑懿宸\.dsh\skills\po-xu-wang\tools')
from wayback import verify_text
result = verify_text('https://example.com/article', '声称的关键文本')
print(json.dumps(result, ensure_ascii=False))
```

#### 使用规则

- 仅在待查内容涉及"某网站曾发布/删除/修改某内容"时调用。
- `verify_content_existed()` 在多个快照中自动搜索目标文本。
- 快照获取较慢（10-30 秒），报告中标注验证结果。

---

### 工具: export_docx.py

将结构化报告导出为格式化的 Word 文档。无需网络。

**支持两种调用模式**（详见模块文档和代码末尾示例）：

| 函数 | 章节结构 | 适用场景 |
|------|----------|----------|
| `export_report()` | 传统固定9章节 dict | 核查报告（向后兼容） |
| `export_report_v2()` | 灵活自定义章节 list | 汇编报告、时间线等所有类型 |

**灵活模式章节类型**：`heading`、`text`、`quote`、`table`、`timeline`、`keyvalue`

#### 调用方式

见 `tools/export_docx.py` 文件末尾的两段示例（灵活模式 `export_report_v2()` / 传统模式 `export_report()`）——
示例随代码维护，本文档不再复制一份，避免两处漂移。

#### 使用规则

- 用户在核查完成后说"导出 Word"/"生成 docx"/"保存报告"时调用。
- `output_dir` 默认为用户指定的目录，不存在则自动创建。
- 文件名从 title 自动生成（非法字符被过滤，截断至 40 字符）。
- **`export_report()` 会自动检测 sections 类型**：若为 list 则路由到 v2 灵活布局，若为 dict 则走传统固定布局。
- **`credibility` 中新增 `coverage_completeness` 字段**，用于记录信息覆盖完整性评估结果。
