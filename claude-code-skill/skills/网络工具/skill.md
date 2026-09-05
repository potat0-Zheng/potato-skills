---
name: network-tools
description: Use when the user asks to test network connectivity, check internet speed, monitor for disconnections, or diagnose WiFi stability issues. Trigger with "测网速", "检测网络", "网络连通性", "看看网络状态", "监控掉线".
---

# 网络工具

两个独立工具，按需选用：

| 工具 | 用途 | 依赖 |
|------|------|------|
| **monitor.js** | 连通性检测（HTTP 请求 3 站点，≥2 不可达 = 断连） | 仅需 Node.js，已包含在本技能目录 |
| **MySpeed** | 带宽测速（Cloudflare 测速） | 需单独安装 MySpeed 服务 |

## 选择路径

**先判断用户意图再选工具，不要两个都启动：**

- 仅测连通性/监控掉线 → 直接用 monitor.js，跳过 MySpeed
- 仅测速/带宽 → 启动 MySpeed（需单独安装）
- 同时需要两者 → 先跑 monitor.js 输出连通性报告，再启动 MySpeed 测速；monitor.js 会自动从 `http://localhost:5216/api/speedtests` 拉取同时段测速数据写入报告

## 连通性检测（monitor.js）— 独立运行

monitor.js 已包含在本技能目录中，无需额外安装。使用纯 HTTP/HTTPS 请求，不依赖任何原生模块，系统自带的任意 Node.js 版本均可运行。

```bash
DURATION=<分钟数> node "<技能目录>/monitor.js"
```

技能目录默认为 `~/.claude/skills/网络工具`（Linux/macOS）或 `%USERPROFILE%\.claude\skills\网络工具`（Windows）。

默认 3 分钟，传 `DURATION=5` 跑 5 分钟。脚本在设定的时长后自动输出报告并退出。

报告包含：
- 断连次数、最长/平均断连时长、网络可用率
- 每次断连的精确时间和不可达站点
- 同时段 MySpeed 测速结果（如 MySpeed 服务正在运行则自动读取）
- 诊断结论

## 带宽测速（MySpeed）— 可选，需单独安装

MySpeed 是独立的测速服务，不在本技能中捆绑。如需同时看到带宽数据：

1. 安装 MySpeed：`git clone https://github.com/gnmyt/MySpeed.git && cd MySpeed && npm install`
2. 启动服务：`PREVIEW_MODE=true NODE_ENV=production node server/index.js &`
3. 确认：`curl -s http://localhost:5216/api/config` 返回 JSON
4. 查看测速结果：`curl -s http://localhost:5216/api/speedtests`

之后运行 monitor.js 时会自动附带同时段测速数据。

## 判断逻辑（monitor.js）

同时请求百度、阿里云、B站。单个站点不可达忽略（可能是服务器问题），≥2 个同时不可达才记为断连。每 2 秒检测一次。

## 注意事项

- 不要用 ICMP ping（DNS 服务器常丢弃 ping 包导致误报），HTTP 请求更准确
- 目标站点可根据需要修改 monitor.js 中的 `TARGETS` 数组
- 报告同时输出到终端和 `disconnect.log` 文件（位于 monitor.js 同目录）
