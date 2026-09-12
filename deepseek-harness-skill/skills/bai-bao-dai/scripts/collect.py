# -*- coding: utf-8 -*-
"""bai-bao-dai 统一采集入口。

职责：按平台分发采集任务。
- 知乎：内嵌爬虫（zhihu/crawl.py），直接可用。
- 微博 / 小红书：驱动 external/MediaCrawler（关键词搜索，jsonl 输出）。
  凭证策略（按用户确认的"有凭证驱动、无凭证标注"）：
    * 有凭证（cookie 或持久化浏览器登录态）→ 真实驱动 MediaCrawler 采集；
    * 无凭证 → 明确输出"[平台] 未采集：<原因>"并返回非 0（不静默假成功）；
    * 尝试驱动但失败（登录态失效/网络/风控）→ 同样标记未采集并返回非 0。

用法：
  # 一次性扫码登录（每个平台一次，会话持久化，此后采集无需 cookie）
  python collect.py --login weibo,xiaohongshu

  python collect.py --event "事件关键词" --platforms zhihu --url <知乎问题URL> --limit 20 --out data/demo/raw
  python collect.py --event "关键词" --platforms weibo,xhs,zhihu --url <知乎URL> \
      --wb-cookie "<微博cookie>" --xhs-cookie "<小红书cookie>" --max-posts 50
  # cookie 也可通过环境变量 BBD_WB_COOKIE / BBD_XHS_COOKIE 提供；
  # 都不提供时，若 MediaCrawler 的 browser_data 登录态存在则尝试复用。
"""
import argparse
import glob
import json
import os
import re
import subprocess
import sys
import time

SKILL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXTERNAL_DIR = os.path.join(SKILL_DIR, "external")


def load_config():
    p = os.path.join(SKILL_DIR, "config.json")
    with open(p, "r", encoding="utf-8") as f:
        return json.load(f)


# ---------- 知乎（内嵌） ----------
def collect_zhihu(url, out_dir, limit):
    crawler = os.path.join(SKILL_DIR, "zhihu", "crawl.py")
    if not os.path.exists(crawler):
        print("[错误] 知乎内嵌爬虫缺失：", crawler)
        return 1
    os.makedirs(out_dir, exist_ok=True)

    m = re.search(r"/question/(\d+)(?:/answer/(\d+))?", url)
    if not m:
        print("[错误] 无法从 URL 提取知乎问题 ID，请检查链接")
        return 1
    qid = m.group(1)
    aid = m.group(2)
    name = f"zhihu_{qid}" + (f"_answer_{aid}" if aid else "") + ".md"
    out_path = os.path.join(out_dir, name)

    print(f"[知乎] 爬取 {'单回答' if aid else '全问题'} → {out_path}")
    cmd = [sys.executable, crawler, url, out_path]
    if limit:
        cmd += ["--limit", str(limit)]
    r = subprocess.run(cmd)
    return r.returncode


# ---------- 微博 / 小红书（external/MediaCrawler 驱动） ----------
MC_DIR = os.path.join(EXTERNAL_DIR, "MediaCrawler")
MC_PY = os.path.join(MC_DIR, ".venv", "Scripts", "python.exe")
if not os.path.exists(MC_PY):  # 兼容非 Windows
    MC_PY = os.path.join(MC_DIR, ".venv", "bin", "python")

MC_PLATFORM = {"weibo": "wb", "xiaohongshu": "xhs"}
MC_SESSION_DIR = {"weibo": "wb_user_data_dir", "xiaohongshu": "xhs_user_data_dir"}


def _mc_session_ok(platform):
    d = os.path.join(MC_DIR, "browser_data", MC_SESSION_DIR[platform])
    return os.path.isdir(d) and bool(os.listdir(d))


