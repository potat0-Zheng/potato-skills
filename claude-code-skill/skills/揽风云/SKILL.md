---
name: converge
description: 信息汇编技能。对指定主题进行多源信息搜索、整理和汇总，产出结构化信息汇编报告或事件时间线。适用于信息汇总、材料整理、事件脉络梳理等场景。
---

# 揽风云 — 信息汇编技能

## 目标

围绕用户指定主题，通过多源搜索与交叉比对，汇总整理信息，产出结构化报告。**不做事实真伪判断**（那是 `/破虚妄` 的职责），专注于"把信息找到、理清、呈现好"。

## 运行模式

| 模式 | 用法 | 输出结构 | 适用场景 |
|------|------|----------|----------|
| `compile`（默认） | `/揽风云` | 信息汇编报告（7 章） | 信息汇总、材料整理 |
| `timeline` | `/揽风云 --mode timeline` | 事件时间线报告（7 章） | 事件脉络、时间线梳理 |

参数：`--summary`（精简输出）、`--scope`（限定范围，如 `--scope 官方`）

## 工作流程

### 阶段 1：解析需求

从指令提取：主题、截止时间、范围约束、深度。

### 阶段 2：首轮搜索

并行 **2–4 次** WebSearch，搜索词设计原则：
- 至少一次中文 / 一次英文（国际语境）
- 覆盖不同角度：官方通报、媒体报道、数据/统计、立场/反应、**现场照片/视频截图**
- 搜索词之间有明确区分度，避免高度重叠

首轮即确定广度，后续只做缺口补充。若搜索结果含 `zhihu.com` 链接，不尝试 WebFetch（必被 L1 阻断），走知乎 API 路径（见下方 L0 策略）。

### 阶段 3：缺口识别与补充

对照首轮结果，按维度查缺：

| 维度 | 检查项 |
|------|--------|
| 事实 | 时间、地点、参与方、结果是否完整 |
| 立场 | 各主要相关方表态是否覆盖 |
| 来源 | 官方/权威媒体/行业分析是否均有 |
| 地域 | 国内/国际视角是否平衡 |
| 时间 | 最新进展是否到截止时间 |

针对缺口执行 **1–2 次**补充搜索。停止条件：核心信息 ≥2 独立来源交叉确认、各方立场已获取、无明显空白。总量控制在两轮以内。

### 阶段 4：信息组织与标注

#### 4.1 引用编号体系

每条事实性陈述末尾标注 `[编号]`，编号从 `[1]` 连续递增，指向来源索引。规则：
- 一 URL 一编号，正文可多次引用同一编号
- 多源交叉确认标注多个编号（如 `[1][3]`）
- 按首次出现顺序分配编号
- 图像引用以 `[图N]` 标注，与文字信源区分
- 推测/分析可引用编号，但不可包装为事实

#### 4.2 来源分级与确定性标记

**来源等级**（仅索引表中标注）：
| 等级 | 含义 | 示例 |
|------|------|------|
| A | 一手官方公报/正式文件 | 政府公告、法律文书 |
| B | 权威通讯社/官方媒体 | 新华社、央视、路透社 |
| C | 商业媒体/行业媒体 | 门户网站、行业刊物 |

**确定性标记**（正文内联，仅用于具体数字/数据）：
| 标记 | 含义 |
|------|------|
| ▲ | 官方渠道确认 |
| ● | 多家独立媒体一致，未经官方文件确认 |
| △ | 单一来源，或不同来源数字有出入 |

所有图像描述默认 △（模型无法视觉验证）；3+ 独立文字来源一致描述同一图像可升级为 ●。

### 阶段 5：撰写与检查

按输出规范组织报告。完成后做覆盖完整性检查：地域 × 主体 × 议题矩阵逐格核实，明确列出已覆盖/未覆盖项及原因。

## 信源获取失败应对

WebFetch 可能因三层原因失败。策略链（按优先级依次尝试）：

