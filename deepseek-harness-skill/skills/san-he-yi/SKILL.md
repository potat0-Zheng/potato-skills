---
name: san-he-yi
description: 三合一——三技能联合报告入口。当用户要求对热点事件产出"多方信息汇总 + 事实核查 + 舆论立场"综合报告（即揽风云×破虚妄×百宝袋联合任务）时使用：按工作流关系依序编排三技能，衔接细则遵行 ~/.dsh/report-theme/CONTRACT-joint.md。通过 /三合一 调用。
---

# 三合一 —— 三技能联合报告入口（薄入口）

本入口**只说明三个 skill 的工作流关系**；衔接细则一律以 `~/.dsh/report-theme/CONTRACT-joint.md` 为准，
不重述任何技能内部内容。每阶段先用 skill 工具加载对应技能，并完整遵循其指令。

## 工作流关系

```
① 百宝袋（bai-bao-dai）—— 采集与计量
   知乎 / 微博 / 小红书真实采集 → 归一化语料 → opinion.py 立场聚类
   输出：raw/ + normalized/（②③ 的原料）；opinion.json（声量/占比的唯一合法来源）
        ↓
② 揽风云（lan-feng-yun）—— 信息汇编
   基于 ① 的语料 + 检索补充：聚类主题、转载簇去重、冲突消解、来源索引
   输出：时间线 + 主题正文 + 素材层（含对立/少数立场标注）+ actors.json（主体档案，可选；交接见 CONTRACT-joint §8）
        ↓
③ 破虚妄（po-xu-wang）—— 事实核查
   对 ② 的正文断言（含引文）登记成册 → 逐条裁决（真实/部分真实/失实/证据不足）
   引文补取证链、背景坐标做时效核验；独立取证，不引用 ② 的转述当证据
   输出：F 表（裁决层）
        ↓
④ 汇合与收尾（百宝袋 build_report.py）
   ①计量 + ②素材 + ③裁决 → report.html → 门禁 check_report.py --joint --strict --data（0 error）
   → 数据对账（本入口独有职责，规则见 CONTRACT-joint §10）
```

## 交接规则（一句话版）

- 阶段间只通过 `data/{event_id}/` 下的工件文件交接，前一阶段工件缺失即停，不跨过、不互相改写对方输出；
  ② 只汇编不判真伪，③ 只裁决不重复汇编，④ 只装配不代写 ②③ 内容（主体档案同此：② 产出 `actors.json`，
  ③ 只提供裁决编号，④ 渲染时从 `facts.json` 登记册补齐裁决词，见 §8）；
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
