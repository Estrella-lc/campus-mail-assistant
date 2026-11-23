# -*- coding: utf-8 -*-
"""关键邮件判定 + 中文摘要引擎。

读取 data/mail_cache.json，产出 data/digest.json：
  - 对每封邮件分类（截止/需回复、课程教务、活动报名、IT通知、其他）
  - 打分排序，挑出「关键邮件」
  - 抽取截止时间、是否需回复、下一步动作
  - 生成中文摘要（规则版，可被 AI 精修版覆盖）

用法：
    python analyze.py
    python analyze.py --days 1     # 只看今天的邮件
"""
from __future__ import annotations

import argparse
import re
import sys
import unicodedata
import uuid
from datetime import date, datetime, timedelta
from urllib.parse import quote

from common import (
    MAIL_CACHE_PATH,
    DIGEST_PATH,
    get_logger,
    load_config,
    now_cn,
    read_json,
    write_json,
)

log = get_logger("analyze")

MONTHS = {
    "jan": 1, "january": 1, "feb": 2, "february": 2, "mar": 3, "march": 3,
    "apr": 4, "april": 4, "may": 5, "jun": 6, "june": 6, "jul": 7, "july": 7,
    "aug": 8, "august": 8, "sep": 9, "sept": 9, "september": 9, "oct": 10,
    "october": 10, "nov": 11, "november": 11, "dec": 12, "december": 12,
}
MONTH_ALT = "|".join(sorted(MONTHS, key=len, reverse=True))

# --------------------------------------------------------------------------
# 分类规则
# --------------------------------------------------------------------------
CATEGORIES = [
    {
        "key": "deadline",
        "label": "截止 / 需回复",
        "color": "#d92d20",
        "base": 40,
        "keywords": [
            "deadline", "due date", "due on", "due by", "submit by", "submission",
            "no later than", "last day", "closing date", "close on", "closes on",
            "expires", "expiry", "expire", "overdue", "action required", "urgent",
            "reminder", "final notice", "before the deadline", "by the end of",
            "application deadline", "registration deadline", "reply by",
            "respond by", "confirm by", "register by", "sign up by",
            "截止", "最晚", "逾期", "报名截止", "请于",
        ],
    },
    {
        "key": "academic",
        "label": "课程教务",
        "color": "#175cd3",
        "base": 30,
        "keywords": [
            "course", "courses", "lecture", "tutorial", "seminar series", "exam",
            "examination", "quiz", "test", "assessment", "assignment", "homework",
            "grade", "grading", "transcript", "gpa", "registration", "enrolment",
            "enrollment", "add/drop", "course selection", "semester", "timetable",
            "syllabus", "curriculum", "thesis", "dissertation", "supervisor",
            "faculty of", "department of", "graduate school", "registry",
            "moodle", "sis", "hku portal", "academic", "credits", "lecturer",
            "professor", "teaching assistant", "class", "worksheet", "midterm",
            "final year project", "capstone", "scholarship", "study load",
            "group formation", "group information", "grouping", "group leader",
            "group member", "team formation", "team member", "study group",
            "课程", "考试", "成绩", "选课", "教务", "导师", "论文", "学分",
            "组队", "分组", "组员", "小组作业",
        ],
    },
    {
        "key": "event",
        "label": "活动 / 讲座报名",
        "color": "#7a5af8",
        "base": 27,
        "keywords": [
            "invitation", "invite", "invites you", "cordially", "rsvp",
            "event", "events", "seminar", "webinar", "workshop", "talk",
            "symposium", "conference", "forum", "career fair", "info session",
            "information session", "orientation", "networking", "sharing session",
            "guest lecture", "distinguished lecture", "open day", "exhibition",
            "competition", "hackathon", "recruitment talk", "sign up", "sign-up",
            "register now", "join us", "limited seats", "first-come-first-served",
            "讲座", "工作坊", "分享会", "招聘会", "报名参加", "活动",
        ],
    },
    {
        "key": "it",
        "label": "IT / 图书馆 / 校园通知",
        "color": "#667085",
        "base": 9,
        "keywords": [
            "itsc", "its ", "system maintenance", "scheduled maintenance",
            "service interruption", "service unavailable", "downtime",
            "password", "pin", "2fa", "mfa", "vpn", "wi-fi", "wifi", "network",
            "library", "libraries", "borrow", "renew", "e-resource", "e-journal",
            "newsletter", "bulletin", "announcement", "notice", "campus",
            "shuttle bus", "canteen", "sports centre", "student card",
            "数据安全", "系统维护", "图书馆", "校园通知",
        ],
    },
]

