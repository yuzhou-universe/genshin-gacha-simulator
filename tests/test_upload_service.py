"""上传安全测试。

需求里的每一条安全要求都对应这里的用例：扩展名白名单、magic bytes 校验、
大小上限、uuid 重命名、路径穿越防护。
"""

from __future__ import annotations

import pytest

from app.core.config import settings
from app.core.exceptions import UploadRejected
from app.services import upload_service

# 一张最小的合法 PNG（1×1 像素）
PNG_BYTES = bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000001000000010806000000"
    "1f15c4890000000a49444154789c63000100000500010d0a2db4000000"
    "0049454e44ae426082"
)
JPEG_BYTES = b"\xff\xd8\xff\xe0" + b"\x00" * 40
WEBP_BYTES = b"RIFF" + b"\x00\x00\x00\x00" + b"WEBP" + b"\x00" * 20


@pytest.fixture
def upload_root(tmp_path):
    root = tmp_path / "uploads"
    root.mkdir()
    return root


# ===========================================================================
# 格式识别
# ===========================================================================
@pytest.mark.parametrize(
    ("data", "expected"),
    [
        (PNG_BYTES, "png"),
        (JPEG_BYTES, "jpeg"),
        (WEBP_BYTES, "webp"),
        (b"not an image at all", None),
        (b"", None),
        (b"GIF89a", None),  # GIF 不在白名单
    ],
)
def test_detect_format(data, expected):
    assert upload_service.detect_format(data[:32]) == expected


# ===========================================================================
# 正常上传
# ===========================================================================
@pytest.mark.parametrize(
    ("filename", "data", "fmt"),
    [
        ("photo.png", PNG_BYTES, "png"),
        ("photo.jpg", JPEG_BYTES, "jpeg"),
        ("photo.jpeg", JPEG_BYTES, "jpeg"),
        ("photo.webp", WEBP_BYTES, "webp"),
        ("PHOTO.PNG", PNG_BYTES, "png"),  # 扩展名大小写不敏感
    ],
)
async def test_accepts_valid_images(upload_root, filename, data, fmt):
    stored = await upload_service.save_image(
        content=data, filename=filename, category="background", upload_dir=upload_root
    )
    assert stored.detected_format == fmt
    assert stored.url.startswith("/static/uploads/background/")
    assert (upload_root / "background" / stored.url.rsplit("/", 1)[-1]).is_file()


async def test_stored_filename_is_uuid_not_original(upload_root):
    """落盘文件名必须是随机 uuid，绝不能包含原始文件名。"""
    stored = await upload_service.save_image(
        content=PNG_BYTES,
        filename="我的 角色立绘.png",
        category="item",
        upload_dir=upload_root,
    )

    assert "我的" not in stored.url
    assert "立绘" not in stored.url
    stem = stored.url.rsplit("/", 1)[-1].removesuffix(".png")
    assert len(stem) == 32 and all(
        c in "0123456789abcdef" for c in stem
    ), f"文件名应为 uuid4().hex，实际是 {stem!r}"
    # 原始文件名只作为日志留痕保留
    assert stored.original_name == "我的 角色立绘.png"


async def test_two_uploads_of_same_file_get_different_names(upload_root):
    a = await upload_service.save_image(
        content=PNG_BYTES, filename="x.png", category="icon", upload_dir=upload_root
    )
    b = await upload_service.save_image(
        content=PNG_BYTES, filename="x.png", category="icon", upload_dir=upload_root
    )
    assert a.url != b.url


# ===========================================================================
# 拒绝路径
# ===========================================================================
async def test_rejects_non_whitelisted_extension(upload_root):
    with pytest.raises(UploadRejected, match="只允许上传"):
        await upload_service.save_image(
            content=PNG_BYTES, filename="evil.svg", category="icon", upload_dir=upload_root
        )


async def test_rejects_fake_extension_by_magic_bytes(upload_root):
    """核心用例：把可执行内容改名成 .png 必须被拒。

    只检查扩展名是不够的 —— 扩展名完全由用户控制。
    """
    with pytest.raises(UploadRejected, match="文件头校验|不是有效的图片"):
        await upload_service.save_image(
            content=b"#!/bin/sh\nrm -rf /\n",
            filename="innocent.png",
            category="icon",
            upload_dir=upload_root,
        )


