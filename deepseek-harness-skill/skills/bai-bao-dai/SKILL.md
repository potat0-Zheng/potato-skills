---
name: bai-bao-dai
description: 百宝袋——热点事件多平台信息汇总与综合报告装配工具。整合微博/小红书/知乎三平台爬虫，执行"采集→归一化→语料治理→事实还原→综合报告装配"流水线，产出结构化 HTML 综合报告。当用户请求对某一热点事件做多平台信息汇总、多方视角还原事件真相、并装配成一份可交付报告时使用；也可单独调用内置爬虫（如仅爬取知乎某问题/回答）。舆论立场类分析由 /判风潮 承担，本技能只做装配与交接触点。通过 /百宝袋 调用。
---

# 百宝袋（bai-bao-dai）

## 目标

对指定热点事件，完成两件事：
1. **多方视角还原事件真相**（事实层）——多源交叉、证据分级、冲突消解、对立观点显式覆盖
2. **装配可交付的综合报告**（呈现层）——把 ①②③ 的工件与 LLM 深度内容装配成结构化 HTML，并守住引用闭环与口径披露

舆论层（立场聚类、占比、少数派、抽检与信度）**由独立技能 `/判风潮` 承担**，本技能只在其两侧提供交接触点，见「阶段 3」。

不过度依赖复杂分析工具，但通过核查方法与工件契约保证深度。最终产出结构化 HTML 综合报告。

## 目录结构

```
{SKILL_DIR}/
├── config.json            # 平台开关、默认参数、限速、探测项、政策工具族注册（不存舆论配置：立场体系归 /判风潮）
├── setup.py               # 依赖与凭证检查
├── zhihu/                 # 知乎爬虫（内嵌，自原 zhihu skill 迁移）
│   ├── crawl.py           # 单回答/全问题（输出回答链接+发布时间）
│   └── cookie.txt         # 知乎 cookie（单一事实源）
├── scripts/
│   ├── collect.py         # 统一采集入口（外部平台驱动 MediaCrawler，无凭证诚实降级）
│   ├── normalize.py       # 多源归一化 JSONL（知乎 md + MediaCrawler 微博/小红书 schema）
│   ├── check.py           # 事实核查辅助（调 china_sources 初查；po-xu-wang 未装时自动降级为 LLM 多源核查）
│   ├── build_report.py    # 综合报告生成（十章结构，含 KPI/条形/堆叠/矩阵等纯 CSS 可视化）
│   ├── selftest.py        # 工具自检（离线层 + 在线最小尝试，见「工具自检」节）
│   ├── relevance_gate.py  # 语料治理（产出 normalized/data.relevant.jsonl，语料冻结的最后一环）
│   └── policy/            # 政策工具族（积木模块，见「政策工具族」节；config.json policy_tools 注册）
│                          # ※ 舆论聚类/抽检脚本已迁出：见 skills/pan-feng-chao/scripts/（阶段 3）
├── templates/report_template.html
├── tests/test_pipeline.py # 离线流水线测试（python -m unittest discover -s tests）
├── external/              # 外部爬虫（MediaCrawler / weiboSpider / Spider_XHS）
└── data/{event_id}/       # 每次事件一个目录；中性示例见 data/example（含 stances.json）
```

## 四阶段工作流

**协同编排契约（联合任务，四技能）**：当任务由 `/缀众章`（揽风云 × 破虚妄 × 百宝袋 × 判风潮）编排时，遵行 `~/.dsh/report-theme/CONTRACT-joint.md`（衔接约束的单一事实源，细则以该文件为准，本段不再重复）。要点：三平台（知乎+微博+小红书）真实采集为硬性要求、任何跳过须经用户确认并写入合规账本；**舆论立场分析不在本技能内执行**——它由 `/判风潮` 在**两个触点**提供（触点 A 立场体系在语料冻结之后、②③ 之前；触点 B 舆论数字在 ②③ 之后），见「阶段 3」；装配用 `build_report.py --joint --compliance data/{event_id}/compliance.json`，交付前跑 `check_report.py --joint --strict`。

### 阶段 1：采集（collect.py）

1. 解析用户输入：提取事件关键词 + 事件 ID（时间戳或短哈希），并**同时确定三组检索词**：
   ① 事件名/热搜原表述；② 中立词（如「长沙 4 岁男童 餐厅 触碰」）；③ 反方词（如「长沙 摸臀风波 女子」）。
   单用①等于用指控方口径做检索式，立场占比会系统性偏向一侧——三组词都要实跑，清单写入报告方法论章。
2. **知乎**：先用 WebSearch 定位相关知乎问题链接，再调内嵌爬虫：
   ```bash
   python "{SKILL_DIR}/scripts/collect.py" --event "关键词" --platforms zhihu --url "<知乎问题URL>" --limit 20 --out "data/{event_id}/raw"
   ```
