# encoding: utf-8
"""PC / Creator 登录后的最小手动测试入口。

直接修改下面三个常量，不使用 argparse：

- ``PLATFORM``: ``pc`` 或 ``creator``
- ``LOGIN_TYPE``: ``qrcode`` 或 ``phone``
- ``NOTE_URL``: 仅 PC 模式需要填写
"""

import json
from typing import Any

from apis.xhs_creator_apis import XHS_Creator_Apis
from apis.xhs_pc_apis import XHS_Apis
from xhs_utils.xhs_creator import XHSCreatorAuth
from xhs_utils.xhs_pc import XHSPcAuth


# 手动选择平台：pc（普通网页版）或 creator（创作者中心）。
PLATFORM = "creator"

# 手动选择登录方式：qrcode（二维码）或 phone（手机验证码）。
LOGIN_TYPE = "qrcode"

# 仅 PLATFORM="pc" 时填写完整笔记链接；不要提交仍有效的 xsec_token。
NOTE_URL = r"https://www.xiaohongshu.com/explore/6a3b5a0b000000002103ee67?xsec_token=ABqVVKFAe9O-N5nfGNLJ1M_pI4-T4Xf3tsOzXC0N4EovY=&xsec_source=pc_feed"


def _print_result(success: bool, message: str, result: Any) -> dict[str, Any]:
    output = {
        "success": success,
        "message": message,
        "result": result,
    }
    print(json.dumps(output, ensure_ascii=False, indent=2, default=str))
    if not success:
        raise RuntimeError(message)
    return output


def _fetch_and_print_pc_note(auth: XHSPcAuth, note_url: str) -> dict[str, Any]:
    """使用已登录的 PC Auth 获取并打印一篇笔记。"""
    api = XHS_Apis(auth).bootstrap()
    success, message, result = api.get_note_info(note_url)
    return _print_result(success, message, result)


def _fetch_and_print_creator_notes(
    auth: XHSCreatorAuth,
) -> dict[str, Any]:
    """使用已登录的 Creator Auth 获取并打印全部已发布作品。"""
    api = XHS_Creator_Apis(auth).bootstrap()
    success, message, result = api.get_all_posted_notes()
    return _print_result(success, message, result)


def pc_qrcode_login(note_url: str) -> dict[str, Any]:
    """PC 纯 HTTP 二维码登录，随后获取一篇笔记。"""
    auth = XHSPcAuth.from_qrcode_login(show_in_terminal=True)
    return _fetch_and_print_pc_note(auth, note_url)


def pc_phone_login(note_url: str) -> dict[str, Any]:
    """PC 纯 HTTP 手机验证码登录，随后获取一篇笔记。"""
    auth = XHSPcAuth.from_phone_login()
    return _fetch_and_print_pc_note(auth, note_url)


def creator_qrcode_login() -> dict[str, Any]:
    """Creator 纯 HTTP 二维码登录，随后获取已发布作品列表。"""
    auth = XHSCreatorAuth.from_qrcode_login(show_in_terminal=True)
    return _fetch_and_print_creator_notes(auth)


def creator_phone_login() -> dict[str, Any]:
    """Creator 纯 HTTP 手机验证码登录，随后获取已发布作品列表。"""
    auth = XHSCreatorAuth.from_phone_login()
    return _fetch_and_print_creator_notes(auth)


# 保留旧入口名，避免已有调用代码失效。
qrcode_login = pc_qrcode_login
phone_login = pc_phone_login


def main() -> None:
    if PLATFORM == "pc":
        if not NOTE_URL:
            raise ValueError('PLATFORM="pc" 时请先填写 NOTE_URL')
        if LOGIN_TYPE == "qrcode":
            pc_qrcode_login(NOTE_URL)
        elif LOGIN_TYPE == "phone":
            pc_phone_login(NOTE_URL)
        else:
            raise ValueError("LOGIN_TYPE 只支持 qrcode 或 phone")
        return

    if PLATFORM == "creator":
        if LOGIN_TYPE == "qrcode":
            creator_qrcode_login()
        elif LOGIN_TYPE == "phone":
            creator_phone_login()
        else:
            raise ValueError("LOGIN_TYPE 只支持 qrcode 或 phone")
        return

    raise ValueError("PLATFORM 只支持 pc 或 creator")


if __name__ == "__main__":
    main()
