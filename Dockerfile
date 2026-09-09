# 方案 A：基于官方 Hermes 镜像（自带 hermes CLI），叠加平台 FastAPI 依赖。
# 好处：容器内 `hermes` 命令可用，专家 profile 下发（profile create / mcp add）能直接跑。
FROM nousresearch/hermes-agent:latest

# 平台运行依赖（用 python -m pip 更稳，避免 pip 不在 PATH 的情况）
RUN python -m pip install --no-cache-dir \
    "fastapi>=0.140.0" \
    "uvicorn[standard]>=0.29.0" \
    "sqlalchemy>=2.0.25" \
    "pydantic>=2.6.0" \
    "requests>=2.31.0" \
    "lark-oapi>=1.7.0,<2.0.0" \
    "pyyaml>=6.0" \
    "python-multipart>=0.0.9"

WORKDIR /app
COPY app ./app
COPY frontend ./frontend

# 清掉 Hermes 镜像自带的 s6-overlay 入口（否则 CMD 会被当成参数交给 /init，uvicorn 跑不起来）。
# 我们只要它的 hermes CLI，不要它容器里的 dashboard / api-server 进程。
ENTRYPOINT []
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
