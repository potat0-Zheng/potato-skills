# 参国是 技能维护日志

## 用途

围绕政策议题进行多源搜索、整理、汇编、简报和宏观视角解读。填补"通用信息汇编"（揽风云）与"意识形态批判分析"（见真章）之间的中观分析层。

## 维护记录

- **2026-05-18** — 初始化技能框架。确立三种运行模式（compile/brief/insight），定义六大宏观视角方法论，内置政策领域知识（效力层级 L1-L7、发布节点与搜索窗口、央地政策联动追踪），支持混合模式（--with-insight）。继承揽风云的搜索两轮制和引用编号体系。
- **2026-05-18** — 新增"扩展工具（规划中）"章节，预留 policy-registry（跨会话政策注册表）和 docnumber-parser（文号批量解析器）设计，触发条件为对应场景月均 ≥ 2 次。
- **2026-09-04** — 新增官方库直查层（国务院政策文件库）：
  - `references/policy-sources.md` — 接口契约（参数/实测响应结构/字段映射/陷阱），2026-09-04 真实调用验证通过（total=107、四分类 gongwen/bumenfile/otherfile/gongbao；发布机构实为 `puborg`、链接实为 `url`、顶层 totalCount 恒为 0 需取分类之和——均按实测修正）
  - `scripts/query_gov_lib.py` — 零依赖查询脚本（urllib/json/re），参数落盘 JSON，失败诚实降级输出 `fallback: use WebSearch`
  - `tests/` — 离线单元测试 12 项（字段映射/清洗/降级/成功路径），`python -m unittest discover -s tests` 全绿
  - SKILL.md 阶段 2 改为"官方库直查优先 → WebSearch 兜底"，▲ 使用边界与"检索不到 ≠ 不存在"写入注意事项
- **2026-09-04（追加）** — flk 法规库接入（国家法律法规数据库）：
  - `scripts/query_flk.py` — 检索 + `--detail` 单条详情；`--sxx 3` 过滤现行有效（sxx 语义经实测样本推定：3=现行有效确定，2=已失效或已被修改，1=已废止）
  - `tests/test_query_flk.py` + fixture — 8 项离线测试全绿（共 20 项：gov 12 + flk 8）
  - `references/policy-sources.md` 新增源二契约；真实调用复核通过：检索"行政处罚法" `--sxx 3` 仅返回现行有效版、detail 返回 ossFile 全文路径
  - SKILL.md 阶段 2/搜索策略/注意事项补充法律类直查与时效字段用法（法律位阶走 flk、文件类走国务院库的两库分工）
- **2026-09-06** — 需求驱动重构（任务声明 + 脉络追踪三件套）：
  - 依据需求端分析（`workflow-design/参国是调整-需求端分析.md`）与实施方案（`workflow-design/参国是调整-实施方案.md`），SKILL.md 从"产物模式导向"升级为"需求任务声明导向"
  - 新增「任务声明」节：5 问入口 + `--need` 映射（scan/timeline/compile/brief/insight/track/profile/compare/verify）；参数表新增 `--need/--angles/--persist/--no-persist`
  - policy-registry 从"规划中"落地：schema（文号去重键 + 三元组兜底、时效状态、关联政策）、`scripts/policy_registry.py`（纯标准库 add/list/stats，原子写 + coverage_log 回写），并已用临时目录实测去重/过滤/统计通过
  - 新增「政策脉络追踪」章节（registry · profile · compare）；输出规范补 模式四档案 / 模式五对比 / verify 核验卡；insight 加视角选择引导（--angles，利益结构为涉分配政策默认必含视角）
  - HTML 专属 CSS 增量补 `.eff-badge`（时效徽章）/`.profile-meta`/`.life-bar`/`.rel-table`，图例补时效徽章说明；选择指南补 track/profile/compare/verify 示例
