import sys, json, time

# Ensure D:\py312 site-packages is importable regardless of interpreter
sys.path.insert(0, r"D:\py312\Lib\site-packages")

import xhs_mcp.server as xhs

t0 = time.time()
try:
    result = xhs.check_login_status()
    print("RESULT:", result)
except Exception as e:
    print("EXCEPTION:", type(e).__name__, str(e))
finally:
    print("elapsed_seconds:", round(time.time() - t0, 1))