| 层级 | 原因 | 特征 | 应对 |
|------|------|------|------|
| L0（知乎专用） | zhihu.com 必被 L1 拦截，但知乎有公开 API | WebSearch 返回 `zhihu.com/question/...` 链接 | `python "{SKILL_DIR}/../知乎/crawl.py" "<URL>" "out.md"` → API 返回完整回答（文本 + `![](图片URL)` + 赞同数）。图片 URL 从返回的 md 中提取，送入 `collect_images.py` 统一下载。API 返回的图片是回答者插入的配图/截图，天然无推荐流污染 |
| L1 | Claude Code 域名安全验证 | `Unable to verify domain X is safe` | ① 用 `site:`/引号限定 2–3 个搜索词拼合 snippet ② `python fetch_page.py "<URL>"`（绕过 L1）③ 多源三角确认 |
| L2 | 网络不可达/被墙 | 连接超时、DNS 失败 | ① Wayback Machine / archive.is ② 搜索引擎摘要先行 ③ RSS/API 绕过 |
| L3 | HTTP 应用层 | 403/429/反爬/登录墙 | 多源三角确认——转向其他可访问的权威信源 |

**知乎工作流**：WebSearch 发现 zhihu 链接 → `crawl.py` 单回答/全问题模式获取全文 → 从 md 中提取 `![](url)` → 图片 URL 传入 `collect_images.py` 下载编码 → 文本内容直接作为信源引用。知乎回答在来源索引中标注等级 C（社交平台），正文中引用其事实主张时标注 `△`。

**核心原则**：搜索摘要（snippet）为默认信息获取方式，WebFetch 仅在摘要不足时使用。Python 脚本单次任务 ≤3 次调用，环境未就绪则回退。

**禁止**：❌ 用外媒中文版替代英文原站（"特供"内容引入系统性扭曲）❌ 对同一 URL/同一 L1 阻断域名反复重试

## 外媒信源清单与获取策略

### 触发条件

外媒信源**默认不激活**。仅在模型判断用户需求涉及以下范畴时启用：

- 国际新闻事件（发生在境外，或涉及外国政府/组织作为主体的新闻）
- 跨国事务（外交关系、国际贸易、国际组织、全球性问题）
- 境外视角（用户明确要求"外媒怎么报""国际反应"等）
- 英文关键词出现在用户指令中（人名、地名、机构名等）

**判断方法**：在阶段 1（解析需求）中，若识别到主题涉及上述范畴，在搜索计划中标注「+外媒」，并在搜索词设计中加入至少一次英文搜索和外媒 `site:` 搜索。若主题纯属中国国内事务（如国内政策、地方新闻、社会事件等），跳过外媒搜索。

### 获取原理

经 2026-06-16 系统实测（测试主题：特朗普 80 岁生日，覆盖 22 家外媒），关键结论如下：

| 通道 | 实测结果 | 可用？ |
|------|----------|--------|
| **WebSearch** | 所有媒体搜索命中，snippet 包含标题、导语、核心数据 | ✅ 主力通道 |
| **WebFetch** | AP News、The Guardian、NYT、France 24、CNN、archive.is 全部 L1 阻断 | ❌ 全灭 |
| **archive.is** | L1 阻断，无法作为绕过路径 | ❌ 不可用 |
| **fetch_page.py 本地** | 用户在中国大陆，被墙站点无法连接 | ❌ 不可用（对被墙站点） |
| **GitHub Code Search** | `gh search code` 搜索公开仓库中引用/摘录的付费文章内容 + `gh api` 直读新闻聚合仓库 | ✅ 可用（零预设，即时） |

**双通道工作流**：WebSearch snippet（主力，覆盖 A/B 级）→ 转载站搜索 → GitHub 补充（C 级媒体恢复路径）→ ≥2 源三角确认。

### 外媒信源分级（按实测可用性）

#### A 级：充分可用（snippet 极丰富 + 多源可交叉）

