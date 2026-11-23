# -*- coding: utf-8 -*-
"""通过 IMAP + OAuth2(XOAUTH2) 抓取港大邮箱收件箱，落盘到 data/mail_cache.json。

用法：
    python fetch_mail.py                 # 抓取最近 7 天收件箱
    python fetch_mail.py --days 3        # 抓取最近 3 天
    python fetch_mail.py --demo          # 使用内置示例数据（无需登录，用于预览）
"""
from __future__ import annotations

import argparse
import base64
import email
import email.policy
import html
import imaplib
import os
import re
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

from common import (
    BASE_DIR,
    MAIL_CACHE_PATH,
    TOKEN_CACHE_PATH,
    get_logger,
    load_config,
    now_cn,
    write_json,
)

log = get_logger("fetch")

IMAP_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun",
               "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")

RE_HTML_TAG = re.compile(r"<(script|style)[^>]*>.*?</\1>", re.I | re.S)
RE_HTML_ANY = re.compile(r"<[^>]+>")
RE_BLANK = re.compile(r"\n{3,}")
RE_URL = re.compile(r"https?://[^\s<>\"')\]]+")
RE_QUOTE_CUT = re.compile(
    r"\n(?:-{2,}\s*(?:Original Message|原始邮件|转发邮件)\s*-{2,}"
    r"|On .{0,120}wrote:"
    r"|发件人[:：].{0,60}\n"
    r"|From: .{0,120}\nSent:)"
    , re.I)

# 收件箱常见的「非人工」发件人特征
NO_REPLY_HINTS = ("noreply", "no-reply", "donotreply", "do-not-reply",
                  "notification", "mailer", "bounce", "postmaster")


# --------------------------------------------------------------------------
# Token
# --------------------------------------------------------------------------
def get_credentials(cfg: dict, prefer: str | None = None) -> tuple[str, str, dict]:
    """静默刷新 access token。

    返回 (access_token, profile_name, profile_dict)。
    profile_name 为 'imap' / 'graph' / 'graph_cli'，决定用哪条取信通道。
    prefer='graph' 时优先尝试 Graph 系方案（拿精确链接），失败再退回 IMAP。
    """
    import json as _json

    import msal  # 延迟导入，--demo 模式无需依赖

    cache = msal.SerializableTokenCache()
    meta: dict = {}
    if TOKEN_CACHE_PATH.exists():
        raw = TOKEN_CACHE_PATH.read_text(encoding="utf-8")
        cache.deserialize(raw)
        try:
            meta = _json.loads(raw)
        except Exception:
            meta = {}

    profiles = {p["name"]: p for p in cfg["auth"]["profiles"]}
    named = [profiles[meta["__profile__"]]] if meta.get("__profile__") in profiles else []
    rest = [p for p in cfg["auth"]["profiles"] if p not in named]
    if prefer:
        first = [p for p in named + rest
                 if p.get("family") == prefer or p["name"] == prefer]
        order = first + [p for p in named + rest if p not in first]
    else:
        order = named + rest

    def _refresh_once():
        for profile in order:
            cid = meta.get("__client_id__") if profile["name"] == meta.get("__profile__") else profile["client_id"]
            try:
                app = msal.PublicClientApplication(cid, authority=cfg["auth"]["authority"],
                                                   token_cache=cache)
                accounts = app.get_accounts()
                if not accounts:
                    continue
                result = app.acquire_token_silent(profile["scopes"], account=accounts[0])
                if result and "access_token" in result:
                    if cache.has_state_changed:
                        TOKEN_CACHE_PATH.write_text(cache.serialize(), encoding="utf-8")
                        data = _json.loads(TOKEN_CACHE_PATH.read_text(encoding="utf-8"))
                        data.update({k: v for k, v in meta.items() if k.startswith("__")})
                        write_json(TOKEN_CACHE_PATH, data)
                    log.info("Token 刷新成功（方案=%s）", profile["name"])
                    return result["access_token"], profile["name"], profile
            except Exception as exc:  # noqa: BLE001
                log.debug("刷新失败(%s/%s): %s", profile["name"], str(cid)[:8], exc)
        return None

    got = _refresh_once()
    if got is None and not os.environ.get("no_proxy"):
        # 本机系统代理（例如 127.0.0.1:10090 的本地代理客户端）没在跑时，
        # requests 仍会按系统/注册表设置走代理，导致连不上 login.microsoftonline.com，
        # 异常被吞掉后表现为「没有可用的邮箱凭证」。这里绕过代理重试一次。
        os.environ["no_proxy"] = "*"
        os.environ["NO_PROXY"] = "*"
        log.warning("首次刷新失败，改为绕过系统代理（no_proxy=*）重试一次")
        got = _refresh_once()
    if got is not None:
        return got

    raise RuntimeError(
        "没有可用的邮箱凭证。请先运行：\n"
        "    python auth_login.py\n"
        "完成一次浏览器授权后即可自动刷新。"
    )