3. **微博 / 小红书**：collect.py 检测凭证后**真实驱动 external/MediaCrawler**（关键词搜索，jsonl 输出到 `data/{event_id}/raw/{platform}/`）：
   ```bash
   python "{SKILL_DIR}/scripts/collect.py" --event "关键词" --platforms weibo,xiaohongshu --out "data/{event_id}/raw" \
       --wb-cookie "<微博cookie>" --xhs-cookie "<小红书cookie>" --max-posts 100
   ```
   - **首选：一次性扫码登录（无需复制/传输 cookie）**。每个平台只需一次：
     ```bash
     python "{SKILL_DIR}/scripts/collect.py" --login weibo,xiaohongshu
     ```
     弹出浏览器窗口 → 用户手机 App 扫码 → 会话持久化到 `browser_data/<platform>_user_data_dir`，此后采集自动复用。
   - 凭证来源优先级：`--wb-cookie/--xhs-cookie` 参数 → 环境变量 `BBD_WB_COOKIE/BBD_XHS_COOKIE` → browser_data 持久化登录态（`--login` 建立）→ 小红书还可回退 MediaCrawler 内置 cookie
   - **MediaCrawler 配置无需手动调整**：collect.py 每次调用自动注入平台/登录方式/关键词/输出路径/条数/headless/评论开关等全部参数；`.env`、数据库、代理配置与本 skill 用法无关
   - **无凭证不静默**：collect.py 明确输出"`[平台] 未采集：<原因>`"并返回非 0；驱动失败（登录态失效/风控）同样标记并在报告中注明
   - **部分成果保全（v0.5）**：MediaCrawler 非 0 退出或超时时，collect.py 仍统计已落盘 jsonl —— 有记录即判「部分采集」（rc=0 并在汇总里标注），只有 0 条才判「未采集」。文案已把「分页取尽（DataFetchError，属正常终止）」与「登录态失效/风控」区分开：前者换更短的关键词，后者才需要重新扫码，**不要靠加超时解决**。
   - **多关键词一次跑（v0.5）**：`--keywords "克兰西,美国妈妈勒死3孩子"`，逗号分隔，各词独立落盘到 `raw/<platform>/<关键词>/`，互不覆盖（旧写法每个关键词单独调一次、共用同一 `--out`，后一次会覆盖前一次）。
   - **先探针后放量（v0.5，长任务省时的第一原则）**：正式采集前先跑
     ```bash
     python "{SKILL_DIR}/scripts/collect.py" --keywords "词1,词2" --platforms weibo,xiaohongshu \
         --probe --out "data/{event_id}/raw"
     ```
     探针为每词 5 帖、不取评论、超时 180s：一次确认「登录态还有效」「关键词有结果」「输出路径可写」三件事，再放量到 `--max-posts 50 --with-comments`。

### 长任务的可观测性：轻量进度同步（v0.5）

浏览器级采集单批 4–8 分钟，期间外部进程是黑箱。collect.py 用三个零依赖的机制把状态同步出来，**不引入任何服务、端口或轮询进程**：

| 机制 | 载体 | 用途 |
| --- | --- | --- |
| 进度文件 | `{out}/_collect_progress.json`（原子替换，约 1KB） | 机器可读的单一事实源：总任务数、已完成任务及各自状态/条数、当前任务已跑秒数与已入库条数 |
| stdout 心跳 | 每 `--heartbeat` 秒一行（默认 15s） | 形如 `[hb] weibo/克兰西 02:15 / 上限 07:00 · 已入库 帖 58 + 评 118 · running`；后台任务用 `job_output` 读到 |
| 查询入口 | `collect.py --status "data/{event_id}/raw"` | 零副作用读取进度文件并打印人类可读的进度表，**任何时刻可查，不影响正在跑的采集** |

用法约定：

1. **进度数字取自真实落盘量**（递归统计 `*.jsonl` 行数，帖与评论分开计），不是预估——因为 MediaCrawler 是边抓边写。**读法限制**：MediaCrawler 会先取完一批结果集再批量落盘，因此单批前 30–60 秒该计数常为 0，属正常而非卡死；心跳行在超过 45 秒仍为 0 时会自行标注「未落盘（结果集取完才写盘，属正常）」。它是**下界**，不是完成度百分比。
2. **向用户同步时只读进度文件**，一次 `--status` 就能报出「已完成 3/6，当前第 4 个关键词已跑 2 分 15 秒、入库 176 条」；不需要打断采集，也不需要翻日志。
3. **每个任务收尾都往进度文件追加一条 done 记录**（含 state 与 note），因此采集结束后它同时是一份**逐批采集台账**，可直接抄进覆盖声明与合规账本。
4. 进度文件与 MediaCrawler 自身的 `_mediacrawler.log`（每次运行覆盖写）都留在 `raw/<platform>/<关键词>/` 下，是排障时的第一现场。

