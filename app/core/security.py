"""密码哈希与输入校验。

使用 argon2-cffi 直接调用，**不使用 passlib**：passlib 已停止维护，
且在新版 Python 上会因标准库 ``crypt`` 模块被移除而导入失败。
"""

from __future__ import annotations

import re
from typing import Final

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

#: 单例。argon2-cffi 默认参数（time_cost=3, memory_cost=64MiB, parallelism=4）
#: 即当前推荐值，无需调整。
_hasher: Final[PasswordHasher] = PasswordHasher()

#: 用于抵御用户名枚举的"假哈希"。当用户名不存在时也跑一次校验，
#: 使响应耗时与"用户存在但密码错"一致。
_DUMMY_HASH: Final[str] = _hasher.hash("dummy-password-for-timing-equalization")

USERNAME_MIN: Final[int] = 3
USERNAME_MAX: Final[int] = 20
PASSWORD_MIN: Final[int] = 8

_USERNAME_RE = re.compile(rf"^[A-Za-z0-9_一-龥]{{{USERNAME_MIN},{USERNAME_MAX}}}$")
_HAS_LETTER_RE = re.compile(r"[A-Za-z]")
_HAS_DIGIT_RE = re.compile(r"\d")


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    """校验密码。任何异常都视为校验失败，绝不向外抛出。"""
    try:
        return _hasher.verify(password_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def fake_verify() -> None:
    """用户不存在时调用，抹平时间差。返回值无意义。"""
    try:
        _hasher.verify(_DUMMY_HASH, "wrong-password")
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        pass


def needs_rehash(password_hash: str) -> bool:
    """参数升级后需要重新哈希时返回 True。"""
    try:
        return _hasher.check_needs_rehash(password_hash)
    except InvalidHashError:
        return True


# --------------------------------------------------------------------------
# 校验
# --------------------------------------------------------------------------
def validate_username(username: str) -> tuple[bool, str]:
    if not username:
        return False, "用户名不能为空"
    if not _USERNAME_RE.match(username):
        return False, f"用户名需为 {USERNAME_MIN}-{USERNAME_MAX} 位的中英文、数字或下划线"
    return True, ""


def validate_password(password: str) -> tuple[bool, str]:
    if len(password) < PASSWORD_MIN:
        return False, f"密码至少 {PASSWORD_MIN} 位"
    if not _HAS_LETTER_RE.search(password):
        return False, "密码必须包含字母"
    if not _HAS_DIGIT_RE.search(password):
        return False, "密码必须包含数字"
    return True, ""
