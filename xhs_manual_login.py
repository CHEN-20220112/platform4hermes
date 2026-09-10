"""小红书登录/切换账号脚本（自动检测版）：打开可见浏览器让你扫码登录，检测到真正登录后自动保存 cookie。

用法：
    D:\\py312\\python.exe D:\\platform4hermes\\xhs_manual_login.py

流程：
    1. 备份并清空旧的 cookies.json（切换账号）
    2. 弹出可见 Chromium 浏览器，打开小红书
    3. 用小红书 App 扫码（或手机号登录）完成登录
    4. 脚本每 3 秒检测一次，检测到 web_session 登录 cookie 即自动保存
    5. 5 分钟内未检测到登录则超时退出
"""
import json
import shutil
import time
from pathlib import Path

COOKIE_DIR = Path.home() / ".xhs-mcp"
COOKIE_FILE = COOKIE_DIR / "cookies.json"

def _has_login(cookies: list) -> bool:
    names = {c.get("name") for c in cookies}
    # 小红书「匿名会话」也会种下 web_session，因此光看 web_session 会误判。
    # 只有登录后才会同时出现 id_token（登录态 token），两者同时存在才算真正登录。
    return "web_session" in names and "id_token" in names


def main():
    from playwright.sync_api import sync_playwright

    COOKIE_DIR.mkdir(parents=True, exist_ok=True)

    # 切换账号：先备份并清空旧 cookie，避免旧登录态残留
    if COOKIE_FILE.exists():
        backup = COOKIE_FILE.with_suffix(".json.bak")
        shutil.copyfile(COOKIE_FILE, backup)
        COOKIE_FILE.unlink()
        print(f"已备份旧账号 cookie 到：{backup.name}")

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
        print("请在浏览器里登录「新账号」（扫码或手机号登录）。")
        print("脚本会自动检测登录状态，检测到后自动保存 cookie。")
        print("=" * 50)

        deadline = time.time() + 300  # 最多等 5 分钟
        while time.time() < deadline:
            time.sleep(3)
            cookies = ctx.cookies()
            if _has_login(cookies):
                COOKIE_FILE.write_text(json.dumps(cookies, ensure_ascii=False), encoding="utf-8")
                print(f"✅ 检测到登录，已保存 {len(cookies)} 条 cookie 到：{COOKIE_FILE}")
                print("切换账号完成！下次发文将使用新账号。")
                browser.close()
                return

        print("⏱️ 超时（5 分钟）未检测到登录，请重新运行本脚本。")
        browser.close()


if __name__ == "__main__":
    main()
