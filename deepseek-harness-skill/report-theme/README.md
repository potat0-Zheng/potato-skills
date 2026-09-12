# report-theme —— 报告家族统一 HTML 皮肤（单一事实源）

## 定位

为「信息类 skill 家族」（当前：百宝袋 bai-bao-dai、揽风云 lan-feng-yun；未来可纳入参国是 can-guo-shi、破虚妄 po-xu-wang 的 HTML 模式）提供统一的 HTML 报告皮肤。

**本目录只存放「皮肤层」（CSS 变量、组件类、结构约定），不包含任何 skill 的内容生成逻辑。**

## 存储方案（方案 C：独立主模板 + 同步分发 + 本地自包含副本）

| 方案 | 结论 | 原因 |
|------|------|------|
| A. 纯独立引用（各 skill 运行时直接读主模板） | ❌ | 破坏 skill「相对独立」：主模板缺失则构建直接失败 |
| B. 纯重复（每个 skill 各写一份） | ❌ | 必然漂移（此前两份副本已出现差异），N 份维护 |
| C. 主模板独立 + 同步分发 + 本地副本 | ✅ | 单一事实源 + 运行时零外部依赖，兼顾一致性与独立性 |

### 工作方式

```
report-theme/
├── report_template.html   # 主模板（单一事实源，含 <!-- theme-v: x.y.z --> 版本标记）
├── sync_theme.py          # 同步/校验脚本（分发主模板到 skill 本地副本）
├── CONTRACT.md            # 报告装配与质量契约（规范 + 写入流程的唯一主文档）
├── check_report.py        # HTML 产物静态自检器（交付质量门，规则 E1-E4 / W1-W8）
├── md_render.py           # 共享 md→HTML 渲染器（含 GFM 管道表格；装配/直写路径使用）
├── README.md              # 本文档
└── _poc/                  # 样式验证 POC（演示组件用法，非交付物）
```

> `build_report.py` 内的 `md_to_html` 是技能内自包含的同算法副本（保持 skill 相对独立），两处算法需同步演进。

同步目标（各 skill 的**本地副本**，运行时只读本地副本）：

| skill | 本地模板路径 |
|-------|--------------|
| bai-bao-dai | `skills/bai-bao-dai/templates/report_template.html` |
| lan-feng-yun | `skills/lan-feng-yun/assets/report_template.html` |
| can-guo-shi | 暂未携带本地副本（按引用继承揽风云），纳入时加入 TARGETS |
| po-xu-wang | 当前无 HTML 输出；新增 HTML 核查模式时加入 TARGETS |

## 使用

```bash
# 修改皮肤：编辑 report_template.html（主模板）后执行——
python report-theme/sync_theme.py        # 分发到全部 skill 本地副本
python report-theme/sync_theme.py --check  # 只检查一致性（防漂移）
```

- **各 skill 内的模板副本请勿手工修改**（文件头部有同步说明注释）；如需实验性样式，先改主模板再同步。
- 运行时各 skill 只读自己的本地副本——主模板目录丢失不影响任何 skill 单独使用（相对独立保留）。
- 升级流程：改主模板（bump `theme-v`）→ `sync_theme.py` → 全家族同步生效，一次升级处处一致。
- **联动维护**：新增 CSS 类时需同步三处——① 主模板加样式；② 百宝袋 `build_report.py` 生成逻辑（如需程序化输出该类）；③ 揽风云 SKILL.md 组件文档（LLM 才知道使用它）。sync 只保证模板文件一致，组件文档的同步需人工维护（见下表）。

## 组件类约定（v0.2 · Editorial 风格）

**设计来源**：Raleighawesome/html-design-skill 的 Editorial 风格（Apache 兼容），经中文适配与报告组件扩展。