def get_access_token(cfg: dict) -> str:
    """兼容旧调用：只取 token。"""
    token, _name, _p = get_credentials(cfg)
    return token


# --------------------------------------------------------------------------
# 解析
# --------------------------------------------------------------------------
def html_to_text(raw_html: str) -> str:
    text = RE_HTML_TAG.sub(" ", raw_html)
    text = re.sub(r"<br\s*/?>|</p>|</div>|</tr>", "\n", text, flags=re.I)
    text = RE_HTML_ANY.sub(" ", text)
    text = html.unescape(text)
    text = text.replace("\xa0", " ").replace("\u200b", "")
    text = re.sub(r"[ \t]{2,}", " ", text)
    return RE_BLANK.sub("\n\n", text).strip()


def strip_quoted(text: str) -> str:
    """去掉引用回复，只保留本次新增内容。"""
    m = RE_QUOTE_CUT.search(text)
    if m and m.start() > 80:
        text = text[: m.start()]
    return text.strip()


def extract_body(msg) -> tuple[str, list[str], list[str]]:
    """返回 (纯文本正文, 附件名列表, 正文中的链接)。"""
    parts_text: list[str] = []
    attachments: list[str] = []
    is_html = False

    for part in msg.walk():
        ctype = part.get_content_type()
        disp = (part.get_content_disposition() or "").lower()
        filename = part.get_filename()
        if disp == "attachment" or (filename and ctype not in ("text/plain", "text/html")):
            if filename:
                attachments.append(str(filename))
            continue
        if ctype == "text/plain":
            try:
                parts_text.append(part.get_content())
            except Exception:
                try:
                    parts_text.append(part.get_payload(decode=True).decode("utf-8", "replace"))
                except Exception:
                    pass
        elif ctype == "text/html":
            if not parts_text:
                is_html = True
                try:
                    parts_text.append(html_to_text(part.get_content()))
                except Exception:
                    pass

    body = "\n".join(p for p in parts_text if p).strip()
    if is_html:
        body = body
    links = []
    for u in RE_URL.findall(body):
        u = u.rstrip(".,;")
        if u not in links:
            links.append(u)
    return body, attachments, links[:12]


