# =============================================================================
# 原神风格抽卡模拟系统
#
# 说明：本项目在 Python 3.14 上开发验证；镜像固定用 3.12-slim，
# 因为它是当前各家 wheel 覆盖最完整的稳定版本（requirements 要求 >=3.11）。
# =============================================================================
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# curl 供 HEALTHCHECK 使用；装完即清理 apt 缓存以缩小镜像
RUN apt-get update \
 && apt-get install -y --no-install-recommends curl \
 && rm -rf /var/lib/apt/lists/*

# 先只拷贝依赖清单：只要依赖没变，改代码就能命中这一层缓存
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# 以非 root 运行。uploads 需要可写（管理员上传素材会落盘到这里）。
RUN useradd --create-home --uid 1000 appuser \
 && mkdir -p /app/static/uploads \
 && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD curl -fsS http://127.0.0.1:8000/login > /dev/null || exit 1

# 启动时会自动执行 Alembic 迁移、引导管理员、写入默认卡池，无需额外步骤。
# 不加 --reload：生产镜像里 uvicorn 自带的 reload 会多起一个进程。
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