DEFAULT_CATEGORY = {"key": "other", "label": "其他", "color": "#98a2b3", "base": 5}

# 需要回复的信号
REPLY_PATTERNS = [
    r"please\s+(?:kindly\s+)?(?:reply|respond|confirm|let\s+us\s+know|advise|inform)",
    r"kindly\s+(?:reply|respond|confirm)",
    r"reply\s+to\s+this\s+email",
    r"respond\s+by",
    r"let\s+me\s+know",
    r"need\s+your\s+(?:slides|input|response|confirmation|feedback|reply|decision)",
    r"can\s+you\s+(?:send|share|confirm|check|provide)",
    r"your\s+(?:response|reply|confirmation)\s+is\s+(?:required|needed)",
    r"\brsvp\b",
    r"action\s+required",
    r"please\s+confirm",
    r"请回复",
]

# 紧急信号
URGENT_PATTERNS = [
    r"\burgent\b", r"\bimportant\b", r"\baction\s+required\b", r"\bfinal\s+notice\b",
    r"immediate\s+attention", r"\bcritical\b", r"紧?急", r"重要通知",
]

# 营销 / 群发信号
MASS_HINTS = ["newsletter", "bulletin", "digest", "weekly", "monthly", "no-reply",
              "noreply", "donotreply", "do-not-reply", "notification", "campaign",
              "marketing", "survey", "promotion"]

# 日期正则
RE_DATE_DMY = re.compile(rf"\b(\d{{1,2}})\s+({MONTH_ALT})[a-z]*\.?,?\s+(\d{{4}})\b", re.I)
RE_DATE_MDY = re.compile(rf"\b({MONTH_ALT})[a-z]*\.?\s+(\d{{1,2}})(?:st|nd|rd|th)?,?\s+(\d{{4}})\b", re.I)
RE_DATE_ISO = re.compile(r"\b(\d{4})[-/](\d{1,2})[-/](\d{1,2})\b")
RE_DATE_SLASH = re.compile(r"\b(\d{1,2})/(\d{1,2})/(\d{4})\b")
RE_DMY_NOYEAR = re.compile(rf"\b(\d{{1,2}})\s+({MONTH_ALT})[a-z]*\.?\b(?!\s*\d{{4}})", re.I)
RE_TIME = re.compile(r"\b(\d{1,2}):(\d{2})\s*(?:(am|pm))?\b", re.I)
RE_DEADLINE_CTX = re.compile(
    r"deadline|due|no\s+later\s+than|submit|submission|close[sd]?\s+on|before|by\s+the\s+end|"
    r"expire[sd]?|last\s+day|closing\s+date|截止|最晚|之前|逾期",
    re.I,
)

RE_SENT_SPLIT = re.compile(r"(?<=[.!?。！？])\s+|\n+")

# 术语中英对照（用于规则版中文摘要里点出关键点）
TERM_CN = {
    "course selection": "选课", "add/drop": "加退选", "deadline": "截止时间",
    "assignment": "作业", "tutorial": "导修课", "lecture": "讲课",
    "examination": "考试", "exam": "考试", "quiz": "测验", "midterm": "期中考",
    "scholarship": "奖学金", "supervisor": "导师", "dissertation": "毕业论文",
    "thesis": "毕业论文", "seminar": "讲座", "webinar": "线上研讨会",
    "workshop": "工作坊", "career fair": "招聘会", "registration": "登记/报名",
    "maintenance": "系统维护", "password": "密码", "library": "图书馆",
    "transcript": "成绩单", "enrolment": "选课注册", "enrollment": "选课注册",
    "rsvp": "请回复确认", "attendance": "出席", "tuition fee": "学费",
    "orientation": "迎新活动", "info session": "说明会", "info session ": "说明会",
}


