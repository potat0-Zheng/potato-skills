# 报告装配与质量契约（Report Contract）

> 适用家族：揽风云（lan-feng-yun）· 破虚妄（po-xu-wang）· 百宝袋（bai-bao-dai）及其 HTML 产物。
> 版本：v1（2026-02）· 配套皮肤 `report_template.html`（theme-v ≥ 0.2.8）。
> 本文件是「规范 + 写入流程」的唯一主文档；模板内的 `CONTENT_START` 注释与各 skill 的 SKILL.md 组件文档是对它的摘要，冲突以本文件为准。

## 0. 三层事实源与联动

| 层 | 事实源 | 维护者 | 说明 |
|---|---|---|---|
| 皮肤层 | `report-theme/report_template.html`（主模板） | 人工 | 改皮肤：改主模板 → bump `theme-v` → `python sync_theme.py` 分发 |
| 内容契约层 | 本文件 `CONTRACT.md` + 模板注释 + 各 skill SKILL.md 组件文档 | 人工 | 新增/变更组件类或规则时三处同步（README「联动维护」） |
| 生成/校验层 | `build_report.py` · `md_render.py` · `assemble_report.py`（规划）· `check_report.py` | 脚本 | 渲染与校验尽量自动化，减少对 LLM 纪律的依赖 |

原则：**报告 = 主模板皮肤 + 各技能内容**；任何产物交付前必须通过 `check_report.py`（0 error）。

## 1. 装配规则（结构骨架）

1. 单文件、UTF-8、离线、零外部请求；`<html lang="zh-CN">`。
2. 结构固定为：`<head>`（含 `<!-- theme-v: x.y.z -->` 标记）→ `<main class="wrap">` → `<!-- CONTENT_START -->` → 正文 → `<!-- CONTENT_END -->` → `</main>`。
3. **`CONTENT_START` 与 `CONTENT_END` 全文必须各恰好出现一次**，且正文只允许落在两个标记之间。
4. 模板正文占位区内的「组件约定」说明注释**不允许出现在成品中**（装配时整段移除；出现在 `CONTENT_END` 之后 = 拼接残留，check E1/E2 拦截）。
5. 正文直接写在 `<main class="wrap">` 内：章标题用 `<h2>`，**不得再包一层 `.wrap`**，不得包 `<section>`/`<article>` 等额外容器（标题编号依赖 `main > h*` 直接子级选择器）。
6. 章节锚点：`<a id="chN" class="sec-anchor"></a>` 紧跟章标题前；目录用 `<nav class="toc">` 内 `<a href="#chN">`。
7. 报头（可选）：`<div class="masthead">` 内 `eyebrow + h1 + intro + hero-figure`；h1 为整篇标题，正文第一级一律用 h2。
8. 页脚：正文末尾（`CONTENT_END` 前）放 `report-meta` 文本（见 §5），新产物必填。

## 2. 标题编号规则（theme-v ≥ 0.2.8）

1. **一级标题（章，h2）**：文本手写中文序数（`一、二、…`），皮肤不追加数字；附录可写作 `附录：…`。
2. **二级及以下（h3/h4/h5）**：由皮肤 CSS 计数器自动渲染 `1.1 / 1.1.1 / 1.1.1.1`（首位=章序，按 h2 出现顺序）。
3. 因此正文中 h3/h4/h5 的**文本不得再手写任何序号前缀**（中文「一、」或数字「1.1」都会与自动编号重复）。
4. 小节标题必须为 `<main>` 的**直接子元素**（与 h2 平级）；`.meta-box`/`.card`/`details` 等容器内的标题不编号。
5. 禁止跳级（如章内首个直接 h4 → 会显示 `x.0.1`）；同一标题层级禁止连续出现两个空转标题（拼接残留特征）。

## 3. Markdown 渲染规则（md → HTML）

适用：百宝袋 `build_report.py` 的 `--viewpoint/--coverage` 注入、揽风云/破虚妄的 md 章节正文、以及未来的统一装配器。

