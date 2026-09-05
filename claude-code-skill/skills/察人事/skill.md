---
name: track-record
description: 轻量级碎片化事实核查，适用于"某人是否说过某话""某人是否做过某事""某事件是否发生过"等单一主张的快速验证。输出简洁的裁决卡片，而非完整核查报告。
model: deepseek-v4-pro
---

# 轻量事实核查技能

## 目标

对单一、碎片化的事实主张进行快速核查——谁说了什么、谁做了什么、某件事是否发生过。与 `破虚妄` 技能的系统性多断言核查不同，本技能聚焦于**单主张 + 快速路由 + 简洁输出**。

## 适用场景

- "某某是否说过这句话？"
- "某某是否在 X 时间做过 Y 事？"
- "某某是不是 Z 公司的高管/创始人？"
- "某内容是否曾在某网站发布过？"
- "某事件是否真的发生过？"

**不适用场景**：涉及多项事实断言的深度报道核查、统计数据的精确验证——这些请使用 `破虚妄` 技能。

## 输入方式

用户以自然语言直接提问，无需粘贴链接或全文。示例：

```
察人事 巴菲特真的说过"别人贪婪时我恐惧"吗？
察人事 马斯克2024年是否参加过上海的人工智能大会？
察人事 张三是阿里巴巴的CTO吗？
察人事 example.com在2023年是否发过一篇关于气候变化的文章？
```

---

## 技能路径变量

- `{{skill_dir}}` → 本技能目录 (`skills/察人事/`)
- 破虚妄 技能工具目录：`{{skill_dir}}/../破虚妄/tools/`

---

## 工作流程（三步法）

### 步骤 1：主张解析与分类

从用户问题中提取以下要素，并自动归类：

| 要素 | 说明 | 示例 |
|------|------|------|
| 主体 (who) | 声称的行为主体 | "巴菲特"、"张三" |
| 主张 (what) | 声称的具体内容 | "说过别人贪婪时我恐惧"、"是CTO" |
| 时间 (when) | 声称发生的时间（可为空） | "2024年"、"2023年" |
| 来源 (where) | 声称发生的平台/场合（可为空） | "微博"、"example.com" |

根据提取结果，将主张归入以下类型之一：

| 类型标签 | 判断依据 | 典型问法 |
|----------|----------|----------|
| `quote` | 主张某人说过/写过某段话 | "XX 是否说过……" |
| `action` | 主张某人做过某事 | "XX 是否参加过/做过……" |
| `position` | 主张某人担任某职务/身份 | "XX 是不是 YY 公司的……" |
| `event` | 主张某事件发生 | "XX 事件是否发生过……" |
| `web_content` | 主张某内容曾在某网站存在 | "XX 网站是否发过……" |

### 步骤 2：按类型路由工具

**只调度 1-2 个最相关工具**，不做全工具并行。工具均复用 破虚妄 技能的现有实现，路径为 `{{skill_dir}}/../破虚妄/tools/`。

| 类型 | 中文语境（优先） | 英文/国际语境 | 兜底 |
|------|-----------------|-------------|------|
| `quote` | weibo_user_posts / weibo_search | WebSearch | WebSearch |
| `action` | thepaper_search + WebSearch | WebSearch | WebSearch |
| `position` | company_verify_executive / company_lookup | WebSearch | WebSearch |
| `event` | piyao_search + thepaper_search | google_factcheck + WebSearch | WebSearch |
| `web_content` | wayback verify_content_existed | wayback verify_content_existed | WebSearch |

**调度原则：**
- 中文语境 → china_sources.py 优先，WebSearch 为辅
- 英文/国际语境 → WebSearch 为主，google_factcheck.py 为辅
- `web_content` 类型 → wayback.py 为唯一专用工具
- 每个工具调用硬超时 **10 秒**（比 破虚妄 更短），失败即跳过，不重试
- 不做探活——直接调用，失败则降级到 WebSearch

**工具调用速查：**

```bash
# 微博搜索（quote 类型）
python -c "
import sys, json
sys.path.insert(0, '{{skill_dir}}/../破虚妄/tools')
from china_sources import ChinaVerifyClient
client = ChinaVerifyClient()
print(json.dumps(client.weibo_search('关键词'), ensure_ascii=False))
print(json.dumps(client.weibo_user_posts('昵称'), ensure_ascii=False))
"

# 辟谣 + 澎湃搜索（event 类型）
python -c "
import sys, json
sys.path.insert(0, '{{skill_dir}}/../破虚妄/tools')
from china_sources import ChinaVerifyClient
client = ChinaVerifyClient()
print(json.dumps(client.piyao_search('关键词'), ensure_ascii=False))
print(json.dumps(client.thepaper_search('关键词'), ensure_ascii=False))
"

# 企业高管验证（position 类型）
python -c "
import sys, json
sys.path.insert(0, '{{skill_dir}}/../破虚妄/tools')
from china_sources import ChinaVerifyClient
client = ChinaVerifyClient()
print(json.dumps(client.company_verify_executive('公司名', '高管名'), ensure_ascii=False))
"

# 网页历史验证（web_content 类型）
python -c "
import sys, json
sys.path.insert(0, '{{skill_dir}}/../破虚妄/tools')
from wayback import verify_text
print(json.dumps(verify_text('https://example.com/article', '声称的关键文本'), ensure_ascii=False))
"

# 已有核查记录（event 类型，英文语境）
python -c "
import sys, json
sys.path.insert(0, '{{skill_dir}}/../破虚妄/tools')
from google_factcheck import make_report
print(json.dumps(make_report('某声称内容'), ensure_ascii=False))
"
```