def parse_message(raw: bytes, body_limit: int) -> dict:
    msg = email.message_from_bytes(raw, policy=email.policy.default)

    subject = ""
    try:
        subject = str(msg.get("Subject", "") or "").strip()
    except Exception:
        subject = "(无法解析的主题)"

    from_name, from_addr = "", ""
    try:
        from_hdr = msg.get("From")
        if from_hdr is not None:
            from_name = str(from_hdr.display_name or "").strip()
            from_addr = str(from_hdr.addresses[0].addr_spec or "").strip() if from_hdr.addresses else ""
    except Exception:
        from_raw = str(msg.get("From", ""))
        m = re.search(r"<([^>]+)>", from_raw)
        from_addr = m.group(1) if m else from_raw.strip()
        from_name = from_raw.split("<")[0].strip().strip('"')

    date_iso, date_ts = "", 0
    try:
        dt = parsedate_to_datetime(msg.get("Date"))
        if dt is not None:
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=now_cn().tzinfo)
            date_iso = dt.astimezone(now_cn().tzinfo).isoformat()
            date_ts = int(dt.timestamp())
    except Exception:
        pass

    def addr_list(header: str) -> list[str]:
        try:
            h = msg.get(header)
            if h is None:
                return []
            return [str(a.addr_spec) for a in h.addresses][:20]
        except Exception:
            return []

    body, attachments, links = extract_body(msg)
    body = strip_quoted(body)[:body_limit]

    return {
        "subject": subject,
        "from_name": from_name,
        "from_addr": from_addr,
        "to": addr_list("To"),
        "cc": addr_list("Cc"),
        "date_iso": date_iso,
        "date_ts": date_ts,
        "is_read": False,          # 由 FLAGS 覆盖
        "has_attachments": bool(attachments),
        "attachments": attachments[:20],
        "body": body,
        "links": links,
        "is_bulk": bool(msg.get("List-Unsubscribe")) or str(msg.get("Precedence", "")).lower() in ("bulk", "list", "junk"),
        "has_list_unsubscribe": bool(msg.get("List-Unsubscribe")),
        "message_id": str(msg.get("Message-ID", "") or ""),
        "importance": str(msg.get("Importance", "normal") or "normal").lower(),
        "flagged": str(msg.get("X-Microsoft-Flag-Status", "") or "").lower() == "flagged",
        "categories": [],
        "web_link": "",
        "link_source": "",
        "graph": False,
    }


def enrich_with_ews_links(cfg: dict, token: str, records: list[dict]) -> int:
    """用 EWS 给 IMAP 抓到的邮件补上「网页版精确链接」。

    IMAP 协议本身拿不到邮件的 ItemId，只能靠主题去搜；EWS 会直接给出 OWA 直达链接，
    并且**用的是同一支 token，不需要额外授权**（实测 HKU 租户可行）。
    失败一律静默降级（退化成按主题搜索），不能影响取信主流程。
    """
    if not records:
        return 0
    try:
        from ews_links import fetch_web_links, normalize_msgid
    except Exception as exc:  # noqa: BLE001
        log.debug("EWS 模块不可用：%s", exc)
        return 0

    days = int(cfg["fetch"]["days_back"]) + 7  # 多取一截，容忍时区/投递延迟
    try:
        mapping = fetch_web_links(token, days=days)
    except Exception as exc:  # noqa: BLE001
        log.warning("EWS 补精确链接失败（本次降级为按主题搜索）：%s", exc)
        return 0

    hit = 0
    for rec in records:
        url = mapping.get(normalize_msgid(rec.get("message_id", "")))
        if url:
            rec["web_link"] = url
            rec["link_source"] = "ews"
            hit += 1
    log.info("EWS 精确链接：%d/%d 封匹配成功", hit, len(records))
    return hit


# --------------------------------------------------------------------------
# IMAP
# --------------------------------------------------------------------------
def imap_connect(cfg: dict, token: str) -> imaplib.IMAP4_SSL:
    host = cfg["imap"]["host"]
    port = int(cfg["imap"]["port"])
    mailbox = cfg["mailbox"]
    auth_string = f"user={mailbox}\x01auth=Bearer {token}\x01\x01"
    conn = imaplib.IMAP4_SSL(host, port)
    try:
        conn.authenticate("XOAUTH2", lambda _: auth_string.encode("utf-8"))
    except imaplib.IMAP4.error as exc:
        raise RuntimeError(
            f"IMAP 认证失败：{exc}\n"
            "可能是学校邮箱未开启 IMAP，或凭证已失效（重新运行 auth_login.py）。"
        ) from exc
    log.info("IMAP 已连接 %s:%s (%s)", host, port, mailbox)
    return conn