# --------------------------------------------------------------------------
# 工具
# --------------------------------------------------------------------------
def norm(text: str) -> str:
    text = unicodedata.normalize("NFKC", text or "")
    return text.replace("\u200b", " ").strip()


def count_hits(text_low: str, keywords: list[str]) -> tuple[int, list[str]]:
    hits = []
    for kw in keywords:
        if kw in text_low:
            hits.append(kw)
    return len(hits), hits


def classify(subject: str, body: str, sender_blob: str) -> dict:
    s_low = subject.lower()
    b_low = body.lower()
    blob = f"{s_low} {sender_blob.lower()}"

    best = DEFAULT_CATEGORY
    best_score = 0
    best_hits: list[str] = []
    for cat in CATEGORIES:
        n_s, hits_s = count_hits(s_low, cat["keywords"])
        n_b, hits_b = count_hits(b_low, cat["keywords"])
        n_from, hits_from = count_hits(blob, cat["keywords"])
        score = n_s * 4 + n_b * 1 + n_from * 1
        if score > best_score:
            best_score = score
            best = cat
            seen = []
            for h in hits_s + hits_from + hits_b:
                if h not in seen:
                    seen.append(h)
            best_hits = seen[:8]
    return {"category": best, "match_score": best_score, "hits": best_hits}


def extract_deadline(text: str, today: date):
    """从正文中找出最可能的截止日期。"""
    candidates: list[tuple[date, str, str]] = []   # (date, time_str, 上下文)
    sentences = [s.strip() for s in RE_SENT_SPLIT.split(text) if s.strip()]

    def add(y, m, d, time_str, ctx):
        try:
            dt = date(int(y), int(m), int(d))
        except ValueError:
            return
        candidates.append((dt, time_str, ctx))

    for sent in sentences:
        ctx = sent[:220]
        ctx_has_dl = bool(RE_DEADLINE_CTX.search(sent))
        time_str = ""
        tm = RE_TIME.search(sent)
        if tm:
            hh, mm, ap = tm.group(1), tm.group(2), (tm.group(3) or "").lower()
            hh = int(hh)
            if ap == "pm" and hh < 12:
                hh += 12
            if ap == "am" and hh == 12:
                hh = 0
            if 0 <= hh <= 23:
                time_str = f"{hh:02d}:{mm}"

        for m in RE_DATE_DMY.finditer(sent):
            add(m.group(3), MONTHS[m.group(2).lower()], m.group(1), time_str, ctx)
        for m in RE_DATE_MDY.finditer(sent):
            add(m.group(3), MONTHS[m.group(1).lower()], m.group(2), time_str, ctx)
        for m in RE_DATE_ISO.finditer(sent):
            add(m.group(1), m.group(2), m.group(3), time_str, ctx)
        for m in RE_DATE_SLASH.finditer(sent):
            d, mo = int(m.group(1)), int(m.group(2))
            if d > 12:
                add(m.group(3), mo, d, time_str, ctx)
            else:
                add(m.group(3), mo, d, time_str, ctx)
        if not candidates and ctx_has_dl:
            for m in RE_DMY_NOYEAR.finditer(sent):
                add(today.year, MONTHS[m.group(2).lower()], m.group(1), time_str, ctx)

    future = [c for c in candidates if c[0] >= today]
    if not future:
        return None
    # 优先：句子中含截止语义；其次：日期最近
    with_ctx = [c for c in future if RE_DEADLINE_CTX.search(c[2])]
    pool = with_ctx or future
    best = min(pool, key=lambda c: c[0])
    days_left = (best[0] - today).days
    return {
        "date": best[0].isoformat(),
        "time": best[1] or "",
        "days_left": days_left,
        "context": best[2].strip(),
        "is_urgent": days_left <= 3,
    }


