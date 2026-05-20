from pathlib import Path
from typing import Optional
from uuid import uuid4

from fastapi import HTTPException, UploadFile, status


PROJECT_ROOT = Path(__file__).resolve().parent.parent
IMAGES_ROOT = PROJECT_ROOT / "images"
FILES_ROOT = PROJECT_ROOT / "files"
PROJECT_IMAGES_DIR = IMAGES_ROOT / "project_images"
PROFILE_IMAGES_DIR = IMAGES_ROOT / "profil_images"
CARD_IMAGES_DIR = IMAGES_ROOT / "card_images"
PROJECT_ATTACHMENTS_DIR = FILES_ROOT / "project_attachments"
IMAGE_CATEGORY_DIRS = {
    "project_images": PROJECT_IMAGES_DIR,
    "profil_images": PROFILE_IMAGES_DIR,
    "card_images": CARD_IMAGES_DIR,
}

ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
ALLOWED_PROJECT_ATTACHMENT_EXTENSIONS = {
    ".jpg",
    ".jpeg",
    ".png",
    ".webp",
    ".gif",
    ".pdf",
    ".doc",
    ".docx",
    ".xls",
    ".xlsx",
}


def _detect_image_type(content: bytes) -> Optional[str]:
    if content.startswith(b"\xff\xd8\xff"):
        return "jpeg"
    if content.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if content.startswith((b"GIF87a", b"GIF89a")):
        return "gif"
    if content.startswith(b"RIFF") and len(content) >= 12 and content[8:12] == b"WEBP":
        return "webp"
    return None


def ensure_image_directories() -> None:
    PROJECT_IMAGES_DIR.mkdir(parents=True, exist_ok=True)
    PROFILE_IMAGES_DIR.mkdir(parents=True, exist_ok=True)
    CARD_IMAGES_DIR.mkdir(parents=True, exist_ok=True)
    PROJECT_ATTACHMENTS_DIR.mkdir(parents=True, exist_ok=True)


def normalize_image_path(image_path: Optional[str]) -> Optional[str]:
    if not image_path:
        return None
    normalized = image_path.strip().replace("\\", "/")
    if not normalized:
        return None
    normalized = "/" + normalized.lstrip("/")
    if not normalized.startswith("/images/"):
        return None
    return normalized


def resolve_image_path(image_path: Optional[str]) -> Optional[Path]:
    normalized = normalize_image_path(image_path)
    if not normalized:
        return None
    candidate = (PROJECT_ROOT / normalized.lstrip("/")).resolve()
    try:
        candidate.relative_to(IMAGES_ROOT.resolve())
    except ValueError:
        return None
    return candidate


def normalize_file_path(file_path: Optional[str]) -> Optional[str]:
    if not file_path:
        return None
    normalized = file_path.strip().replace("\\", "/")
    if not normalized:
        return None
    normalized = "/" + normalized.lstrip("/")
    if not normalized.startswith("/files/"):
        return None
    return normalized


def resolve_file_path(file_path: Optional[str]) -> Optional[Path]:
    normalized = normalize_file_path(file_path)
    if not normalized:
        return None
    candidate = (PROJECT_ROOT / normalized.lstrip("/")).resolve()
    try:
        candidate.relative_to(FILES_ROOT.resolve())
    except ValueError:
        return None
    return candidate


def list_image_paths(category: Optional[str] = None) -> list[str]:
    ensure_image_directories()
    if category is not None and category not in IMAGE_CATEGORY_DIRS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Noto'g'ri image category",
        )

    directories = (
        [IMAGE_CATEGORY_DIRS[category]]
        if category is not None
        else list(IMAGE_CATEGORY_DIRS.values())
    )

    image_paths: list[str] = []
    for directory in directories:
        for file_path in sorted(directory.iterdir()):
            if not file_path.is_file() or file_path.name.startswith("."):
                continue
            image_paths.append(f"/images/{file_path.relative_to(IMAGES_ROOT).as_posix()}")
    return image_paths


def _validate_image_extension(filename: Optional[str]) -> str:
    suffix = Path(filename or "").suffix.lower()
    if suffix not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Faqat jpg, jpeg, png, webp yoki gif fayl yuklash mumkin",
        )
    return suffix


def _validate_project_attachment_extension(filename: Optional[str]) -> str:
    suffix = Path(filename or "").suffix.lower()
    if suffix not in ALLOWED_PROJECT_ATTACHMENT_EXTENSIONS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Faqat jpg, jpeg, png, webp, gif, pdf, doc, docx, xls yoki xlsx fayl yuklash mumkin",
        )
    return suffix


async def save_image(upload: UploadFile, category: str) -> str:
    ensure_image_directories()

    if not upload.filename:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Fayl nomi topilmadi",
        )

    suffix = _validate_image_extension(upload.filename)
    content = await upload.read()
    if not content:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Bo'sh fayl yuklash mumkin emas",
        )

    detected_type = _detect_image_type(content)
    if detected_type not in {"jpeg", "png", "webp", "gif"}:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Yuklangan fayl image emas",
        )

    if category == "project":
        target_dir = PROJECT_IMAGES_DIR
        relative_dir = "project_images"
    elif category == "profile":
        target_dir = PROFILE_IMAGES_DIR
        relative_dir = "profil_images"
    elif category == "card":
        target_dir = CARD_IMAGES_DIR
        relative_dir = "card_images"
    else:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Noto'g'ri image category",
        )

    filename = f"{uuid4().hex}{suffix}"
    file_path = target_dir / filename
    file_path.write_bytes(content)

    return f"/images/{relative_dir}/{filename}"


async def save_project_attachment_file(upload: UploadFile, project_id: int) -> tuple[str, str, int, Optional[str]]:
    ensure_image_directories()

    if not upload.filename:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Fayl nomi topilmadi",
        )

    suffix = _validate_project_attachment_extension(upload.filename)
    content = await upload.read()
    if not content:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Bo'sh fayl yuklash mumkin emas",
        )

    if suffix in {".jpg", ".jpeg", ".png", ".webp", ".gif"}:
        detected_type = _detect_image_type(content)
        if detected_type not in {"jpeg", "png", "webp", "gif"}:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Yuklangan image fayl yaroqsiz",
            )
    elif suffix == ".pdf" and not content.startswith(b"%PDF"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Yuklangan PDF fayl yaroqsiz",
        )

    project_dir = PROJECT_ATTACHMENTS_DIR / str(project_id)
    project_dir.mkdir(parents=True, exist_ok=True)
    stored_name = f"{uuid4().hex}{suffix}"
    file_path = project_dir / stored_name
    file_path.write_bytes(content)

    relative_path = f"/files/project_attachments/{project_id}/{stored_name}"
    return relative_path, upload.filename, len(content), upload.content_type


def delete_image_if_exists(image_path: Optional[str]) -> None:
    absolute_path = resolve_image_path(image_path)
    if absolute_path and absolute_path.exists() and absolute_path.is_file():
        absolute_path.unlink()


def delete_file_if_exists(file_path: Optional[str]) -> None:
    absolute_path = resolve_file_path(file_path)
    if absolute_path and absolute_path.exists() and absolute_path.is_file():
        absolute_path.unlink()
