# -*- coding: utf-8 -*-
"""每日一键运行：抓取邮件 -> 分析 -> 生成中文摘要 -> 可选弹系统通知。

这是定时任务和手动运行共用的入口。

用法：
    python run_daily.py                # 抓取 + 分析
    python run_daily.py --notify      # 顺便弹 Windows 通知
    python run_daily.py --days 1      # 只看最近 1 天
    python run_daily.py --demo        # 用示例数据（无需登录）
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

from common import BASE_DIR, DIGEST_PATH, get_logger, now_cn, read_json

log = get_logger("daily")
PY = sys.executable


def run_step(script: str, args: list[str]) -> int:
    cmd = [PY, str(BASE_DIR / script), *args]
    log.info("执行: %s", " ".join(cmd))
    proc = subprocess.run(cmd, cwd=str(BASE_DIR))
    return proc.returncode


def notify_via_windows(title: str, body: str) -> None:
    """调用 notify.ps1 弹系统通知（尽力而为，失败不影响主流程）。"""
    ps1 = BASE_DIR / "notify.ps1"
    if not ps1.exists():
        return
    ps_exe = Path(r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe")
    exe = ps_exe if ps_exe.exists() else Path("powershell.exe")
    try:
        subprocess.run(
            [str(exe), "-NoProfile", "-ExecutionPolicy", "Bypass",
             "-File", str(ps1), "-Title", title, "-Body", body],
            timeout=30, creationflags=subprocess.CREATE_NO_WINDOW,
        )
        log.info("系统通知已发送")
    except Exception as exc:  # noqa: BLE001
        log.warning("系统通知失败: %s", exc)


def build_notify_text(digest: dict) -> tuple[str, str]:
    st = digest.get("stats", {})
    key_n = st.get("key", 0)
    slot = digest.get("slot", "今日")
    title = f"HKU 邮件助手 · {slot}汇总"
    parts = [f"关键邮件 {key_n} 封"]
    if st.get("urgent_key"):
        parts.append(f"{st['urgent_key']} 封 3 天内截止")
    if st.get("unread"):
        parts.append(f"未读 {st['unread']} 封")
    headline = digest.get("headline_cn", "")
    body = "，".join(parts) + "。\n" + (headline[:160] if headline else "")
    return title, body


def ensure_widget() -> bool:
    """确保桌面小窗在运行；不在则自动拉起（自愈）。

    返回 True 表示本次有拉起动作。同时重置「收起」状态，保证用户能看到。
    """
    widget_dir = BASE_DIR / "widget"
    exe = widget_dir / "node_modules" / "electron" / "dist" / "electron.exe"
    if not exe.exists():
        log.warning("未找到小窗程序: %s", exe)
        return False

    # 检测进程是否已在运行
    try:
        chk = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq electron.exe"],
            capture_output=True, timeout=15,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        out = (chk.stdout or b"").decode("utf-8", errors="ignore")
        if "electron.exe" in out:
            log.debug("小窗已在运行")
            return False
    except Exception as exc:  # noqa: BLE001
        log.debug("进程检测失败（继续尝试拉起）: %s", exc)

    # 重置收起状态，让小窗以展开模式出现
    state = BASE_DIR / "data" / "widget_state.json"
    try:
        data = json.loads(state.read_text(encoding="utf-8")) if state.exists() else {}
        if data.get("collapsed"):
            data["collapsed"] = False
            state.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                             encoding="utf-8")
    except Exception as exc:  # noqa: BLE001
        log.debug("重置小窗状态失败（忽略）: %s", exc)

    env = {k: v for k, v in __import__("os").environ.items()
           if k != "ELECTRON_RUN_AS_NODE"}
    try:
        # 关键：不要用 Popen 直接启动 electron——自动化/定时环境的父进程结束时，
        # 子进程可能被 Job 对象连带回收（表现为"每次拉起后几分钟内消失"）。
        # 经 cmd /c start 中介启动可彻底脱离父进程；CREATE_BREAKAWAY_FROM_JOB
        # 允许脱离当前 Job（若环境不允许 breakaway 会抛错，此时退回普通启动）。
        flags = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_BREAKAWAY_FROM_JOB
        try:
            subprocess.Popen(
                ["cmd", "/c", "start", "", str(exe), "."],
                cwd=str(widget_dir), env=env, creationflags=flags,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
        except OSError:
            subprocess.Popen(
                ["cmd", "/c", "start", "", str(exe), "."],
                cwd=str(widget_dir), env=env,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
        log.info("小窗未在运行，已自动拉起")
        return True
    except Exception as exc:  # noqa: BLE001
        log.warning("小窗自动拉起失败: %s", exc)
        return False


def main() -> int:
    parser = argparse.ArgumentParser(description="HKU 邮件助手每日运行")
    parser.add_argument("--notify", action="store_true", help="运行后弹 Windows 系统通知")
    parser.add_argument("--days", type=int, default=None)
    parser.add_argument("--demo", action="store_true", help="使用示例数据")
    parser.add_argument("--ensure-widget", action="store_true",
                        help="只确保小窗在运行（供 Windows 计划任务保活调用），不抓邮件")
    args = parser.parse_args()

    if args.ensure_widget:
        ensure_widget()
        return 0

    extra = []
    if args.days:
        extra += ["--days", str(args.days)]

    log.info("=== 开始每日汇总 %s ===", now_cn().strftime("%Y-%m-%d %H:%M"))

    fetch_args = ["--demo"] if args.demo else list(extra)
    rc = run_step("fetch_mail.py", fetch_args)
    if rc != 0:
        log.error("抓取失败，退出码 %s", rc)
        return rc

    rc = run_step("analyze.py", extra)
    if rc != 0:
        log.error("分析失败，退出码 %s", rc)
        return rc

    digest = read_json(DIGEST_PATH, {}) or {}
    title, body = build_notify_text(digest)

    ensure_widget()

    if args.notify:
        notify_via_windows(title, body)

    log.info("=== 汇总完成：重点 %s 封 ===", digest.get("stats", {}).get("key", 0))
    print("\n" + title)
    print(body)
    print("\n桌面小窗若已在运行，会自动刷新并弹出通知。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
