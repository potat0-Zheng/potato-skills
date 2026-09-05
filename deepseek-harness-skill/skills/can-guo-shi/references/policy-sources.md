# 官方政策源契约库（policy-sources）

参国是「官方库直查层」的信源契约。每个源一节：端点 / 认证 / 参数 / 响应 / 字段映射 / 陷阱。
**原则：接口命中的结构化字段优先于 WebSearch 摘要；接口失效或零命中 → 诚实降级 WebSearch 并留痕。**

> 验证状态：**源一已于 2026-09-04 实测通过**（`scripts/query_gov_lib.py` 真实调用返回 107 条命中、四分类、文号/机构/链接齐全）。契约中"实测"标记的行以真实响应为准，覆盖此前基于公开文档的推断（doc 版字段 source/piclinksurl 实测为空，正确字段为 puborg/url）。

---

## 源一：国务院政策文件库（sousuo.www.gov.cn）✅ 已实测

### 基本信息

| 项 | 值 |
|---|---|
| 网页入口 | `https://sousuo.www.gov.cn/zcwjk/policyDocumentLibrary` |
| API 端点 | `GET https://sousuo.www.gov.cn/search-gov/data` |
| 认证 | 无（免鉴权） |
| 请求方式 | GET query 参数，需带浏览器 UA |
| 响应格式 | JSON（UTF-8） |
| 查询脚本 | `scripts/query_gov_lib.py`（零依赖；参数落盘 JSON：`python query_gov_lib.py params.json out.json`） |

### 请求参数（实测有效）

| 参数 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `t` | string | `zhengcelibrary` | 搜索主题（脚本固定） |
| `q` | string | 必填 | 查询关键词 |
| `searchfield` | string | `title` | `title`(标题) / `content`(全文) |
| `sort` | string | `score` | `score`(相关度) / `pubtime`(时间) |
| `p` | int | `0` | 页码（从 0 起；**实测 p/n 的翻页语义不稳定，先取首页**） |
| `n` | int | 10 | **实测每分类单页最多返回 20 条**（n 传 20 时 each 分类 currentNum=20），超过走分页/精确过滤 |
| `pubtimeyear` | string | | 年份过滤，如 `2025` |
| `bmfl` | string | | 发文机关过滤（实测生效性待复核，建议以 q + 时间过滤为主） |
| `childtype` | string | | 分类过滤（见分类枚举） |
| `mintime` / `maxtime` | string | | 起止时间 `YYYY-MM-DD` |

### 响应结构（2026-09-04 实测）

```jsonc
{
  "searchVO": {
    "totalCount": 0,          // 实测恒为 0，不可用！
    "catMap": {               // 四分类，各自带 totalCount 与 listVO（每类≤20条/页）
      "gongwen":    { "totalCount": 12, "listVO": [ { "pcode": "国办发〔2022〕35号",
                    "puborg": "国务院办公厅",            // ← 发布机构（source 为空串）
                    "title": "…<em>营商</em>…的通知",   // ← <em> 高亮需剥离
                    "pubtimeStr": "2022.10.31", "pubtime": "1667206500000",
                    "url": "https://www.gov.cn/zhengce/zhengceku/…" } ] },  // ← 原文链接（piclinksurl 为空串）
      "bumenfile":  { "totalCount": 23, "listVO": [ /* 部门文件 */ ] },
      "otherfile":  { "totalCount": 51, "listVO": [ /* 解读，puborg/pcode 为空 */ ] },
      "gongbao":    { "totalCount": 21, "listVO": [ /* 国务院公报载文，无 pcode/puborg，标题含 <br> */ ] }
    }
  }
}
```

**命中总数 = 各分类 `totalCount` 之和**（如 12+23+51+21=107）；脚本已按此计算。

### 分类枚举（childtype = catMap 键）

