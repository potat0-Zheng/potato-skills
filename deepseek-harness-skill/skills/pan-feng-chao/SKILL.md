---
name: pan-feng-chao
description: 判风潮——热点事件舆论立场分析与质量自检工具。按事件定制立场体系，对治理后语料做立场聚类、立场占比与少数派结构、声量对比人数与同源文本披露，并以固定种子的分层人工抽检给出误判率、编码信度、归类覆盖率与未识别层漏检估计；另附每日分母、变点筛查、事件对齐、口径漂移告警与工件指纹。当用户请求【单独】做舆论/立场分析（立场聚类、占比、少数派、抽检与信度、词表与口径核查），且不需要产出综合报告时使用。通过 /判风潮 调用。
---

# 判风潮 · 舆论立场分析

**一句话**：把"这个事件的舆论是什么样"变成**可复算、可抽检、可证伪**的工件 —— 立场体系由本 skill 构造，立场数字由本 skill 产出，报告渲染与事实裁决不归本 skill。

## 1. 边界

**做**：立场体系构建（编码手册）· 立场聚类与占比 · 少数派与人数口径 · 人工抽检与编码信度 · 覆盖率与漏检估计 · 时序结构（每日分母/变点）· 质量自检（走势闸门/漂移/指纹）· 词表构造辅助。

**不做**：
- **采集与治理** → ① 百宝袋（本 skill 只吃治理后语料）；
- **事实裁决** → ③ 破虚妄（`facts.json` 归它，本 skill 不读不写）；
- **报告装配与渲染** → ④ 百宝袋装配端（本 skill 只出工件，不写 HTML 报告）；
- **情感极性** → 本 skill **只做立场**，不做正负情绪；
- **联合态下的 `viewpoint.md` / `stance_cards.json`** → 见 §8 排他声明。

**何时用本 skill**：只需要舆论/立场分析本身（占比、少数派、抽检、信度、词表与口径核查）时。
**何时不用**：要一份多方汇总＋核查＋舆论的综合报告 → 走 `/缀众章`，由它编排本 skill。

## 2. 输入

**最小输入 schema**（独立态只要满足它就能跑，不必依赖 ① 的某个文件）：

```json
{"platform":"weibo","id":"…","type":"post|comment|answer","time":"YYYY-MM-DD HH:MM:SS",
 "author":"…","content":"…","url":"…","parent_id":"","metrics":{"likes":0}}
```

- `platform / id` 是**记录签名**，全流程用它对齐（抽检池、回写、复现都依赖它唯一）；
- `time` 决定时间分层与走势闸门，缺失会导致"无有效日"；`metrics.likes` 决定点赞档分层；
- 语料必须是**治理后**（已判相关性、已剔无关噪声）。未治理的语料跑出来的覆盖率无意义。

**联合态输入**：

| 来源 | 工件 | 用途 |
|---|---|---|
| ① 百宝袋 | `data/{event_id}/normalized/data.relevant.jsonl` | 主输入语料 |
| ② 揽风云 | `timeline.json`（`{date,type,tag,title,text,count,ref}`） | 可选：事件对齐（E4.3） |
| 本人 | `stances.json`、`opinion_rules.json`、`institutional.json`、LLM 标注文件 | 词典/规则/机构/外部标注 |

> `timeline.json` **不能直接用**：`--events` 要 `[{"time","label","source"}]`。转换规则：`date→time`（区间取起点）、`tag` 或 `title→label`、`ref→source`（`ref` 为空则**该条会被跳过**——无来源的事件时点不得进入报告）。事件清单也可由人工直接给。

## 3. 输出工件

| 工件 | 产出方 | 说明 |
|---|---|---|
| `opinion.json` / `opinion.md` | 本 skill | 立场聚类主工件；**文件名与字段名不变**，字段只增不改（④ 按字段读） |
| `spotcheck_{stance,rule,unclassified}.json` | 本 skill | 抽检判读单与结论；`--apply` 会把结论**回写**进 `opinion.json` 的 `spotcheck` 键 |
| `spotcheck*.md` / `spotcheck*_结论.md` | 本 skill | 人读判读单 / 结论片段（给报告用） |
| `spotcheck*.html` | 本 skill（`--html`） | 离线点选判读单；导出 JSON 后仍由 `--apply` 读入，**JSON 是唯一权威** |
| `stances.json` + `references/codebook.md` | 本 skill | 立场体系与判读标准（联合态 = 触点 A 的交付） |
| `viewpoint.md` / `stance_cards.json` | **仅独立态** | 由 agent 依 `opinion.json`、判读标准与 `codebook.md` 撰写（**无脚本生成**）；联合态由 ② 揽风云产出，本 skill 不产出（§8） |