| 类/结构 | 用途 |
|---------|------|
| 设计令牌（`:root` CSS 变量） | 象牙底色/纸张白/石板墨/陶土强调/橄榄+燕麦辅助；衬线/无衬线/等宽三套字体栈（中文优先，离线回退） |
| `meta-box`（`<dl>`） | 结论速览/元数据卡片（百宝袋程序化输出） |
| `kpis` + `kpi-card`（`kpi-num`/`kpi-lab`/`kpi-sub`） | 报告仪表盘大数字卡（百宝袋「结论速览」程序化输出；报告头部需要"一眼看规模/可信度"时可手写） |
| `chapter-intro` | 章节导读一行（build_report 程序化生成，`--intros` 可覆盖；无 section-head 序号故不缩进） |
| `details.fold` | 长文本/长原文收纳折叠（`<summary>` 触发，零 JS；打印时自动展开内容） |
| `bars`（`bar-row`/`bar-track`/`bar-fill`/`bar-val`） | 立场占比横向条形图（build_report 第五章自动输出，纯 CSS） |
| `stat-line` | 来源/分级统计小结行（build_report 第六章输出，mono 小字） |
| `table.matrix` + `cell-ok`/`cell-miss`/`cell-gap` | 覆盖缺口三态矩阵（第七章；暖纸砖红=已覆盖/灰=未采集/橙=缺口；v0.2.11 起已覆盖由绿改暖） |
| `stacks`（`stack-row`/`stack-seg.s1-5`/`stack-legend`） | 焦点转移堆叠条：立场 × 日期（第五章；依赖 opinion `stance_timeline` 字段） |
| `table.conflict` | 口径冲突对照表（第四章可选；facts.json 断言 `conflicts` 字段） |
| `actors-side` / `.card .quotes` | 事件角色小词典（第一章可选折叠卡组，`--actors` 输入，按 side 分组） |
| `table.dualtrack`（`cell-claim`/`cell-verify`） | 说法×证实双轨时间轴（第二章可选；`--track` 输入，徽章复用 `.mark-*`） |
| `h2` | 章标题（衬线 26px + 下边框）；LLM 可用 `.section-head` + `.index`（19px 陶土衬线序号 01/02/03）增强 |
| h3/h4/h5 自动编号（v0.2.8） | 章内小节标题渲染 1.1 / 1.1.1 / 1.1.1.1（首位=章序，CSS 计数器，`<main>` 直接子级标题）；章标题 h2 文本保留手写中文序数（一、二、…） |
| `table` + `th/td` | 来源索引 / 立场分布 / 覆盖声明表格；`tr.source-A/B/C` 分级着色；`tr[id^="ref"]` 编号列等宽 |
| `.ref` + `id="refN"` | `[编号]` 引用链接与索引锚点（正文→索引跳转） |
| `mark-official / mark-multi / mark-single / mark-caution` | 确定性标记徽章：▲官方（绿）/ ●多源（蓝）/ △单源（橙）/ 警示 |
| `report-row` | 时间线行（日期列 130px + 内容），`stance-row` 立场行（标签列 + 内容列，已修"内容挤入窄列"问题） |
| `.grid` + `.card` | 卡片网格（结论要点/立场卡），`.note` 重点框，`.pill`/`.toc` 导航 |
| `img-card` / `img-fallback` | 图像嵌入与回退 |
| `legend` / `caption` / `.subtitle` | 图例、图片说明、副标题（等宽灰字） |
| `.masthead` / `.hero-grid` / `.hero-figure` | 报头区（可选，LLM 内容自建：眉题 + 大标题 + KPI 摘要卡） |
| `.bnav`（v0.2.12） | 左侧常驻目录栏：固定左缘 240px、常展开、透明背景（`body:has(.bnav)` 为内容让位）；条目 `.bnav-item(.t)` 超长省略 + 悬停 title，内联 JS 滚动高亮 `.bnav-item.active`（当前章指示）；≤880px 与打印自动隐藏；无触发钮/遮罩 |
| `.tl` / `.tl-item` / `.tl-dot.tg-*` / `.tl-tag.tg-*`（v0.2.9） | 竖式里程碑时间轴（ch2：事件节点 + 当日采集量 `.tl-cnt`，纯 CSS） |
| `.v.v-true` / `.v.v-part`（v0.2.9） | 裁决徽章族（真实=砖红系、部分真实·观点性=金，v0.2.11 起；与 `.mark-*` 证据徽章分族） |
| `.claims` / `.claim` / `.claim-title`（v0.2.9） | 断言卡（ch3：编号 + 断言原文 + 裁决徽章 + fold 说明；`.claim-title` 模型摘要行，非标题元素） |
| `.abstract`（v0.2.9→v0.2.17） | 事件摘要块（ch1，模型生成）：标签化清单 `dl`（`dt` 标签列衬线加粗 16px + `dd` 内容列无衬线同正文；推荐标签序：一句话结论/事件定性/事实核查/平台采样/舆论主形态/少数派/事件跨度，≤640px 堆叠）；旧两段式 `.abs-label+.abs-txt` 兼容 |
| 表格 th/td（v0.2.13） | 全局文字水平+垂直居中；md 管道表 `:---` 对齐以行内样式优先 |
| `.chart-split` / `.donut` / `.donut-legend` / `.trend`（v0.2.13） | 图表双栏（两列、上下居中、≤880 单列）+ 环形图（conic-gradient 百分比直转 deg、mask 掏洞成环，不支持回退实心饼）+ 静态 SVG 趋势折线（.trend-head/网格/折线 .s1-.s4/面积/圆点/图例；坐标由生成端按数据算） |
| `.scards` / `.scard` / `.ssteps` / `.scard-note`（v0.2.13） | 立场卡组 + 论证链步骤带（自动数字圆点+箭头）；跨卡对齐：卡头两行、节点等高、防呆齐底。**书写限制**：单步 ≤14 汉字、3–5 步、卡内不放引语、引用编号放卡尾 note 行首（见「立场卡组书写限制」；超限由 check W15 提示） |
| `.tl-refs`（v0.3 装配端约定） | 时间线节点引用行（`milestones[].ref` → 可点击 `[N]`），纯文字行、无新样式 |
| `.quote-list` / `.quote-item`（v0.2.13） | 表格/卡内「高赞代表」逐条成块：`.quote-like` 赞数徽章 + `.quote-txt`，条目虚线分隔、窄屏单列 |
| `.viewpoint-meta` / `.viewpoint-snap`（v0.2.13） | 视点综合信息条（浅纸底陶土左边条）+ 「◷ 动态快照 · 日期」徽章；h3:has(+ .viewpoint-meta) 只调间距不动编号 |
| `.table-scroll`（v0.2.13） | 长表滚动容器：max-height min(58vh,540px) + 内置滚动 + 吸顶表头；打印自动展开全文 |
| `.refpop`（v0.2.13） | 引用悬停气泡（装配端 refpop JS 读本页 #refN 索引行自动浮出：来源/标题/类型/分级/查看原文；无 JS 回退纯跳转） |
| `.stack-seg.s6` / `.stack-legend i.s1–s6`（v0.2.13） | 第 6 立场段色 + 图例色块补色（此前 `<i class="s1">` 无色块、图例透明） |
| `.rail-card` / `.rail-actors` / `.racard` / `table.rail` / `.rn` / `.rail-cross`（v0.2.18） | **主体轨道网格**（`--actors` 对象形态，挂 ch2 末为 x.1 小节）：`.rail-card` 容器（无边框 + 阴影底座 #f7f5f0）> `.rail-head`（`.rail-title` 左侧陶土竖条 + `.rail-meta` 主体/阶段/跨度/拓扑）+ `.rail-actors`（身份简介卡组：`.racard`（主轴 `.main` 带陶土 inset 色条与陶土阴影）= `.racard-h`（`.racard-id` 编号徽章 + `.racard-n` 名称）+ `.racard-r` 角色行 + `.racard-b` 身份简介 + `.racard-claims` 主张 #N）+ `table.rail`（行=主体、列=**离散阶段**；`th .rg` 日期区间、`td.rail-actor` 内 `.aid` 编号 + `.sub` 角色、`td.cell` 内节点胶囊 `.rn.<kind>`、活跃列 `td.hot`）+ `.rail-cross` 交锋带）；`.rn` 类型编码 act ●官方行动 / decl ▲声明·发布 / view ○表态·被指 / judge ◈裁决 / quiet ┄静默（虚线）；编号 `.aid` ↔ `.racard-id` 一一对应 |
| 响应式 / 无障碍 / 打印 | 880px/640px 断点、reduced-motion、打印分页（h2 分页 + 表格防拆分 + 表头重复；目录栏/时间轴打印降级） |

