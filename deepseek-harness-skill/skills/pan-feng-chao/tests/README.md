# tests —— 判风潮回归测试

## 怎么跑

```bash
cd "<skill 目录>"
python -m unittest discover -s tests          # 全量（约 5 秒，62 条）
python -m unittest discover -s tests -p "test_stats.py"    # 只跑某一份
```

**依赖**：只用标准库，不联网、不装任何第三方包；不写 skill 自带的数据（每次把 `data/example/` 复制到
`tests/_tmp/` 再跑，退出时自动清理）。

**为什么不用 `tempfile.mkdtemp()` 的默认目录**：本沙箱（Windows/DSH）下它不可写，重定向 `TMP/TEMP` 也无效，
测试会以 `PermissionError` 全灭。故 `_util.workdir()` 一律先 `os.makedirs` 到 `tests/_tmp/`（见 SKILL.md §9）。

**为什么不在测试里用管道读子进程输出**：本沙箱下管道会 `EPERM`。`_util.run()` 因此把 stdout/stderr
**重定向到文件**（`tests/_tmp/<模块>/out.txt|err.txt`）或直接丢弃；需要 stdout 的用例改为在进程内调用函数
（如 `test_digest_*` 用 `contextlib.redirect_stdout`）。

## 三份测试各自守什么

| 文件 | 守的不变量 |
|---|---|
| `test_stats.py` | 数字地基：Wilson/Newcombe 区间、**多项口径的两类别差值必须比 naive 同-n 更宽**、簇稳健 deff>1、后分层加权、缺失层披露、κ/α、**变点能定位人造突变且平稳序列不报点**、同源文本区分"跨账号 vs 同账号重复"、候选簇检出词表外种子、人数占比可加总 100% |
| `test_opinion.py` | ① B 层判定验收（10 反例 / 5 正例 / 3 匿名化形态）；② 单标签与平票规则；**③ 否定与反讽只标记、不改判**；④ 工件契约字段齐全（④ 装配端按字段读）；⑤ `summary` 与主字段一致、逐日明细自洽、`stance_timeline` 不含"未识别"；⑥ 未知输出 `null` 而非 0；⑦ `coverage_of_judgeable_norule` 与是否跑规则层无关；⑧ **新功能开关关闭后既有字段逐位相同**；⑨ 两次运行字节相同；⑩ `--digest` 一屏且显著小于工件、老 schema 也不崩；⑪ 词表 diff 能识别改名 |
| `test_spotcheck.py` | ① 未分类池 ≡ `opinion.residual.count`；② 机构帖不进任何抽检池；③ 池不同源**默认硬失败（exit 2）**、显式允许才降级；④ core 必判完、**enrich 留空不阻塞**；⑤ `--apply` 回写 `opinion.json`；⑥ 旧判读单（无 `block`）仍可用且不留幽灵字段；⑦ `--status` 只需 `--out`；⑧ 离线 HTML 无外链、内嵌表单条目数一致；⑨ 导出标记不一致只警告不阻塞 |

## 改动脚本后必须做什么

1. `python -m unittest discover -s tests` 全绿；
2. 若新增了结果键：同步加入 `spotcheck.py` 的**过期键清除清单**（否则重跑 `--apply` 留幽灵结论）；
3. 若改了方法（不只是重构）：跑一次 `opinion.py --prev` 看漂移报告，确认变动项并在报告里披露；
4. 收紧/放宽本文档里的断言时，先想清楚**是代码错了还是断言错了**——`test_stats.py` 的多项差值、`test_opinion.py` 的否定窗口这两条，最初就是断言写错（把物理上更窄的口径当成"应该更宽"），已在注释里记明原因。