| 媒体 | 搜索策略 | 实测命中 | Snippet 质量 | 转载路径 |
|------|----------|----------|-------------|----------|
| **AP News** | `site:apnews.com` 或关键词直接搜索 | ✅ | 🟢 极丰富 | 通讯社稿，被 Korea Times / Daily Mail / 2news 等广泛转载 |
| **Reuters** | ⚠️ `site:reuters.com` 可能无命中，改用关键词搜索（去掉 `site:`）+ "Reuters" | ✅ 间接 | 🟢 经 Bernama / Cazin / Vietnam.vn 转载出现 | 通讯社稿，大量转载站可获取 |
| **BBC News** | `site:bbc.com/news` | ✅ | 🟢 丰富 | 无需转载，直接命中 |
| **The Guardian** | `site:theguardian.com` | ✅ 6篇+ | 🟢 极丰富 | 被 Freitag（德）等转载 |
| **NPR** | `site:npr.org` | ✅ 6篇+ | 🟢 丰富 | 无需转载 |
| **France 24** | `site:france24.com` | ✅ 10篇+ | 🟢 极丰富 | AFP 稿在其上可见 |
| **Al Jazeera** | `site:aljazeera.com` | ✅ 5篇+ | 🟢 丰富 | 无需转载 |
| **CNN** | `site:cnn.com` | ✅ 10篇+ | 🟢 极丰富 | 无需转载 |
| **Politico** | `site:politico.com` | ✅ 4篇+ | 🟢 丰富 | 无需转载 |
| **SCMP** | `site:scmp.com` | ✅ 6篇+ | 🟢 丰富 | 无需转载 |
| **Washington Post** | ⚠️ `site:washingtonpost.com` 可能无直接命中，改用关键词 + "Washington Post" | ✅ 间接 | 🟢 经 The Nightly (Australia) 全文转载，署名 Washington Post | WaPo 原文经国际媒体转载流通 |
| **USA Today** | `site:usatoday.com` 或关键词 | ✅ | 🟢 丰富 | Herald Scotland 等转载 |
| **Foreign Policy** | `site:foreignpolicy.com` | ✅ | 🟢 丰富 | 无需转载 |
| **DW / 德国媒体群** | ⚠️ `site:dw.com` 可能无命中，改用 `site:deutschlandfunk.de` 或 `site:tagesspiegel.de` | ✅ 间接 | 🟢 Deutschlandfunk / Tagesspiegel / 3sat 覆盖充分 | 德国公共媒体生态替代 |

#### B 级：薄但可用（snippet 薄或角度单一，需 A 级补充）

| 媒体 | 搜索策略 | 实测命中 | 限制 | 补救 |
|------|----------|----------|------|------|
| **New York Times** | `site:nytimes.com` | ✅ 2篇 | 🟡 Snippet 中等，标题+模糊导语，细节不足 | 用 AP/CNN/Guardian 的 snippet 交叉补充同一事实 |
| **Bloomberg** | `site:bloomberg.com` 可能无命中；改用关键词 + "Bloomberg" | ✅ 间接 | 🟡 仅经 Moneycontrol（印度）/ AFR（澳洲）转载片段出现 | 用 Reuters/CNBC 的转载替代 |
| **WSJ** | `site:wsj.com` 可能无命中；改用关键词 + "Wall Street Journal" | ✅ 间接 | 🟡 仅 Epstein 生日卡诉讼角度被 HuffPost/Daily Beast/Yahoo 转引 | 单角度，需其他媒体补充其他维度 |

#### C 级：WebSearch 不可用，GitHub 可部分恢复

| 媒体 | WebSearch 症状 | GitHub 恢复路径 | 可恢复内容 |
|------|---------------|----------------|-----------|
| **Financial Times** | `site:ft.com` 无命中 | `gh search code "ft.com"` → 聚合仓库（TypeThe0ry/news, mh-weekly）含 FT 摘要 | 标题 + 英文摘要 |
| **The Economist** | `site:economist.com` 无命中 | `gh search code "economist.com"` → HN 存档含文章链接 + 聚合仓库含编辑要点 | 链接 + 讨论 + 摘要 |
| **Foreign Affairs** | `site:foreignaffairs.com` 无命中 | `gh search code "foreignaffairs.com"` → 个人博客常大段引用原文（blockquote） | 原文摘录 |
| **Nikkei Asia** | `site:asia.nikkei.com` 无命中 | `gh search code "nikkei" "asia"` → API 文档 + 摘要仓库偶有收录 | 标题 + 短摘要 |
| **The Atlantic** | `site:theatlantic.com` 无命中 | `gh search code "theatlantic.com"` → 聚合仓库偶有收录 | 标题 + 链接 |

### 搜索工作流

#### 首轮（并行 2-4 次）