## 4. 口径

**四层口径（A 个人表达 / B 机构帖 / C 未识别残差 / D 治理剔除噪声）以 `~/.dsh/report-theme/CONTRACT-joint.md §9.1` 为唯一权威，本文件不复制口径文本。** 只记三个实现要点：

1. **A 层是立场分母**；B 层只作信息供给方，**不进分母**（仅按账号名机构特征词判定，是识别下界）；
2. 覆盖率**两个口径并排披露**：`coverage_rate` = A / 全部语料（保守下界）、`coverage_of_judgeable` = A / 可判集合（契约口径）；跨报告比较用 `coverage_of_judgeable_norule`（去掉规则层依赖后同语料同值）；
3. **未识别 ≠ 无关**，也**不得**当成一个立场画进占比。

阈值集中在 `{SKILL_DIR}/config.json`，取值优先级 **命令行 > config.json > 内置默认**；文件缺失时用内置默认，`thresholds.sources` 与 `pipeline` 会如实披露来源（此时为 `builtin_default`）。

## 5. 标准流程

```
① 百宝袋 采集 → 归一化 → 治理
        ↓
★ 触点 A（联合态）：立场体系 —— 只出体系，不出数字
    构造 stances.json + codebook.md ──► opinion.py 先跑一轮取 df/候选词，再定稿
        ↓
② 揽风云 汇编       ③ 破虚妄 核查
        ↓
★ 触点 B：舆论分析（一次出齐）
    opinion.py → --digest 读摘要 → spotcheck.py --make（core+enrich）
    → 人工判读（--html 或 md）→ --apply → --report
        ↓
④ 百宝袋 装配 → report.html → check_report.py
```

**顺序纪律（硬约束）**：
1. **语料冻结后**才可跑 `opinion.py` / `spotcheck.py --make`；冻结后不得再改语料；
2. 重跑聚类后，**必须重新 `--make` 与 `--apply`**（旧判读单的标签会与新聚类漂移）；
3. **定量结果不得早于 ②③ 发布**（"一数一源"＋避免取材被数字牵着走）；触点 A 只交付立场体系；
4. 词典修订**至多一次**；仍不达标就走标注层路径并如实披露（不得改分母、不得改成"方向性参考"）。

**立场体系四步**：① 立场枚举（依据语料与首轮搜索的观点分歧）→ ② 词典构造（含 `re:` 正则）→ ③ **词频预检**（`dictionary_quality.zero_hit_keywords` 剔零命中词、`vocabulary_advice` 看变体、`candidates` 看疑似未命名立场）→ ④ 上限与止损。

## 6. 命令速查

脚本位于 `{SKILL_DIR}/scripts/`（**脚本名不得更改**：门禁 G11 与 ④ 的提示串引用它们）。完整参数见 `--help`。

**`opinion.py`** — 立场聚类与全部结构诊断：

```bash
python "{SKILL_DIR}/scripts/opinion.py" \
  --in data/{event_id}/normalized/data.relevant.jsonl \
  --out data/{event_id}/opinion.json --event "{事件名}" \
  --stances data/{event_id}/stances.json \
  [--total-raw N] [--rules R.json] [--institutional I.json] \
  [--llm-labels A.jsonl] [--llm-labels-b B.jsonl] \
  [--events events.json] [--event-window 3] [--prev 上次/opinion.json] \
  [--candidates lexical|embed|off] [--candidate-min-size 4] [--change-points on|off] \
  [--structure on|off] [--terms off|jieba] [--dup-threshold 3] [--heavy-threshold 5] \
  [--minority-threshold 10] [--mid-samples 12] [--coverage-target 70] \
  [--trend-min-days 3] [--trend-min-day-denominator 20]
python "{SKILL_DIR}/scripts/opinion.py" --digest data/{event_id}/opinion.json   # 读摘要（推荐）
```

> 阈值类参数（`--minority-threshold`/`--mid-samples`/`--coverage-target`/`--trend-*`）一般不必手打：改 `config.json` 更省事，优先级 命令行 > config.json > 内置默认。

**`spotcheck.py`** — 人工抽检与信度：

```bash
S="{SKILL_DIR}/scripts/spotcheck.py"; O=data/{event_id}
python "$S" --kind stance --in $O/normalized/data.relevant.jsonl --opinion $O/opinion.json \
    --stances $O/stances.json --out $O/spotcheck_stance.json --make --n 10 --coder A --html   # 立场核
python "$S" --kind unclassified … --make --n 12                     # 未识别核
python "$S" --kind rule --rules $O/opinion_rules.json … --make       # 规则核（必须传 --rules）
python "$S" --out $O/spotcheck_stance.json --status --pending        # 看状态/待判读（推荐）
# 人工判读：打开 spotcheck_stance.html 点选并导出，或填 spotcheck_stance.md → .json
python "$S" … --apply --coder-b $O/spotcheck_stance_B.json           # 计算误判率/信度并回写
python "$S" --kind stance --in $O/normalized/data.relevant.jsonl --opinion $O/opinion.json \
    --stances $O/stances.json --out $O/spotcheck_stance.json --report   # 结论片段
```