def first_meaningful_sentence(body: str, limit: int = 240) -> str:
    for sent in RE_SENT_SPLIT.split(body):
        s = sent.strip(" \t-•*")
        if len(s) < 25:
            continue
        if re.match(r"^(dear|hi|hello|to whom it may concern)\b", s, re.I):
            continue
        return s[:limit]
    return body.strip()[:limit]


def cn_terms(text: str) -> list[str]:
    low = text.lower()
    found = []
    for en, cn in TERM_CN.items():
        if en.strip() in low and cn not in found:
            found.append(cn)
    return found


def sender_cn(name: str, addr: str) -> str:
    n = norm(name)
    if n:
        return n
    local = (addr or "").split("@")[0]
    return local or "未知发件人"


def is_personal_sender(addr: str, from_name: str, is_bulk: bool, subject: str) -> bool:
    addr_low = (addr or "").lower()
    if is_bulk:
        return False
    if any(h in addr_low for h in ("noreply", "no-reply", "donotreply", "do-not-reply",
                                   "notification", "mailer", "bounce", "postmaster")):
        return False
    blob = f"{from_name} {subject}".lower()
    if any(h in blob for h in MASS_HINTS):
        return False
    return True


def detect_reply_needed(subject: str, body: str) -> tuple[bool, list[str]]:
    blob = f"{subject}\n{body}"
    for p in REPLY_PATTERNS:
        if re.search(p, blob, re.I):
            return True, []
    return False, []


def score_email(rec: dict, today: date) -> dict:
    subject = norm(rec.get("subject", ""))
    body = norm(rec.get("body", ""))
    from_addr = rec.get("from_addr", "") or ""
    from_name = norm(rec.get("from_name", ""))
    is_bulk = bool(rec.get("is_bulk"))

    cls = classify(subject, body, f"{from_addr} {from_name}")
    cat = cls["category"]
    score = float(cat["base"])
    reasons: list[str] = [f"属于「{cat['label']}」类"]

    deadline = extract_deadline(f"{subject}\n{body}", today)
    if deadline:
        score += 14
        reasons.append("含明确截止时间")
        if deadline["days_left"] <= 1:
            score += 22
            reasons.append("截止就在今天/明天")
        elif deadline["days_left"] <= 3:
            score += 18
            reasons.append(f"仅剩 {deadline['days_left']} 天")
        elif deadline["days_left"] <= 7:
            score += 12
            reasons.append(f"剩 {deadline['days_left']} 天")
        else:
            score += 5

    needs_reply, _ = detect_reply_needed(subject, body)
    if needs_reply:
        score += 18
        reasons.append("需要你回复")

    urgent = bool(re.search("|".join(URGENT_PATTERNS), f"{subject}\n{body}", re.I))
    if urgent:
        score += 10
        reasons.append("含紧急措辞")

    if not rec.get("is_read", True):
        score += 5
        reasons.append("未读")

    personal = is_personal_sender(from_addr, from_name, is_bulk, subject)
    if personal:
        score += 8
        reasons.append("来自个人发件人")
    else:
        score -= 10

    # 真人（老师/职员/同学）直接点名要求你回复，权重远高于群发通知——
    # 这类邮件往往没有关键词、也没写 deadline，但漏掉代价很高。
    if personal and needs_reply:
        score += 12
        reasons.append("个人来信且要求回复，别漏")

    if is_bulk:
        score -= 8

    blob_low = f"{from_name} {subject}".lower()
    if any(h in blob_low for h in MASS_HINTS):
        score -= 6

    if rec.get("has_attachments"):
        score += 3
        reasons.append("带附件")

    # 微软侧的重要度信号（Graph 通道才能拿到）
    if str(rec.get("importance", "")).lower() == "high":
        score += 12
        reasons.append("发件人标记为高重要性")
    if rec.get("flagged"):
        score += 10
        reasons.append("已被标记旗标")

    return {
        "score": int(round(score)),
        "category_key": cat["key"],
        "category_label": cat["label"],
        "category_color": cat["color"],
        "keywords_hit": cls["hits"],
        "deadline": deadline,
        "needs_reply": needs_reply,
        "is_urgent": urgent,
        "is_personal": personal,
        "reasons_cn": reasons,
    }


