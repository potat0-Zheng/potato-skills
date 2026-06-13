# 🥔 Potato Skills

> 土豆先生的 Claude Code 中文技能工具箱
> 共 10 个技能，覆盖政策分析、事实核查、信息汇编、文本处理、知识管理等领域。

---

## 技能速查

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
| 知乎 | 内容采集 | 爬取知乎问题/回答，输出 Markdown |

---

## 安装

### 方式一：一键安装全部（推荐）

```bash
git clone https://github.com/potat0-Zheng/potato-skills.git /tmp/potato-skills && \
cp -r /tmp/potato-skills/skills/* ~/.claude/skills/ && \
rm -rf /tmp/potato-skills
```

### 方式二：安装单个技能

```bash
# 以「见真章」为例，替换 {技能名} 即可
git clone --depth 1 --filter=blob:none --sparse \
  https://github.com/potat0-Zheng/potato-skills.git /tmp/potato-skill && \
cd /tmp/potato-skill && \
git sparse-checkout set skills/见真章 && \
cp -r skills/见真章 ~/.claude/skills/ && \
cd ~ && rm -rf /tmp/potato-skill
```

### 方式三：直接下载单文件

适合不含脚本/模板的轻量技能（如察人事、理脉络）。含资源文件的技能（如见真章、揽风云）建议用方式二。

```bash
# 替换 {技能名} 和 {文件名}（skill.md 或 SKILL.md）
curl -o ~/.claude/skills/{技能名}/{文件名} --create-dirs \
  https://raw.githubusercontent.com/potat0-Zheng/potato-skills/master/skills/{技能名}/{文件名}
```

---

## 目录结构

```
potato-skills/
├── README.md
└── skills/
    ├── 参国是/
    ├── 察人事/
    ├── 简浮辞/
    ├── 见真章/        ← 资源最丰富（含模板、范例、参考框架）
    ├── 揽风云/
    ├── 理脉络/
    ├── 纳百川/
    ├── 纳灵光/
    ├── 破虚妄/
    └── 知乎/
```

---

<p align="center"><i>想法比代码更重要。</i></p>