def fetch_recent(cfg: dict, conn: imaplib.IMAP4_SSL) -> list[dict]:
    days = int(cfg["fetch"]["days_back"])
    limit = int(cfg["fetch"]["max_messages"])
    body_limit = int(cfg["fetch"]["body_chars"])
    folder = cfg["imap"]["folder"]

    typ, _ = conn.select(folder, readonly=True)   # readonly：不改动已读状态
    if typ != "OK":
        raise RuntimeError(f"无法打开文件夹 {folder}")

    since = now_cn() - timedelta(days=days)
    imap_date = f"{since.day:02d}-{IMAP_MONTHS[since.month - 1]}-{since.year}"
    typ, data = conn.search(None, f'(SINCE "{imap_date}")')
    if typ != "OK":
        raise RuntimeError("IMAP 搜索失败")
    ids = data[0].split()
    log.info("SINCE %s 命中 %d 封，取最近 %d 封", imap_date, len(ids), limit)
    ids = ids[-limit:]

    records: list[dict] = []
    for num in reversed(ids):               # 新的在前
        try:
            typ, resp = conn.fetch(num, "(FLAGS BODY.PEEK[])")
            if typ != "OK":
                continue
            flags_blob, raw = b"", None
            for item in resp:
                if isinstance(item, tuple):
                    flags_blob += item[0] or b""
                    raw = item[1]
                elif isinstance(item, (bytes, bytearray)):
                    flags_blob += bytes(item)
            if not raw:
                continue
            rec = parse_message(raw, body_limit)
            rec["is_read"] = b"\\Seen" in flags_blob
            rec["uid"] = num.decode("ascii", "ignore")
            records.append(rec)
        except Exception as exc:  # noqa: BLE001
            log.warning("跳过一封邮件 %s: %s", num, exc)

    records.sort(key=lambda r: r.get("date_ts", 0), reverse=True)
    return records


