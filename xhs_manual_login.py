"""小红书登录脚本（自动检测版）：打开可见浏览器让你扫码登录，检测到登录后自动保存 cookie。

用法：
    python xhs_manual_login.py

流程：
    1. 弹出可见 Chromium 浏览器，打开小红书
    2. 用小红书 App 扫码（或手机号登录）完成登录
    3. 脚本每 3 秒检测一次，一旦发现登录 cookie 就自动保存到 ~/.xhs-mcp/cookies.json
    4. 5 分钟内未检测到登录则超时退出
"""
import json
import time
from pathlib import Path

COOKIE_DIR = Path.home() / ".xhs-mcp"
COOKIE_FILE = COOKIE_DIR / "cookies.json"

# 小红书登录态相关 cookie（命中其一即视为已登录）
_LOGIN_COOKIE_NAMES = {"web_session", "a1"}


def _has_login(cookies: list) -> bool:
    return any(c.get("name") in _LOGIN_COOKIE_NAMES for c in cookies)


def main():
    from playwright.sync_api import sync_playwright

    print("正在启动浏览器，请稍候…")
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=False)  # 可见浏览器
        ctx = browser.new_context()
        page = ctx.new_page()
        try:
            page.goto("https://www.xiaohongshu.com", timeout=60000, wait_until="domcontentloaded")
            page.wait_for_timeout(2000)
        except Exception as e:
            print(f"打开页面失败（可忽略，继续）：{e}")

        print()
        print("=" * 50)
        print("请在浏览器里完成小红书登录（扫码或手机号登录）。")
        print("脚本会自动检测登录状态，检测到后自动保存 cookie。")
        print("=" * 50)

        deadline = time.time() + 300  # 最多等 5 分钟
        while time.time() < deadline:
            time.sleep(3)
            cookies = ctx.cookies()
            if _has_login(cookies):
                COOKIE_DIR.mkdir(parents=True, exist_ok=True)
                COOKIE_FILE.write_text(json.dumps(cookies, ensure_ascii=False), encoding="utf-8")
                print(f"✅ 检测到登录，已保存 {len(cookies)} 条 cookie 到：{COOKIE_FILE}")
                print("现在可以重启 Hermes 网关，让 xhs-mcp 带上登录信息。")
                browser.close()
                return

        print("⏱️ 超时（5 分钟）未检测到登录，请重新运行本脚本。")
        browser.close()


if __name__ == "__main__":
    main()
