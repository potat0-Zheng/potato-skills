---
name: bai-bao-dai
description: 百宝袋——热点事件多方信息汇总与视点综合分析工具。整合微博/小红书/知乎三平台爬虫，执行"采集→事实还原→舆论把握→综合报告"四阶段流水线。当用户请求对某一热点事件进行多平台信息汇总、多方视角还原真相、舆论走向分析时使用；也可单独调用内置爬虫（如仅爬取知乎某问题/回答）。通过 /百宝袋 调用。
---

# 百宝袋（bai-bao-dai）

## 目标

对指定热点事件，完成两件事：
1. **多方视角还原事件真相**（事实层）——多源交叉、证据分级、冲突消解、对立观点显式覆盖
2. **多方评论把握舆论走向**（舆论层）——按立场聚类评论、识别少数派与对立观点、判断焦点转移

不过度依赖复杂分析工具，但通过核查方法与立场聚类保证深度。最终产出结构化 HTML 综合报告。

## 目录结构

```
{SKILL_DIR}/
├── config.json            # 平台开关、默认参数、限速、探测项（立场词典按事件定制，不存默认）
├── setup.py               # 依赖与凭证检查
├── zhihu/                 # 知乎爬虫（内嵌，自原 zhihu skill 迁移）
│   ├── crawl.py           # 单回答/全问题（输出回答链接+发布时间）
│   └── cookie.txt         # 知乎 cookie（单一事实源）
├── scripts/
│   ├── collect.py         # 统一采集入口（外部平台驱动 MediaCrawler，无凭证诚实降级）
│   ├── normalize.py       # 多源归一化 JSONL（知乎 md + MediaCrawler 微博/小红书 schema）
│   ├── check.py           # 事实核查辅助（调 china_sources 初查；po-xu-wang 未装时自动降级为 LLM 多源核查）
│   ├── opinion.py         # 舆论立场聚类（单标签，命中关键词最多者胜；立场词典按事件定制，--stances 传入）
│   ├── build_report.py    # 综合报告生成（9 章结构）
│   └── selftest.py        # 工具自检（离线层 + 在线最小尝试，见「工具自检」节）
├── templates/report_template.html
├── tests/test_pipeline.py # 离线流水线测试（python -m unittest discover -s tests）
├── external/              # 外部爬虫（MediaCrawler / weiboSpider / Spider_XHS）
└── data/{event_id}/       # 每次事件一个目录；中性示例见 data/example（含 stances.json）
```

## 四阶段工作流

### 阶段 1：采集（collect.py）

1. 解析用户输入：提取事件关键词 + 事件 ID（时间戳或短哈希）
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

### 阶段 2：归一化 + 事实还原（normalize.py + check.py）

1. 归一化：
   ```bash
   python "{SKILL_DIR}/scripts/normalize.py" --in "data/{event_id}/raw" --out "data/{event_id}/normalized/data.jsonl" --event "关键词"
   ```
   （自动识别知乎 md 与 MediaCrawler 微博/小红书 jsonl；知乎回答含发布时间与回答链接）
2. 事实还原（核心方法论，沿用破虚妄思路）：
   - 从采集内容 + WebSearch 提取关键事实断言
   - 调 check.py 标准化：
     ```bash
     python "{SKILL_DIR}/scripts/check.py" --event "关键词" --claims "断言1,断言2,..." --out "data/{event_id}/facts.json"
     ```
   - **由 LLM 结合多源证据逐条裁决**：真实 / 部分真实 / 失实 / 证据不足，输出 facts.md（供 --viewpoint 注入）
   - **降级说明**：本技能可独立使用。若本机未安装 po-xu-wang skill，check.py 自动输出"待核查"（china_sources=False），由 LLM 直接用 WebSearch 完成多源核查——不影响流水线其余环节

### 阶段 3：舆论把握（opinion.py）

1. **构造本事件的立场词典（必需）**：立场不能依赖全局默认（config.json 的 stance_keywords 恒为空）。依据采集内容与首轮搜索看到的观点分歧，构造 `{立场: [关键词,...]}` 写入 `data/{event_id}/stances.json`（中性示例见 `data/example/stances.json`）：
   ```json
   {
     "支持方A": ["关键词1", "关键词2"],
     "反对方B": ["关键词3", "关键词4"]
   }
   ```
2. 聚类（单标签：命中关键词最多者胜，平票按词典插入顺序，无命中归"无关/其他"）：
   ```bash
   python "{SKILL_DIR}/scripts/opinion.py" --in "data/{event_id}/normalized/data.jsonl" --out "data/{event_id}/opinion.json" --event "关键词" --stances "data/{event_id}/stances.json"
   ```
3. 结合 opinion.md 由 LLM 做**视点综合**：提炼各立场论证结构、标注少数派与对立观点、判断焦点转移（如从"赔偿金额"转向"调解制度"），写入 viewpoint.md