def graph_fetch(cfg: dict, token: str) -> list[dict]:
    """通过 Microsoft Graph 读取收件箱。

    比 IMAP 多拿到的关键信息：
      - webLink：每封邮件在网页版里的**精确打开链接**（点进去直达那一封，不用再搜索）
      - importance / flag：微软侧的「高重要性」「已加旗标」信号，可用于打分
    """
    import requests

    days = int(cfg["fetch"]["days_back"])
    limit = min(int(cfg["fetch"]["max_messages"]), 200)
    body_limit = int(cfg["fetch"]["body_chars"])
    since = (now_cn() - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")

    url = "https://graph.microsoft.com/v1.0/me/mailFolders/inbox/messages"
    params = {
        "$top": str(min(limit, 100)),
        "$orderby": "receivedDateTime desc",
        "$filter": f"receivedDateTime ge {since}",
        "$select": ("id,internetMessageId,conversationId,subject,from,toRecipients,ccRecipients,"
                    "receivedDateTime,isRead,hasAttachments,importance,flag,categories,"
                    "bodyPreview,body,webLink"),
    }
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}

    items: list[dict] = []
    next_url = url
    while next_url and len(items) < limit:
        resp = requests.get(next_url, headers=headers,
                            params=params if next_url == url else None, timeout=60)
        if resp.status_code == 401:
            raise RuntimeError("Graph 授权已失效，请重新运行 auth_login.py")
        if resp.status_code >= 400:
            raise RuntimeError(f"Graph 请求失败 {resp.status_code}: {resp.text[:300]}")
        payload = resp.json()
        items.extend(payload.get("value", []))
        next_url = payload.get("@odata.nextLink")
        params = None

    log.info("Graph 返回 %d 封（SINCE %s）", len(items), since)

    records: list[dict] = []
    for m in items[:limit]:
        body_obj = m.get("body") or {}
        content = body_obj.get("content") or ""
        if (body_obj.get("contentType") or "").lower() == "html":
            body = html_to_text(content)
        else:
            body = content
        body = strip_quoted(body)[:body_limit]

        frm = (m.get("from") or {}).get("emailAddress") or {}
        to_list = [((r.get("emailAddress") or {}).get("address") or "")
                   for r in (m.get("toRecipients") or [])]
        cc_list = [((r.get("emailAddress") or {}).get("address") or "")
                   for r in (m.get("ccRecipients") or [])]
        dt_iso = m.get("receivedDateTime") or ""
        ts = 0
        if dt_iso:
            try:
                from datetime import datetime as _dt
                ts = int(_dt.fromisoformat(dt_iso.replace("Z", "+00:00")).timestamp())
            except Exception:
                ts = 0

        links = []
        for u in RE_URL.findall(body):
            u = u.rstrip(".,;")
            if u not in links:
                links.append(u)

        records.append({
            "uid": m.get("id") or "",
            "subject": m.get("subject") or "",
            "from_name": frm.get("name") or "",
            "from_addr": (frm.get("address") or "").lower(),
            "to": [x for x in to_list if x][:20],
            "cc": [x for x in cc_list if x][:20],
            "date_iso": dt_iso,
            "date_ts": ts,
            "is_read": bool(m.get("isRead")),
            "has_attachments": bool(m.get("hasAttachments")),
            "attachments": [],
            "body": body,
            "body_preview_raw": m.get("bodyPreview") or "",
            "links": links[:12],
            "is_bulk": False,
            "has_list_unsubscribe": False,
            "message_id": m.get("internetMessageId") or "",
            "conversation_id": m.get("conversationId") or "",
            "importance": (m.get("importance") or "normal").lower(),
            "flagged": ((m.get("flag") or {}).get("flagStatus") or "").lower() == "flagged",
            "categories": m.get("categories") or [],
            "web_link": m.get("webLink") or "",
            "link_source": "graph" if m.get("webLink") else "",
            "graph": True,
        })

    records.sort(key=lambda r: r.get("date_ts", 0), reverse=True)
    return records


# --------------------------------------------------------------------------
# EWS（备用通道：IMAP 993 端口被网络阻断时使用）
# --------------------------------------------------------------------------
_EWS_URL = "https://outlook.office365.com/EWS/Exchange.asmx"
_EWS_NS = {
    "s": "http://schemas.xmlsoap.org/soap/envelope/",
    "m": "http://schemas.microsoft.com/exchange/services/2006/messages",
    "t": "http://schemas.microsoft.com/exchange/services/2006/types",
}
_EWS_HEAD = """<?xml version="1.0" encoding="utf-8"?>
<soap:Envelope xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
  xmlns:m="http://schemas.microsoft.com/exchange/services/2006/messages"
  xmlns:t="http://schemas.microsoft.com/exchange/services/2006/types"
  xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/">
  <soap:Header><t:RequestServerVersion Version="Exchange2013"/></soap:Header>
  <soap:Body>"""
_EWS_TAIL = "</soap:Body></soap:Envelope>"

_EWS_FIND = _EWS_HEAD + """
    <m:FindItem Traversal="Shallow">
      <m:ItemShape>
        <t:BaseShape>IdOnly</t:BaseShape>
        <t:AdditionalProperties>
          <t:FieldURI FieldURI="item:Subject"/>
          <t:FieldURI FieldURI="item:DateTimeReceived"/>
          <t:FieldURI FieldURI="message:InternetMessageId"/>
          <t:FieldURI FieldURI="item:WebClientReadFormQueryString"/>
        </t:AdditionalProperties>
      </m:ItemShape>
      <m:IndexedPageItemView MaxEntriesReturned="{page}" Offset="{offset}" BasePoint="Beginning"/>
      <m:SortOrder><t:FieldOrder Order="Descending">
        <t:FieldURI FieldURI="item:DateTimeReceived"/>
      </t:FieldOrder></m:SortOrder>
      <m:ParentFolderIds><t:DistinguishedFolderId Id="inbox"/></m:ParentFolderIds>
    </m:FindItem>""" + _EWS_TAIL

