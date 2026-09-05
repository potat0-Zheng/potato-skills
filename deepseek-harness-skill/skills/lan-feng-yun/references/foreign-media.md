# 外媒信源清单与获取策略

> 环境注记：本文档由原 SKILL.md「外媒信源清单与获取策略」章节整体迁出。其中「WebFetch / archive.is / L1 阻断」为 Claude Code 语境下的历史实测结论；在 DSH 环境（无 WebFetch 工具）下，全文抓取统一改走 `pwsh` + Python `requests`，GitHub 通道经 `gh` CLI 执行（已确认本机可用）。

## 触发条件

外媒信源**默认不激活**。仅在模型判断用户需求涉及以下范畴时启用：

- 国际新闻事件（发生在境外，或涉及外国政府/组织作为主体的新闻）
- 跨国事务（外交关系、国际贸易、国际组织、全球性问题）
- 境外视角（用户明确要求"外媒怎么报""国际反应"等）
- 英文关键词出现在用户指令中（人名、地名、机构名等）

**判断方法**：在阶段 1（解析需求）中，若识别到主题涉及上述范畴，在搜索计划中标注「+外媒」，并在搜索词设计中加入至少一次英文搜索和外媒 `site:` 搜索。若主题纯属中国国内事务（如国内政策、地方新闻、社会事件等），跳过外媒搜索。

## 获取原理

经 2026-06-16 系统实测（测试主题：特朗普 80 岁生日，覆盖 22 家外媒），关键结论如下：

| 通道 | 实测结果 | 可用？ |
|------|----------|--------|
| **WebSearch** | 所有媒体搜索命中，snippet 包含标题、导语、核心数据 | ✅ 主力通道 |
| **WebFetch** | AP News、The Guardian、NYT、France 24、CNN、archive.is 全部 L1 阻断 | ❌ 全灭 |
| **archive.is** | L1 阻断，无法作为绕过路径 | ❌ 不可用 |
| **fetch_page.py 本地** | 用户在中国大陆，被墙站点无法连接 | ❌ 不可用（对被墙站点） |
| **GitHub Code Search** | `gh search code` 搜索公开仓库中引用/摘录的付费文章内容 + `gh api` 直读新闻聚合仓库 | ✅ 可用（零预设，即时） |

**双通道工作流**：WebSearch snippet（主力，覆盖 A/B 级）→ 转载站搜索 → GitHub 补充（C 级媒体恢复路径）→ ≥2 源三角确认。

## 外媒信源分级（按实测可用性）

### A 级：充分可用（snippet 极丰富 + 多源可交叉）

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

### B 级：薄但可用（snippet 薄或角度单一，需 A 级补充）

| 媒体 | 搜索策略 | 实测命中 | 限制 | 补救 |
|------|----------|----------|------|------|
| **New York Times** | `site:nytimes.com` | ✅ 2篇 | 🟡 Snippet 中等，标题+模糊导语，细节不足 | 用 AP/CNN/Guardian 的 snippet 交叉补充同一事实 |
| **Bloomberg** | `site:bloomberg.com` 可能无命中；改用关键词 + "Bloomberg" | ✅ 间接 | 🟡 仅经 Moneycontrol（印度）/ AFR（澳洲）转载片段出现 | 用 Reuters/CNBC 的转载替代 |
| **WSJ** | `site:wsj.com` 可能无命中；改用关键词 + "Wall Street Journal" | ✅ 间接 | 🟡 仅 Epstein 生日卡诉讼角度被 HuffPost/Daily Beast/Yahoo 转引 | 单角度，需其他媒体补充其他维度 |

### C 级：WebSearch 不可用，GitHub 可部分恢复

| 媒体 | WebSearch 症状 | GitHub 恢复路径 | 可恢复内容 |
|------|---------------|----------------|-----------|
| **Financial Times** | `site:ft.com` 无命中 | `gh search code "ft.com"` → 聚合仓库（TypeThe0ry/news, mh-weekly）含 FT 摘要 | 标题 + 英文摘要 |
| **The Economist** | `site:economist.com` 无命中 | `gh search code "economist.com"` → HN 存档含文章链接 + 聚合仓库含编辑要点 | 链接 + 讨论 + 摘要 |
| **Foreign Affairs** | `site:foreignaffairs.com` 无命中 | `gh search code "foreignaffairs.com"` → 个人博客常大段引用原文（blockquote） | 原文摘录 |
| **Nikkei Asia** | `site:asia.nikkei.com` 无命中 | `gh search code "nikkei" "asia"` → API 文档 + 摘要仓库偶有收录 | 标题 + 短摘要 |
| **The Atlantic** | `site:theatlantic.com` 无命中 | `gh search code "theatlantic.com"` → 聚合仓库偶有收录 | 标题 + 链接 |

## 搜索工作流

### 首轮（并行 2-4 次）

当主题触发外媒搜索时，首轮搜索词设计加入：

1. **至少一次英文关键词搜索**（不加 `site:`），捕获通讯社转载 + 多源覆盖
2. **至少一次 A 级媒体 site: 搜索**，如 `site:apnews.com` 或 `site:theguardian.com`
3. **若涉及中东/亚洲**，加一次 `site:aljazeera.com` 或 `site:scmp.com`
4. 搜索词之间保持明确区分度（不同关键词角度、不同媒体）

### 补充轮（1-2 次 WebSearch + 可选 GitHub）

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

### 三角确认标准

外媒报道的事实主张必须满足以下之一：

- ≥2 家独立外媒 snippet 一致（不同媒体集团）
- 1 家外媒 + 1 家国内官媒一致
- 若仅 1 家外媒 snippet 可获取 → 标注 △（单一来源）

## 注意事项

- **双通道获取**：主力 = WebSearch snippet（A/B 级媒体）；补充 = GitHub 搜索/聚合仓库（C 级媒体恢复）。WebFetch 和 archive.is 不可用
- **通讯社稿优先走转载**：Reuters / AP / AFP 用关键词搜索（去掉 `site:`）比 `site:` 限定更有效
- **禁止用外媒中文版替代英文原站**：中文版经过编辑筛选和翻译加工，引用时注明中文版，来源等级降为 C
- **转载标注**：中文门户网站转载的"据 XX 报道"不视为一手来源，等级标 C，索引注明转载链
- **C 级媒体走 GitHub**：FT / Economist / Foreign Affairs 优先 `gh search code` 搜摘录；Nikkei Asia / The Atlantic 仅聚合仓库碰运气。GitHub 内容等级标 C，正文引用标 △。单次任务 GitHub 命令 ≤3 次
