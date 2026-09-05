# 🥔 Potato Skills

> 土豆先生的 AI 技能工具箱
> 仓库分为 `claude-code-skill/`（Claude Code 技能，共 11 个）与 `deepseek-harness-skill/`（DeepSeek Harness 技能，共 14 个，含同名技能的 dsh 适配版）。

---

## Claude Code 技能速查（claude-code-skill/skills/）

| 技能 | 类型 | 说明 |
|------|------|------|
| 参国是 | 政策分析 | 政策信息追踪、汇编、简报与宏观视角解析 |
| 察人事 | 事实核查 | 轻量级碎片化事实核查——「某人是否说过某话」快速验证 |
| 简浮辞 | 文本处理 | 中文文本语义压缩——剥离虚词，保留实义骨架 |
| 见真章 | 深度分析 | 新闻评论与政策分析——批判性·结构性视角 |
| 揽风云 | 信息汇编 | 多源信息搜索、整理、结构化报告 |
| 理脉络 | 可视化 | 自然语言 → Obsidian Mermaid.js 图表 |
| 纳百川 | 工具 | 从 agentskill.sh / GitHub 安装技能 |
| 纳灵光 | 知识管理 | 碎片化想法提炼、结构化，融入知识体系 |
| 破虚妄 | 事实核查 | 日常工作用事实核查——结构化核查报告 |
| 网络工具 | 网络 | 网络连通性检测与带宽测速，含 monitor.js 脚本 |
| 知乎 | 内容采集 | 爬取知乎问题/回答，输出 Markdown |

---

## DeepSeek Harness 技能速查（deepseek-harness-skill/skills/）

> 以下 11 个为本仓库 Claude Code 同名技能的 **dsh 适配版**（内容按 DeepSeek Harness 环境调整，功能同上表，不再重复介绍）：
> `can-guo-shi`（参国是）、`cha-ren-shi`（察人事）、`jian-fu-ci`（简浮辞）、`jian-zhen-zhang`（见真章）、`lan-feng-yun`（揽风云）、`li-mai-luo`（理脉络）、`na-bai-chuan`（纳百川）、`na-ling-guang`（纳灵光）、`po-xu-wang`（破虚妄）、`network-tools`（网络工具）、`zhihu`（知乎）

新增技能如下：

| 技能（目录名） | 类型 | 说明 |
|------|------|------|
| 百宝袋（bai-bao-dai） | 信息聚合 | 热点事件多方信息汇总与视点综合分析——微博/小红书/知乎三平台采集流水线 |
| 问候语（greeting） | 小工具 | 根据当前时间自动生成对应时段的问候语 |
| 微信读书（Tencent-WeChatReading） | 阅读助手 | 搜索书籍、管理书架、查看笔记划线、浏览书评、阅读统计、发现推荐好书 |

---

## 安装

### 方式一：一键安装全部（推荐）

```bash
git clone https://github.com/potat0-Zheng/potato-skills.git /tmp/potato-skills && \
cp -r /tmp/potato-skills/claude-code-skill/skills/* ~/.claude/skills/ && \
rm -rf /tmp/potato-skills
```

### 方式二：安装单个技能

```bash
# 以「见真章」为例，替换 {技能名} 即可
git clone --depth 1 --filter=blob:none --sparse \
  https://github.com/potat0-Zheng/potato-skills.git /tmp/potato-skill && \
cd /tmp/potato-skill && \
git sparse-checkout set claude-code-skill/skills/见真章 && \
cp -r claude-code-skill/skills/见真章 ~/.claude/skills/ && \
cd ~ && rm -rf /tmp/potato-skill
```

### 方式三：直接下载单文件

适合不含脚本/模板的轻量技能（如察人事、理脉络）。含资源文件的技能（如见真章、网络工具）建议用方式二。

```bash
# 替换 {技能名} 和 {文件名}（skill.md 或 SKILL.md）
curl -o ~/.claude/skills/{技能名}/{文件名} --create-dirs \
  https://raw.githubusercontent.com/potat0-Zheng/potato-skills/master/claude-code-skill/skills/{技能名}/{文件名}
```

---

## 目录结构

```
potato-skills/
├── README.md
├── claude-code-skill/          ← Claude Code 技能
│   └── skills/
│       ├── 参国是/
│       ├── 察人事/
│       ├── 简浮辞/
│       ├── 见真章/        ← 资源最丰富（含模板、范例、参考框架）
│       ├── 揽风云/
│       ├── 理脉络/
│       ├── 纳百川/
│       ├── 纳灵光/
│       ├── 破虚妄/
│       ├── 网络工具/      ← 含 monitor.js 连通性检测脚本
│       └── 知乎/
└── deepseek-harness-skill/     ← DeepSeek Harness 技能
    └── skills/
        ├── can-guo-shi/        ← 参国是的 dsh 适配版
        ├── cha-ren-shi/        ← 察人事的 dsh 适配版
        ├── jian-fu-ci/         ← 简浮辞的 dsh 适配版
        ├── jian-zhen-zhang/    ← 见真章的 dsh 适配版
        ├── lan-feng-yun/       ← 揽风云的 dsh 适配版
        ├── li-mai-luo/         ← 理脉络的 dsh 适配版
        ├── na-bai-chuan/       ← 纳百川的 dsh 适配版
        ├── na-ling-guang/      ← 纳灵光的 dsh 适配版
        ├── po-xu-wang/         ← 破虚妄的 dsh 适配版
        ├── network-tools/      ← 网络工具的 dsh 适配版
        ├── zhihu/              ← 知乎的 dsh 适配版
        ├── bai-bao-dai/        ← 百宝袋（新增）
        ├── greeting/           ← 问候语（新增）
        └── Tencent-WeChatReading/  ← 微信读书（新增）
```

---

<p align="center"><i>想法比代码更重要。</i></p>
