# -*- coding: utf-8 -*-
"""bai-bao-dai 工具自检（工具可用性最小尝试）。

按用户要求（如"自检 / 先测下工具 / 检查环境"）运行。两层：

- 离线层（--offline）：环境与本地工具链——用内置微型夹具跑通
  normalize → opinion → build_report，证明本地脚本完好；另报 check.py
  可选依赖 china_sources（po-xu-wang 未装则降级为 LLM 多源核查，不影响主流程）。
- 在线层（默认执行，--skip-online 跳过）：对网络采集工具做最小真实尝试——
  微博/小红书：各驱动 MediaCrawler 做 1 条关键词搜索（复用 collect.py 凭据检测）；
  知乎：cookie 文件存在性（静态）＋ 若 config.json 的 probe.zhihu_question
  配置了探测问题，则发 1 次 API 探测请求验证 cookie 真伪（HTTP 非 200 = cookie 过期）。

退出码：0 = 无失败（可含"skip"可选能力）；1 = 存在失败项。
输出：默认人类可读；--json 时仅输出 JSON 摘要，便于 agent 程序化解析。

失败处理（本脚本只报告，决策由调用方执行）：
  某工具失败时，正式任务开始前须询问用户：跳过该工具，或更新凭证
  （知乎：更新 zhihu/cookie.txt；微博/小红书：collect.py --login 扫码或提供 cookie）。
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile

SKILL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(SKILL_DIR, "scripts")
MC_DIR = os.path.join(SKILL_DIR, "external", "MediaCrawler")

# 离线夹具：极简中性文本（不绑定任何真实事件）
ZHIHU_MD = """# 老旧小区加装电梯，一楼住户反对怎么办？
> 问题ID：12345678 | 总回答数：2 | 实际爬取：2
> 来源：https://www.zhihu.com/question/12345678
---
## [1] 规划师
- 回答者ID：u1
- 赞同数：10
- 发布时间：2025-06-01 10:20
- 主页：https://www.zhihu.com/people/u1
- 回答链接：https://www.zhihu.com/question/12345678/answer/a1

支持加装电梯，方便老人出行。
---
## [2] 一楼住户
- 回答者ID：u2
- 赞同数：8
- 发布时间：2025-06-02 14:05
- 主页：https://www.zhihu.com/people/u2
- 回答链接：https://www.zhihu.com/question/12345678/answer/a2

