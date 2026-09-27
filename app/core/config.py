"""全局配置。所有可调项集中在此，通过 .env / 环境变量覆盖，代码中不出现硬编码密钥。

**只读资源与可写数据是分开的**，这是能被安装成软件的前提：

- ``RESOURCE_DIR``：模板、静态文件、Alembic 脚本。打包后位于 PyInstaller 的
  解包目录，**安装到 Program Files 时这里是只读的**。
- ``DATA_DIR``：数据库、上传素材、运行时 .env。必须在用户可写的位置，
  否则装完第一次启动就会因为"无法写入数据库"而失败。

开发时两者都指向项目根，保持原有的开发体验不变。
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# 路径解析住在 paths.py，刻意不依赖本模块 —— 否则首次运行时
# 「先写 .env 再读配置」的顺序就无从实现。详见 paths.py 的说明。
from app.core.paths import APP_DIR_NAME, DATA_DIR, RESOURCE_DIR, is_frozen

__all__ = [
    "APP_DIR_NAME",
    "BASE_DIR",
    "DATA_DIR",
    "DEV_SECRET_KEY",
    "RESOURCE_DIR",
    "Settings",
    "get_settings",
    "is_frozen",
    "settings",
]

# 向后兼容：项目里仍有地方引用 BASE_DIR，语义等同只读资源目录
BASE_DIR = RESOURCE_DIR

# 开发环境下 SECRET_KEY 的默认值。生产环境若仍为此值，启动时会强制报错。
DEV_SECRET_KEY = "dev-only-insecure-secret-change-me-in-production"


class Settings(BaseSettings):
    # 两个 .env 都读，数据目录里的优先（后者覆盖前者）。
    # 打包版没有源码目录的写权限，它的 .env 由启动器在数据目录里生成。
    model_config = SettingsConfigDict(
        env_file=(RESOURCE_DIR / ".env", DATA_DIR / ".env"),
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
    #: 会话 Cookie 是否只走 HTTPS。
    #: 留空则跟随 debug（生产=True）。**必须能单独关掉**：
    #: 桌面版是 http://127.0.0.1 访问的，Cookie 一旦带上 Secure 标记
    #: 浏览器就不会发送，登录会直接失效 —— 而这时 DEBUG 恰恰应该是 false。
    session_https_only: bool | None = None

    # ---------- 数据库 ----------
    # 默认值在 __init__ 里按 DATA_DIR 算出绝对路径。
    # 不能用 "./gacha.db"：那是相对**当前工作目录**的，
    # 双击 exe 时工作目录不受控，会导致"每次启动都在不同地方新建一个空库"。
    database_url: str = ""
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

    # ---------- 原石自助增加 ----------
    #: 可选的增加档位（逗号分隔）。**服务端按此白名单校验**，
    #: 客户端只能在这几档里选，不能传任意数值 —— 否则就等于开了个随便刷的口子。
    primogem_topup_options: str = "10,160,1600"
    #: 原石余额上限
    primogem_topup_cap: int = 500_000

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

    @field_validator("database_url")
    @classmethod
    def _default_database_url(cls, v: str) -> str:
        """没配 DATABASE_URL 时，落到数据目录下的绝对路径。"""
        if v and v.strip():
            return v
        return f"sqlite+aiosqlite:///{(DATA_DIR / 'gacha.db').as_posix()}"

    @property
    def topup_options(self) -> list[int]:
        """把 ``"10,160,1600"`` 解析成正整数列表。

        用逗号分隔的字符串而不是 ``list[int]``：pydantic-settings 对复杂类型
        默认按 JSON 解析，环境变量里写 ``10,160,1600`` 会直接报错，
        而 JSON 数组在 shell 里又很难写得好看。
        """
        values: list[int] = []
        for chunk in self.primogem_topup_options.split(","):
            chunk = chunk.strip()
            if not chunk:
                continue
            try:
                value = int(chunk)
            except ValueError:
                continue
            if value > 0 and value not in values:
                values.append(value)
        return values or [10, 160, 1600]  # 配置写坏时回落到出厂档位，而不是变成空列表

    @property
    def ai_enabled(self) -> bool:
        """没有 API Key 时直接走本地模板，不做无谓的网络请求。"""
        return bool(self.deepseek_api_key.strip())

    # ---------------- 路径 ----------------
    # 只读资源来自 RESOURCE_DIR，可写数据来自 DATA_DIR。
    # 这两个分开是打包能成立的关键：装到 Program Files 后前者只读。
    @property
    def upload_dir(self) -> Path:
        """用户上传的素材。放数据目录 —— 程序目录装完就不可写了。"""
        return DATA_DIR / "uploads"

    @property
    def templates_dir(self) -> Path:
        return RESOURCE_DIR / "templates"

    @property
    def static_dir(self) -> Path:
        """内置静态资源（CSS/JS/内置图片）。只读。"""
        return RESOURCE_DIR / "static"

    @property
    def alembic_ini(self) -> Path:
        return RESOURCE_DIR / "alembic.ini"

    @property
    def alembic_dir(self) -> Path:
        return RESOURCE_DIR / "alembic"

    @property
    def runtime_env_file(self) -> Path:
        """运行时 .env。打包版由启动器在数据目录里生成。"""
        return DATA_DIR / ".env"

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
