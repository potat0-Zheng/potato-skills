# HTML 导出与图像嵌入

> 本文档由原 SKILL.md「导出 → HTML」章节迁出，路径已修复为 `{SKILL_DIR}\scripts\`，并清理了遗留示例关键词。`{SKILL_DIR}` 即本技能目录。

## 导出步骤

1. 读取 `assets/report_template.html`（CSS/结构骨架），替换 `<!-- TITLE -->` 和 `<!-- CONTENT_START/END -->` 之间的内容。
2. 文件名从标题自动生成（去非法字符，截断 40 字符），UTF-8 编码。
3. 默认目录：用户指定 → 无指定则当前工作区。
4. 引用渲染：`<a href="#ref1" class="ref">[1]</a>` + 索引锚点 `<tr id="ref1">`。
5. 图像嵌入：调用 `collect_images.py` 一步完成。脚本自动提取每张图片的 alt 文本、DOM 位置（正文/侧栏）、上下文文本，按综合得分排序。

## collect_images.py 用法

```powershell
$env:PYTHONIOENCODING='utf-8'; $env:PYTHONUTF8='1'
python "{SKILL_DIR}\scripts\collect_images.py" '<JSON>' `
  --max-images 5 --output "<工作区>\images.json" `
  --keywords "主题关键词1,主题关键词2,地点,人物"
```

`<JSON>` 为文章列表，格式：`[{"url": "https://...", "label": "来源名称"}, ...]`。

`--output` 标志将含 `data_uri` 的完整结果写入文件（避免 STDOUT 截断 base64），STDOUT 仅输出轻量摘要供 AI 选图。

> 注意：若 JSON 参数经 PowerShell 传参被引号吞掉导致 `json.loads` 报错，改为先 Write JSON 到 `.json` 文件、再用 Python 脚本读文件调用 `collect()`（见 SKILL.md「执行环境契约」）。

## 图像选取标准（AI 从摘要中选择时）

1. **资格门槛**：`is_in_body=true` · alt 不含广告/萌宠/综艺/海报等无关词 · 嵌入成功 · 非图表截图
2. **上下文匹配**：`alt` 或 `surrounding_text` 中至少包含一个事件关键词（人物/地点/行为/时间）
3. **优先级排序**（取 Top 3）：信息密度 > 来源权威 > 独特性

## 依赖

`collect_images.py` 依赖 `requests`（可选，缺失则跳过下载）+ `Pillow`（可选，缺失则裸字节上限回退）；**不依赖 bs4**。`embed_image.py` 为单图下载嵌入的轻量替代（requests 优先 / urllib 回退）。环境未就绪则回退为图片链接（`<div class="img-fallback">`）。