当主题触发外媒搜索时，首轮搜索词设计加入：

1. **至少一次英文关键词搜索**（不加 `site:`），捕获通讯社转载 + 多源覆盖
2. **至少一次 A 级媒体 site: 搜索**，如 `site:apnews.com` 或 `site:theguardian.com`
3. **若涉及中东/亚洲**，加一次 `site:aljazeera.com` 或 `site:scmp.com`
4. 搜索词之间保持明确区分度（不同关键词角度、不同媒体）

#### 补充轮（1-2 次 WebSearch + 可选 GitHub）

**WebSearch 补充**：
1. A 级未直接命中的媒体（Reuters / Washington Post）→ 去掉 `site:`，用「关键词 + 媒体名」搜索转载站
2. B 级媒体（NYT / Bloomberg / WSJ）→ 仅在需要特定独家角度时定向搜索

**GitHub 补充**（C 级媒体恢复，两条命令模板）：

```bash
# 方案 A：搜索付费文章在公开仓库中的引用/摘录
gh search code "<媒体域名>" "<标题关键词>" --limit 5 --json path,repository,url
# 命中文件后读取内容
gh api repos/{owner}/{repo}/contents/{path} --jq '.content' | base64 -d

# 方案 B：直读已知新闻聚合仓库的最新内容
gh api repos/TypeThe0ry/news/contents/news/$(date +%Y-%m-%d).md --jq '.content' | base64 -d 2>/dev/null
# 若当日无内容，列目录取最新文件
gh api repos/TypeThe0ry/news/contents/news --jq '.[-1].name'
```

**已知聚合仓库**（定期通过 `gh search repos "news archive rss"` 发现新源并更新此表）：

| 仓库 | 覆盖的 C 级媒体 | 更新频率 | 内容形式 |
|------|----------------|----------|----------|
| `TypeThe0ry/news` | FT, WSJ, NYT | 每日 | 标题 + 英文摘要 + 来源标签 |
| `mikko-huotari/mh-weekly` | Bloomberg, FT, Economist | 每周 | 结构化 JSON + 人工编辑要点 |
| `lavkeshdwivedi/geo-pulse` | NYT | 多日 | 标题 + 一句话摘要 + 来源 |
| `strangeloopcanon/foresight-forge` | NYT | 每日 | 一句话摘要 + 领域标签 |
| `vitoplantamura/HackerNewsRemovals` | Economist | 每日自动 | 文章 URL + 标题 + HN 排名 |
| `Turi-Labs/Newsletter-Editor-Agents` | Economist, NYT | 多日 | HN 帖子存档含链接 |

**GitHub 使用原则**：不替代 WebSearch——仅用于恢复 WebSearch 无法获取的 C 级媒体内容。方案 A（`gh search code`）优先于方案 B（聚合仓库），因方案 A 覆盖面更广。单次任务 GitHub 命令调用 ≤3 次。GitHub 恢复的内容标注来源等级 C（社区摘录/聚合），正文引用标注 △。

#### 三角确认标准

外媒报道的事实主张必须满足以下之一：

- ≥2 家独立外媒 snippet 一致（不同媒体集团）
- 1 家外媒 + 1 家国内官媒一致
- 若仅 1 家外媒 snippet 可获取 → 标注 △（单一来源）

### 注意事项

- **双通道获取**：主力 = WebSearch snippet（A/B 级媒体）；补充 = GitHub 搜索/聚合仓库（C 级媒体恢复）。WebFetch 和 archive.is 不可用
- **通讯社稿优先走转载**：Reuters / AP / AFP 用关键词搜索（去掉 `site:`）比 `site:` 限定更有效
- **禁止用外媒中文版替代英文原站**：中文版经过编辑筛选和翻译加工，引用时注明中文版，来源等级降为 C
- **转载标注**：中文门户网站转载的"据 XX 报道"不视为一手来源，等级标 C，索引注明转载链
- **C 级媒体走 GitHub**：FT / Economist / Foreign Affairs 优先 `gh search code` 搜摘录；Nikkei Asia / The Atlantic 仅聚合仓库碰运气。GitHub 内容等级标 C，正文引用标 △。单次任务 GitHub 命令 ≤3 次

## 输出规范