def build_summary_cn(rec: dict, meta: dict, today: date) -> dict:
    """生成规则版中文摘要与建议动作。"""
    from_c = sender_cn(rec.get("from_name", ""), rec.get("from_addr", ""))
    subject = norm(rec.get("subject", ""))
    body = norm(rec.get("body", ""))
    cat_label = meta["category_label"]
    gist = first_meaningful_sentence(body)
    terms = cn_terms(f"{subject} {body}")
    dl = meta["deadline"]

    bits: list[str] = [f"「{from_c}」发来的{cat_label}类邮件"]
    if terms:
        bits.append("涉及：" + "、".join(terms[:4]))
    if dl:
        d = datetime.fromisoformat(dl["date"]).date()
        when = f"{d.month} 月 {d.day} 日"
        if dl["time"]:
            when += f" {dl['time']}"
        if dl["days_left"] == 0:
            bits.append(f"⚠️ 截止时间就是今天（{when}）")
        elif dl["days_left"] < 0:
            bits.append(f"截止时间 {when} 已过")
        else:
            bits.append(f"截止时间 {when}（还剩 {dl['days_left']} 天）")
    if meta["needs_reply"]:
        bits.append("邮件中明确要求回复")
    if meta["is_urgent"]:
        bits.append("原邮件标注为紧急/重要")

    summary_cn = "；".join(bits) + f"。\n主旨：{subject}"
    if gist:
        summary_cn += f"\n原文要点：{gist}"

    # 建议动作
    action_bits: list[str] = []
    if dl and dl["days_left"] >= 0:
        if dl["days_left"] == 0:
            action_bits.append("今天内完成")
        elif dl["days_left"] <= 3:
            action_bits.append(f"{dl['days_left']} 天内完成")
        else:
            action_bits.append(f"{dl['days_left']} 天内完成")
    if meta["needs_reply"]:
        action_bits.append("回复发件人")
    if rec.get("links"):
        action_bits.append("打开邮件里的链接办理")
    if not action_bits:
        action_bits.append("抽空浏览即可")

    return {
        "summary_cn": summary_cn,
        "action_cn": "、".join(action_bits),
        "gist": gist,
        "terms_cn": terms,
    }


RE_SUBJ_PREFIX = re.compile(
    r"^\s*(?:re|fw|fwd|答复|转发|回复)\s*[:：]\s*"
    r"|^\s*(\[[^\]\n]{1,24}\]|【[^】\n]{1,24}】)\s*"
    r"|^\s*(?:urgent|important|reminder|action\s+required|final\s+notice)\s*[:：]\s*",
    re.I,
)


def clean_subject_for_search(subject: str) -> str:
    """把主题清理成适合做站内搜索的关键词。"""
    s = subject or ""
    for _ in range(3):
        new = RE_SUBJ_PREFIX.sub("", s)
        if new == s:
            break
        s = new
    s = re.sub(r"[“”\"'（）()\[\]【】]+", " ", s)
    s = re.sub(r"\s+", " ", s).strip(" -–—·|:：,，.。")
    return s[:80]