### 长任务的执行顺序（硬性）

采集与流水线**不得交叉**，否则会白做一遍：

```
① 探针（--probe）→ ② 放量采集（--keywords 一次跑完，期间用 --status 报进度）
→ ③ 采集全绿/逐批登记完成后，才冻结「事件窗口」（normalize.py --since/--until）
→ ④ normalize → relevance_gate（至此语料冻结）→ /判风潮 触点 A（产出 stances.json，只出体系不出数字）
→ ⑤ ②揽风云 / ③破虚妄 → /判风潮 触点 B（opinion → spotcheck --make → 人工判读 → --apply）
→ ⑥ build_report 装配 → check_report 门禁 → §10 数据对账
```

- 第 ③ 步之前**不要**跑 normalize，也不要在语料冻结前调用 `/判风潮`：窗口一变，语料集合随之改变，抽检抽样单（固定 seed 分层抽样）会整批换个样本，人工判读成果全部作废。
- `/判风潮` 的 `spotcheck.py --make` 判读单必须在**语料冻结之后**生成，`--apply` 把结论写回 `opinion.json`；重跑聚类会覆盖 `spotcheck` 字段，重跑后须重新 apply——**这两条纪律不因脚本换了归谁而豁免**。
- 归因纪律：**含中文的驱动脚本一律用 Python 写**（Windows PowerShell 5.1 按 GBK 读 UTF-8 无 BOM 的 `.ps1`，中文关键词会直接变成解析错误）。

### 阶段 2：归一化 + 语料治理 + 事实还原（normalize.py + relevance_gate.py + check.py）

1. 归一化（**必须带事件时间窗**）：
   ```bash
   python "{SKILL_DIR}/scripts/normalize.py" --in "data/{event_id}/raw" \
       --out "data/{event_id}/normalized/data.jsonl" --event "关键词" \
       --since 2026-08-26 --until 2026-09-10
   ```
   - `--since/--until` 是**必填语义**（事件起点 / 信息截止）。规则：帖文/回答按自身时间；评论按**自身时间**，
     时间缺失时继承父帖时间；**父帖越窗则评论一并剔除**（不允许"父帖相关"就把评论留在语料里）。
     越窗记录另存 `normalized/data.prewindow.jsonl`（背景语境，**不得**并入分析语料）。
   - 知乎解析：只有带元信息行（回答者ID/赞同数/回答链接）的 `## ` 块才算回答；回答正文里自带的二级标题
     会并入上一条，**不再切成多条记录**（此前一篇回答被切成 11 条、作者/id 都是小标题）。
   - （v0.3 起：评论记录的平台按来源文件路径回填；保留 `parent_id` 供评论随父帖与引用回指。）
2. 语料治理（**必须走脚本，不得现写一次性脚本**）：
   ```bash
   python "{SKILL_DIR}/scripts/relevance_gate.py" --in "data/{event_id}/normalized/data.jsonl" \
       --out "data/{event_id}/normalized/data.relevant.jsonl" \
       --excluded "data/{event_id}/normalized/excluded.jsonl" \
       --strong "事件强特征词,…" --weak "弱特征词,…" --min-hits 1 --json
   ```
   - **产物文件名固定**：`normalized/data.relevant.jsonl`（留下的）+ `normalized/excluded.jsonl`（剔除的）。
     装配端 `build_report.py` 会自动读取前者并给出「原始 → 按分析口径」的口径；不得改用别的文件名。
   - 判定规则：帖文按正文特征词；**评论一律随父帖**（中文评论全靠「她/这孩子」指代，逐条判必然误杀）；
     知乎回答恒相关（采集端按问题 URL 抓取，相关度由问题保证，回答又短又口语，用关键词判会大批误杀）。
   - 脚本输出的 `dropped/dropped_pct/dropped_by_platform/dropped_by_type/dropped_reasons`
     **必须原样写进覆盖声明**（剔除量、占比、构成），剔除占比 >30% 时同时写进摘要——门禁 G8 会查。