### 模式一：信息汇编报告（`compile`）

1. **任务概述** — 一句话概括需求和范围
2. **核心内容** — 按主题组织。事实陈述标 `[编号]`，图像标 `[图N]`。原文引用块保留来源名称。确定性标记（▲/●/△）内联于数字之后
3. **来源索引** — `[编号]` 来源名称、URL、等级（A/B/C）、类型。按首次出现顺序排列
4. **图像附录** — 可选。每项含 `[图N]`、声称内容（snippet/配文描述）、出处、URL、视觉验证状态（默认"未验证 △"）
5. **覆盖完整性声明** — 已覆盖/未覆盖维度，未覆盖项注明原因
6. **方法论说明** — 搜索轮次、搜索词设计、来源数、引用主张数
7. **汇编时间戳** — 信息截止时间、汇编完成时间、事件跨度

### 模式二：事件时间线（`timeline`）

1. 事件概述 → 2. 时间线（日期+描述+`[编号]`）→ 3. 各方反应（按主体分类）→ 4. 关键节点分析 → 5. 来源索引 → 6. 图像附录（可选）→ 7. 汇编时间戳

### 摘要模式（`--summary`）

精简核心内容 + 来源列表。不输出完整章节。

## 导出

### HTML

用户要求导出 HTML 时：
1. 读取 `report_template.html`（CSS/结构骨架），替换 `<!-- TITLE -->` 和 `<!-- CONTENT_START/END -->` 之间的内容
2. 文件名从标题自动生成（去非法字符，截断 40 字符），UTF-8 编码
3. 默认目录：用户指定 → 无指定则 `E:\AI\揽风云\`
4. 引用渲染：`<a href="#ref1" class="ref">[1]</a>` + 索引锚点 `<tr id="ref1">`
5. 图像嵌入：调用 `collect_images.py` 一步完成。脚本自动提取每张图片的 alt 文本、DOM 位置（正文/侧栏）、上下文文本，按综合得分排序

```powershell
python C:\Users\11474\.claude\skills\揽风云\collect_images.py '<JSON>' \
  --max-images 5 --output D:\AI\images.json \
  --keywords "邢台,七熙广场,踹头,警方"
```

`--output` 标志将含 data_uri 的完整结果写入文件（避免 STDOUT 截断 base64），STDOUT 仅输出轻量摘要供 AI 选图。

**图像选取标准**（AI 从摘要中选择时）：

1. **资格门槛**：`is_in_body=true` · alt 不含广告/萌宠/综艺/海报等无关词 · 嵌入成功 · 非图表截图
2. **上下文匹配**：`alt` 或 `surrounding_text` 中至少包含一个事件关键词（人物/地点/行为/时间）
3. **优先级排序**（取 Top 3）：信息密度 > 来源权威 > 独特性

依赖：`pip install requests beautifulsoup4 Pillow`（Pillow 可选）。

### Word

调用 `C:\Users\郑懿宸\.claude\skills\破虚妄\tools\export_docx.py` 的 `export_report_v2()`。

## 注意事项

- **不做真伪判断**：不区分事实与谣言。需核查 → `/破虚妄`
- **信息截止点**：必须标注，注明搜索引擎索引延迟可能遗漏最新信息
- **可追溯信源**：每条事实标 `[编号]`，URL 集中索引。设计目标：正文 +4 字符/条，避免 URL 内联造成 ~30% token 膨胀
- **不确定性坦诚**：数字出入不做强行统一，标 △ 列各说法
- **搜索节制**：两轮以内。本技能价值在**整理**，穷尽不如清晰
- **外媒信源条件触发**：外媒搜索默认不激活。仅在模型判断用户需求涉及国际事务/境外视角时启用（见「外媒信源清单与获取策略 → 触发条件」）。启用后走 WebSearch（主力）+ GitHub 补充（C 级恢复）+ 三角确认，不走 WebFetch。C 级媒体通过 `gh search code` 和聚合仓库恢复，GitHub 单次调用 ≤3 次
- **编码**：含中文 Python 脚本不可 `-c` 内联，先 Write 到文件
- **与 `/破虚妄` 关系**：`/破虚妄` ="这事是真的吗"，`/揽风云` ="关于这事都有哪些说法"