一楼采光噪音受影响，不公平。
"""

WEIBO_JSONL = [
    {"note_id": "w1", "content": "支持加装，老人上下楼太难了", "create_date_time": "2025-06-01 08:00",
     "liked_count": "900", "comments_count": "30", "shared_count": "5",
     "note_url": "https://m.weibo.cn/detail/w1", "nickname": "微博用户A", "source_keyword": "自检"},
]
XHS_JSONL = [
    {"note_id": "x1", "title": "", "desc": "一楼反对加装", "time": "2025-06-01",
     "liked_count": "500", "comment_count": "20", "share_count": "3",
     "note_url": "https://www.xiaohongshu.com/explore/x1", "nickname": "小红用户C", "source_keyword": "自检"},
]
STANCES = {
    "支持加装": ["支持加装", "方便老人", "老人上下楼"],
    "一楼住户反对": ["一楼", "采光", "噪音", "不公平", "反对"],
}


def run_script(name, *args):
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    return subprocess.run([sys.executable, os.path.join(SCRIPTS, name), *args],
                          capture_output=True, text=True, encoding="utf-8",
                          errors="replace", env=env)


def offline_checks(results):
    """本地工具链最小尝试：normalize → opinion(--stances) → build_report。返回是否失败。"""
    any_fail = False
    tmp = tempfile.mkdtemp(prefix="bbd_selftest_offline_")
    try:
        raw = os.path.join(tmp, "raw")
        for plat in ("zhihu", "weibo", "xiaohongshu"):
            os.makedirs(os.path.join(raw, plat), exist_ok=True)
        with open(os.path.join(raw, "zhihu", "q.md"), "w", encoding="utf-8") as f:
            f.write(ZHIHU_MD)
        with open(os.path.join(raw, "weibo", "c.jsonl"), "w", encoding="utf-8") as f:
            f.write("\n".join(json.dumps(x, ensure_ascii=False) for x in WEIBO_JSONL) + "\n")
        with open(os.path.join(raw, "xiaohongshu", "c.jsonl"), "w", encoding="utf-8") as f:
            f.write("\n".join(json.dumps(x, ensure_ascii=False) for x in XHS_JSONL) + "\n")
        st_path = os.path.join(tmp, "stances.json")
        with open(st_path, "w", encoding="utf-8") as f:
            json.dump(STANCES, f, ensure_ascii=False, indent=2)
        facts = os.path.join(tmp, "facts.json")
        with open(facts, "w", encoding="utf-8") as f:
            json.dump({"event": "自检", "claims": []}, f, ensure_ascii=False)

        norm = os.path.join(tmp, "data.jsonl")
        opinion = os.path.join(tmp, "opinion.json")
        report = os.path.join(tmp, "report.html")

        steps = [
            ("normalize", ["--in", raw, "--out", norm, "--event", "自检"], norm),
            ("opinion", ["--in", norm, "--out", opinion, "--event", "自检",
                         "--stances", st_path], opinion),
            ("build_report", ["--event", "自检", "--facts", facts, "--opinion", opinion,
                              "--normalized", norm, "--out", report], report),
        ]
        for name, args, expect in steps:
            r = run_script(name + ".py", *args)
            ok = r.returncode == 0 and os.path.exists(expect)
            results.append({"tool": name, "layer": "offline", "status": "ok" if ok else "fail",
                            "detail": "" if ok else (r.stderr or r.stdout or "")[-300:]})
            any_fail |= not ok
            if not ok:
                return any_fail
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return any_fail


def zhihu_check(results):
    """知乎：cookie 静态检查 +（配置探测问题时的）1 次 API 网络探测。返回是否失败。"""
    crawl_py = os.path.join(SKILL_DIR, "zhihu", "crawl.py")
    cookie_path = os.path.join(SKILL_DIR, "zhihu", "cookie.txt")
    if not os.path.exists(crawl_py):
        results.append({"tool": "zhihu", "layer": "online", "status": "fail",
                        "detail": "zhihu/crawl.py 缺失"})
        return True
    cookie = ""
    if os.path.exists(cookie_path):
        with open(cookie_path, "r", encoding="utf-8") as f:
            cookie = f.read().strip()
    if not cookie:
        results.append({"tool": "zhihu", "layer": "online", "status": "fail",
                        "detail": "zhihu/cookie.txt 缺失或为空（需用户更新）"})
        return True

    qid = ""
    try:
        with open(os.path.join(SKILL_DIR, "config.json"), "r", encoding="utf-8") as f:
            qid = (json.load(f).get("probe") or {}).get("zhihu_question", "")
    except Exception:
        pass
    if not qid:
        results.append({"tool": "zhihu", "layer": "online", "status": "ok",
                        "detail": "cookie 文件就绪（未配置 probe.zhihu_question，网络验证留待真实任务）"})
        return False

    sys.path.insert(0, os.path.join(SKILL_DIR, "zhihu"))
    try:
        import crawl as zh
        url = ("https://www.zhihu.com/api/v4/questions/%s/answers?offset=0&limit=1"
               "&sort_by=default&platform=desktop" % qid)
        resp = zh.api_get(url)
        ok = resp is not None and "paging" in resp and resp.get("data")
        detail = "" if ok else "API 探测失败（HTTP 非 200 / 无数据 = cookie 失效或需登录验证）"
        results.append({"tool": "zhihu", "layer": "online", "status": "ok" if ok else "fail",
                        "detail": detail})
        return not ok
    except Exception as e:
        results.append({"tool": "zhihu", "layer": "online", "status": "fail",
                        "detail": "探测异常：%s" % e})
        return True


def mediacrawler_check(platform, results):
    """微博/小红书：1 条最小关键词搜索（复用 collect.py 凭据检测与驱动）。返回是否失败。"""
    sys.path.insert(0, SCRIPTS)
    try:
        import collect
    except Exception as e:
        results.append({"tool": platform, "layer": "online", "status": "fail",
                        "detail": "collect.py 导入失败：%s" % e})
        return True
    if not os.path.isdir(MC_DIR):
        results.append({"tool": platform, "layer": "online", "status": "fail",
                        "detail": "external/MediaCrawler 未部署"})
        return True
    ok, _lt, note = collect.credential_status(platform, "", "")
    if not ok:
        results.append({"tool": platform, "layer": "online", "status": "fail",
                        "detail": note + "（需 collect.py --login 扫码或提供 cookie）"})
        return True
    probe_dir = os.path.join(tempfile.gettempdir(), "bbd_selftest_online")
    os.makedirs(probe_dir, exist_ok=True)
    try:
        rc, n = collect.run_mediacrawler(platform, "测试", probe_dir, 1, "", "", False, 180)
        good = rc == 0 and n >= 1
        detail = ""
        if rc == 2:
            detail = "驱动失败（可能登录态失效/风控/无结果，见上方输出）"
        elif not good:
            detail = "未产出数据（rc=%s, n=%s）" % (rc, n)
        results.append({"tool": platform, "layer": "online", "status": "ok" if good else "fail",
                        "detail": detail})
        return not good
    finally:
        shutil.rmtree(probe_dir, ignore_errors=True)


def main():
    ap = argparse.ArgumentParser(description="bai-bao-dai 工具自检")
    ap.add_argument("--offline", action="store_true", help="仅跑离线层，跳过全部在线尝试")
    ap.add_argument("--skip-online", action="store_true", help="跳过在线层")
    ap.add_argument("--json", action="store_true", help="输出 JSON 摘要")
    args = ap.parse_args()

    results = []
    any_fail = offline_checks(results)
    if not (args.offline or args.skip_online):
        any_fail |= zhihu_check(results)
        any_fail |= mediacrawler_check("weibo", results)
        any_fail |= mediacrawler_check("xiaohongshu", results)

    # check.py 的可选依赖仅报告，不判失败
    sys.path.insert(0, SCRIPTS)
    try:
        import check
        has = bool(getattr(check, "HAS_CHINA", False))
        results.append({"tool": "check(china_sources)", "layer": "offline",
                        "status": "ok" if has else "skip",
                        "detail": "" if has else "po-xu-wang 未安装，核查降级为 LLM 多源"})
    except Exception as e:
        results.append({"tool": "check(china_sources)", "layer": "offline", "status": "skip",
                        "detail": "import 异常（不影响主流程）：%s" % e})

    if args.json:
        print(json.dumps({"exit_code": 1 if any_fail else 0, "results": results},
                         ensure_ascii=False, indent=2))
    else:
        for r in results:
            print("[%s/%s] %s%s" % (r["layer"], r["tool"], r["status"],
                                    ("：" + r["detail"]) if r.get("detail") else ""))
        print("")
        print("[selftest] 结论：" + ("存在失败项（详见上）。正式任务前请询问用户：跳过该工具 or 更新凭证。"
                                    if any_fail else "全部通过/就绪（skip 为可选能力，不影响主流程）。"))
    return 1 if any_fail else 0


if __name__ == "__main__":
    sys.exit(main())