| 值 | 含义 | 效力层级线索 | 实测备注 |
|---|---|---|---|
| `gongwen` | 国务院文件（国令、国发、国函、国办发等） | L2 行政法规 / L6 规范性文件 | 有 pcode+puborg |
| `bumenfile` | 国务院部门文件 | L4 部门规章 / L6 | 有 pcode+puborg（联合发文机构以空格连接） |
| `otherfile` | **解读**（政策解读、答记者问、时评） | `--with-insight` 官方解读素材 | 无 pcode/puborg，仅有标题+时间+url |
| `gongbao` | 国务院公报载文 | 文本权威（公报版） | 无 pcode/puborg，标题含 `<br>` 与全角空格 |

### 字段映射（API → 参国是政策条目，实测版）

| API 字段 | 政策条目字段 | 备注 |
|---|---|---|
| `pcode` | 文号 | 可直接喂给 docnumber-parser（规划中）；gongwen/bumenfile 有值 |
| `puborg` | 发布机构 | 实测字段（`source` 为空串勿用） |
| `pubtime`（毫秒） | 发布日期 | 脚本换算为 `YYYY-MM-DD`；`pubtimeStr`（`2022.10.31`）为兜底 |
| `url` | 来源 URL（索引引用） | 实测字段（`piclinksurl` 为空串勿用）；gongbao 为公报页 |
| catMap 键 + 标题前缀 | 效力层级（L1-L7 线索） | gongwen 中"国令"→L2、"国发/国办发"→L6 |
| `title`（剥 `<em>`/`<br>` 后） | 政策名称 | 公报类标题需清洗 |

### 命中后的证据规则

- 官方库直查命中 → 条目**存在性**与**文号/机构/日期字段**可标 `▲`（官方渠道），来源 URL 入来源索引（A 级）。
- **正文级事实（数字、条款内容）以原文页/全文为准**再标 ▲；仅凭接口标题/摘要不可标 ▲。
- 解读类（`otherfile`）作为官方解读素材，优于媒体解读，用于 `--with-insight`。
- 命中后仍建议 1 次 WebSearch 交叉（防索引库与官网不同步），两源一致才标 ● 的场景不变。

### 陷阱与合规（实测）

- **必须带浏览器 UA**（脚本已内置），否则可能被拒。
- **顶层 `totalCount` 恒为 0**；命中总数取各分类 `totalCount` 之和。
- **每分类单页上限约 20 条**；n/p 翻页语义待复核——需要全量时改用精确关键词或 `pubtimeyear`/`mintime`/`maxtime` 缩小窗口，不要依赖盲目翻页。
- 标题含 `<em>` 高亮、`<br>` 换行、全角空格（公报类尤甚），使用前清洗。
- 网络可达性因环境而异：本机 python urllib 直连成功（2026-09-04），curl 同机 SSL 失败——若脚本报网络错，先用浏览器确认接口是否仍存活（官网可能改版）。
- 限速：脚本级间隔 ≥1s；仅供学习研究；公开免鉴权接口，不涉及付费库。
- **"检索不到 ≠ 不存在"**：官方库索引覆盖有限（公报/地方文件需另寻），零命中必须走 WebSearch 补充，不得据此断言"未出台"。

---

## 源二：国家法律法规数据库（flk.npc.gov.cn）✅ 已实测（2026-09-04）

法律/行政法规/司法解释/地方性法规的**权威全文库**，覆盖国务院政策库没有的 L1 法律层级；免费、无需登录。

### 基本信息

| 项 | 值 |
|---|---|
| 网页入口 | `https://flk.npc.gov.cn/`（检索页 `/search`，详情 `/detail?id={bbbs}`） |
| 检索端点 | `POST /law-search/search/list`（JSON body） |
| 详情端点 | `GET /law-search/search/flfgDetails?bbbs={bbbs}` |
| 下载端点 | `GET /law-search/download/pc?format=docx\|pdf&bbbs={bbbs}` → `data.url` 签名 OSS 链接 |
| 认证 | 无（实测无需 cookie；必须带 UA + `Referer: https://flk.npc.gov.cn/...`） |
| 查询脚本 | `scripts/query_flk.py`（零依赖；检索 + `--detail`；`--sxx 3` 过滤现行有效） |

