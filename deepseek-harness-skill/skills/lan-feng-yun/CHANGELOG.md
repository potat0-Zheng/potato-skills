# 揽风云 技能变更日志

## v5 (2026-08-18) — DSH 适配 + 渐进式披露瘦身

**背景**：技能从 Claude Code 迁移至 DSH 后，存在路径硬编码、bs4 依赖缺失、L1（WebFetch 域名验证）假设残留等问题；且 286 行 SKILL.md 中约 44% 为条件触发的外媒清单，每次调用全量注入挤占上下文。

**改动**：

1. **frontmatter**：新增 `model: deepseek-v4-pro`（对齐同族 po-xu-wang）。
2. **渐进式披露瘦身**：SKILL.md 286 → ~135 行，将条件触发内容迁出为按需加载的 references：
   - `references/foreign-media.md` — 原「外媒信源清单与获取策略」整章（逐字迁入，加 DSH 语境注记）
   - `references/source-fallback.md` — 原「信源获取失败应对」，L1 由「Claude Code 域名验证」改写为「搜索零命中」应对
   - `references/html-export.md` — 原「导出→HTML」细节 + 图像选取标准
3. **脚本工程化**：脚本平移到 `scripts/`；`fetch_page.py` 重写为纯标准库（`html.parser` + urllib 回退，去除 bs4 依赖）。
4. **路径修复**：全部改为 `{SKILL_DIR}` 相对引用；知乎脚本 `../知乎` → `../zhihu`；Word 导出 `..\破虚妄` → `..\po-xu-wang`。
5. **执行环境契约**：新增 DSH/Windows 契约（UTF-8 写文件、`$env:PYTHONIOENCODING`、JSON 参数落盘）。
6. **资产**：`report_template.html` 平移至 `assets/`。

**方法论零改动**：compile/timeline 双模式、5 阶段工作流、引用编号体系、来源分级 A/B/C、确定性标记 ▲/●/△、7 章输出规范、三条「禁止」均逐字保留。

---

## 历史记录（摘自 README）

- **2026-06-10 (v4)** — 集成知乎 API 管道（L0 层 + crawl.py）
- **2026-06-10 (v3)** — 图片管线重构（collect_images.py + report_template.html）
- **2026-06-10 (v2)** — SKILL.md 精简；新增 embed_image.py / fetch_page.py
- **2026-06-10** — 新增图像资料处理章节 + L1 应对策略
- **2026-05-15** — 初始化技能框架