3. 事实还原（核心方法论，沿用破虚妄思路）：
   - 从采集内容 + WebSearch 提取关键事实断言
   - 调 check.py 标准化：
     ```bash
     python "{SKILL_DIR}/scripts/check.py" --event "关键词" --claims "断言1,断言2,..." --out "data/{event_id}/facts.json"
     ```
   - **由 LLM 结合多源证据逐条裁决并回填 facts.json**：每条断言 verdict 取 真实/部分真实/失实/证据不足，同时写入 verdict_source（llm_websearch / china_sources+llm / manual）与 verified_by/verified_at，保证"事实还原"可复现、可追溯；未裁决的断言保持"待核查"，不得伪装成已核查（check.py 只做初查、不产终裁）；每条断言可附 `evidence_level`（1=官方文件/本人账号原文 · 2=权威媒体全文 · 3=转载引文 · 4=搜索摘要/标题层）——verdict 为「真实」且证据等级为 3/4 时，报告显示为「报道属实（原文未核）」（展示降级，facts.json 语义不变，见 CONTRACT-joint）
   - **一条断言只承载一个可独立裁决的主张**：不得把强弱悬殊的从句（如"另有媒体称 X，且校方未回应"）并进同一条，
     否则强项会被弱项拖成「证据不足」。否定性从句（未见/未回应）若确需保留，单独登记。
   - **降级说明**：本技能可独立使用。若本机未安装 po-xu-wang skill，check.py 自动输出"待核查"（china_sources=False），由 LLM 直接用 WebSearch 完成多源核查——不影响流水线其余环节

### 阶段 3：舆论把握（由 `/判风潮` 执行）

**本阶段不在百宝袋内执行。** 舆论把握（立场聚类、占比与少数派结构、人工抽检与编码信度、归类覆盖率与词表核查）已整体移交独立技能 **`/判风潮`**（`~/.dsh/skills/pan-feng-chao/`；判读标准见其 `references/codebook.md`，阈值集中在其 `config.json`）。本技能在该阶段只剩两处交接触点：

| 触点 | 位置 | 执行者 | 产出工件 |
| --- | --- | --- | --- |
| **A：立场体系**（只出体系，不出数字） | 阶段 2 语料治理之后、②③ 之前 | `/判风潮` | `data/{event_id}/stances.json`（可选 `opinion_rules.json`）+ `codebook.md` |
| **B：舆论分析**（一次出齐） | ②③ 之后、阶段 4 装配之前 | `/判风潮` | `opinion.json`（含回写的 `spotcheck` 字段）+ `spotcheck_{stance,rule,unclassified}.json/.md` |

**立场体系（触点 A）由 `/判风潮` 构造**：依据本事件的实际观点分歧产出 `stances.json` 与判读口径 `codebook.md`（含词频预检、纳入/排除操作判据）。**本技能不再教用户手写立场词典**，`config.json` 也不再保存任何舆论配置（旧的 `stance_keywords` / `stance_note` 已删除）。

**仍然有效、不得省略的五条纪律**（原阶段 3 的硬约束，继续约束 `/判风潮` 的调用方）：

1. **顺序不可换**：语料冻结（`normalize` + `relevance_gate` 完成、事件窗口定死）**之前不得**启动立场聚类。窗口一变，语料集合随之改变，抽检的固定 seed 分层样本整批更换，人工判读成果全部作废。
2. **重跑即重做**：重跑聚类会覆盖 `opinion.json` 的 `spotcheck` 字段，因此每次重跑后必须重做抽检三步（`--make` → 人工逐条判读 → `--apply`），不得沿用旧结论。
3. **定量不得早于 ②③ 发布**：触点 B 的立场数字排在 ②揽风云、③破虚妄 之后，避免"先定调、后补证"。
4. **口径唯一权威**：联合态的四层口径（A 个人表达 / B 机构帖 / C 未识别残差 / D 治理剔除噪声）一律以 `~/.dsh/report-theme/CONTRACT-joint.md` **§9.1** 为准。本 SKILL.md 只引用、不复述，避免出现第二事实源。
5. **工件名与字段名不变**：仍产出 `opinion.json` 与 `spotcheck_*.json`；阶段 4 的 `build_report.py`、门禁 `check_report.py` 按既有字段读取，改名须先改契约。

**视点综合（`viewpoint.md`）的归属**：联合任务中由 ②揽风云 产出（见 CONTRACT-joint §9.2 立场卡组与视点综合）；百宝袋独立使用时由 LLM 依据 `/判风潮` 的 `opinion.json` / `opinion.md` 撰写，再经 `--viewpoint` 注入。**"焦点转移 / 走势"一类结论只能在多日采集下给出**：单一时点采集只能写"各发布日占比"，不得写成舆论演变（CONTRACT-joint §5）。

### 阶段 4：综合报告（build_report.py）

