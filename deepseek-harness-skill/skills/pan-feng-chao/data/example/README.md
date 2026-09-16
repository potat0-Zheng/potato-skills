# data/example —— 中性示例（不是真实事件）

**用途**：给自检、演示、回归测试提供一个**规模小、特征全**的语料。主题是虚构的"小区是否设置快递柜"，
不指向任何真实事件、真实人物或真实机构（机构名用「示例日报」「示***报」这类占位名）。

## 内容

| 文件 | 说明 |
|---|---|
| `normalized/data.jsonl` | 34 条治理后语料（3 天 × 3 平台），字段即 SKILL.md §2 的最小 schema |
| `stances.json` | 3 个立场 + 可选 `intensity` 强度词表 |
| `opinion_rules.json` | 规则层示例（口语判据 + `noise_patterns`） |
| `events.json` | 事件对齐示例（2 条，均带 `source`） |
| `llm_labels.jsonl` | 外部 LLM 标注示例（含 1 条低置信、1 条与词典层不一致） |

## 这份示例刻意覆盖了哪些情况

- **三种立场**：支持设置 / 反对设置（各含单方特征词）/ 要求先公示（程序性立场）；
- **两类机构帖**：完整机构名（`示例日报`）与**匿名化形态**（`示***报`）——后者是 B 层识别下界的关键；
- **同源文本两类**：跨账号同源 3 条（值得人工核）与同账号重复 3 条（刷屏/复读），二者必须被区分；
- **一条反讽**（`支持设置？呵呵，先看看消防通道吧。`）：脚本只标记 `irony_suspect`，**不自动改判**；
- **一个疑似未命名立场**：4 条"维护费/分摊"的记录不含任何现有立场词 → 应被 `candidates.clusters` 检出（种子词 `维护费`）；
- **短文本与非表态内容**：`求链接`/`围观`/`mark`/`先看看再说` → 进"纯反应"段，不进占比；
- **强度词**：反对派多为"强烈"、支持派含"温和"，用于演示 `intensity_mix`。

## 怎么用

```bash
S="<skill 目录>/scripts"; E="<skill 目录>/data/example"
python "$S/opinion.py" --in "$E/normalized/data.jsonl" --out /tmp/ex/opinion.json --event "快递柜示例" \
    --stances "$E/stances.json" --rules "$E/opinion_rules.json" \
    --events "$E/events.json" --llm-labels "$E/llm_labels.jsonl"
python "$S/opinion.py" --digest /tmp/ex/opinion.json      # 读摘要，别整体读 JSON
python "$S/spotcheck.py" --kind unclassified --in "$E/normalized/data.jsonl" \
    --opinion /tmp/ex/opinion.json --out /tmp/ex/spotcheck_unclassified.json --make --n 5 --html
```

**预期结果（几个"应该看到"的点）**：覆盖率约六成、A 层 21 条 / 3 个立场；
`selfcheck.trend_claim_forbidden = true`（只有 3 天、每日分母远小于 20，**不得谈走势**）；
`stance_comparisons` 里"支持 vs 反对"的区间跨 0（不得声称差异）、"支持 vs 要求先公示"的区间不跨 0（可以说）；
机构帖 2 条；同源文本 2 簇；候选簇 1 个。

## 为什么不随附 `opinion.json`

随附的工件会与脚本版本漂移（字段只增不改，但数值会随方法修订而变），历史上已经造成过"夹具过期还被当模板抄"的问题。
需要工件时**现场跑一次**（上面的命令），或直接跑回归测试：`python -m unittest discover -s tests`。