> 抽样与富集的相关开关：`--enrich-n 20`（富集块上限，`0` 关闭）、`--dup-threshold 3`、`--no-time-strata`（关闭时间分层）、
> `--llm-conf-min 0.7`（LLM 低置信阈值）、`--allow-pool-mismatch`（仅在确认差异原因后使用）。

> 参数规则（实测）：**`--status` 是唯一只需 `--out` 的模式**；`--make` / `--apply` / `--report` 都要齐 `--kind` / `--in` / `--out`。

**`vocab.py`** — 词表版本差异（改词表＝改口径，必须留痕）：

```bash
python "{SKILL_DIR}/scripts/vocab.py" --diff old_stances.json new_stances.json --corpus 语料.jsonl
```

**共用模块**（不单独调用）：`stats.py`（Wilson/Newcombe/多项差值/后分层/簇稳健/Neyman/κ/α）· `structure.py`（作者集中度、SimHash 同源文本）· `manifest.py`（工件指纹，**不含时间戳**以保证同输入同输出）· `enhance.py`（可选依赖探测与降级披露）。

**三个核的读法**：`stance`/`rule` 核每立场抽 N 条判"这条真属于该立场吗"（对/错）；`unclassified` 核判"这条含立场表达吗"，含则选立场，再按分层加权回推全段。

## 7. 结论边界（不得越线）

| 红线 | 机器依据 |
|---|---|
| 覆盖率不足**不得**写"主流/压倒性/普遍" | `coverage_rate` 低于 `coverage_target` 时脚本告警；④ 的门禁 G7/G10 也拦 |
| 有效日不足**不得**谈走势 | `selfcheck.trend_claim_forbidden`、`effective_days`（阈值 `trend_min_days`/`trend_min_day_denominator`） |
| 占比必须与**误判率、覆盖率、未识别层估计并排** | `spotcheck.per_stance[].error_rate`、`coverage_rate`、`stratified` |
| 误判率 >`error_rate_max`（默认 30%）的立场**不得**作主结论 | `unreliable_stances` |
| 两个立场/群体的差**不得**凭点估计声称 | `stance_comparisons[].verdict`：区间跨 0 即"不得声称有差异" |
| 声量**不等于**人数 | `author_distribution`、`audience_structure`（条·人比、HHI）；占比是"条数结构"，不是"人数结构" |
| 同源文本**不等于**水军 | `audience_structure.duplication` 只披露：跨账号同源才值得人工核，须人工抽检后才能下判断 |
| 单判读人的误判率**没有信度支撑** | `intercoder`：未提供第二判读人时为 null，不得声称判读可靠 |
| 反讽**不得**自动改判 | `negated` / `irony_note` 只是复核提示，改判必须人工 |
| LLM 层只能是**上界** | `llm_layer.status = "未抽检"`；与词典层并列不合并、不进分母 |
| 事件前后**不得**称因果 | `event_alignment.events[].caveat`：仅时间相邻 |
| 未知**不得**写成 0 | `--total-raw` 未传 → `residual.split` 输出 `null` |

## 8. 跨技能协作

**排他声明（联合态）**：`/缀众章` 下，`viewpoint.md` 与 `stance_cards.json` **仍由 ②揽风云产出**。二者现在**同属 CONTRACT-joint §9.2「立场卡组与视点综合（② 的两件叙事工件）」**（v0.6 起该节补上了 `viewpoint.md` 的归属；此前它只在 §11 工件清单里出现过名字、**没有归属**，本文件当时据此声明"§9.2 不覆盖 `viewpoint.md`"——该说法已作废）。判风潮只提供方法与判据（编码手册、论证链判据），不重复产出、不代写。**独立态下这两个工件才由判风潮产出。**

| 对象 | 判风潮给什么 | 判风潮要什么 | 硬约束 |
|---|---|---|---|
| ① 百宝袋（采集/装配） | `opinion.json`（含 `spotcheck` 回写）、`spotcheck_*.json` | 治理后语料路径 | ④ 按字段读；文件名与字段名不可改 |
| ② 揽风云 | 立场体系（`stances.json` + codebook） | `timeline.json`（可选，需转换，见 §2） | 联合态不产出 viewpoint/stance_cards |
| ③ 破虚妄 | 无 | 无 | 不读不写 `facts.json`；裁决不改变立场判定 |
| ④ 百宝袋装配 | 同上 | — | 渲染逻辑归 ④；本 skill 不写报告 HTML |
| 门禁 `check_report.py` | 工件与字段 | — | 脚本名 + 工件名不变 → 门禁零改动 |

