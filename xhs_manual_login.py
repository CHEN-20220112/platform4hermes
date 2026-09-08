"""小红书手动登录脚本：打开可见浏览器让你扫码登录，然后保存 cookie 供 xhs-mcp 复用。

用法：
    python xhs_manual_login.py

流程：
    1. 弹出 Chromium 浏览器，打开小红书首页（可能显示登录二维码）
    2. 用小红书 App 扫码（或切到手机号/验证码登录）完成登录
    3. 回到本终端按回车
    4. 脚本把 cookie 保存到 ~/.xhs-mcp/cookies.json
"""
import json
from pathlib import Path

COOKIE_DIR = Path.home() / ".xhs-mcp"
COOKIE_FILE = COOKIE_DIR / "cookies.json"


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
        print("登录成功后，回到本终端按回车保存 cookie。")
        print("=" * 50)
        input()

        cookies = ctx.cookies()
        if not cookies:
            print("未获取到任何 cookie，可能尚未登录。")
            browser.close()
            return

        COOKIE_DIR.mkdir(parents=True, exist_ok=True)
        COOKIE_FILE.write_text(json.dumps(cookies, ensure_ascii=False), encoding="utf-8")
        print(f"✅ 已保存 {len(cookies)} 条 cookie 到：{COOKIE_FILE}")
        print("现在重启 Hermes 网关（hermes gateway restart）后，xhs-mcp 就会带上这些登录信息。")

        browser.close()


if __name__ == "__main__":
    main()
