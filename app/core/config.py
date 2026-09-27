"""全局配置。所有可调项集中在此，通过 .env / 环境变量覆盖，代码中不出现硬编码密钥。"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# 项目根目录：app/core/config.py -> app/core -> app -> <root>
BASE_DIR = Path(__file__).resolve().parent.parent.parent

# 开发环境下 SECRET_KEY 的默认值。生产环境若仍为此值，启动时会强制报错。
DEV_SECRET_KEY = "dev-only-insecure-secret-change-me-in-production"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=BASE_DIR / ".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ---------- 应用 ----------
    app_name: str = "原神风格抽卡模拟系统"
    debug: bool = True
    secret_key: str = DEV_SECRET_KEY
    session_max_age: int = 14 * 24 * 3600  # 14 天
    session_cookie: str = "gacha_session"

    # ---------- 数据库 ----------
    database_url: str = "sqlite+aiosqlite:///./gacha.db"
    db_echo: bool = False

    # ---------- 管理员引导 ----------
    admin_username: str = "admin"
    admin_password: str = "Admin12345"

    # ---------- 登录安全 ----------
    login_max_attempts: int = 5
    login_lockout_seconds: int = 300

    # ---------- 新用户初始货币 ----------
    initial_primogems: int = 16000
    initial_intertwined_fate: int = 20
    initial_acquaint_fate: int = 20

    # ---------- DeepSeek Flash ----------
    deepseek_api_key: str = ""
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_model: str = "deepseek-flash"
    deepseek_timeout: float = 8.0
    deepseek_max_retries: int = 2
    # Flash 默认开启 thinking。生成"一句点评"这类短文本必须关闭，
    # 否则延迟与费用都消耗在思维链上。
    deepseek_disable_thinking: bool = True

    # ---------- 上传 ----------
    upload_max_bytes: int = 5 * 1024 * 1024  # 5 MB

    # ---------- 分页 ----------
    history_page_size: int = 20
    history_max_page_size: int = 200

    @field_validator("deepseek_base_url")
    @classmethod
    def _strip_trailing_slash(cls, v: str) -> str:
        return v.rstrip("/")

    @property
    def ai_enabled(self) -> bool:
        """没有 API Key 时直接走本地模板，不做无谓的网络请求。"""
        return bool(self.deepseek_api_key.strip())

    @property
    def upload_dir(self) -> Path:
        return BASE_DIR / "static" / "uploads"

    @property
    def templates_dir(self) -> Path:
        return BASE_DIR / "templates"

    @property
    def static_dir(self) -> Path:
        return BASE_DIR / "static"

    def validate_production_safety(self) -> None:
        """生产环境下拒绝以开发默认密钥启动。"""
        if not self.debug and self.secret_key == DEV_SECRET_KEY:
            raise RuntimeError(
                "SECRET_KEY 仍为开发默认值，且 DEBUG=false。"
                '请生成随机密钥后写入 .env：python -c "import secrets; print(secrets.token_urlsafe(48))"'
            )


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
