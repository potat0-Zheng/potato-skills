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

v0.5 增补（长任务可观测性与部分成果保全）：
  1. **部分成果保全**：MediaCrawler 非 0 退出或超时时，仍统计已落盘 jsonl 并保留；
     有记录即判「部分采集」（rc=0 并显式标注），只有 0 条才判「未采集」。
     「分页取尽（DataFetchError）」与「登录态失效/风控」在文案上区分开——
     这两类原因此前被合并成一句「运行异常」，导致分页正常的采集被误记为失败。
  2. **多关键词**：--keywords "a,b,c"（或重复 --event），一次调用串行跑完，
     每个关键词独立输出目录 out/<platform>/<slug>/，互不覆盖。
  3. **最小探针**：--probe 以 1 词 × 5 帖 × 无评论 × 短超时验证登录态与关键词是否有结果，
     通过后再放量（先探针后放量是长任务省时的第一原则）。
  4. **轻量进度同步**：采集期间每隔 --heartbeat 秒把一条状态写进
     out/_collect_progress.json（原子替换，约 1KB），同时向 stdout 打一行心跳；
     任何时刻可用 `collect.py --status <out>` 零副作用读取当前进度。
"""
import argparse
import glob
import json
import os
import re
import signal
import subprocess
import sys
import time
from datetime import datetime

SKILL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXTERNAL_DIR = os.path.join(SKILL_DIR, "external")

PROGRESS_NAME = "_collect_progress.json"
HEARTBEAT_DEFAULT = 15


def load_config():
    p = os.path.join(SKILL_DIR, "config.json")
    with open(p, "r", encoding="utf-8") as f:
        return json.load(f)


# ---------- 轻量进度同步（v0.5） ----------
def _slug(text, width=24):
    """关键词 → 目录名片段（保留中英文数字，其余换下划线）。"""
    s = re.sub(r"[^\w\u4e00-\u9fff]+", "_", str(text or "").strip())
    s = re.sub(r"_+", "_", s).strip("_")
    return (s[:width] or "kw")


def progress_path(out_dir):
    return os.path.join(out_dir, PROGRESS_NAME)


def progress_write(out_dir, state):
    """原子写进度文件：先写 .tmp 再 replace，读取方永远看到完整 JSON。"""
    p = progress_path(out_dir)
    state["updated"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    tmp = p + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=1)
        os.replace(tmp, p)
    except Exception:
        pass


def _count_jsonl(root):
    """统计 root 下已落盘的 jsonl 记录数（帖子/评论分开计）。"""
    posts = comments = 0
    for fp in glob.glob(os.path.join(root, "**", "*.jsonl"), recursive=True):
        try:
            with open(fp, "r", encoding="utf-8", errors="replace") as f:
                n = sum(1 for line in f if line.strip())
        except Exception:
            continue
        if "comment" in os.path.basename(fp).lower():
            comments += n
        else:
            posts += n
    return posts, comments


def progress_status(out_dir, echo=True):
    """读取进度文件并打印（零副作用的查询入口）。返回状态 dict 或 None。"""
    p = progress_path(out_dir)
    if not os.path.exists(p):
        if echo:
            print(f"[status] 未找到进度文件：{p}")
        return None
    try:
        with open(p, "r", encoding="utf-8") as f:
            st = json.load(f)
    except Exception as e:
        if echo:
            print(f"[status] 进度文件读取失败：{e}")
        return None
    if not echo:
        return st
    cur = st.get("current") or {}
    print("[status] %s · 起始 %s · 更新 %s"
          % (st.get("event") or "—", st.get("started") or "—", st.get("updated") or "—"))
    done = st.get("done_tasks") or []
    print("[status] 进度 %d/%d 个任务完成%s"
          % (len(done), st.get("total_tasks") or 0,
             ("，当前：" + "%s/%s" % (cur.get("platform"), cur.get("keyword")))
             if cur.get("keyword") else "，当前无任务在跑"))
    if cur.get("keyword"):
        print("[status] 当前任务 %s 秒 · 已入库 帖 %s + 评 %s"
              % (cur.get("elapsed_s"), cur.get("posts"), cur.get("comments")))
    for t in done:
        print("[status]   - %-6s %-18s %-10s 帖 %-4s 评 %-4s %s"
              % (t.get("platform"), str(t.get("keyword"))[:18], t.get("state"),
                 t.get("posts"), t.get("comments"), t.get("note") or ""))
    if st.get("summary"):
        print("[status] 汇总 %s（进度文件 %s）" % (st["summary"], p))
    return st


def _kill_tree(proc):
    """超时后结束进程树（MediaCrawler 会拉起 Chromium 子进程）。"""
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                           capture_output=True)
        else:
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass


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


def _classify_failure(returncode, tail, posts, comments):
    """把退出码 + 输出尾部翻译成人能用的原因（v0.5：区分分页取尽与登录态失效）。"""
    if "WinError" in tail or "PermissionError" in tail or "拒绝访问" in tail:
        return "浏览器启动被拒绝（沙箱/权限限制，请在普通终端或放权后运行 collect.py）"
    if any(k in tail for k in ("风控", "captcha", "block", "403", "429")):
        return "疑似触发平台风控"
    if any(k in tail for k in ("Login state result: False", "登录态失效", "请重新登录",
                               "扫码失效", "not login")):
        return "登录态失效，需重新扫码或更新 cookie（用 --login <平台>）"
    if "DataFetchError" in tail or "RetryError" in tail:
        # 分页取尽／结果集小于请求量时 MediaCrawler 会抛这个错，属正常终止而非故障
        return ("分页已取尽（DataFetchError：结果集小于请求条数），属正常终止"
                if (posts or comments) else
                "该关键词无搜索结果（DataFetchError 且零产出）——换更短/更通用的关键词，而非加超时")
    if tail.strip():
        return "运行异常：" + tail.strip().splitlines()[-1][:160]
    return f"未知错误（退出码 {returncode}）"


def run_mediacrawler(platform, event, out_dir, max_posts, wb_cookie, xhs_cookie,
                     with_comments, timeout, heartbeat=HEARTBEAT_DEFAULT, hook=None):
    """驱动 MediaCrawler 关键词搜索，结果 jsonl 写入 out_dir。

    返回 (rc, 记录数, state)。state ∈ {ok, partial, failed, timeout, no_credential}。
    v0.5：改为 Popen + 轮询——既能在超时/异常时保全已落盘成果，
    也能把「已入库多少条」这种真实进度写进进度文件（黑箱 → 可观测）。
    """
    if not os.path.isdir(MC_DIR):
        print(f"[{platform}] 未采集：external/MediaCrawler 未部署")
        return 1, 0, "failed"
    if not os.path.exists(MC_PY):
        print(f"[{platform}] 未采集：MediaCrawler 虚拟环境缺失（{MC_PY}），请重建 .venv")
        return 1, 0, "failed"

    ok, lt, note = credential_status(platform, wb_cookie, xhs_cookie)
    if not ok:
        print(f"[{platform}] 未采集：{note}")
        return 1, 0, "no_credential"

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
            return 1, 0, "no_credential"
        cmd += ["--cookies", cookie]

    print(f"[{platform}] 驱动 MediaCrawler：{' '.join(cmd[:10])} ...", flush=True)
    env = os.environ.copy()
    if platform == "xiaohongshu":
        # 排序通道决定结果集质量：默认 general（相关度）
        env.setdefault("MC_XHS_SORT_TYPE", os.environ.get("MC_XHS_SORT_TYPE", "general"))
        print(f"[xiaohongshu] 搜索排序={env['MC_XHS_SORT_TYPE']}"
              f"（popularity_descending 会引入大量与事件无关的爆款帖子）", flush=True)

    log_path = os.path.join(out_dir, "_mediacrawler.log")
    t0 = time.time()
    try:
        logf = open(log_path, "w", encoding="utf-8", errors="replace")
    except Exception:
        logf = None
    try:
        proc = subprocess.Popen(cmd, cwd=MC_DIR, env=env,
                                stdout=logf or subprocess.DEVNULL,
                                stderr=subprocess.STDOUT)
    except Exception as e:
        if logf:
            logf.close()
        print(f"[{platform}] 未采集：MediaCrawler 启动失败：{e}")
        return 2, 0, "failed"

    state = "ok"
    while True:
        try:
            proc.wait(timeout=heartbeat)
            break
        except subprocess.TimeoutExpired:
            pass
        elapsed = int(time.time() - t0)
        posts, comments = _count_jsonl(out_dir)
        if hook:
            hook(posts, comments, elapsed)
        hint = ""
        if elapsed >= 45 and (posts + comments) == 0:
            # MediaCrawler 先取完结果集再落盘，单批前期计数恒为 0，属正常而非卡死
            hint = " · 未落盘（结果集取完才写盘，属正常）"
        print("[hb] %s/%-16s %02d:%02d / 上限 %02d:%02d · 已入库 帖 %d + 评 %d · running%s"
              % (platform, str(event)[:16], elapsed // 60, elapsed % 60,
                 timeout // 60, timeout % 60, posts, comments, hint), flush=True)
        if elapsed > timeout:
            state = "timeout"
            _kill_tree(proc)
            try:
                proc.wait(timeout=20)
            except Exception:
                pass
            print(f"[{platform}] 达到超时上限 {timeout}s，已终止进程树并保全已落盘成果", flush=True)
            break

    if logf:
        logf.close()
    posts, comments = _count_jsonl(out_dir)
    total = posts + comments
    elapsed = int(time.time() - t0)

    try:
        with open(log_path, "r", encoding="utf-8", errors="replace") as f:
            tail = f.read()[-1200:]
    except Exception:
        tail = ""

    if state == "timeout":
        if total:
            print(f"[{platform}] 部分采集：超时截断，已入库 {total} 条（帖 {posts} + 评 {comments}）"
                  f"→ {out_dir}")
            return 0, total, "partial"
        print(f"[{platform}] 未采集：超时 {timeout}s 且零产出——若多批均如此，"
              f"先查登录态（--login {platform}）而不是继续加时")
        return 2, 0, "timeout"

    rc_code = proc.returncode
    if rc_code != 0:
        reason = _classify_failure(rc_code, tail, posts, comments)
        if total:
            # v0.5 关键修复：非 0 退出但已有落盘数据 → 保全并判「部分采集」
            print(f"[{platform}] 部分采集：MediaCrawler 退出码 {rc_code}（{reason}）；"
                  f"已入库 {total} 条（帖 {posts} + 评 {comments}）→ {out_dir}")
            return 0, total, "partial"
        print(f"[{platform}] 未采集：MediaCrawler 退出码 {rc_code}（{reason}）")
        return 2, 0, "failed"

    if total == 0:
        print(f"[{platform}] 未采集：运行完成但未产出数据"
              f"（无搜索结果或登录态失效；关键词「{event}」可试更短/更通用的写法）")
        return 2, 0, "failed"
    print(f"[{platform}] 采集完成：{total} 条记录（帖 {posts} + 评 {comments}，{elapsed}s）→ {out_dir}")
    return 0, total, "ok"


def collect_external(platform, event, out_dir, max_posts, wb_cookie, xhs_cookie,
                     with_comments, timeout, heartbeat=HEARTBEAT_DEFAULT, hook=None):
    return run_mediacrawler(platform, event, out_dir, max_posts, wb_cookie, xhs_cookie,
                            with_comments, timeout, heartbeat=heartbeat, hook=hook)


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
    ap.add_argument("--event", default="", help="事件关键词/描述（单关键词；多关键词用 --keywords）")
    ap.add_argument("--keywords", default="",
                    help="多关键词，逗号分隔（v0.5）：一次调用串行跑完，各词独立目录 out/<platform>/<词>/，互不覆盖")
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
    ap.add_argument("--heartbeat", type=int, default=HEARTBEAT_DEFAULT,
                    help="进度心跳与进度文件刷新间隔秒数（v0.5，默认 15）")
    ap.add_argument("--probe", action="store_true",
                    help="最小探针（v0.5）：每平台每词只取 5 帖、不取评论、超时 180s，验证登录态与关键词是否有结果")
    ap.add_argument("--status", default="",
                    help="零副作用查询：读取 <目录>/_collect_progress.json 并打印当前进度（v0.5）")
    ap.add_argument("--out", default="data/default/raw")
    args = ap.parse_args()

    if args.sort_type:
        os.environ["MC_XHS_SORT_TYPE"] = args.sort_type

    out_root = os.path.join(SKILL_DIR, args.out) if not os.path.isabs(args.out) else args.out

    # 零副作用查询入口：不发起任何采集
    if args.status:
        st = progress_status(args.status if os.path.isabs(args.status)
                             else os.path.join(SKILL_DIR, args.status))
        return 0 if st else 1

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

    keywords = [k.strip() for k in args.keywords.split(",") if k.strip()] or \
               ([args.event.strip()] if args.event.strip() else [])
    if not keywords:
        print("[collect] 采集模式需要 --event 或 --keywords 提供关键词")
        return 1

    cfg = load_config()
    os.makedirs(out_root, exist_ok=True)

    plats = [p.strip() for p in args.platforms.split(",") if p.strip()]
    if args.probe:
        args.max_posts = min(args.max_posts, 5)
        args.with_comments = False
        args.timeout = min(args.timeout, 180)
        args.heartbeat = min(args.heartbeat, 10)
        print("[collect] 探针模式：每词 5 帖 · 不取评论 · 超时 %ds" % args.timeout)

    ext_kws = list(keywords)
    total_tasks = sum(1 for p in plats if p in cfg["platforms"]
                      for _ in ([None] if p == "zhihu" else ext_kws))
    print(f"[collect] keywords={keywords!r} platforms={plats} 任务数={total_tasks}")

    prog = {
        "event": " , ".join(keywords),
        "started": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "out": out_root,
        "platforms": plats,
        "mode": "probe" if args.probe else "full",
        "max_posts": args.max_posts,
        "with_comments": bool(args.with_comments) and not args.probe,
        "timeout_s": args.timeout,
        "total_tasks": total_tasks,
        "done_tasks": [],
        "current": {},
        "records": {"posts": 0, "comments": 0},
    }
    progress_write(out_root, prog)

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
            prog["current"] = {"platform": "zhihu", "keyword": "url", "elapsed_s": 0}
            progress_write(out_root, prog)
            zrc = collect_zhihu(args.url, os.path.join(out_root, "zhihu"), args.limit)
            rc = max(rc, zrc)
            prog["done_tasks"].append({"platform": "zhihu", "keyword": args.url[:60],
                                       "state": "ok" if zrc == 0 else "failed",
                                       "posts": 0, "comments": 0, "note": "知乎回答（md）"})
            prog["current"] = {}
            progress_write(out_root, prog)
            continue

        for kw in ext_kws:
            kw_dir = os.path.join(out_root, p, _slug(kw))
            prog["current"] = {"platform": p, "keyword": kw, "elapsed_s": 0}
            progress_write(out_root, prog)

            def hook(posts, comments, elapsed, _p=p, _kw=kw):
                prog["current"] = {"platform": _p, "keyword": _kw, "elapsed_s": elapsed,
                                   "posts": posts, "comments": comments}
                prog["records"] = {"posts": posts, "comments": comments}
                progress_write(out_root, prog)

            prc, n, state = collect_external(
                p, kw, kw_dir, args.max_posts, args.wb_cookie, args.xhs_cookie,
                args.with_comments, args.timeout, heartbeat=args.heartbeat, hook=hook)
            posts, comments = _count_jsonl(kw_dir)
            note = {"ok": "完整", "partial": "部分（已保全落盘数据）",
                    "timeout": "超时且零产出", "failed": "失败",
                    "no_credential": "无凭证"}[state]
            prog["done_tasks"].append({"platform": p, "keyword": kw, "state": state,
                                       "posts": posts, "comments": comments, "note": note})
            prog["current"] = {}
            prog["records"] = {"posts": posts, "comments": comments}
            progress_write(out_root, prog)
            print("[progress] %d/%d 任务完成 · %s/%s → %s（帖 %d + 评 %d）"
                  % (len(prog["done_tasks"]), total_tasks, p, kw, note, posts, comments),
                  flush=True)
            # 部分采集（有数据）不判失败，只在汇总里标注
            if prc != 0:
                rc = max(rc, prc)

    partial = [t for t in prog["done_tasks"] if t["state"] == "partial"]
    failed = [t for t in prog["done_tasks"]
              if t["state"] in ("failed", "timeout", "no_credential")]
    prog["current"] = {}
    prog["finished"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    prog["summary"] = {
        "tasks": len(prog["done_tasks"]), "partial": len(partial), "failed": len(failed),
        "by_state": {s: len([t for t in prog["done_tasks"] if t["state"] == s])
                     for s in ("ok", "partial", "timeout", "failed", "no_credential")},
    }
    progress_write(out_root, prog)

    tail = ""
    if partial:
        tail += f"；{len(partial)} 个任务为部分采集（已保全数据，请在覆盖声明中标注）"
    if failed:
        tail += f"；{len(failed)} 个任务未采集"
    print("[collect] 完成%s" % tail)
    print(f"[collect] 进度文件：{progress_path(out_root)}"
          f"（随时用 collect.py --status {args.out} 查询）")
    return rc


if __name__ == "__main__":
    sys.exit(main())
