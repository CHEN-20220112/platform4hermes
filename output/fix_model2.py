import sys

PROFILES = ["xhsoperator", "newshunter", "courseproduce"]
BASE = r"C:\Users\hcdl20251117\AppData\Local\hermes\profiles"

OLD = "default: deepseek/deepseek-v4-flash-0731"
NEW = (
    "default: deepseek/deepseek-flash\n"
    "  provider: deepseek\n"
    "  base_url: https://api.deepseek.com/v1"
)

for p in PROFILES:
    path = f"{BASE}\\{p}\\config.yaml"
    try:
        with open(path, encoding="utf-8") as f:
            text = f.read()
        before = text
        text = text.replace(OLD, NEW)
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        print(f"[{p}] {'changed' if text != before else 'no-change'}")
        for line in text.splitlines():
            if line.startswith(("model:", "  default:", "  provider:", "  base_url:")):
                print("   ", line)
    except Exception as e:
        print(f"[{p}] ERROR: {e}")