def _mc_builtin_cookie():
    """MediaCrawler config/base_config.py 内置的默认 cookie（可能过期）。"""
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "mc_base_config", os.path.join(MC_DIR, "config", "base_config.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return getattr(mod, "COOKIES", "") or ""
    except Exception:
        return ""


def credential_status(platform, wb_cookie, xhs_cookie):
    """返回 (可用?, 登录方式, 说明)。"""
    cookie = wb_cookie if platform == "weibo" else xhs_cookie
    if cookie:
        return True, "cookie", "已提供 cookie"
    if _mc_session_ok(platform):
        return True, "qrcode_session", "复用 MediaCrawler 持久化浏览器登录态(browser_data)"
    if platform == "xiaohongshu" and _mc_builtin_cookie():
        return True, "cookie", "使用 MediaCrawler 内置 cookie（可能已过期）"
    return False, "", "无 cookie 且无持久化登录态"


def run_mediacrawler(platform, event, out_dir, max_posts, wb_cookie, xhs_cookie, with_comments, timeout):
    """驱动 MediaCrawler 关键词搜索，结果 jsonl 写入 out_dir。返回 (rc, 记录数)。"""
    if not os.path.isdir(MC_DIR):
        print(f"[{platform}] 未采集：external/MediaCrawler 未部署")
        return 1, 0
    if not os.path.exists(MC_PY):
        print(f"[{platform}] 未采集：MediaCrawler 虚拟环境缺失（{MC_PY}），请重建 .venv")
        return 1, 0

    ok, lt, note = credential_status(platform, wb_cookie, xhs_cookie)
    if not ok:
        print(f"[{platform}] 未采集：{note}")
        return 1, 0

    # MediaCrawler 登录类型仅接受 qrcode/phone/cookie；
    # "qrcode_session"（复用 browser_data 持久化登录态）映射为 --lt qrcode
    lt_arg = "qrcode" if lt == "qrcode_session" else lt

    mc_platform = MC_PLATFORM[platform]
    os.makedirs(out_dir, exist_ok=True)

    cmd = [MC_PY, "main.py",
           "--platform", mc_platform,
           "--lt", lt_arg,
           "--type", "search",
           "--keywords", event,
           "--start", "1",
           "--save_data_option", "jsonl",
           "--save_data_path", out_dir,
           "--crawler_max_notes_count", str(max_posts),
           "--get_comment", "y" if with_comments else "n",
           "--get_sub_comment", "n",
           "--headless", "y",
           "--enable_ip_proxy", "n",
           ]
    if lt == "cookie":
        cookie = wb_cookie if platform == "weibo" else (xhs_cookie or _mc_builtin_cookie())
        if not cookie:
            print(f"[{platform}] 未采集：cookie 登录方式但 cookie 为空")
            return 1, 0
        cmd += ["--cookies", cookie]

    print(f"[{platform}] 驱动 MediaCrawler：{' '.join(cmd[:10])} ...")
    env = os.environ.copy()
    if platform == "xiaohongshu":
        # 排序通道决定结果集质量：默认 general（相关度）
        env.setdefault("MC_XHS_SORT_TYPE", os.environ.get("MC_XHS_SORT_TYPE", "general"))
        print(f"[xiaohongshu] 搜索排序={env['MC_XHS_SORT_TYPE']}"
              f"（popularity_descending 会引入大量与事件无关的爆款帖子）")
    try:
        r = subprocess.run(cmd, cwd=MC_DIR, timeout=timeout,
                           capture_output=True, text=True,
                           encoding="utf-8", errors="replace", env=env)
    except subprocess.TimeoutExpired:
        print(f"[{platform}] 未采集：MediaCrawler 运行超时（>{timeout}s），可能登录态失效或风控")
        return 2, 0
    except Exception as e:
        print(f"[{platform}] 未采集：MediaCrawler 启动失败：{e}")
        return 2, 0

    if r.returncode != 0:
        # 从输出中提取失败原因（最后 600 字符）
        tail = (r.stderr or "")[-600:] + (r.stdout or "")[-200:]
        reason = "未知错误"
        if "WinError" in tail or "PermissionError" in tail or "拒绝访问" in tail:
            reason = "浏览器启动被拒绝（沙箱/权限限制，请在普通终端运行 collect.py）"
        elif any(k in tail for k in ("扫码", "登录", "login", "cookie")):
            reason = "登录态失效，需重新扫码或更新 cookie"
        elif any(k in tail for k in ("风控", "验证", "block", "403", "429")):
            reason = "疑似触发平台风控"
        elif tail.strip():
            reason = "运行异常：" + tail.strip().splitlines()[-1][:160]
        print(f"[{platform}] 未采集：MediaCrawler 退出码 {r.returncode}（{reason}）")
        return 2, 0

    # 收集输出的 jsonl（递归），记录数统计
    n = 0
    time.sleep(0.5)
    for fp in glob.glob(os.path.join(out_dir, "**", "*.jsonl"), recursive=True):
        try:
            with open(fp, "r", encoding="utf-8") as f:
                n += sum(1 for line in f if line.strip())
        except Exception:
            pass
    if n == 0:
        print(f"[{platform}] 未采集：MediaCrawler 运行完成但未产出数据（可能无搜索结果或登录态失效）")
        return 2, 0
    print(f"[{platform}] 采集完成：{n} 条记录（jsonl）→ {out_dir}")
    return 0, n


def collect_external(platform, event, out_dir, max_posts, wb_cookie, xhs_cookie, with_comments, timeout):
    return run_mediacrawler(platform, event, out_dir, max_posts, wb_cookie, xhs_cookie, with_comments, timeout)


def login_platform(platform, timeout=600):
    """一次性扫码登录：建立/刷新 browser_data 持久化登录态。

    运行 MediaCrawler 非 headless 模式，弹出浏览器窗口；
    用户用手机 App 扫码登录后，会话自动保存到 browser_data/<platform>_user_data_dir，
    此后 collect.py 采集自动复用，无需再复制/传输 cookie。
    返回 0 成功 / 1 失败 / 2 环境不可用。
    """
    if not os.path.isdir(MC_DIR):
        print(f"[{platform}] MediaCrawler 未部署：{MC_DIR}")
        return 1
    if not os.path.exists(MC_PY):
        print(f"[{platform}] MediaCrawler 虚拟环境缺失（{MC_PY}），请先重建 .venv")
        return 1

    mc_platform = MC_PLATFORM[platform]
    session_dir = os.path.join(MC_DIR, "browser_data", MC_SESSION_DIR[platform])
    has_session = os.path.isdir(session_dir) and bool(os.listdir(session_dir))

    print("=" * 60)
    print(f"[{platform}] 扫码登录流程")
    if has_session:
        print("  - 检测到已有持久化登录态，将先尝试直接复用；失效才会弹出登录窗口")
    print("  - 即将打开浏览器窗口（非 headless）")
    print("  - 请用手机 App（微博/小红书）扫浏览器中的二维码完成登录")
    print("  - 登录成功后脚本会自动保存会话并做 1 条最小搜索验证")
    print("=" * 60)

    cmd = [MC_PY, "main.py",
           "--platform", mc_platform,
           "--lt", "qrcode",
           "--type", "search",
           "--keywords", "测试",
           "--start", "1",
           "--save_data_option", "jsonl",
           "--save_data_path", os.path.join(SKILL_DIR, "data", "_login_probe"),
           "--crawler_max_notes_count", "1",
           "--get_comment", "n",
           "--get_sub_comment", "n",
           "--headless", "n",          # 必须非 headless，否则无法扫码
           "--enable_ip_proxy", "n",
           ]
    print(f"[{platform}] 启动：{' '.join(cmd[:9])} ...")
    try:
        r = subprocess.run(cmd, cwd=MC_DIR, timeout=timeout)
    except subprocess.TimeoutExpired:
        print(f"[{platform}] 登录流程超时（>{timeout}s）——若二维码未扫，请重试")
        return 1
    except Exception as e:
        print(f"[{platform}] 登录启动失败：{e}")
        return 1

    if r.returncode != 0:
        print(f"[{platform}] 登录未完成：MediaCrawler 退出码 {r.returncode}（网络/风控/浏览器问题）")
        return 1
    if os.path.isdir(session_dir) and os.listdir(session_dir):
        print(f"[{platform}] 登录态已保存：{session_dir}")
        print(f"[{platform}] 后续采集无需 cookie，collect.py 将自动复用（凭证检测：browser_data ✓）")
        return 0
    print(f"[{platform}] 登录流程结束但未检测到会话文件，建议重试或改用 cookie 方式")
    return 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--event", default="", help="事件关键词/描述")
    ap.add_argument("--platforms", default="zhihu", help="逗号分隔：weibo,xiaohongshu,zhihu")
    ap.add_argument("--login", default="", help="一次性扫码登录模式：weibo / xiaohongshu（登录一次，会话持久化，此后采集无需 cookie）")
    ap.add_argument("--url", default="", help="知乎问题/回答 URL（知乎必需）")
    ap.add_argument("--limit", type=int, default=0, help="知乎全问题爬取条数上限")
    ap.add_argument("--days", type=int, default=7, help="保留参数：外部平台按 max-posts 限条数")
    ap.add_argument("--max-posts", type=int, default=200, help="外部平台最大帖子数")
    ap.add_argument("--wb-cookie", default=os.environ.get("BBD_WB_COOKIE", ""), help="微博 cookie（或环境变量 BBD_WB_COOKIE）")
    ap.add_argument("--xhs-cookie", default=os.environ.get("BBD_XHS_COOKIE", ""), help="小红书 cookie（或环境变量 BBD_XHS_COOKIE）")
    ap.add_argument("--with-comments", action="store_true", help="同时采集一级评论（更慢）")
    ap.add_argument("--sort-type", default="", help="小红书搜索排序：general（默认，相关度）/ popularity_descending（最热，易引入无关爆款）/ time_descending")
    ap.add_argument("--timeout", type=int, default=600, help="外部爬虫单次运行超时（秒）")
    ap.add_argument("--out", default="data/default/raw")
    args = ap.parse_args()

    if args.sort_type:
        os.environ["MC_XHS_SORT_TYPE"] = args.sort_type

    # 一次性登录模式：--login weibo,xiaohongshu
    if args.login:
        rc = 0
        for p in [x.strip() for x in args.login.split(",") if x.strip()]:
            if p not in MC_PLATFORM:
                print(f"[collect] 未知平台 {p}（可选：weibo/xiaohongshu）")
                rc = max(rc, 1)
                continue
            rc = max(rc, login_platform(p, args.timeout))
        return rc

    if not args.event:
        print("[collect] 采集模式需要 --event 关键词")
        return 1

    cfg = load_config()
    out_dir = os.path.join(SKILL_DIR, args.out) if not os.path.isabs(args.out) else args.out
    os.makedirs(out_dir, exist_ok=True)

    plats = [p.strip() for p in args.platforms.split(",") if p.strip()]
    print(f"[collect] event={args.event!r} platforms={plats}")

    rc = 0
    for p in plats:
        if p not in cfg["platforms"]:
            print(f"[collect] 未知平台 {p}，跳过（可选：weibo/xiaohongshu/zhihu）")
            continue
        if p == "zhihu":
            if not args.url:
                print("[知乎] 需要 --url 提供问题/回答链接（定位问题请先用搜索或 LLM）")
                rc = max(rc, 1)
                continue
            rc = max(rc, collect_zhihu(args.url, os.path.join(out_dir, "zhihu"), args.limit))
        else:
            prc, n = collect_external(
                p, args.event, os.path.join(out_dir, p),
                args.max_posts, args.wb_cookie, args.xhs_cookie,
                args.with_comments, args.timeout)
            rc = max(rc, prc)

    print("[collect] 完成" + ("（部分平台未采集，请在报告中标注覆盖完整性）" if rc else ""))
    return rc


if __name__ == "__main__":
    sys.exit(main())