```bash
python "{SKILL_DIR}/scripts/build_report.py" --event "关键词" \
    --title "报告主标题（可含 <em>强调</em>）" --intro "报头一句话导语" \
    --facts "data/{event_id}/facts.json" --opinion "data/{event_id}/opinion.json" \
    --normalized "data/{event_id}/normalized/data.relevant.jsonl" \
    --viewpoint "data/{event_id}/viewpoint.md" \
    --coverage "data/{event_id}/coverage.md" \
    --abstract "data/{event_id}/abstract.md" \
    --timeline-milestones "data/{event_id}/timeline.json" \
    --actors "data/{event_id}/actors.json" \
    --stance-cards "data/{event_id}/stance_cards.json" \
    --out "data/{event_id}/report.html"
```
（`--normalized` 指向**治理后**的 `data.relevant.jsonl`：装配端据此产出「原始 → 分析」口径、按平台分层收来源，
KPI 的"分析语料/事件跨度"也以它为准；传 `data.jsonl` 会退回未治理口径并在控制台提示。
`--actors` 可选：联合任务由 ②揽风云 产出，见「新增约定」与 CONTRACT-joint §8）

**质量门（交付前必跑，0 error 才可交付；自检/装配规范见 `~/.dsh/report-theme/CONTRACT.md`）**：
```bash
python "~/.dsh/report-theme/check_report.py" --joint --strict \
    --data "data/{event_id}" "data/{event_id}/report.html"
```
`--data` 是**数据对账**开关（CONTRACT-joint §10）：不传只能查报告形态，查不出"KPI 数字与时间线矛盾 /
语料含事件前记录 / 断言说'仅见单条'而语料里有十几条 / 同一指标两个数"这一类问题。联合任务必须带 `--data`。
LLM 深度内容（`--viewpoint/--coverage/--abstract`）只写 md：md 渲染器支持 GFM 管道表格（自动转 `<table><thead><tbody>`）、加粗、列表、引用，并**自动剥离标题手写序号前缀（「一、/1.」）**；**禁止在 md 里手写 HTML 或让 `<p>| …` 管道行残留**（check E3 会拦截）。

新增约定（v0.2.10）：
- **主体视角档案（v0.2.18，`--actors` 对象形态）**：由 ②揽风云 产出 `actors.json`（schema 与判据见其 SKILL.md「阶段 6：主体档案」），装配后挂**第二章末**为 h3 小节（CSS 计数器自动编号 x.1），含身份简介卡组 + 主体×**离散阶段**轨道矩阵 + 跨主体交锋带 + 节点编码图例。`claims` 只写断言编号，**裁决词由本脚本从 `--facts` 登记册自动补齐**（不自行判定，见 CONTRACT-joint §8）。`--actors` 传**数组**时仍走旧「角色小词典」（v0.2.6 行为，挂第一章末）。装配后门禁新增 E5（行列数不齐）/ W12（空壳主轴）/ W13（编号不匹配）/ W14（claims 越界）。
- **事件摘要（ch1，v0.2.16 标签化）**：`.abstract` 用标签化清单 `<dl>`（`dt` 标签列 + `dd` 内容列，≤640px 堆叠）：装配命令 `--abstract` 传**结构化标签式 md**——每个标签单独成段写 `**一句话结论**` 等，其后空行接该标签内容（缺省不渲染）：
  ```markdown
  **一句话结论**
  …全篇总括…
  **事件定性**
  …
  **事实核查**
  N 条断言 → 真实/部分真实/证据不足（详见事实核查章）
  **平台采样**
  各平台样本量与补采说明
  **舆论主形态**
  按条数/按赞同加权的派别占比
  **少数派**
  低占比高质派别/玩梗反讽等
  **事件跨度**
  起→止日期与节点
  ```
  推荐标签序即上表；标签按报告类型可增减，dd 长文可再分 p，末尾 `.abs-foot`「模型生成·以各章为准」。旧两段式（空行分段：段一 事件→进展→舆论；段二 真实性→结构提示）仍自动兼容。
- **竖式时间轴（`--timeline-milestones`）**：`[{date,type,tag,title,text,count}]`，type∈official/media/view/bg/quiet/check；缺省回退“日期×记录数”简表。
- **断言小标题**：`facts.json` 每条 claims 增加 `short_title`（≤24 字、只压缩原文、保留限定词），渲染为 ch3 断言卡标题；ch4 只做裁决压缩视图（分工说明 + 分布胶囊 + 分组速览 + 冲突表），**两章不再复述**。
- **来源分级**：`sources[].grade`（A/B/C）输出 `class="source-A/B/C"`；自动采集帖默认 C。
- **来源索引锚点域（v0.5，`--sample-anchors` / `--source-cap`）**：装配端会按平台分层自动追加若干「平台语料样本链接」供逐帖回溯，它们按设计不逐条出现在正文引用中。旧行为把样本与精选信源混在同一 `refN` 域，导致每条样本都触发门禁 **W1**「索引有锚点但正文未引用」，只能靠 `--allow W1` 压下去。现在默认 `--sample-anchors sample`：样本行改用独立锚点 `id="sampleK"`（保留 `source-C` 分级底色），精选信源独占 `[1]–[N]`，W1 不再误报，`--allow` 只需保留 GW。`--source-cap N` 控制样本条数（默认 50，`0` = 不追加）；想回到旧行为用 `--sample-anchors ref`。注意：**被第六章高赞代表引用的样本地址仍计入精选区间**（它们承担举证角色），所以精选条数 ≠ `sources.json` 条数。
- **左侧常驻目录栏**（v0.2.12）：默认注入（`--no-nav-drawer` 关闭），透明底、常展开、随滚动高亮当前章，并自动移除顶部 toc。