## 立场卡组书写限制

`.ssteps` 是 `flex: 1 1 0; min-width: 120px` 的横向弹性槽，`.scards` 是 `align-items: stretch` 的等高网格。
两者叠加的后果是：**单步文字一长就换行增高，最短的卡被拉伸到与最高卡等高，底部留大片空白**。
按 ≥300px 卡宽（`minmax(min(300px,100%),1fr)`）反算——两步一行、每步可用文字宽约 75px、字号 11.5px、
行高 1.5——单步文字的安全上限约为：

| 项 | 限制 | 说明 |
| --- | --- | --- |
| 单步字数 | **≤14 个汉字**（标点计入、引用编号不计） | 每行约 6 字，2 行内不增高 |
| 步骤数 | 3–5 步 | 5 步在窄卡下占 3 行，已接近上限 |
| 引用编号 | 不内联在步骤内 | 3 个 chip 约吃掉 42px；统一放卡尾 `.scard-note` 行首「证据 [N][N] · …」 |
| 卡尾 note | 2–3 行（约 ≤55 字） | 各卡长度相近，避免跨卡等高留白 |
| 引语 | **卡内不放** | 代表性原文归第五章「高赞代表」表；放进卡内即与该表逐条重复 |

超限由 `check_report.py` 的 **W15** 提示（`--strict` 下计为失败）。同族限制见 CONTRACT.md §4 与主模板 `.scards` 注释。