### 检索参数（实测有效）

| 参数 | 说明 |
|---|---|
| `searchContent` | 检索词（脚本 `q`） |
| `searchRange` | `1`=标题（默认） / `2`=正文 |
| `searchType` | `1`=精确 / `2`=模糊（默认） |
| `sxx` | 时效过滤数组：`[3]`=现行有效（实测确定）；不传=全部 |
| `pageNum` / `pageSize` | 分页（pageSize 上限约 100） |
| `orderByParam.sort` | `""`(默认) / `gbrq`(公布日) / `sxrq`(施行日) / `sxx` / `flxz` / `zdjg` |
| `flfgCodeId` / `zdjgCodeId` | 分类/机关过滤（code 来自 `GET /law-search/search/enumData`，未接入 CLI，需要时手填） |

### 字段映射（API → 参国是政策条目，实测版）

| API 字段 | 政策条目字段 | 备注 |
|---|---|---|
| `title` | 政策名称 | 含 `<em class='highlight'>` 高亮，用去标签清洗 |
| `flxz` | 效力层级线索 | 枚举：宪法/法律/行政法规/监察法规/地方法规/司法解释。近似对照：法律→L1、行政法规→L2、地方法规→L3、司法解释→L4 参照；监察法规介于 L2 与 L4 之间（以官方位阶为准） |
| `zdjgName` | 发布机构 | 如"全国人民代表大会""国务院" |
| `gbrq` / `sxrq` | 公布日期 / 施行日期 | 可为 `null`（如待施行/无施行标注） |
| `sxx` | **时效状态** | **3=现行有效（实测确定）**；2=已失效或已被修改（推定）；1=已废止（推定）。精确语义以 flk 官网时效筛选项为准 |
| `bbbs` | 条目 ID | 详情/下载均用 |
| detail: `ossFile` / `xgwj` | 全文路径 / 关联文件 | 全文下载须走 `download/pc` 接口（detail 的 oss 直连路径会返回 SPA 页）；`xgwj` 为关联文件（修改决定等） |

### 证据规则与用法（对接参国是）

- **L1 法律/司法解释/地方法规的检索与现行有效核验走 flk**，国务院文件走源一——两库分工：文件（规章级以下+解读） vs 法规（法律位阶）。
- **同题多版本并存**（实测：2021/2017 行政处罚法同库）→ 用 `--sxx 3` 过滤出**现行有效**版本；旧版 `sxx=2` 标"已失效或已被修改"，直接填"关联政策"栏（被替代关系）。这是官方字段标注，属状态核验而非真伪判断，不越破虚妄边界。
- 详情可拿 `ossFile` 经下载接口取 **docx 全文** → 正文级事实（条款、数字）可标 ▲。
- 检索命中后仍建议 1 次 WebSearch 交叉。

### 陷阱（实测）

- 高亮标签是 `<em class='highlight'>`（与源一的 `<em>` 不同），清洗统一用去标签正则。
- `sxrq`/`gbrq` 可为 `null`；不要把 null 当缺失错误。
- 需 UA + Referer；实测无需 cookie，但官网若加风控需先 GET `/index` 取 cookie。
- 下载勿直连 `ossFile` 路径（返回 SPA 页），走 `download/pc` 接口拿签名 URL。

---

## 后续候选源（待侦察，未固化为契约）

| 源 | 说明 | 侦察点 |
|---|---|---|
| 各省政策文件库 | 央地联动追踪的省级节点 | 各省结构不一，先侦察浙/沪/粤等；无 JSON 则维持 WebSearch |
| 中国政府网政策库 gov.cn/zhengce | 国务院及国办文件最全 | 与 sousuo API 的关系与去重策略 |