报告章节（已实现）：报头（masthead：eyebrow + h1 + intro + 自动数字 hero-figure）→ 结论速览 → 时间线 → 核心事实汇编 → 事实核查结果 → 舆论观点综合（含 LLM 视点分析）→ 来源索引 → 覆盖完整性声明 → 方法论 → 时间戳。**报头说明**：`--title/--intro` 均留空时自动回退（h1=event、无导语）；hero-figure 数字由脚本从采集量/断言/立场自动生成，无需手填。

`--viewpoint/--coverage` 为 LLM 深度内容注入点（markdown 自动转 HTML）；**每章自动带程序化导读行**（数据驱动的导航句；可用 `--intros <json>` 覆盖，键为章节号 1-9 或 appendix）。**可视化（纯 CSS）**：第一章 KPI 仪表盘；可选「主体视角档案」（`--actors` **对象形态** = 身份简介卡组 + 主体×离散阶段轨道矩阵 + 交锋带，挂第二章末）或「事件角色小词典」（`--actors` **数组形态**，折叠卡组按 side 分组，挂第一章末）；第二章可选「说法×证实双轨时间轴」（`--track`，claim/verify 两轨 + 类型徽章）；第四章可选「口径冲突对照」（facts.json 断言可选 `conflicts` 字段）；第五章立场占比条形图 + 焦点转移堆叠条（依赖 opinion.json 新增 `stance_timeline` 字段）；第六章来源/分级小结（`--sources` 条目可选 `grade` 字段）；第七章覆盖缺口三态矩阵。

## 独立工具用法（单独取一个工具）

### 仅爬知乎（单回答 / 全问题）
```bash
# 全问题（可 --limit N 限制条数）
python "{SKILL_DIR}/zhihu/crawl.py" "<知乎问题URL>" "<输出.md>" --limit 5
# 单回答（URL 含 /answer/）
python "{SKILL_DIR}/zhihu/crawl.py" "<含/answer/的URL>" "<输出.md>"
```

### 仅做舆论分析

**不在本技能范围**：舆论聚类、立场占比与少数派、抽检与编码信度已迁至独立技能 **`/判风潮`**（脚本位于 `~/.dsh/skills/pan-feng-chao/scripts/`）。百宝袋不再提供该入口；需要单独做舆论分析时请直接调用 `/判风潮`。

### 依赖检查
```bash
python "{SKILL_DIR}/setup.py"
```

## 工具自检（可选，按用户要求触发）

当用户要求"先测下工具 / 自检 / 检查环境"时，在正式任务开始前执行：

```bash
python "{SKILL_DIR}/scripts/selftest.py"            # 离线层 + 在线最小尝试
python "{SKILL_DIR}/scripts/selftest.py" --offline   # 仅本地工具链（无网络）
python "{SKILL_DIR}/scripts/selftest.py" --json      # 结构化输出（供 agent 解析）
```

**自检内容**：
- 离线层：用内置微型夹具跑通 normalize → build_report，验证本地工具链；另报 check.py 可选依赖 china_sources（po-xu-wang 未装不影响主流程）。
- 在线层（微博/小红书）：各驱动 MediaCrawler 做 1 条最小关键词搜索，判定"可用 / 登录态失效 / 风控 / 未部署"。
- 在线层（知乎）：cookie 文件存在性；若 config.json 的 `probe.zhihu_question` 配置了探测问题，再发 1 次 API 请求验证 cookie 真伪（HTTP 非 200 = 过期）。未配置时仅静态检查，网络验证随真实任务发生。

**失败处理铁律（任务开始前询问用户，不得擅自决定）**：
0. **先分类阻隔原因，勿把「权限」当「失败」**：① 权限阻隔（沙箱放权/浏览器启动/文件写）→ 用 danger-full-access 升级重试同一条命令或向用户申请授权，属正常索取流程，**不算工具失败、不触发「跳过」**；② 凭证阻隔 → 走下方二选一；③ 网络/风控等环境阻隔 → 如实报告并给替代路径（如 dsh-ssh 远端执行）。只有用户明确选择「跳过」，才可跳过该工具并记入覆盖声明；
1. 汇总失败工具清单（selftest 已逐项输出）；
2. 对每个失败工具向用户提问二选一：
   - **跳过该工具** → 任务继续，报告中"覆盖完整性声明"须注明该平台未采集；
   - **更新凭证** → 知乎：用户更新 `zhihu/cookie.txt`；微博/小红书：用户执行 `collect.py --login weibo,xiaohongshu` 扫码或提供新 cookie，然后重跑 selftest 确认；