### 阶段 4：综合报告（build_report.py）

```bash
python "{SKILL_DIR}/scripts/build_report.py" --event "关键词" \
    --facts "data/{event_id}/facts.json" --opinion "data/{event_id}/opinion.json" \
    --normalized "data/{event_id}/normalized/data.jsonl" \
    --viewpoint "data/{event_id}/viewpoint.md" \
    --coverage "data/{event_id}/coverage.md" \
    --out "data/{event_id}/report.html"
```

报告章节（已实现）：结论速览 → 时间线 → 核心事实汇编 → 事实核查结果 → 舆论观点综合（含 LLM 视点分析）→ 来源索引 → 覆盖完整性声明 → 方法论 → 时间戳。`--viewpoint/--coverage` 为 LLM 深度内容注入点（markdown 自动转 HTML）。

## 独立工具用法（单独取一个工具）

### 仅爬知乎（单回答 / 全问题）
```bash
# 全问题（可 --limit N 限制条数）
python "{SKILL_DIR}/zhihu/crawl.py" "<知乎问题URL>" "<输出.md>" --limit 5
# 单回答（URL 含 /answer/）
python "{SKILL_DIR}/zhihu/crawl.py" "<含/answer/的URL>" "<输出.md>"
```

### 仅做舆论聚类
```bash
python "{SKILL_DIR}/scripts/opinion.py" --in "<normalized.jsonl>" --out "<输出.json>"
```

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
- 离线层：用内置微型夹具跑通 normalize → opinion → build_report，验证本地工具链；另报 check.py 可选依赖 china_sources（po-xu-wang 未装不影响主流程）。
- 在线层（微博/小红书）：各驱动 MediaCrawler 做 1 条最小关键词搜索，判定"可用 / 登录态失效 / 风控 / 未部署"。
- 在线层（知乎）：cookie 文件存在性；若 config.json 的 `probe.zhihu_question` 配置了探测问题，再发 1 次 API 请求验证 cookie 真伪（HTTP 非 200 = 过期）。未配置时仅静态检查，网络验证随真实任务发生。

**失败处理铁律（任务开始前询问用户，不得擅自决定）**：
1. 汇总失败工具清单（selftest 已逐项输出）；
2. 对每个失败工具向用户提问二选一：
   - **跳过该工具** → 任务继续，报告中"覆盖完整性声明"须注明该平台未采集；
   - **更新凭证** → 知乎：用户更新 `zhihu/cookie.txt`；微博/小红书：用户执行 `collect.py --login weibo,xiaohongshu` 扫码或提供新 cookie，然后重跑 selftest 确认；
3. 用户做出选择前不开始正式采集。

setup.py（静态体检：依赖/凭证是否存在）与 selftest.py（最小真实尝试：工具是否真能跑通）互为补充：环境变动或凭证失效后先跑 selftest。

## 强制质量检查项（交付前核对）

1. **对立/少数观点必须显式覆盖**并标注信源稀缺度（防止主流叙事单边化）
2. **舆论按立场聚类**（非简单正负情感），识别 <10% 的少数派
3. **覆盖完整性声明**：明确列出未采到的立场/平台/信源（如"家属方未回应""小红书未部署"）
4. **事实冲突必须消解**：口径/时间/立场差异分析 + 给出更可信一方
5. **确定性标记**：▲官方确认 / ●多源一致 / △单源存疑，不确定信息不得伪装成事实
6. **自检留痕**：若任务前执行过工具自检，报告中注明结果与用户选择（跳过某工具 / 已更新凭证）

## 依赖与凭证

- Python 3.10+、requests；MediaCrawler 另需其 `.venv`（已部署，含 playwright 浏览器）
- 知乎 cookie 存于 `{SKILL_DIR}/zhihu/cookie.txt`；失效时提示用户更新（浏览器 F12 → api/v4 请求 Cookie）
- 微博/小红书凭证（三选一，collect.py 自动检测）：
  1. **`--login weibo,xiaohongshu` 一次性扫码登录**（推荐，无需复制/传输 cookie，会话存 browser_data）
  2. `--wb-cookie / --xhs-cookie` 参数，或环境变量 `BBD_WB_COOKIE / BBD_XHS_COOKIE`
  3. 小红书可回退 MediaCrawler 内置 cookie（`config/base_config.py` 的 COOKIES，可能过期）
- 凭证缺失时 collect.py 明确标注"未采集"，**不静默假成功**；setup.py 一键静态体检；任务开始前可按需运行 selftest.py 做工具可用性最小尝试（见「工具自检」节）

## 注意事项

- 仅供学习研究，禁止商业用途与大规模爬取；控制频率（config.json 限速为参考值，脚本内置 sleep）
- 采集仅限公开信息，匿名化处理（本机 MediaCrawler 为教学版，用户信息已脱敏）；遵守各平台条款
- 搜索引擎索引有延迟，报告须标注信息截止时间（build_report 自动生成）
