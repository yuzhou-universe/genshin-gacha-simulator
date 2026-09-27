"""首次运行时的运行时配置生成。

**为什么必须有这一步**：打包后的应用没有源码目录里的 `.env`，设置会回落到
代码里的默认值 —— 也就是 `SECRET_KEY` 是开发占位串、管理员密码是 `Admin12345`。
那等于**每个下载了安装包的人都知道管理员密码**，而且所有安装共用同一个会话
签名密钥（可以互相伪造 Cookie）。

所以首次运行时在**数据目录**里生成一份 `.env`：随机的会话密钥 + 随机的管理员
密码。启动器会把管理员密码显示给用户，并提示可以随时修改。
"""

from __future__ import annotations

import secrets
import string
from dataclasses import dataclass
from pathlib import Path

# 只从 paths 导入：本模块必须在 Settings 被初始化**之前**运行，
# 也就是要在数据目录里先把 .env 写好。详见 paths.py 的说明。
from app.core.paths import DATA_DIR, RESOURCE_DIR


@dataclass(frozen=True, slots=True)
class RuntimeCredentials:
    """首次运行生成的（或从既有 .env 读回的）运行时凭据。"""

    env_path: Path
    admin_username: str
    admin_password: str
    #: 本次是否是新生成的（用来决定启动器要不要提示"这是新密码，请记下"）
    generated: bool


def generate_password(groups: int = 3, size: int = 4) -> str:
    """生成好念也好敲的随机密码，如 ``Xk7m-Pq2n-Rt9v``。

    用连字符分组而不是一串纯随机字符：这个密码要用户手抄或手动输入，
    分组之后抄错的概率低很多。字符集刻意排除了容易混淆的
    ``0/O``、``1/l/I``。密码策略要求含字母和数字，这里保证两者都有。
    """
    alphabet = "".join(c for c in string.ascii_letters + string.digits if c not in "0O1lI")
    while True:
        chunks = ["".join(secrets.choice(alphabet) for _ in range(size)) for _ in range(groups)]
        password = "-".join(chunks)
        # 兜底校验，确保满足注册时的密码策略（含字母 + 含数字 + 至少 8 位）
        if any(c.isalpha() for c in password) and any(c.isdigit() for c in password):
            return password


def read_env_value(env_path: Path, key: str) -> str:
    """从 .env 里读一个键的值。只做最简单的解析，够用即可。"""
    if not env_path.exists():
        return ""
    try:
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            name, _, value = line.partition("=")
            if name.strip() == key:
                return value.strip().strip('"').strip("'")
    except OSError:
        return ""
    return ""


def ensure_runtime_env(data_dir: Path | None = None) -> RuntimeCredentials:
    """确保数据目录里有一份可用的 .env，返回其中的凭据。

    已存在则原样读回，**不会覆盖** —— 用户改过的管理员密码必须被尊重，
    否则每次启动都会把密码重置回随机值。
    """
    data_dir = data_dir or DATA_DIR
    data_dir.mkdir(parents=True, exist_ok=True)
    env_path = data_dir / ".env"

    if env_path.exists():
        return RuntimeCredentials(
            env_path=env_path,
            admin_username=read_env_value(env_path, "ADMIN_USERNAME") or "admin",
            admin_password=read_env_value(env_path, "ADMIN_PASSWORD"),
            generated=False,
        )

    password = generate_password()
    env_path.write_text(_render_env(data_dir, password), encoding="utf-8")
    return RuntimeCredentials(
        env_path=env_path,
        admin_username="admin",
        admin_password=password,
        generated=True,
    )


def _render_env(data_dir: Path, admin_password: str) -> str:
    secret_key = secrets.token_urlsafe(48)
    db_path = (data_dir / "gacha.db").as_posix()
    return f"""# 本文件由程序首次运行时自动生成，位于用户数据目录。
# 修改后重启程序生效。

# 会话签名密钥：随机生成。改动它会让所有已登录的会话失效。
SECRET_KEY={secret_key}

# 管理员账号（用它可以进入 /admin 后台）
ADMIN_USERNAME=admin
ADMIN_PASSWORD={admin_password}

# 桌面版以 http://127.0.0.1 提供服务，必须允许非 HTTPS 的 Cookie，
# 否则浏览器不会回传会话，登录会直接失效。
SESSION_HTTPS_ONLY=false

# 关掉调试模式（不向外暴露堆栈），但不影响上面的 Cookie 设置
DEBUG=false

# 数据库与上传素材都在本文件所在目录，程序目录是只读的
DATABASE_URL=sqlite+aiosqlite:///{db_path}

# 想启用 AI 点评就在下面填上密钥（留空则用本地模板，抽卡不受影响）
DEEPSEEK_API_KEY=
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_MODEL=deepseek-flash
"""


def describe_paths() -> str:
    """给启动器与排障用的一行路径说明。"""
    return f"资源目录: {RESOURCE_DIR}\n数据目录: {DATA_DIR}"
