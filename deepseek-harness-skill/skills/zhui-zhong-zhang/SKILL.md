---
name: zhui-zhong-zhang
description: 缀众章——热点事件「综合报告」的多技能编排入口（把多方的产物连缀成一份）。当用户要求对某一热点事件产出**一份完整的综合报告**（信息汇总 + 事实核查 + 立场结构，四技能联合：揽风云×破虚妄×百宝袋×判风潮）时使用；**只要单个环节就不要走本入口**（只要立场分析 → 判风潮；只要事实核查 → 破虚妄；只要信息汇编 → 揽风云），直接用对应技能更快。按工作流关系依序编排，衔接细则遵行 ~/.dsh/report-theme/CONTRACT-joint.md。通过 /缀众章 调用。
---

# 缀众章 —— 热点事件综合报告的多技能编排入口（薄入口）

本入口**只说明各 skill 的工作流关系**；衔接细则一律以 `~/.dsh/report-theme/CONTRACT-joint.md` 为准，
不重述任何技能内部内容。每阶段先用 skill 工具加载对应技能，并完整遵循其指令。

**关于本名的两次修正**（都写在这里，免得后来人以为对不上号）：
- **名字**：本入口原名「三合一」/`san-he-yi`，2026-09 改为「缀众章」/`zhui-zhong-zhang`。
  「三合一」是个**数量描述**，不是意象——判风潮加进来时它就已经过期一次了（家族里其余名字
  揽风云 / 破虚妄 / 判风潮 都描述「对某对象做了什么」，本名归队）。**旧称 `/三合一` 不再可用**。
- **技能数**：原先写「三技能」，是因为立场分析当时还挂在百宝袋内。它已独立为 `pan-feng-chao`（判风潮），
  联合线仍是四段，但**判风潮只通过两个触点介入**——所以标题与正文一律**不写技能计数**，见下图 ★。

## 工作流关系

```
① 百宝袋（bai-bao-dai）—— 采集与语料治理
   知乎 / 微博 / 小红书真实采集 → 归一化（normalize）→ 语料治理（relevance_gate）
   ★ 至此「语料冻结」：窗口定死后不得再改语料集合
   输出：raw/ + normalized/data.relevant.jsonl
        ↓
★ 触点 A：判风潮（pan-feng-chao）· 立场体系 —— 只出体系，不出任何数字
   产出 stances.json（+ 可选 opinion_rules.json）与判读标准 codebook.md
   → ②③ 由此获得「立场依据」，但此时**还没有任何占比数字**
        ↓
② 揽风云（lan-feng-yun）—— 信息汇编
   基于 ① 的语料 + 检索补充：聚类主题、转载簇去重、冲突消解、来源索引
   输出：时间线 + 主题正文 + 素材层（含对立/少数立场标注）+ stance_cards.json / viewpoint.md
        + actors.json（主体档案，可选；交接见 CONTRACT-joint §8）
        ↓
③ 破虚妄（po-xu-wang）—— 事实核查
   对 ② 的正文断言（含引文）登记成册 → 逐条裁决（真实/部分真实/失实/证据不足）
   引文补取证链、背景坐标做时效核验；独立取证，不引用 ② 的转述当证据
   输出：facts.json（断言登记册）
        ↓
★ 触点 B：判风潮（pan-feng-chao）· 舆论分析 —— 一次出齐
   产出 opinion.json（含回写的 spotcheck 字段）+ spotcheck_{stance,rule,unclassified}.json/.md/.html
   → **定量结果不得早于 ②③ 发布**（避免"先定调、后补证"）
        ↓
④ 汇合与收尾（百宝袋 build_report.py）
   ①计量 + ②素材 + ③裁决 + ★判风潮数字 → report.html
   → 门禁 check_report.py --joint --strict --data（0 error）
   → 数据对账（本入口独有职责，规则见 CONTRACT-joint §10）
```

## 交接规则（一句话版）

- 阶段间只通过 `data/{event_id}/` 下的工件文件交接，前一阶段工件缺失即停，不跨过、不互相改写对方输出；
  ② 只汇编不判真伪，③ 只裁决不重复汇编，④ 只装配不代写 ②③ 内容（主体档案同此：② 产出 `actors.json`，
  ③ 只提供裁决编号，④ 渲染时从 `facts.json` 登记册补齐裁决词，见 §8）；
- **★ 两个触点的顺序是硬约束**（CONTRACT-joint §1）：**触点 A 在 ②③ 之前、触点 B 在 ②③ 之后**；
  语料冻结前不得启动立场聚类（窗口一变，抽检的固定 seed 分层样本整批更换、人工判读成果作废）；
  重跑聚类必须重做抽检三步（`--make` → 人工判读 → `--apply`）；
- **立场数字只来自判风潮的 `opinion.json`**，**不来自 `facts.json.opinion_analysis`**（后者是 ③ 的
  「观点辨析与推理说明」，属论证结构分析，不是占比）；两者出现在同一份报告里时不得互相替代或混算；
- **④ 是唯一能看到全部工件的阶段，因此数据对账只能在 ④ 执行**：门禁 0 error ≠ 数据自洽，
  交付前必须按 §10 对账一次（规则与判据见该节，本入口不重述）；
- 任何跳过/降级必须经用户确认并写入合规账本 `data/{event_id}/compliance.json`（细则见 §1/§4）。

## 装配与门禁命令（必须走流水线）

```bash
python "{SKILL_DIR}/../bai-bao-dai/scripts/build_report.py" --joint \
    --compliance "data/{event_id}/compliance.json" --out "data/{event_id}/report.html" …
python "~/.dsh/report-theme/check_report.py" --joint --strict \
    --data "data/{event_id}" "data/{event_id}/report.html"
```

`--data` 是数据对账的开关：不传就只能查报告自身形态（现有行为），查不出数字矛盾与断言与语料不符。
门禁 0 error 之后，仍须按 §10 完成对账；对账不通过的项回退到对应阶段修复，不得只改措辞绕过。
