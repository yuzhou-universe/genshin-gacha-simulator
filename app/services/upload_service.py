"""素材上传。

安全要求（逐条对应需求）：

1. **扩展名白名单**：只允许 jpg/jpeg/png/webp。
2. **magic bytes 校验真实文件类型**：扩展名是用户可控的，把 .exe 改名成 .png
   就能绕过。必须读文件头确认真实格式，且扩展名要与真实格式一致。
3. **大小上限**：超过 ``UPLOAD_MAX_BYTES`` 直接拒绝。
4. **重命名为 uuid4**：绝不使用原始文件名拼接路径。原始名可能含 ``../``、
   空字节、Windows 保留名（CON、NUL）、超长路径等，是路径穿越的经典入口。
5. **落盘路径二次校验**：解析后的绝对路径必须仍在 uploads 目录内，
   作为纵深防御（即使前面的重命名被绕过也逃不出去）。
6. 数据库只存**相对 URL**，不存服务器绝对路径。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from pathlib import Path

from app.core.config import settings
from app.core.exceptions import UploadRejected

#: 允许的扩展名 -> 该扩展名对应的真实格式标记
ALLOWED_EXTENSIONS: dict[str, str] = {
    ".jpg": "jpeg",
    ".jpeg": "jpeg",
    ".png": "png",
    ".webp": "webp",
}

#: 允许的上传类别（决定落到哪个子目录）
ALLOWED_CATEGORIES = frozenset({"background", "banner", "icon", "item", "misc"})

#: 文件头魔数。注意 WEBP 需要同时校验首尾两段。
_MAGIC: list[tuple[str, bytes, int, bytes | None]] = [
    ("jpeg", b"\xff\xd8\xff", 0, None),
    ("png", b"\x89PNG\r\n\x1a\n", 0, None),
    ("webp", b"RIFF", 0, b"WEBP"),  # RIFF....WEBP
]

#: 单次读取的头部长度，足够覆盖所有魔数
_HEADER_SIZE = 32


@dataclass(slots=True)
class StoredFile:
    """上传结果。``url`` 是可直接放进 <img src> 的相对路径。"""

    url: str
    relative_path: str
    size: int
    content_type: str
    original_name: str
    detected_format: str


def detect_format(header: bytes) -> str | None:
    """根据文件头判断真实格式。识别不了返回 None。"""
    for name, magic, offset, trailing in _MAGIC:
        if not header[offset : offset + len(magic)] == magic:
            continue
        if trailing is not None:
            # WEBP: RIFF????WEBP
            if header[8:12] != trailing:
                continue
        return name
    return None


def safe_extension(filename: str) -> str:
    """取小写扩展名。不信任原始文件名的其余部分。"""
    # 只取最后一段，避免 "a.png/../../x" 这类构造
    name = Path(filename.replace("\\", "/")).name
    return Path(name).suffix.lower()


def resolve_within(base: Path, *parts: str) -> Path:
    """把相对片段拼到 base 下，并确认结果是 base 内部的一个**文件路径**。

    这是防止路径穿越的最后一道闸：即便文件名里带了 ``../``，
    解析后的绝对路径也会被发现不在 base 之内。

    解析结果等于 base 本身也要拒绝：Windows 会剥掉路径末端的点，
    于是 ``"...."`` 这类名字会塌缩成 base 自己，返回它就意味着调用方
    可能去"写一个目录"。
    """
    candidate = base.joinpath(*parts).resolve()
    root = base.resolve()
    if candidate == root or root not in candidate.parents:
        raise UploadRejected("非法的文件路径")
    return candidate


async def save_image(
    *,
    content: bytes,
    filename: str,
    category: str,
    upload_dir: Path | None = None,
) -> StoredFile:
    """校验并保存一张图片，返回可公开访问的相对 URL。

    校验顺序刻意是「大小 → 扩展名 → 真实格式 → 格式与扩展名一致」：
    先挡掉大文件避免无谓的解析开销，再做内容校验。
    """
    if category not in ALLOWED_CATEGORIES:
        raise UploadRejected(f"不支持的上传类别: {category}")

    if not content:
        raise UploadRejected("文件内容为空")

    if len(content) > settings.upload_max_bytes:
        limit_mb = settings.upload_max_bytes / 1024 / 1024
        raise UploadRejected(f"文件超过 {limit_mb:.1f} MB 上限")

    ext = safe_extension(filename)
    if ext not in ALLOWED_EXTENSIONS:
        allowed = "、".join(sorted(ALLOWED_EXTENSIONS))
        raise UploadRejected(f"只允许上传 {allowed} 格式的图片")

    detected = detect_format(content[:_HEADER_SIZE])
    if detected is None:
        raise UploadRejected("文件内容不是有效的图片（未通过文件头校验）")

    if detected != ALLOWED_EXTENSIONS[ext]:
        raise UploadRejected(f"文件扩展名（{ext}）与实际内容（{detected}）不符，已拒绝")

    # 重命名：完全丢弃原始文件名，只保留经过校验的扩展名
    target_dir = resolve_within(upload_dir or settings.upload_dir, category)
    target_dir.mkdir(parents=True, exist_ok=True)

    stored_name = f"{uuid.uuid4().hex}{ext}"
    target = resolve_within(target_dir, stored_name)

    target.write_bytes(content)

    relative = f"/static/uploads/{category}/{stored_name}"
    return StoredFile(
        url=relative,
        relative_path=f"uploads/{category}/{stored_name}",
        size=len(content),
        content_type=f"image/{'jpeg' if detected == 'jpeg' else detected}",
        original_name=Path(filename.replace("\\", "/")).name[:120],
        detected_format=detected,
    )


def delete_stored(url: str, upload_dir: Path | None = None) -> bool:
    """删除已上传的文件。只接受本站 uploads 下的相对路径。"""
    if not url.startswith("/static/uploads/"):
        return False

    relative = url[len("/static/uploads/") :]
    base = upload_dir or settings.upload_dir
    try:
        # resolve_within 会拒绝任何试图逃逸的路径
        target = resolve_within(base, *relative.split("/"))
    except UploadRejected:
        return False

    if target.is_file():
        target.unlink()
        return True
    return False
