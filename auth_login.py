# -*- coding: utf-8 -*-
"""HKU 邮箱 OAuth2 设备码授权（一次性登录）。

微软已于 2022-10 起废弃 IMAP 的旧式密码认证，必须使用 OAuth2。
本脚本走「设备码流程」：终端给你一个短码，
你在浏览器打开提示的网址输入该码，用 student@university.edu 登录即可。

会依次尝试两种授权方案（config.json 的 auth.profiles）：
  1. imap  —— 港大 ITS 官方推荐的 Thunderbird 通道，能读邮件
  2. graph —— Microsoft Graph，能额外拿到每封邮件在网页版里的精确链接
第一个成功的会被记住，后续自动静默刷新。

用法：
    python auth_login.py                      # 自动按顺序尝试
    python auth_login.py --profile graph      # 只试某个方案
    python auth_login.py --silent             # 只检查现有凭证是否有效
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys

import msal

from common import (
    TOKEN_CACHE_PATH,
    get_logger,
    load_config,
    now_cn,
    read_json,
    write_json,
)

log = get_logger("auth")


def _copy_to_clipboard(text: str) -> bool:
    """把设备码放进剪贴板，用户直接粘贴即可（无需装第三方库）。"""
    if not text:
        return False
    try:
        proc = subprocess.run(["clip"], input=text.encode("utf-8"),
                              shell=True, timeout=10,
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return proc.returncode == 0
    except Exception:  # noqa: BLE001
        return False


def _open_browser(url: str) -> bool:
    """用默认浏览器打开授权页。"""
    if not url:
        return False
    try:
        if hasattr(os, "startfile"):          # Windows
            os.startfile(url)                 # noqa: S606
        else:
            import webbrowser
            webbrowser.open(url)
        return True
    except Exception:  # noqa: BLE001
        return False


def _build_cache() -> msal.SerializableTokenCache:
    cache = msal.SerializableTokenCache()
    if TOKEN_CACHE_PATH.exists():
        cache.deserialize(TOKEN_CACHE_PATH.read_text(encoding="utf-8"))
    return cache


def _persist(cache: msal.SerializableTokenCache, profile: dict, cfg: dict, result: dict) -> None:
    text = cache.serialize()
    meta = read_json(TOKEN_CACHE_PATH, {}) or {}
    meta.update({k: v for k, v in meta.items() if k.startswith("__")})
    meta["__profile__"] = profile["name"]
    meta["__client_id__"] = profile["client_id"]
    meta["__mailbox__"] = cfg["mailbox"]
    meta["__authorized_at__"] = now_cn().isoformat()
    meta["__account__"] = (result.get("id_token_claims") or {}).get("preferred_username", cfg["mailbox"])
    TOKEN_CACHE_PATH.write_text(text, encoding="utf-8")
    # msal 的缓存与我们的元数据写在同一个文件里：先写缓存，再把元数据合并进去
    data = read_json(TOKEN_CACHE_PATH, {}) or {}
    data.update(meta)
    write_json(TOKEN_CACHE_PATH, data)


def get_profile(cfg: dict, name: str | None = None) -> dict | None:
    profiles = cfg["auth"]["profiles"]
    if not name:
        return None
    for p in profiles:
        if p["name"] == name:
            return p
    return None


def try_silent(cfg: dict, cache: msal.SerializableTokenCache):
    """用缓存里的 refresh token 静默换 access token。"""
    meta = read_json(TOKEN_CACHE_PATH, {}) or {}
    client_id = meta.get("__client_id__")
    profile_name = meta.get("__profile__")
    if not client_id:
        return None, None
    profile = get_profile(cfg, profile_name) or cfg["auth"]["profiles"][0]
    scopes = profile["scopes"]
    try:
        app = msal.PublicClientApplication(client_id, authority=cfg["auth"]["authority"], token_cache=cache)
        accounts = app.get_accounts()
        if not accounts:
            return None, None
        result = app.acquire_token_silent(scopes, account=accounts[0])
        if result and "access_token" in result:
            if cache.has_state_changed:
                TOKEN_CACHE_PATH.write_text(cache.serialize(), encoding="utf-8")
                data = read_json(TOKEN_CACHE_PATH, {}) or {}
                data.update(meta)
                write_json(TOKEN_CACHE_PATH, data)
            log.info("静默获取 Token 成功（方案=%s）", profile_name)
            return result, profile
    except Exception as exc:  # noqa: BLE001
        log.debug("静默刷新失败: %s", exc)
    return None, None


def interactive_login(cfg: dict, cache: msal.SerializableTokenCache,
                      only_profile: str | None, only_client: str | None,
                      max_codes: int = 1):
    """max_codes：同一方案最多连续发几个设备码。

    设备码只有 15 分钟寿命，用户可能刚好不在电脑前。
    设成 >1 时，码一过期就自动补发新码（重新写剪贴板 + 开浏览器），
    用户不用掐着时间，也不用反复重跑脚本。
    """
    profiles = cfg["auth"]["profiles"]
    if only_profile:
        # --profile graph 既匹配 name=graph，也匹配 family=graph（graph_cli 等备用通道一起试）
        profiles = [p for p in profiles
                    if p["name"] == only_profile or p.get("family") == only_profile]
    mailbox = cfg["mailbox"]
    last_error = ""

    for idx, profile in enumerate(profiles, 1):
        client_id = only_client or profile["client_id"]
        print("\n" + "=" * 70)
        print(f"[方案 {idx}/{len(profiles)}] {profile.get('label', profile['name'])}")
        print("=" * 70)
        try:
            app = msal.PublicClientApplication(client_id, authority=cfg["auth"]["authority"],
                                               token_cache=cache)
        except Exception as exc:  # noqa: BLE001
            last_error = str(exc)
            print(f"无法初始化客户端：{exc}\n")
            continue

        for attempt in range(1, max_codes + 1):
            try:
                if attempt > 1:
                    print(f"\n--- 第 {attempt} 个设备码（上一个已过期，自动补发）---")
                flow = app.initiate_device_flow(scopes=profile["scopes"])
                if "user_code" not in flow:
                    last_error = str(flow.get("error_description") or flow)
                    print(f"无法发起设备码流程：{last_error[:300]}\n")
                    break

                print(flow["message"])
                print("-" * 70)
                print(f"登录账号： {mailbox}    （选「工作/学校账户」）")

                # 尽量替用户省事：自动开授权页 + 把设备码放进剪贴板
                uri = flow.get("verification_uri") or "https://login.microsoft.com/device"
                code = flow.get("user_code", "")
                copied = _copy_to_clipboard(code)
                opened = _open_browser(uri)
                print(f"已在浏览器打开授权页：{'是' if opened else '否（请手动打开）'} {uri}")
                print(f"设备码已复制到剪贴板：{'是，直接 Ctrl+V 粘贴即可' if copied else '否，请手动输入'}  [{code}]")
                mins = int(flow.get("expires_in", 900)) // 60
                tail = "（过期会自动补发新码）" if attempt < max_codes else "；过期需重新运行本脚本。"
                print(f"设备码有效期约 {mins} 分钟{tail}")
                print("=" * 70 + "\n", flush=True)

                result = app.acquire_token_by_device_flow(flow)
                if "access_token" in result:
                    _persist(cache, profile, cfg, result)
                    log.info("授权成功！方案=%s 账号=%s", profile["name"],
                             (result.get("id_token_claims") or {}).get("preferred_username", mailbox))
                    print(f"\n✅ 授权成功，使用的方案：{profile.get('label', profile['name'])}")
                    return True, profile

                last_error = str(result.get("error_description") or result)
                print(f"\n❌ 该次授权未完成：{last_error[:300]}\n")

                expired = ("AADSTS70016" in last_error or "AADSTS70019" in last_error
                           or "expired" in last_error.lower())
                if expired:
                    print("原因：设备码已过期（说明这个码没有被成功输入过）。")
                    if attempt < max_codes:
                        print("正在自动补发新码，请稍候……\n", flush=True)
                        continue
                    print("提示：重新运行本脚本即可拿到新码。\n")
                # 非过期类错误（租户拒绝、需要管理员批准等）重试无意义，直接换下一个方案
                break
            except Exception as exc:  # noqa: BLE001
                last_error = str(exc)
                print(f"\n❌ 该方案异常：{exc}\n")
                break

    print("\n" + "=" * 70)
    print("所有方案都未成功。最后一条错误信息：")
    print(last_error[:900])
    print(
        "\n排查建议：\n"
        "  1. 登录时选「工作/学校账户」，账号必须是 student@university.edu；\n"
        "  2. HKU 邮箱基于 Microsoft 365，IMAP 需在学校侧开启（HKU ITS 知识库有 enable-imap 说明）；\n"
        "  3. 若提示需要管理员批准（AADSTS65001 / consent required），\n"
        "     说明学校租户未授权该应用，请联系 HKU ITSC（ithelp@university.example）；\n"
        "  4. 也可以用 python auth_login.py --client-id <学校提供的应用ID> 指定应用。\n"
    )
    return False, None


def main() -> int:
    parser = argparse.ArgumentParser(description="HKU 邮箱 OAuth2 授权")
    parser.add_argument("--profile", default=None, choices=["imap", "graph"],
                        help="只尝试指定方案")
    parser.add_argument("--client-id", dest="client_id", default=None,
                        help="手动指定 Azure 应用（客户端）ID")
    parser.add_argument("--silent", action="store_true", help="仅检查凭证，不做交互登录")
    parser.add_argument("--codes", type=int, default=1,
                        help="同一方案最多连续发几个设备码（默认 1；设大一点可容忍码过期，自动补发）")
    parser.add_argument("--force", action="store_true",
                        help="忽略已有凭证，强制重新走交互登录（用于补授权另一条通道，如已有 IMAP 再补 Graph）")
    args = parser.parse_args()

    cfg = load_config()
    cache = _build_cache()

    # 已有凭证且未指定 --force 时直接复用；加 --force 才能给另一条通道补授权
    # （否则 try_silent 会用旧方案直接成功返回，永远走不到交互登录）
    if not args.force:
        result, profile = try_silent(cfg, cache)
        if result:
            print(f"已有有效凭证（方案：{profile.get('label', profile['name'])}），无需重新登录。")
            print("如需为另一条通道补授权，请加 --force。")
            return 0

    if args.silent:
        print("没有可用凭证，请先运行 python auth_login.py 完成一次授权。")
        return 2

    ok, _ = interactive_login(cfg, cache, args.profile, args.client_id,
                              max_codes=max(1, args.codes))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