3. 用户做出选择前不开始正式采集。

setup.py（静态体检：依赖/凭证是否存在）与 selftest.py（最小真实尝试：工具是否真能跑通）互为补充：环境变动或凭证失效后先跑 selftest。

## 强制质量检查项（交付前核对）

1. **对立/少数观点必须显式覆盖**并标注信源稀缺度（防止主流叙事单边化）
2. **舆论数字必须带误判率**：立场占比与抽检误判率**并排呈现**（门禁 G11 会查），<10% 少数派须显式标注；数据由 `/判风潮` 产出，百宝袋只负责在第六章如实渲染——**未跑抽检时报告必须声明"占比为未校正读数"**（v0.6 起 `_spotcheck_html` 自动声明，不再静默留白）
3. **覆盖完整性声明**：明确列出未采到的立场/平台/信源（如"家属方未回应""小红书未部署"），
   **并给出语料治理口径**：原始采集 N 条 → 分析 M 条（剔除 X 条、占比、平台/类型构成）
4. **事实冲突必须消解**：口径/时间/立场差异分析 + 给出更可信一方
5. **确定性标记**：▲官方确认 / ●多源一致 / △单源存疑，不确定信息不得伪装成事实
6. **自检留痕**：若任务前执行过工具自检，报告中注明结果与用户选择（跳过某工具 / 已更新凭证）
7. **数字一数一源**：报告内同一指标只能有一个数；"原始量/分析量/剔除量"三个口径必须逐处写明是哪一个
   （`check_report.py --joint --data` 的 R1/R2 会查）
8. **采集检索词公开**：报告方法论章须列出实际使用的检索词（含中立词/反方词那一组），占比随检索词偏移，读者有权知道检索式
7. **引用闭环**（v0.3）：时间线节点用 `ref` 字段、断言用 `claims[].ref` / `evidence[].ref`、高赞代表由样本 `ref_url` 自动挂号，
   三处都必须可点击跳转来源索引；交付前 `check_report.py` 以 W1（双向一致）、W16（高赞代表引用率）、E6（断言可溯源）复核
8. **口径披露**（v0.3）：立场占比必须与 `coverage_rate`（归类覆盖率）同时呈现，不得只给占比不给分母口径（门禁 G7）；
   机构帖与未归类残差不得作为一个「立场」进入占比与图表
9. **立场卡组**（v0.3）：`stance_cards.json` 为阶段②必产工件，卡内只写抽象逻辑链（主张→要件→推论→代价→落点）、
   **不放引语**（代表性原文归第五章「高赞代表」表）；书写限制见 report-theme「立场卡组书写限制」，超限由 check W15 提示

## 依赖与凭证

- Python 3.10+、requests；MediaCrawler 另需其 `.venv`（已部署，含 playwright 浏览器）
- 知乎 cookie 存于 `{SKILL_DIR}/zhihu/cookie.txt`；失效时提示用户更新（浏览器 F12 → api/v4 请求 Cookie）
- 微博/小红书凭证（三选一，collect.py 自动检测）：
  1. **`--login weibo,xiaohongshu` 一次性扫码登录**（推荐，无需复制/传输 cookie，会话存 browser_data）
  2. `--wb-cookie / --xhs-cookie` 参数，或环境变量 `BBD_WB_COOKIE / BBD_XHS_COOKIE`
  3. 小红书可回退 MediaCrawler 内置 cookie（`config/base_config.py` 的 COOKIES，可能过期）
- 凭证缺失时 collect.py 明确标注"未采集"，**不静默假成功**；setup.py 一键静态体检；任务开始前可按需运行 selftest.py 做工具可用性最小尝试（见「工具自检」节）

## 运行环境：DSH/Windows 沙箱（MediaCrawler 权限与超时）

MediaCrawler 需启动 Playwright/Chromium **浏览器进程**，并读写 `{SKILL_DIR}/external/MediaCrawler/browser_data`（用户目录，工作区之外）。在 DSH 文件沙箱受限模式下浏览器启动会被拒绝（collect 输出"浏览器启动被拒绝（沙箱/权限限制）"）——**这是权限问题，不是凭证问题**。