def build_links(cfg: dict, rec: dict) -> dict:
    """生成跳转链接。

    优先用 Graph 返回的精确 webLink（点进去直达那一封邮件）；
    没有时退化为网页版搜索：先按清理后的主题搜，再按发件人搜。
    """
    web = cfg.get("webmail", {})
    inbox = web.get("inbox_url", "https://outlook.office365.com/mail/inbox")
    search = web.get("search_url", "https://outlook.office365.com/mail/search?q=")

    exact = (rec.get("web_link") or "").strip()
    subject = clean_subject_for_search(norm(rec.get("subject", "")))
    from_addr = (rec.get("from_addr") or "").strip()

    if exact:
        primary = exact
        exact_hit = True
    elif len(subject) >= 8:
        primary = search + quote(subject)
        exact_hit = False
    elif from_addr:
        primary = search + quote(f"from:{from_addr}")
        exact_hit = False
    else:
        primary = inbox
        exact_hit = False

    sender_link = search + quote(f"from:{from_addr}") if from_addr else inbox
    return {"web_link": primary, "sender_link": sender_link,
            "inbox_link": inbox, "link_is_exact": exact_hit}


def relative_day(date_iso: str, today: date) -> str:
    try:
        d = datetime.fromisoformat(date_iso).date()
    except Exception:
        return ""
    delta = (today - d).days
    if delta <= 0:
        return "今天"
    if delta == 1:
        return "昨天"
    if delta < 7:
        return f"{delta} 天前"
    return f"{d.month}/{d.day}"


# --------------------------------------------------------------------------
# 同一话题的重复推送合并（例：「2nd/3rd/.../6th Reminder: ECOM6008A Grouping」）
# --------------------------------------------------------------------------
_THREAD_PREFIX = re.compile(
    r"^\s*(?:(?:re|fwd|fw|答复|转发)\s*[:：]\s*)*"
    r"(?:(?:\[[^\]]{1,24}\]|【[^】]{1,24}】)\s*)*"
    r"(?:(?:\d+\s*(?:st|nd|rd|th)?\s*(?:reminder|notice|call)|reminder|提醒)\s*[:：\-—]?\s*)*",
    re.IGNORECASE,
)
_THREAD_TAIL = re.compile(
    r"\s*[\(（]\s*\d{1,2}\s*[-–/\s]?\s*"
    r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)\w*\s*(?:\d{4})?\s*[\)）]\s*$"
    r"|\s*[\(（]\s*\d{4}\s*[-–]\s*\d{4}\s*[\)）]\s*$",
    re.IGNORECASE,
)


def thread_key(subject: str) -> str:
    """把「Nth Reminder: 同一主题」归一成同一个 key，用于合并刷屏邮件。"""
    s = norm(subject)
    s = _THREAD_PREFIX.sub("", s)
    s = _THREAD_TAIL.sub("", s)
    s = re.sub(r"[^\w\u4e00-\u9fff]+", "", s).lower()
    return s


def merge_threads(scored: list[dict]) -> list[dict]:
    """同一话题只保留最有代表性的一封，并记录被合并的推送数量。"""
    buckets: dict[str, list[dict]] = {}
    order: list[str] = []
    for item in scored:
        k = thread_key(item.get("subject", "")) or item.get("id", "")
        if k not in buckets:
            buckets[k] = []
            order.append(k)
        buckets[k].append(item)

    merged: list[dict] = []
    for k in order:
        group = buckets[k]
        if len(group) == 1:
            group[0]["dup_count"] = 1
            merged.append(group[0])
            continue
        # 代表邮件：先看分数，再取最新的一封
        rep = sorted(group, key=lambda x: (-x["score"], -(x.get("date_ts", 0) or 0)))[0]
        dates = [g.get("date_iso", "") for g in group if g.get("date_iso")]
        rep["dup_count"] = len(group)
        rep["dup_first_iso"] = min(dates) if dates else ""
        rep["dup_last_iso"] = max(dates) if dates else ""
        rep["dup_titles"] = [g.get("subject", "") for g in group]
        merged.append(rep)
    merged.sort(key=lambda x: (-x["score"], -(x.get("date_ts", 0) or 0)))
    return merged


