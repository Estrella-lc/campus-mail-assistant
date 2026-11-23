# -*- coding: utf-8 -*-
"""用 EWS（Exchange Web Services）给每封邮件补上「网页版精确链接」。

背景：IMAP 通道拿不到邮件的 ItemId，只能按主题搜索；Graph 通道需要额外授权
（在我们这个租户里会被要求管理员审批）。而 EWS 端点
`https://outlook.office365.com/EWS/Exchange.asmx` 会直接返回
`WebClientReadFormQueryString`——也就是 OWA 打开这一封邮件的直达链接。

实测：用 IMAP 通道那支 token（audience = outlook.office.com）访问 EWS 返回 200，
所以**不需要任何新的授权**，也不需要管理员审批。

匹配方式：用 EWS 的 `message:InternetMessageId` 对齐 IMAP 的 `Message-ID` 头。

注意：微软已宣布 EWS 在 Exchange Online 上逐步退役，所以本模块必须允许失败——
调用方拿不到结果时自动退回「按主题搜索」。
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET

import requests

NS = {
    "s": "http://schemas.xmlsoap.org/soap/envelope/",
    "m": "http://schemas.microsoft.com/exchange/services/2006/messages",
    "t": "http://schemas.microsoft.com/exchange/services/2006/types",
}
EWS_URL = "https://outlook.office365.com/EWS/Exchange.asmx"

_SOAP_TMPL = """<?xml version="1.0" encoding="utf-8"?>
<soap:Envelope xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
  xmlns:m="http://schemas.microsoft.com/exchange/services/2006/messages"
  xmlns:t="http://schemas.microsoft.com/exchange/services/2006/types"
  xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/">
  <soap:Header><t:RequestServerVersion Version="Exchange2013"/></soap:Header>
  <soap:Body>
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
      <m:SortOrder>
        <t:FieldOrder Order="Descending">
          <t:FieldURI FieldURI="item:DateTimeReceived"/>
        </t:FieldOrder>
      </m:SortOrder>
      <m:ParentFolderIds><t:DistinguishedFolderId Id="inbox"/></m:ParentFolderIds>
    </m:FindItem>
  </soap:Body>
</soap:Envelope>"""


def normalize_msgid(value: str) -> str:
    """把 Message-ID 归一化，便于 IMAP 与 EWS 两侧对齐。"""
    s = (value or "").strip().strip("<>").strip()
    return s.lower()


def _find_page(token: str, offset: int, page: int, timeout: int = 30) -> tuple[list[dict], bool]:
    """取一页收件箱邮件，返回 (记录列表, 是否还有下一页)。"""
    body = _SOAP_TMPL.format(page=page, offset=offset)
    resp = requests.post(
        EWS_URL,
        data=body.encode("utf-8"),
        headers={
            "Authorization": "Bearer " + token,
            "Content-Type": "text/xml; charset=utf-8",
            "SOAPAction": '"http://schemas.microsoft.com/exchange/services/2006/messages/FindItem"',
        },
        timeout=timeout,
    )
    if resp.status_code != 200:
        raise RuntimeError(f"EWS HTTP {resp.status_code}: {resp.text[:200]}")

    root = ET.fromstring(resp.text)
    node = root.find(".//m:FindItemResponseMessage", NS)
    if node is None:
        raise RuntimeError("EWS 返回结构异常")
    if node.get("ResponseClass") != "Success":
        code = node.findtext(".//m:ResponseCode", default="?", namespaces=NS)
        raise RuntimeError(f"EWS ResponseCode={code}")

    folder = node.find("m:RootFolder", NS)
    items: list[dict] = []
    if folder is not None:
        for msg in folder.findall(".//t:Message", NS):
            items.append({
                "subject": msg.findtext("t:Subject", default="", namespaces=NS) or "",
                "received": msg.findtext("t:DateTimeReceived", default="", namespaces=NS) or "",
                "msg_id": msg.findtext("t:InternetMessageId", default="", namespaces=NS) or "",
                "web_link": msg.findtext("t:WebClientReadFormQueryString", default="", namespaces=NS) or "",
            })
    more = (folder is not None and folder.get("IncludesLastItemInRange") == "false")
    return items, more


def fetch_web_links(token: str, days: int = 14, page: int = 250,
                    max_items: int = 1000) -> dict[str, str]:
    """返回 {归一化 Message-ID: OWA 直达链接}。

    失败时抛异常，由调用方决定是否降级（不要让它影响取信主流程）。
    """
    result: dict[str, str] = {}
    offset = 0
    while offset < max_items:
        items, more = _find_page(token, offset, page)
        if not items:
            break
        for it in items:
            key = normalize_msgid(it["msg_id"])
            if key and it["web_link"]:
                # EWS 返回的是 XML 转义过的字符串（&amp;），ElementTree 已解码
                result[key] = it["web_link"]
        offset += page
        if not more:
            break
    return result


def looks_like_owa_link(url: str) -> bool:
    return bool(re.match(r"^https://outlook\.office(365)?\.com/(owa|mail)/", url or ""))