- **DSH 会话内采集 / 自检**：对 collect.py（及 selftest.py 在线层）命令授予 **danger-full-access** 后执行（已实测：放权后微博可直接采集成功）。审批被拒或无法授权时，转普通终端运行，或经 dsh-ssh 到远端主机执行（本机沙箱不放开浏览器时该限制无解）。
- **免扫码是常态**：只要 `browser_data/<platform>_user_data_dir` 存在（一次 `--login` 扫码建立），collect 即判为"持久化登录态"，以 MediaCrawler `qrcode` 模式**静默复用会话**，不弹码、不需 cookie。报错文案"需 `--login` 扫码或提供 cookie"是通用提示，不代表缺凭证——先检查对应 `*_user_data_dir` 的最后写入时间。
- **小红书卡死识别（重要）**：xhs 登录态老化（数日/数周未用）或被风控时，headless 会话会长期停滞在等待/验证，**直至超时仍可能不返回**。处置：首轮超时后**不要盲目加时重试**（已实测 240s→480s 重试同样无产出），应检查 `xhs_user_data_dir` 时效，过旧则提示用户 `collect.py --login xiaohongshu`（有头扫码刷新会话）；必要时在有头/人工环境下采集 xhs。
- **服务端吊销判据（实测 2026-09）**：本地 cookie 未过期≠可用——小红书会主动吊销长期不活跃的 `web_session`（本地 expires 仍 2027，服务端已判失效）。快速诊断法：以 danger-full-access 起 MediaCrawler 约 60–90s 探针，日志出现 `[XiaoHongShuClient.pong] Login state result: False` → **服务端吊销，本地任何 cookie 均无效，唯一恢复路径是重新登录**（`--login xiaohongshu` 或注入新 cookie）；出现 `acw_tc / sec_poison_id` 过期仅为风控短期标记，可忽略。此场景下"验证 cookie 过期"必须走服务端 pong，不能只看本地过期时间。
- **临时目录（v0.6 起已自愈）**：`selftest.py` 与 `tests/` 默认用系统临时目录；在受限沙箱（workspace-write）下系统目录不可写，二者会**自动回退**到工作区内的 `skills/bai-bao-dai/tests/_tmp/`。**不再需要**手工把 `TMP/TEMP` 重定向到工作区（旧做法仍有效，但已非必需）。

## 政策工具族（scripts/policy · 模块化积木）

当任务涉及「政策类信息」（政策/法规检索、文号解析、公文解析、时效核验）时，除热点事件流水线外，可使用 policy 工具族。

**模块化约定：每个模块=一块积木——既可单独 CLI 调用（输出 JSON），也可被其他模块或流水线 import 拼接；依赖惰性加载，缺依赖时给安装提示（退出码 2），不静默假成功。**注册与依赖声明见 `config.json` 的 `policy_tools`。

| 模块 | 干什么 | 单独用法（示例） |
|---|---|---|
| `query_gov.py` | 国务院政策文件库直查（需网络） | `python scripts/policy/query_gov.py "营商环境" --n 10 --out out.json` |
| `query_flk.py` | 国家法律法规库检索/时效核验（需网络） | `python scripts/policy/query_flk.py "行政处罚法" --sxx 3 --out out.json` |
| `docnumber.py` | 文号批量解析（零依赖） | `python scripts/policy/docnumber.py "（国办发〔2026〕12号）"` |
| `pdf_parse.py` | 政务 PDF 文本/表格提取 | `python scripts/policy/pdf_parse.py text 报告.pdf` |
| `ocr_parse.py` | 扫描件 OCR（PP-StructureV3） | `python scripts/policy/ocr_parse.py 红头扫描.png`；`--check` 预检 |
| `policy_selftest.py` | 离线自检（无需网络） | `python scripts/policy/policy_selftest.py --json` |

**拼积木示例**：
1. 采集+核验：`query_flk.py` 检索 → 读 out.json →（LLM）组织成条目；
2. 解析链：`pdf_parse.py text` 提取正文 → `docnumber.py` 抽出文号 → `query_gov.py` 反查出处与发文机构；
3. 事件扩展：热点事件含政策通报时，`collect.py` 采集 → 对命中文本调 `docnumber/pdf_parse` 提取政策要素 → `normalize.py` → `build_report.py`。

**约定**：官方直查（query_*）失败输出 `{"ok": false, "error":..., "fallback": "use WebSearch"}` 且退出码 1——不得静默假成功；OCR/PDF 依赖缺失时打印 pip 安装提示。本族默认不包含 AGPL/GPL 重引擎（如 MinerU/Marker）；需商用重解析时另行评估。

## 注意事项

- 仅供学习研究，禁止商业用途与大规模爬取；控制频率（config.json 限速为参考值，脚本内置 sleep）
- 采集仅限公开信息，匿名化处理（本机 MediaCrawler 为教学版，用户信息已脱敏）；遵守各平台条款
- 搜索引擎索引有延迟，报告须标注信息截止时间（build_report 自动生成）