1. **唯一渲染器**：md → HTML 只允许经 `md_render.md_to_html()`（report-theme 版）或 `build_report.md_to_html()`（技能内自包含，算法同源）转换。**禁止把 Markdown 原文直接写进正文**。
2. 支持子集：标题（`#`..`######`，渲染为 h3..h8 以避开章级 h2）、**GFM 管道表格**、无序列表、引用、粗体 `**`、斜体 `*`、行内代码 `` ` ``、行内链接 `[t](url)`、段落。
3. **标题前缀自动剥离**：渲染标题时剥离「一、二、…、」与「1. / 1、/ 1)」等手写序号前缀，配合 h2 中文序数 + h3/h4/h5 自动编号，正文不得再手写序号。
3. **管道表格必须渲染为 `<table><thead><tbody>`**（含 `---` 分隔行；`:---:`/`---:`/`:---` 映射居中/右/左对齐）。不允许以 `<p>| … |</p>` 文本段落形式落下（check E3 拦截）。
4. 渲染顺序与容器闭合：表格/标题/列表/引用互不嵌套；段落连续输出。
5. 特例（LLM 直写 HTML 时，同属禁止项，check W6 提示）：
   - `<p>` 内不得出现管道行、`**`、`[文本](url)`、`#` 标题语法残留；
   - 实体必须完整（`&amp;`/`&lt;` 等以 `;` 收尾；截断实体如 `&amp;q` 禁止，check W4 提示）；
   - 文字截断须以 `…` 或句末标点收尾，禁止单词/引文半截中断。

## 4. 组件用法速记（与模板注释一致）

- 结论速览：`<div class="meta-box"><dl><dt>键</dt><dd>值</dd>…</dl></div>`（**不要**用 h3+ul+内联 style 自制）。
- 引用：正文 `<a href="#refN" class="ref">[N]</a>` ↔ 索引锚点行 `<tr id="refN" class="source-A|B|C">`。
  - **来源分级必填**：`source-A`（官方/权威）、`source-B`（主流媒体）、`source-C`（自媒体/UGC）——分级底色依赖该 class。
  - 正文引用集合与索引锚点集合必须双向一致（check W1）。
- 确定性标记（语义固定，勿挪用）：`mark-official`=▲官方确认 / `mark-multi`=●多源一致 / `mark-single`=△单源存疑 / `mark-caution`=警示（如「证据不足」可用，但图例须注明）。
- 表格：一律带 `<thead>`+`<tbody>`（斑马纹与打印表头依赖它们；check W2 提示缺 thead 的表格）。
- 章节导读：`<p class="chapter-intro">`；图例 `<p class="legend">`；折叠 `<details class="fold">`。
- 数值类文本（KPI/图表/stat-line/正文）应从同一份数据渲染，**不得同一指标两处数字不一致**（如章导读 24 条 vs stat-line 74 条）。

## 5. report-meta 页脚（新产物必填）

正文末尾（`CONTENT_END` 前）输出一段结构化文本页脚，形如：

```html
<p class="footer">
  生成器：百宝袋 v? · 皮肤 theme-v 0.2.8 · 生成时间 2026-09-06 22:39<br>
  信息截止：2026-09-06（检索快照）· 数据指纹：sha256 前 12 位（可选）<br>
  覆盖声明：…（可选）· 仅供学习研究
</p>
```

字段：`生成器`、`theme-v`、`生成时间`、`信息截止`；可选：`数据指纹`、`覆盖声明`。
用途：可审计、可机器解析（check W5 只校验存在性）。皮肤样式待 v0.2.9 提供专用 `.report-meta` 布局。

## 6. 写入流程（Write Flow）

