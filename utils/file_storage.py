import imghdr
from pathlib import Path
from typing import Optional
from uuid import uuid4

from fastapi import HTTPException, UploadFile, status


PROJECT_ROOT = Path(__file__).resolve().parent.parent
IMAGES_ROOT = PROJECT_ROOT / "images"
PROJECT_IMAGES_DIR = IMAGES_ROOT / "project_images"
PROFILE_IMAGES_DIR = IMAGES_ROOT / "profil_images"
CARD_IMAGES_DIR = IMAGES_ROOT / "card_images"

ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif"}


def ensure_image_directories() -> None:
    PROJECT_IMAGES_DIR.mkdir(parents=True, exist_ok=True)
    PROFILE_IMAGES_DIR.mkdir(parents=True, exist_ok=True)
    CARD_IMAGES_DIR.mkdir(parents=True, exist_ok=True)


def _validate_image_extension(filename: Optional[str]) -> str:
    suffix = Path(filename or "").suffix.lower()
    if suffix not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Faqat jpg, jpeg, png, webp yoki gif fayl yuklash mumkin",
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

    detected_type = imghdr.what(None, h=content)
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


def delete_image_if_exists(image_path: Optional[str]) -> None:
    if not image_path:
        return

    normalized = image_path.strip().lstrip("/")
    if not normalized.startswith("images/"):
        return

    absolute_path = PROJECT_ROOT / normalized
    if absolute_path.exists() and absolute_path.is_file():
        absolute_path.unlink()
