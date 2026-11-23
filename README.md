# 校园邮件助手 · Campus Mail Assistant

**桌面悬浮小窗 + 每日中文邮件汇总：定时抓取校园邮箱，按「截止 / 课程教务 / 活动报名」三类挑出关键邮件，生成中文摘要，弹系统通知，点一下直达原邮件。**

个人效率工具项目 · 独立开发 · 2026-09

---

## 解决的问题

校园邮箱里混着大量选课通知、作业提醒、讲座报名和群发邮件，真正要紧的「有截止时间、需要回复」的信息常被淹没。这个工具把「抓取 → 打分 → 摘要 → 提醒 → 直达」做成一条自动流水线。

## 功能设计

- **抓取**：IMAP + XOAUTH2 读取收件箱；用同一支凭证走 **EWS 通道补齐每封邮件的 OWA 直达链接**（实测 36/36 命中），解决 IMAP 拿不到网页版唯一 ID 的问题
- **打分**：关键邮件判定 = 类别 × 是否有明确截止时间 × 剩余天数 × 是否要求回复 × 是否未读 × 发件人是否个人，阈值可在 `config.json` 调
- **摘要**：生成中文摘要与「建议动作」，按关键邮件 / 值得关注 / 其他邮件分层展示
- **桌面端**：Electron 悬浮小窗，可拖动、收起成小药丸、置顶、收进系统托盘，15 秒自动刷新
- **通知**：Windows 系统通知，每天定时运行

## 技术栈

Python 3.10+（IMAP / EWS / OAuth2 设备码授权） · Electron（桌面小窗） · Windows 定时任务

## 文件结构

```
campus-mail-assistant/
├─ config.json              # 邮箱、抓取范围、打分阈值（示例账号）
├─ common.py                # 公共模块
├─ auth_login.py            # OAuth2 设备码授权
├─ fetch_mail.py            # 抓取邮件（IMAP + XOAUTH2）
├─ analyze.py               # 关键邮件判定 + 中文摘要引擎
├─ ews_links.py             # EWS 通道获取邮件直达链接
├─ run_daily.py             # 每日一键运行入口
├─ notify.ps1               # Windows 系统通知
├─ step*.bat                # 双击：授权 / 启动小窗 / 立即运行
├─ fixtures/demo_mail.json  # 示例邮件数据（离线预览用）
└─ widget/                  # Electron 桌面小窗
```

## 运行方式

```bash
# 离线看效果（用示例数据，无需邮箱）
python fetch_mail.py --demo
python analyze.py

# 真实使用：先改成你自己的邮箱配置
python auth_login.py            # 设备码授权
python fetch_mail.py --days 3   # 抓最近 3 天
python analyze.py               # 生成汇总
python run_daily.py --notify    # 一键：抓取 + 分析 + 通知

# 桌面小窗
cd widget && npm install && npm start
```

## 发布前检查（重要）

- 本仓库是**公开脱敏版**：真实邮箱、联系人、邮件正文、OAuth 凭证**均不在仓库中**。`config.json` 与 `auth_login.py` 中的账号已改为 `student@university.edu` 等示例值。
- 若要在本机真实使用：把 `config.json` / `auth_login.py` 里的示例邮箱改成你自己的学校邮箱，重新跑一次设备码授权即可。授权凭证会存在本机 `data/token_cache.json`（已加入 `.gitignore`，不会进仓库）。
- 示例邮件 `fixtures/demo_mail.json` 为虚构教学数据，公开链接为学校官网，不含个人信息。