async def test_rejects_extension_content_mismatch(upload_root):
    """扩展名与真实格式不符也要拒绝。"""
    with pytest.raises(UploadRejected, match="不符"):
        await upload_service.save_image(
            content=PNG_BYTES, filename="x.jpg", category="icon", upload_dir=upload_root
        )

    with pytest.raises(UploadRejected, match="不符"):
        await upload_service.save_image(
            content=JPEG_BYTES, filename="x.png", category="icon", upload_dir=upload_root
        )


async def test_rejects_oversized_file(upload_root, monkeypatch):
    monkeypatch.setattr(settings, "upload_max_bytes", 100)
    with pytest.raises(UploadRejected, match="上限"):
        await upload_service.save_image(
            content=PNG_BYTES + b"\x00" * 200,
            filename="big.png",
            category="icon",
            upload_dir=upload_root,
        )


async def test_rejects_empty_file(upload_root):
    with pytest.raises(UploadRejected, match="为空"):
        await upload_service.save_image(
            content=b"", filename="empty.png", category="icon", upload_dir=upload_root
        )


async def test_rejects_unknown_category(upload_root):
    with pytest.raises(UploadRejected, match="类别"):
        await upload_service.save_image(
            content=PNG_BYTES, filename="x.png", category="evil", upload_dir=upload_root
        )


# ===========================================================================
# 路径穿越
# ===========================================================================
def test_resolve_within_blocks_traversal(tmp_path):
    """带 ../ 的路径必须被拒。"""
    root = tmp_path / "uploads"
    root.mkdir()

    assert upload_service.resolve_within(root, "sub", "ok.png").is_relative_to(root)

    for evil in ("..", "../..", "sub/../../..", "a/b/../../../.."):
        with pytest.raises(UploadRejected):
            upload_service.resolve_within(root, *[p for p in evil.split("/") if p])


def test_resolve_within_allows_odd_but_contained_names(tmp_path):
    """含点的普通名字不是穿越，应当放行 —— 过度防御会掩盖真正的漏洞。"""
    root = tmp_path / "uploads"
    root.mkdir()

    for name in ("..hidden", "a..b", "...c", "x....y.png"):
        resolved = upload_service.resolve_within(root, *[p for p in name.split("/") if p])
        assert resolved.is_relative_to(root.resolve())


def test_resolve_within_rejects_names_that_collapse_to_root(tmp_path):
    """``....`` 这类名字要被拒 —— 不是因为它穿越，而是因为在 Windows 上
    路径末端的点会被文件系统剥掉，它解析后塌缩成根目录本身，
    写下去等于去写一个目录。"""
    root = tmp_path / "uploads"
    root.mkdir()

    with pytest.raises(UploadRejected):
        upload_service.resolve_within(root, "....")


@pytest.mark.parametrize(
    "evil_name",
    [
        "../../../etc/passwd.png",
        "..\\..\\windows\\system32\\evil.png",
        "a/../../b.png",
        "....//....//x.png",
    ],
)
async def test_traversal_in_filename_cannot_escape(upload_root, evil_name):
    """带穿越片段的文件名：要么被拒，要么落在 uploads 内 —— 绝不能逃出去。"""
    try:
        await upload_service.save_image(
            content=PNG_BYTES, filename=evil_name, category="icon", upload_dir=upload_root
        )
    except UploadRejected:
        return  # 被拒也是正确结果

    # 若被接受，落盘位置必须仍在根目录内
    written = [p for p in upload_root.rglob("*") if p.is_file()]
    assert written, "声称成功就必须真的写了文件"
    for path in written:
        assert path.resolve().is_relative_to(upload_root.resolve())


def test_safe_extension_strips_directories():
    assert upload_service.safe_extension("photo.PNG") == ".png"
    assert upload_service.safe_extension("a/b/c.jpg") == ".jpg"
    assert upload_service.safe_extension("..\\..\\x.webp") == ".webp"
    assert upload_service.safe_extension("noext") == ""


# ===========================================================================
# 删除
# ===========================================================================
async def test_delete_stored_removes_file(upload_root):
    stored = await upload_service.save_image(
        content=PNG_BYTES, filename="x.png", category="icon", upload_dir=upload_root
    )
    assert upload_service.delete_stored(stored.url, upload_dir=upload_root) is True


def test_delete_rejects_foreign_paths(upload_root):
    """只允许删除本站 uploads 下的文件。"""
    for url in (
        "/etc/passwd",
        "https://example.com/x.png",
        "/static/uploads/../../app/main.py",
        "../../app/main.py",
    ):
        assert upload_service.delete_stored(url, upload_dir=upload_root) is False