### 步骤 3：输出裁决卡片

核查完成后，输出以下简洁结构（固定 5 个区块）：

```
## 🔍 核查结果

**主张**：[一句话复述用户的核心主张]

**裁决**：[真实 / 很可能真实 / 失实 / 很可能失实 / 证据不足]

**证据**：[2-4 句话说明找到的关键证据，以及从证据到结论的推理过程]

**来源**：
- [来源名称](链接)
- [来源名称](链接)

**置信度**：高 / 中 / 低（附一句话说明局限）
```

- 裁决使用中文标签，不使用 emoji 以外的图标。
- "证据"部分必须说明推理链，不能只罗列事实。
- 若工具全部失败、仅靠 WebSearch 得出结论，须在"置信度"中注明"仅依赖搜索引擎多源交叉验证"。
- 不做观点辨析、不做方法论展开、不做证据矛盾矩阵。

---

## 裁决标签使用标准

| 标签 | 含义 | 使用条件 |
|------|------|----------|
| `真实` | 主张可被可靠证据直接证实 | 有官方记录/一手来源/多源一致确认 |
| `很可能真实` | 证据倾向支持但非完全确证 | 有可信报道但非一手来源，或来源较为单一 |
| `失实` | 主张可被可靠证据直接推翻 | 官方辟谣/一手证据明确否定 |
| `很可能失实` | 证据倾向否定但非完全确证 | 有矛盾信息但缺乏权威定论 |
| `证据不足` | 现有手段无法获取足够证据 | 工具均超时、无相关搜索结果、信息真空 |

---

## 与 破虚妄 技能的边界

| 维度 | 察人事 | 破虚妄 |
|------|-----------|-------|
| 主张数量 | 单一主张 | 多项断言并行 |
| 工具调度 | 1-2 个精准工具 | 全匹配工具并行 |
| 超时 | 10 秒 | 15 秒 + 1 次重试 |
| 探活 | 不做 | 阶段 0 探活 |
| 内容获取 | 直接从用户问题提取 | L1→L2→L3 三级回退链 |
| 输出 | 5 区块裁决卡片 | 9 章节结构化报告 |
| 导出 | 不支持 | 支持 docx 导出 |
| 观点辨析 | 不做 | 包含逻辑推理链 |
| 统计工具 | 不涉及 | gov_stats + world_bank |

**互操作：** 若用户在 察人事 中发现需要深度核查的内容，应主动建议用户使用 `破虚妄` 技能进行系统性核查。

---

## 使用示例

```
用户：察人事 罗永浩是不是说过"彪悍的人生不需要解释"？

技能：
## 🔍 核查结果
**主张**：罗永浩曾说过"彪悍的人生不需要解释"
**裁决**：真实
**证据**：该语录出自罗永浩早年新东方课堂语录，后收录于《我的奋斗》等公开出版物，本人多次在公开场合引用。微博和澎湃新闻均有相关记录。
**来源**：
- 微博搜索 "罗永浩 彪悍的人生"
- 澎湃新闻相关报道
**置信度**：高（多源一致确认，本人未否认）
```

```
用户：察人事 2024年马云是否参加了达沃斯论坛？

技能：
## 🔍 核查结果
**主张**：马云2024年参加了达沃斯世界经济论坛
**裁决**：很可能失实
**证据**：搜索达沃斯2024年官方议程和参会者名单未发现马云记录，主流媒体报道中亦无其出席的相关报道。马云自2020年后极少公开出席国际论坛。
**来源**：
- WebSearch "马云 达沃斯 2024"
- 世界经济论坛2024年官方参会名单
**置信度**：中（基于搜索结果的反证，但无法完全排除非公开行程）
```

---

## 注意事项

- 始终保持证据优先，禁止仅凭模型内部知识给出裁决（内部知识可作为线索指引搜索方向，但不能作为裁决依据）。
- 若所有工具调用失败（网络问题等），须如实告知用户"当前网络环境无法完成核查"并给出改进建议。
- 中文主体优先使用 china_sources.py 的国内源，境外工具仅作为补充。
- 裁决卡片中的"来源"必须包含可访问的链接或明确的出处描述，不可只写"网络搜索"。