_EWS_GET = _EWS_HEAD + """
    <m:GetItem>
      <m:ItemShape><t:BaseShape>Default</t:BaseShape></m:ItemShape>
      <m:ItemIds>{ids}</m:ItemIds>
    </m:GetItem>""" + _EWS_TAIL


def _ews_post(token: str, body: str, action: str, timeout: int = 90):
    import requests

    resp = requests.post(
        _EWS_URL,
        data=body.encode("utf-8"),
        headers={
            "Authorization": "Bearer " + token,
            "Content-Type": "text/xml; charset=utf-8",
            "SOAPAction": f'"{action}"',
        },
        timeout=timeout,
    )
    if resp.status_code != 200:
        raise RuntimeError(f"EWS HTTP {resp.status_code}: {resp.text[:200]}")
    return ET.fromstring(resp.content)


def _ews_ts(text: str) -> int:
    """EWS 的 DateTimeReceived（ISO8601、通常 UTC）转 unix 时间戳。"""
    try:
        dt = datetime.fromisoformat((text or "").replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return int(dt.timestamp())
    except Exception:
        return 0


def ews_fetch(cfg: dict, token: str) -> list[dict]:
    """通过 EWS 读取收件箱（IMAP 无法连接时的备用通道）。

    走 https://outlook.office365.com/EWS/Exchange.asmx，与 IMAP 共用同一支 token，
    不需要额外授权。实测该域名 443 可用，而 993 端口在某些网络下会被阻断。
    """
    days = int(cfg["fetch"]["days_back"])
    limit = int(cfg["fetch"]["max_messages"])
    body_limit = int(cfg["fetch"]["body_chars"])
    cutoff = int((now_cn() - timedelta(days=days)).timestamp())

    find_action = "http://schemas.microsoft.com/exchange/services/2006/messages/FindItem"
    get_action = "http://schemas.microsoft.com/exchange/services/2006/messages/GetItem"

    # 1) 找出收件箱里时间窗内的邮件 Id（新的在前）
    picked: list[dict] = []
    offset = 0
    page = 100
    while len(picked) < limit:
        root = _ews_post(token, _EWS_FIND.format(page=page, offset=offset), find_action)
        node = root.find(".//m:FindItemResponseMessage", _EWS_NS)
        if node is None or node.get("ResponseClass") != "Success":
            raise RuntimeError("EWS FindItem 返回异常")
        folder = node.find("m:RootFolder", _EWS_NS)
        msgs = folder.findall(".//t:Message", _EWS_NS) if folder is not None else []
        if not msgs:
            break
        stop = False
        for msg in msgs:
            ts = _ews_ts(msg.findtext("t:DateTimeReceived", default="", namespaces=_EWS_NS))
            if ts and ts < cutoff:
                stop = True
                break
            iid = msg.find("t:ItemId", _EWS_NS)
            if iid is None:
                continue
            picked.append({
                "id": iid.get("Id") or "",
                "ck": iid.get("ChangeKey") or "",
                "subject": msg.findtext("t:Subject", default="", namespaces=_EWS_NS) or "",
                "received": msg.findtext("t:DateTimeReceived", default="", namespaces=_EWS_NS) or "",
                "msg_id": msg.findtext("t:InternetMessageId", default="", namespaces=_EWS_NS) or "",
                "web_link": msg.findtext("t:WebClientReadFormQueryString", default="", namespaces=_EWS_NS) or "",
            })
            if len(picked) >= limit:
                break
        if stop or (folder is not None and folder.get("IncludesLastItemInRange") == "true"):
            break
        offset += page

    if not picked:
        log.info("EWS 时间窗内没有邮件")
        return []

    # 2) 分批取详情（正文 + 发件人 + 已读/附件）
    records: list[dict] = []
    chunk = 20
    for i in range(0, len(picked), chunk):
        batch = picked[i:i + chunk]
        ids_xml = "".join(
            '<t:ItemId Id="{}" ChangeKey="{}"/>'.format(
                html.escape(b["id"], quote=True), html.escape(b["ck"], quote=True))
            for b in batch
        )
        root = _ews_post(token, _EWS_GET.format(ids=ids_xml), get_action)
        for msg in root.findall(".//t:Message", _EWS_NS):
            raw_body = msg.findtext("t:Body", default="", namespaces=_EWS_NS) or ""
            body = html_to_text(raw_body) if "<" in raw_body[:200] else raw_body
            body = strip_quoted(body).strip()[:body_limit]

            frm = msg.find("t:From/t:Mailbox", _EWS_NS)
            from_name = (frm.findtext("t:Name", default="", namespaces=_EWS_NS) or "") if frm is not None else ""
            from_addr = (frm.findtext("t:EmailAddress", default="", namespaces=_EWS_NS) or "").lower() if frm is not None else ""

            to_list, cc_list = [], []
            for box in msg.findall("t:ToRecipients/t:Mailbox", _EWS_NS):
                addr = (box.findtext("t:EmailAddress", default="", namespaces=_EWS_NS) or "").lower()
                if addr:
                    to_list.append(addr)
            for box in msg.findall("t:CcRecipients/t:Mailbox", _EWS_NS):
                addr = (box.findtext("t:EmailAddress", default="", namespaces=_EWS_NS) or "").lower()
                if addr:
                    cc_list.append(addr)

            # BaseShape=Default 不返回 DateTimeReceived，按 Sent → Created 兜底
            dt_txt = ""
            for tag in ("t:DateTimeReceived", "t:DateTimeSent", "t:DateTimeCreated"):
                dt_txt = msg.findtext(tag, default="", namespaces=_EWS_NS) or ""
                if dt_txt:
                    break
            ts = _ews_ts(dt_txt)
            dt_iso = ""
            if ts:
                dt_iso = datetime.fromtimestamp(ts, tz=now_cn().tzinfo).isoformat()

            links = []
            for u in RE_URL.findall(body):
                u = u.rstrip(".,;")
                if u not in links:
                    links.append(u)

            atts = []
            for a in msg.findall("t:Attachments/t:FileAttachment", _EWS_NS):
                name = a.findtext("t:Name", default="", namespaces=_EWS_NS) or ""
                if name:
                    atts.append(name)

            records.append({
                "uid": msg.find("t:ItemId", _EWS_NS).get("Id") if msg.find("t:ItemId", _EWS_NS) is not None else "",
                "subject": msg.findtext("t:Subject", default="", namespaces=_EWS_NS) or "",
                "from_name": from_name,
                "from_addr": from_addr,
                "to": to_list[:20],
                "cc": cc_list[:20],
                "date_iso": dt_iso,
                "date_ts": ts,
                "is_read": (msg.findtext("t:IsRead", default="", namespaces=_EWS_NS) or "").lower() == "true",
                "has_attachments": (msg.findtext("t:HasAttachments", default="", namespaces=_EWS_NS) or "").lower() == "true",
                "attachments": atts[:20],
                "body": body,
                "body_preview_raw": body[:200],
                "links": links[:12],
                "is_bulk": False,
                "has_list_unsubscribe": False,
                "message_id": msg.findtext("t:InternetMessageId", default="", namespaces=_EWS_NS) or "",
                "conversation_id": "",
                "importance": "normal",
                "flagged": False,
                "categories": [],
                "web_link": msg.findtext("t:WebClientReadFormQueryString", default="", namespaces=_EWS_NS) or "",
                "link_source": "ews",
                "graph": False,
            })

    # 用 FindItem 的结果补上缺失的 Message-ID / 网页链接（GetItem 偶尔不带）
    by_id = {b["id"]: b for b in picked}
    for rec in records:
        src = by_id.get(rec["uid"])
        if not src:
            continue
        if not rec["message_id"]:
            rec["message_id"] = src["msg_id"]
        if not rec["web_link"]:
            rec["web_link"] = src["web_link"]
        if not rec["subject"]:
            rec["subject"] = src["subject"]
        # 以收件时间（FindItem 的 DateTimeReceived）为准，比 Sent/Created 更贴近"到达时间"
        ts = _ews_ts(src["received"])
        if ts:
            rec["date_ts"] = ts
            rec["date_iso"] = datetime.fromtimestamp(ts, tz=now_cn().tzinfo).isoformat()

    records.sort(key=lambda r: r.get("date_ts", 0), reverse=True)
    log.info("EWS 取到 %d 封（时间窗 %d 天）", len(records), days)
    return records


def load_demo(cfg: dict) -> list[dict]:
    """示例数据，用于没登录时预览整条链路。"""
    import json

    demo_path = BASE_DIR / "fixtures" / "demo_mail.json"
    if not demo_path.exists():
        raise RuntimeError(f"缺少示例数据：{demo_path}")
    records = json.loads(demo_path.read_text(encoding="utf-8"))
    # 把示例日期平移到最近几天，保证「今天」看起来是新鲜的
    base = now_cn()
    for i, rec in enumerate(records):
        d = base - timedelta(hours=3 * i + 1)
        rec["date_iso"] = d.isoformat()
        rec["date_ts"] = int(d.timestamp())
        rec["uid"] = rec.get("uid") or f"demo-{i}"
    log.info("已载入 %d 封示例邮件（DEMO 模式）", len(records))
    return records


def main() -> int:
    parser = argparse.ArgumentParser(description="抓取港大邮箱收件箱")
    parser.add_argument("--days", type=int, default=None, help="覆盖配置里的抓取天数")
    parser.add_argument("--demo", action="store_true", help="使用内置示例数据")
    parser.add_argument("--source", default="auto", choices=["auto", "imap", "graph", "ews"],
                        help="取信通道：auto 按已授权方案自动选择，IMAP 连不上时自动切 EWS")
    args = parser.parse_args()

    cfg = load_config()
    if args.days:
        cfg["fetch"]["days_back"] = args.days

    if args.demo:
        records = load_demo(cfg)
        mode = "demo"
    else:
        token, profile_name, _profile = get_credentials(cfg, prefer="graph")
        source = args.source if args.source != "auto" else (
            "graph" if profile_name.startswith("graph") else "imap"
        )

        if source == "graph":
            records = graph_fetch(cfg, token)
            mode = "graph"
        elif source == "ews":
            records = ews_fetch(cfg, token)
            mode = "ews"
        else:
            try:
                conn = imap_connect(cfg, token)
            except (OSError, imaplib.IMAP4.error) as exc:
                # 993 端口被网络阻断（TLS 握手超时/重置）时，自动改走 EWS（443）
                log.warning("IMAP 连接失败，改走 EWS 通道：%s", exc)
                records = ews_fetch(cfg, token)
                mode = "ews"
            else:
                try:
                    records = fetch_recent(cfg, conn)
                finally:
                    try:
                        conn.logout()
                    except Exception:
                        pass
                mode = "imap"
                # IMAP 拿不到邮件在网页版里的唯一 ID；用同一支 token 走 EWS 补齐精确链接
                enrich_with_ews_links(cfg, token, records)

    payload = {
        "fetched_at": now_cn().isoformat(),
        "mode": mode,
        "mailbox": cfg["mailbox"],
        "days_back": cfg["fetch"]["days_back"],
        "count": len(records),
        "messages": records,
    }
    write_json(MAIL_CACHE_PATH, payload)
    log.info("已写入 %s（%d 封）", MAIL_CACHE_PATH.name, len(records))
    print(f"OK 抓取完成：{len(records)} 封邮件 -> data/mail_cache.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
