import sys

PROFILES = ["xhsoperator", "newshunter", "courseproduce"]
BASE = r"C:\Users\hcdl20251117\AppData\Local\hermes\profiles"

for p in PROFILES:
    path = f"{BASE}\\{p}\\config.yaml"
    try:
        with open(path, encoding="utf-8") as f:
            text = f.read()

        before = text
        # 1) 模型 ID 补 deepseek/ 前缀
        text = text.replace(
            "default: deepseek-v4-flash-0731",
            "default: deepseek/deepseek-v4-flash-0731",
        )
        # 2) 去掉 nous 残留 provider/base_url，让 deepseek/ 前缀自动路由到 DeepSeek
        text = text.replace("  provider: nous\n", "")
        text = text.replace(
            "  base_url: https://inference-api.nousresearch.com/v1\n", ""
        )

        with open(path, "w", encoding="utf-8") as f:
            f.write(text)

        print(f"[{p}] {'changed' if text != before else 'no-change'}")
        # 打印 model 块
        for line in text.splitlines():
            if line.startswith("model:") or "default:" in line or "provider" in line or "base_url" in line:
                print("   ", line)
    except Exception as e:
        print(f"[{p}] ERROR: {e}")
