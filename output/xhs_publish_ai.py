import sys, json, time

sys.path.insert(0, r"D:\py312\Lib\site-packages")
import xhs_mcp.server as xhs

TITLE = "AI圈炸锅！OpenAI造芯片、DeepSeek冲刺IPO🔥"
CONTENT = (
    "宝子们，今天 AI 圈的瓜有点多👇\n\n"
    "🔥 OpenAI 牵手三星造芯片\n"
    "OpenAI 要跟三星合作开发下一代 AI 芯片了！从模型到硬件都想自己抓在手里，算力焦虑是真的藏不住了～\n\n"
    "⚡ DeepSeek 双线加速：新模型 + IPO\n"
    "DeepSeek V4.1 Flash 明日发布，同时还传出在筹备科创板 IPO。国产大模型这是要名利双收的节奏？\n\n"
    "🤖 Meta 发布个人 AI 智能体 Muse\n"
    "AI 竞争从「聊天」转向「替你干活」了。Meta 的 Muse 主打任务执行，以后写邮件、订行程可能真不用自己动手。\n\n"
    "🦾 智元发布 AGILE2.0 感控一体模型\n"
    "机器人圈进展也很猛，AGILE2.0 把「感知 + 控制」做进一个模型，具身智能离走进生活又近一步。\n\n"
    "💰 阿里、腾讯参投 UniPat AI 3 亿美元融资\n"
    "巨头继续押注 AI，3 亿美元砸向 UniPat，赛道热钱还在疯狂涌入。\n\n"
    "🇨🇳 中方反驳美「AI 蒸馏」指责\n"
    "商务部回应：美方指控「于事无凭、于法无据」。AI 领域的博弈，注定是接下来长期的主线。\n\n"
    "💡 今天的 AI 圈，你最关心哪一条？评论区聊聊👇"
)
TAGS = ["AI", "人工智能", "OpenAI", "DeepSeek", "今日热点"]
IMAGE = r"C:\Users\hcdl20251117\AppData\Local\hermes\output\cover.png"

t0 = time.time()
try:
    login = xhs.check_login_status()
    print("LOGIN:", login)
    if not login.startswith("Logged in as"):
        print("STOP: not logged in, skip publish")
        sys.exit(2)

    result = xhs.publish_content(
        title=TITLE,
        content=CONTENT,
        images=[IMAGE],
        tags=TAGS,
    )
    print("PUBLISH:", result)
except Exception as e:
    import traceback
    print("EXCEPTION:", type(e).__name__, str(e))
    traceback.print_exc()
    sys.exit(1)
finally:
    print("elapsed_seconds:", round(time.time() - t0, 1))
