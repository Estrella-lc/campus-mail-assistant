# -*- coding: utf-8 -*-
"""HKU 邮件小窗守护进程（无窗口，常驻后台）。

每 60 秒检查一次桌面小窗是否在运行，不在则自动拉起。
由 Windows「启动」文件夹的 bat 以 pythonw 启动，与 WorkBuddy 会话完全解耦，
因此它启动的 Electron 进程不会被会话清理机制回收。

手动退出守护：任务管理器结束 pythonw.exe 即可（小窗不受影响）。
"""
import sys
import time

sys.path.insert(0, r"C:\Users\16964\WorkBuddy\2026-09-28-15-05-08\hku-mailbot")

from run_daily import ensure_widget  # noqa: E402


def main() -> None:
    # 启动后先等一小会儿，避开登录高峰
    time.sleep(20)
    while True:
        try:
            ensure_widget()
        except Exception:  # noqa: BLE001
            pass
        time.sleep(60)


if __name__ == "__main__":
    main()