# --------------------------------------------------------------------------
# 主流程
# --------------------------------------------------------------------------
def main() -> int:
    parser = argparse.ArgumentParser(description="分析邮件并生成中文摘要")
    parser.add_argument("--days", type=int, default=None, help="只分析最近 N 天")
    parser.add_argument("--all", action="store_true", help="分析缓存中的全部邮件")
    args = parser.parse_args()

    cfg = load_config()
    cache = read_json(MAIL_CACHE_PATH)
    if not cache or not cache.get("messages"):
        print("没有可分析的邮件，请先运行 fetch_mail.py（或用 --demo 生成示例数据）。")
        return 2

    now = now_cn()
    today = now.date()
    messages = cache["messages"]

    if args.days and not args.all:
        cutoff = now - timedelta(days=args.days)
        messages = [m for m in messages if m.get("date_ts", 0) >= cutoff.timestamp()]

    scored: list[dict] = []
    is_demo = cache.get("mode") == "demo"
    inbox_url = cfg.get("webmail", {}).get("inbox_url", "https://outlook.office365.com/mail/inbox")
    for rec in messages:
        meta = score_email(rec, today)
        summ = build_summary_cn(rec, meta, today)
        links_info = build_links(cfg, rec)
        if is_demo:
            # 示例邮件在真实邮箱里并不存在，跳转统一指向收件箱，避免"点进去什么都没有"
            links_info = {"web_link": inbox_url, "sender_link": inbox_url,
                          "inbox_link": inbox_url, "link_is_exact": False,
                          "link_source": "demo"}
        else:
            links_info["link_source"] = rec.get("link_source") or "search"
        item = {
            "id": rec.get("uid") or rec.get("message_id") or str(uuid.uuid4())[:8],
            "subject": norm(rec.get("subject", "")),
            "from_name": norm(rec.get("from_name", "")) or rec.get("from_addr", ""),
            "from_addr": rec.get("from_addr", ""),
            "date_iso": rec.get("date_iso", ""),
            "date_ts": rec.get("date_ts", 0),
            "date_label": relative_day(rec.get("date_iso", ""), today),
            "is_read": bool(rec.get("is_read")),
            "has_attachments": bool(rec.get("has_attachments")),
            "attachments": rec.get("attachments", []),
            "body_preview": norm(rec.get("body", ""))[:400],
            "links": rec.get("links", [])[:5],
            "is_demo": is_demo,
            **links_info,
            **meta,
            **summ,
        }
        scored.append(item)

    scored.sort(key=lambda x: (-x["score"], x.get("date_ts", 0) or 0))

    # 合并同一话题的重复推送（连续多封 "Nth Reminder: ..." 只留一封），避免关键邮件被刷屏
    raw_count = len(scored)
    scored = merge_threads(scored)
    deduped = raw_count - len(scored)

    key_th = int(cfg["scoring"]["key_threshold"])
    watch_th = int(cfg["scoring"]["watch_threshold"])
    key_emails = [s for s in scored if s["score"] >= key_th]
    watch_emails = [s for s in scored if watch_th <= s["score"] < key_th]
    others = [s for s in scored if s["score"] < watch_th]

    # 分类统计
    cat_stats: dict[str, dict] = {}
    for s in scored:
        c = cat_stats.setdefault(s["category_key"], {
            "key": s["category_key"], "label": s["category_label"],
            "color": s["category_color"], "count": 0, "key_count": 0,
            "subjects": [],
        })
        c["count"] += 1
        if s["score"] >= key_th:
            c["key_count"] += 1
        if len(c["subjects"]) < 6:
            c["subjects"].append(s["subject"])

    # 截止时间汇总（只保留真正需要你动手的，排除系统维护等纯通知类日期）
    deadlines = [
        s for s in scored
        if s["deadline"] and (
            s["category_key"] in ("deadline", "academic", "event")
            or s["score"] >= watch_th
        )
    ]
    deadlines.sort(key=lambda s: s["deadline"]["date"])

    unread = sum(1 for s in scored if not s["is_read"])
    today_count = sum(1 for s in scored if s["date_label"] == "今天")

    # 中文总览
    head_bits = [f"本次共抓取 {raw_count} 封邮件（今天 {today_count} 封，未读 {unread} 封）"]
    if deduped:
        head_bits.append(f"其中 {deduped} 封是同一话题的重复提醒，已合并")
    if key_emails:
        head_bits.append(f"其中 {len(key_emails)} 封需要你处理")
    if deadlines:
        first = deadlines[0]["deadline"]
        d = datetime.fromisoformat(first["date"]).date()
        head_bits.append(f"最近一个截止时间是 {d.month} 月 {d.day} 日")
    if not key_emails:
        head_bits.append("没有发现需要立刻处理的邮件，可以放心")

    urgent_key = [s for s in key_emails if s["deadline"] and s["deadline"]["days_left"] <= 3]
    if urgent_key:
        head_bits.append(f"⚠️ 有 {len(urgent_key)} 封 3 天内截止，建议优先看")

    # 分类中文小结
    cat_summary_cn = []
    label_order = ["deadline", "academic", "event", "it", "other"]
    for k in label_order:
        c = cat_stats.get(k)
        if not c:
            continue
        if c["key_count"]:
            tail = f"其中 {c['key_count']} 封需重点处理"
        else:
            tail = "均为日常通知"
        sample = "、".join(f"《{s[:52]}》" for s in c["subjects"][:3])
        cat_summary_cn.append({
            "key": c["key"], "label": c["label"], "color": c["color"],
            "count": c["count"], "key_count": c["key_count"],
            "summary_cn": f"{c['label']}共 {c['count']} 封，{tail}。{sample}",
        })

    digest = {
        "run_id": now.strftime("%Y%m%d-%H%M%S"),
        "generated_at": now.isoformat(),
        "generated_at_label": now.strftime("%Y-%m-%d %H:%M"),
        "slot": "中午" if now.hour < 18 else "晚间",
        "mailbox": cfg["mailbox"],
        "source_mode": cache.get("mode", "imap"),
        "window": {
            "days_back": cache.get("days_back", cfg["fetch"]["days_back"]),
            "fetched_at": cache.get("fetched_at", ""),
        },
        "summary_source": "rule",
        "stats": {
            "total": len(scored),
            "raw_total": raw_count,
            "deduped": deduped,
            "key": len(key_emails),
            "watch": len(watch_emails),
            "unread": unread,
            "today": today_count,
            "deadline_count": len(deadlines),
            "urgent_key": len(urgent_key),
        },
        "headline_cn": "；".join(head_bits) + "。",
        "category_summary_cn": cat_summary_cn,
        "deadline_board": [
            {
                "subject": s["subject"],
                "from_name": s["from_name"],
                "date": s["deadline"]["date"],
                "date_label": f"{datetime.fromisoformat(s['deadline']['date']).month}月{datetime.fromisoformat(s['deadline']['date']).day}日"
                              + (f" {s['deadline']['time']}" if s["deadline"]["time"] else ""),
                "days_left": s["deadline"]["days_left"],
                "category_label": s["category_label"],
                "category_color": s["category_color"],
                "web_link": s["web_link"],
            }
            for s in deadlines[:8]
        ],
        "key_emails": key_emails,
        "watch_emails": watch_emails,
        "other_emails": [
            {
                "subject": s["subject"], "from_name": s["from_name"],
                "date_label": s["date_label"], "category_label": s["category_label"],
                "category_color": s["category_color"], "is_read": s["is_read"],
                "web_link": s["web_link"],
            }
            for s in others
        ],
        "ai": {"enriched": False, "notes_cn": ""},
    }

    write_json(DIGEST_PATH, digest)
    log.info("生成 digest.json：总 %d / 重点 %d / 关注 %d / 其他 %d",
             len(scored), len(key_emails), len(watch_emails), len(others))
    print(f"OK 分析完成：{len(scored)} 封，重点 {len(key_emails)} 封")
    print("总览：" + digest["headline_cn"])
    for s in key_emails[:6]:
        print(f"  [{s['score']:>3}] {s['category_label']} | {s['subject'][:60]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