## 装配端新增输入（v0.3，皮肤无变更）

`theme-v` 保持 0.2.18（本轮仅新增装配约定与校验规则，未改 CSS）：

| 输入 | 位置 | 说明 |
| --- | --- | --- |
| `ref` | `timeline.json` 各节点 | 引用编号串（如 `"[1][5]"`）→ `.tl-refs` 行 |
| `ref` | `facts.json` `claims[]` / `evidence[]` | 合并去重后渲染「引用：」行 |
| `ref_url` / `platform` / `id` | `opinion.json` 各 `top_samples[]` | 装配端解析为 `[N]` 并优先收录进来源索引 |
| `coverage_rate` / `denominator` / `residual` / `institutional_layer` | `opinion.json` | 渲染第五章口径统计行（占比与覆盖率并列） |
| `like_pct` | `opinion.json` 各立场 | 第五章表格「赞同占比」列 |

校验新增：**E6** 断言可溯源 · **G7** 占比须披露归类覆盖率 · **W15** 卡组书写超限 · **W16** 高赞代表缺引用。

## 版本与演进

- **v0.2.18（当前）**：**主体轨道网格**（`--actors` v2 对象形态）——新增 `.rail-card / .rail-head / .rail-actors / .racard / table.rail / .rn / .rail-cross / .rn-legend` 组件族，把「主体视角」做成报告的第二认知轴：**身份简介卡组**（谁是谁 + 在事件中的位置）+ **主体×离散阶段轨道矩阵**（谁在何时做了什么）+ **跨主体交锋带**（指控→否认→悬置的关系序列）+ 节点类型编码（act/decl/view/judge/quiet，静默显式登记）。关键设计：**列取离散阶段而非时间比例** → 跨度悬殊（20 年历史背景 vs 1 天爆发）不失真；无边框设计（#f7f5f0 底座 + 阴影层次，主轴卡用陶土 inset 色条与陶土阴影分层）。配套：`build_report.py` 新增 `_actors_rail_html()`（`--actors` 分流：对象→轨道网格挂 ch2 末 / 数组→旧角色小词典挂 ch1 末，向后兼容），`claims` 裁决词从 `facts.json` 登记册自动补齐（CONTRACT-joint §9）；`check_report.py` 新增 E5 / W12–W14；`lan-feng-yun` SKILL.md 新增「阶段 6：主体档案」方法节；`CONTRACT-joint` 增 §9 并 bump `contract=joint-v0.2`。
- **v0.2.17**：摘要排版细节——`.abstract` 的 `dt` 标签由等宽灰字改为**衬线加粗（16px/600，与正文同字号，同 `.claim-title` 类小标题体系）**、`dd` 内容由衬线 15.5px/两端对齐改为**无衬线 16px/行高 1.72 与正文一致**（去两端对齐）；标签与内容首行基线自然对齐，摘要块字体与整篇正文/小标题体系协调一致。
- **v0.2.16**：摘要标签化——ch1 事件摘要块 `.abstract` 由「两段式散文」改为「标签化清单」：`.abstract dl` 两列（`dt` 标签列等宽 mono + `dd` 内容列衬线正文，≤640px 堆叠单列、组间留白）；推荐标签序：一句话结论 → 事件定性 → 事实核查 → 平台采样 → 舆论主形态 → 少数派 → 事件跨度（标签按报告类型可增减，dd 长文可再分 p）；新增 `.abs-foot` 注脚（"模型生成·以各章为准"）。旧 `.abs-label/.abs-txt` 两段式样式保留兼容（生成器 `--abstract` 自动识别：结构化标签式 md → dl 行；旧两段式 → 段落）。
- **v0.2.15**：根治右空——正文段落移除独立 `max-width`（不再 72ch/56em 限宽），行宽跟随 `.wrap` 容器满排，消除段落右缘与标题/表格不齐的右侧空白。
- **v0.2.14**：正文段落试以 56em≈896px 限宽（已被 v0.2.15 取代）。
- **v0.2.13**：可视化与交互批——① 表格 th/td 全局文字水平+垂直居中（md 表 `:---` 对齐内联样式优先可覆盖）；② 图表双栏 `.chart-split`（上下居中、≤880 单列）+ 环形图 `.donut/.donut-legend`（conic-gradient 分段 + mask 掏洞）+ 趋势折线 `.trend`（静态 SVG，坐标由生成端计算，皮肤只供样式）；③ 堆叠条补 `.stack-seg.s6` 与 `.stack-legend i.s1–s6` 图例色块（此前图例透明不可见）；④ 立场卡组 `.scards/.scard/.ssteps/.scard-note`（卡头两行/节点等高/引用区弹性/防呆齐底的跨卡对齐）；⑤ 表格内高赞条目 `.quote-list/.quote-item`；⑥ 视点综合信息条 `.viewpoint-meta/.viewpoint-snap`（h3:has 增强不动自动编号）；⑦ 长表滚动容器 `.table-scroll`（固定高度 min(58vh,540px)+吸顶表头，打印展开全文）；⑧ 引用悬停气泡 `.refpop`（装配端 refpop JS 读索引行自动浮出，无 JS 回退跳转）；⑨ 折叠渐缓弹出改 WAAPI 命令式动画（皮肤不再写 animation 规则——CSS 动画在 details 隐藏子树不重放，装配端 `_fold_js()` 每次 open 现开新动画）；⑩ 左侧目录栏滚动高亮坐标修复（rect.top 与阈值同用视口坐标）。配套：build_report `_bnav_html()` spy 修复、新增 `_fold_js()/_refpop_js()` 注入、来源索引表自动套 `.table-scroll`；模板注释/导出细则同步。
- **v0.2.12**：目录栏改版——右侧毛玻璃抽屉（`.bnav-trigger`/`.bnav-overlay`/开关 JS）废弃，改为**左侧常驻目录栏**：固定左缘 240px、常展开、透明背景、右缘 1px 细线；`body:has(.bnav)` 为内容让位（.wrap 在剩余宽度内居中）；条目删 `data-full` 悬停 Pill（改原生 title）、去 `.x` 关闭钮与遮罩；JS 只留滚动高亮 `.bnav-item.active`（当前章指示）；≤880px 与打印自动隐藏。配套：build_report `_bnav_html()` 重写、模板注释/CONTRACT/导出细则同步。触发：目录常驻左侧 + 当前章指示，便于长报告章节定位。
- **v0.2.11**：暖调收敛（经真实报告验收）——v0.2.9/0.2.10 新增组件的大面积多色相（绿/蓝/紫）收敛回旧版纸暖观感，**组件结构不变、仅色值调整**：时间轴 `.tl-dot/.tl-tag.tg-*`（official=砖红 / media=墨 / view=金 / bg=灰 / quiet=灰 / check=橄榄）、来源分级 `.source-A` 底改暖纸 `#e9e0cc`、覆盖矩阵 `cell-ok` 改暖纸底砖红字、裁决徽章 `.v.v-true/.vpill.v-true`（真实=砖红系 `#f6e9dd/#a44a2e`，部分真实=金不变）、堆叠段 `.stack-seg.s1-5`（红/金/橄榄/墨/灰）。行内 `.mark-*` 证据徽章语义色（▲绿/●蓝/△金）保留。触发：多色组件致整页主色偏离旧版纸暖砖红观感时。
- **v0.2.10**：生成器批写回模板——补第四章分布胶囊 `.vpills/.vpill` 与 `table.conflict` 列宽；模板注释固化：ch4 裁决压缩视图（分工说明 + 分布胶囊 + 分组速览 + 冲突表兼容两种 schema）、生成器参数 `--abstract`/`--timeline-milestones`/`--nav-drawer`、来源分级默认 C、md 标题前缀自动剥离、单对 CONTENT 装配。配套 `build_report.py` 已实现同批功能（theads、断言卡、压缩核查、摘要、悬浮目录注入、时间轴、来源分级）。
- **v0.2.9**：组件批——右侧毛玻璃悬浮目录抽屉（`.bnav-*`，需内联 JS，两段式防掉帧：滑入用不透明面板、`body.bnav-settled` 后启用 backdrop-filter；滚动高亮/省略+悬停 Pill 全文；打印与 reduced-motion 降级）；竖式里程碑时间轴（`.tl` 系列，ch2）；裁决徽章族（`.v`/`.v-true`/`.v-part`，与 `.mark-*` 证据徽章分族）；断言卡（`.claims/.claim/.claim-title` + `details.fold`，ch3；`short_title` 为模型摘要、非标题元素不进编号）；事件摘要块（`.abstract`，ch1，模型生成两段式）。打印规则扩展至新组件。
- **v0.2.8**：标题编号方案——章标题 h2 文本保留手写中文序数（一、二、…）不变；章内小节标题由 CSS 计数器自动渲染：h3 →「1.1」、h4 →「1.1.1」、h5 →「1.1.1.1」（首位=章序，按 h2 出现顺序）。只作用于 `<main>` 直接子级标题（.meta-box/.card/details 内标题不编号）；小节标题文本勿再手写序号前缀。
- **v0.2.7**：标题层级增强——h4 由 16px/灰 上调为 18px/石板墨，h5 补正式样式（15px 灰），与正文（16px）拉开区分；原因：经百宝袋极简 md 渲染注入的小节标题（md 二级→h4）此前与正文几乎同号同色、不突出（见舆论观点综合章）。同时 h2–h5 统一纳入衬线族声明。
- **v0.2.6**：P2 组件——事件角色小词典（`.actors-side` + `.card .quotes`，`--actors` 输入、第一章折叠卡组、按 side 分组）与说法×证实双轨时间轴（`table.dualtrack`，`--track` 输入、第二章表、徽章复用 `.mark-*`）；无输入不渲染。
- **v0.2.5**：P1 可视化层——立场占比条形 `.bars`、来源/分级小结 `.stat-line`、覆盖缺口三态矩阵 `table.matrix`（`cell-ok/miss/gap`）、焦点转移堆叠 `.stacks`（`.stack-seg.s1-5` + `.stack-legend`）、口径冲突对照 `table.conflict`；均纯 CSS、打印友好、数据缺失自动不渲染。
- **v0.2.4**：新增折叠组件 `details.fold`（长原文/长列表收纳，零 JS；`@media print` 自动展开内容，避免打印丢信息）。
- **v0.2.3**：新增章节导读样式 `.chapter-intro`（build_report 程序化导航句，`--intros` 可覆盖）。
- **v0.2.2**：新增报告仪表盘 KPI 组件（`.kpis`/`.kpi-card` 及 `.kpi-num/lab/sub`，百宝袋结论速览自动输出）；打印防拆分覆盖 `.kpi-card`；模板注释同步补充用法。v0.2.1 为细节修正与行尾统一。
- **v0.2.0**：Editorial 定稿。中文适配（字体栈/行高 1.72/取消负字距）、报告必需组件（表格/引用锚点/确定性标记/时间线/立场行）、已确认修正（章节序号 19px、stance-row 布局）、打印分页、响应式、纯离线（零外部请求）。
- **演进预留（多模板空间）**：当前为单模板；模板接口（`<!-- TITLE -->` + `CONTENT_START/END` 标记 + 类词汇表）风格无关，天然可容纳多套风格。未来若需第二套风格（如深色变体、紧凑版），将 report-theme 升级为注册表（`templates/<style-id>/` + `index.yaml`，仿 Raleighawesome），`sync_theme.py` 支持按 skill 分发选定风格或全部风格 + 默认指针，百宝袋 `build_report.py` 加 `--style` 参数。契约层不变，生成代码几乎不动。
- POC 演示：`_poc/editorial_laoren.html`（E:\AI\揽风云\样式验证_Editorial_老人进店事件.html）展示组件用法。