**阶段一冻结边界**：`CONTRACT-joint.md`、`check_report.py`、`checks/`、②③ 与 `/缀众章` 入口**一律不改**；口径变更（多标签、跨事件基线结论化、门禁接入走势判据）属阶段二。
**已解除**：`zhui-zhong-zhang`（原「三合一」）入口图**已经含有本 skill 的两个触点**（阶段二的 C 组落地），联合态下仍由本文件说明被 ①/② 调用的方式、由加载了本 skill 的 agent 自行衔接。

## 9. 执行环境与自检

- **依赖**：Python 3.10+，**默认路径零第三方依赖**；无需爬虫凭证与浏览器。可选增强（jieba / sentence-transformers）装了才用，缺失即在 `pipeline.degraded` 披露，且**开关前后除 `pipeline` 外逐字段相同**。
- **沙箱（Windows/DSH）**：含中文的脚本一律**落盘为 `.py` 再执行**；自检的临时目录必须建在**可写路径**（`tempfile.mkdtemp()` 的默认目录在本沙箱不可写，重定向 `TMP/TEMP` 无效）。
- **可复现**：固定 `--seed`；`run_manifest` 记录输入/词表/脚本的 sha256 与参数（不含时间戳）；跨 `PYTHONHASHSEED` 两次运行产物字节相同。
- **回写影响指纹**：`spotcheck --apply` 会写回 `opinion.json`，故 `opinion.json` 的 sha256 在回写后必然变化——`spotcheck` 的 `run_manifest.reference` 记录的是**读取时**的指纹，核验时须按此理解。
- **阈值来源已落盘**：§4 的 `config.json` 现在真实存在（含按键归属与优先级说明）；缺失键仍回落到内置默认。
- **自检**（回归套件，约 5 秒、只用标准库、不联网）：

```bash
cd "{SKILL_DIR}"
python -m unittest discover -s tests      # 62 条：数字地基 / 工件契约 / 池口径 / 兼容性
```

  临时目录建在 `tests/_tmp/`（本沙箱的 `mkdtemp` 默认目录不可写）、子进程输出重定向到文件（管道在本沙箱会 EPERM）——两条注意事项见 `tests/README.md`。
  想手动跑一遍看效果，用自带的中性示例：`data/example/`（34 条、覆盖 3 立场 + 2 类机构帖 + 两类同源文本 + 1 个疑似未命名立场，见其 README）。

## 10. 上下文纪律（必守）

工件会随语料增长（1 万条语料下 `opinion.json` 约 11 万字符、判读单约 5–6 万字符；而 `--digest` 摘要不到 1 KB）。

- **读结论用 `opinion.py --digest <工件>`；读判读单状态用 `spotcheck.py --out <工件> --status [--pending]`**；
- 需要明细读 `opinion.md` / `spotcheck*.md`；**禁止整体读取 `opinion.json` / `spotcheck.json`**（要某字段就按字段取）；
- `spotcheck*.md`（判读单）是**给人判读的**，不要喂给模型；判读走 `--html` 或人工填；
- 脚本源码只在需要扩展或排障时读，并分段/按符号读，不要整读（单文件已超千行）；
- `references/codebook.md` 是**判读标准与词典构造的唯一出处**，构造/修订词典、判定边界案例、处置反讽、报出结论**前必读**（该文件已落盘；口径的权威定义仍在 CONTRACT-joint §9.1，本手册只写 §9 未覆盖的方法）。

## 11. 扩展与维护（改本 skill 时）

- **字段只增不改**；**新字段一律新开顶层键，绝不往既有容器里塞**（④ 的 `_stance_trend` 会把 `stance_timeline[day]` 的每个键当成立场画段）；
- 新增结果键必须同步加入 `spotcheck.py` 的过期键清除清单，否则重跑 `--apply` 会留下"幽灵结论"；
- 任何"可能改口径"的能力先做成影子字段，默认路径与报告都不引用，等 CONTRACT 阶段二解冻再切换；
- 改完必跑：① `python -m unittest discover -s tests` 全绿（内含"新功能开关关闭后既有字段逐位相同""两次运行字节相同"两条机器判据）；② 若已做百宝袋迁移，另跑 `python ~/.dsh/checks/run_all.py`，正例门禁 error 集合须仍**恰为** `G7/G8/R3/R4`（不是 0 error）。
