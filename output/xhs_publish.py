import sys, json, time

sys.path.insert(0, r"D:\py312\Lib\site-packages")
import xhs_mcp.server as xhs

TITLE = "光伏首超煤电！今天这7件大事刷屏了🔥"
CONTENT = (
    "宝子们，9月10日这天信息量真的有点大👇\n\n"
    "🔥 俄乌突然宣布停火三天\n"
    "俄乌双方都宣布暂停打击对方首都三天。突然的「休战」，是释放信号还是战术调整？大家都在猜～\n\n"
    "⚡ 美伊对峙继续升级\n"
    "美方称打击了3艘伊朗油轮，伊朗反手说袭击了美航母等目标。中东局势一天比一天紧，油价和全球股市都在跟着抖。\n\n"
    "🌋 印尼火山爆发，超17万乘客受影响\n"
    "火山喷发导致机场关闭，雅加达等地航班大面积取消，连澳航都停飞了。近期有出行计划的一定要查清楚！\n\n"
    "☀️ 光伏装机首次超越煤电！\n"
    "国内大好消息——光伏装机容量正式超过煤电，成为我国第一大电源。绿电时代的里程碑，未来更清洁啦～\n\n"
    "🌾 秋粮进入产量形成关键期\n"
    "秋收关键时刻，多地分类施策全力保丰收。饭碗端得稳，心里才踏实。\n\n"
    "🏔️ 西藏吉隆泥石流救援持续推进\n"
    "核心区域救援通道已打通，救援仍在全力进行，愿受灾群众早日平安。\n\n"
    "🎢 USJ 25周年秋活动今日开跑\n"
    "日本环球影城秋季活动上线，9月11日恐怖之夜15周年也开张，胆小的慎入😂\n\n"
    "💡 今日热点浓缩到这儿，你更关心哪一件？评论区聊聊👇"
)
TAGS = ["今日热点", "俄乌停火", "光伏超煤电", "国际新闻", "社会新闻"]
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