### 6.1 单技能标准路径（百宝袋）
```bash
# 阶段 1-3：collect → normalize → opinion（数据落 data/{event_id}/）
# 阶段 4：渲染
python "{SKILL_DIR}/scripts/build_report.py" --event "关键词" \
    --facts data/{event_id}/facts.json --opinion data/{event_id}/opinion.json \
    --normalized data/{event_id}/normalized/data.jsonl \
    --viewpoint data/{event_id}/viewpoint.md --coverage data/{event_id}/coverage.md \
    --abstract data/{event_id}/abstract.md \
    --timeline-milestones data/{event_id}/timeline.json \
    --out data/{event_id}/report.html
# 质量门（交付前必跑；0 error 才允许交付）
python "~/.dsh/report-theme/check_report.py" "data/{event_id}/report.html"
```
LLM 深度内容（viewpoint/coverage/abstract）写作时只写 md，**表格用 GFM 管道语法**，由渲染器负责转真表格。新增数据字段与参数（v0.2.10）：
- `facts.json claims[].short_title`：断言卡小标题（模型摘要，≤24 字、只压缩原文、保留限定词）；
- `--abstract`：ch1 两段式摘要 md（空行分段：段一 事件→进展→舆论；段二 真实性→结构提示）；直接装配时 ch1 摘要可用 v0.2.16 标签化 `.abstract dl`（一句话结论/事件定性/事实核查/平台采样/舆论主形态/少数派/事件跨度，见模板注释）；
- `--timeline-milestones`：ch2 竖式时间轴 `[{date,type,tag,title,text,count}]`，type∈official/media/view/bg/quiet/check；缺省回退日期×条数表；
- `sources[].grade`：ref 行分级 `source-A/B/C`；自动采集帖默认 C（UGC）；
- `--nav-drawer/--no-nav-drawer`：默认注入左侧常驻目录栏（v0.2.12：透明底、常展开、滚动高亮当前章）并移除顶部 toc。

### 6.2 揽风云 / 破虚妄 HTML 路径（LLM 直写或 md 章节）
1. 读 `assets/report_template.html`（揽风云）或主模板；替换 `<!-- TITLE -->` 与标记间正文。
2. 正文按 §1–§4 书写；md 章节若存在，先经 `md_render.md_to_html()` 转换再嵌入。
3. 交付前运行同一条质量门：
```bash
python "~/.dsh/report-theme/check_report.py" "输出.html"
```

### 6.3 三技能综合报告（装配路径，规划中）
目标：多技能产物经统一装配器（`assemble_report.py`，manifest JSON 定义章节/引用/元数据）合并输出，
替代「手工把多份产物粘贴进模板」——后者是 CONTENT_END 残留、双标题、ref 分级缺失等结构性缺陷的直接来源。
装配器输出自动满足 §1–§5，并内联跑 check_report。

## 7. 自检规则总表（check_report.py 实现）

| ID | 级别 | 规则 | 修复指引 |
|---|---|---|---|
| E1 | error | `CONTENT_START` 出现次数 ≠ 1 | 合并/装配时清理，只留一对标记 |
| E2 | error | `CONTENT_END` 出现次数 ≠ 1，或标记顺序错乱 | 清理尾部残留（模板注释段 + 第二个标记） |
| E3 | error | `<p>` 内出现 md 管道表格残留（`<p>|`） | 用 md_render/build_report 转成真表格 |
| E4 | error | 标题为空（`<h*> </h*>`） | 删除或补标题文本 |
| W1 | warning | 正文 `.ref` 引用 ↔ `id="refN"` 锚点不双向一致 | 补齐缺失锚点或删除悬空引用 |
| W2 | warning | `<table>` 缺 `<thead>`（斑马纹/打印表头失效） | 补 thead/tbody |
| W3 | warning | `tr[id^="ref"]` 缺 `class="source-A/B/C"` | 按来源分级补 class |
| W4 | warning | 疑似截断实体（`&amp;` 后紧跟字母、行尾半截实体） | 修复为完整实体/补齐文本 |
| W5 | warning | 缺 report-meta 页脚（生成器/theme-v/时间） | 补 §5 页脚 |
| W6 | warning | `<p>` 内 md 残留（`**`、`[t](url)`、`#` 标题语法） | 走渲染器重转 |
| W7 | warning | 相邻同级别标题连续（疑似双标题残留） | 删空转标题 |
| W8 | warning | 非 `<!DOCTYPE html>` 开头 | 以模板整页输出 |
| W9 | warning | 断言卡缺 `.claim-title` | 补 `facts.json claims[].short_title` |
| W10 | warning | 第一章有仪表盘但缺 `.abstract` 摘要 | 补 `.abstract` 摘要（`--abstract` 两段式 或 v0.2.16 标签化 dl 直写） |
| W11 | warning | 附录下首个标题为 h4（跳级 → `x.0.y`） | 补 h3 或降级为 h3 |

`--strict`：warning 计为失败（交付门禁建议开）；默认仅 error 使退出码非 0。

## 8. 变更记录

- v1：确立三层契约与写入流程；md 渲染器补表格支持；check_report.py 上线（E1-E4/W1-W8）。
