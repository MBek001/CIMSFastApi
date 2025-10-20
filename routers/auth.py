from fastapi import APIRouter, Depends, HTTPException, status, BackgroundTasks
from fastapi.security import OAuth2PasswordRequestForm
from schemes.schemes_auth import *
from auth_utils.auth_func import *
from auth_utils.email_service import email_service

from config import VERIFICATION_CODE_EXPIRE_MINUTES, PASSWORD_RESET_EXPIRE_MINUTES
from sqlalchemy import func
router = APIRouter(prefix="/auth",tags=['Autentifikatsiya'])
from auth_utils.db_code_storage import db_code_storage



@router.post("/register", response_model=SuccessResponse, summary="Ro'yxatdan o'tish")
async def register(
    user_data: UserCreate,
    background_tasks: BackgroundTasks,
    session: AsyncSession = Depends(get_async_session),
):
    # Email mavjudligini tekshirish
    result = await session.execute(select(user).where(user.c.email == user_data.email))
    existing_user = result.fetchone()
    print(existing_user)

    if existing_user:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Bu email allaqachon ro'yxatdan o'tgan",
        )

    # Database'dagi foydalanuvchilar soni
    users_count_result = await session.execute(select(func.count(user.c.id)))
    users_count = users_count_result.scalar()
    is_first_user = users_count == 0

    # Birinchi user CEO bo‘ladi
    if is_first_user:
        role = UserRole.CEO
        company_code = "ceo"
        is_admin = True
        is_staff = True
        is_superuser = True
        print(f"🚀 Birinchi user yaratilmoqda: {user_data.email} - CEO sifatida")
    else:
        role = user_data.role
        company_code = user_data.company_code
        is_admin = False
        is_staff = False
        is_superuser = False

    # Parolni xeshlash
    hashed_password = get_password_hash(user_data.password)

    user_dict = {
        "email": user_data.email,
        "name": user_data.name,
        "surname": user_data.surname,
        "password": hashed_password,
        "company_code": company_code,
        "telegram_id": user_data.telegram_id,
        "role": role,
        "is_active": False,
        "is_admin": is_admin,
        "is_staff": is_staff,
        "is_superuser": is_superuser,
    }

    result = await session.execute(insert(user).values(**user_dict))
    user_id = result.inserted_primary_key[0]

    # Birinchi userga barcha sahifalarga ruxsat
    if is_first_user:
        permissions_to_add = [
            {"user_id": user_id, "page_name": PageName.ceo},
            {"user_id": user_id, "page_name": PageName.payment_list},
            {"user_id": user_id, "page_name": PageName.project_toggle},
            {"user_id": user_id, "page_name": PageName.crm},
            {"user_id": user_id, "page_name": PageName.finance_list},
        ]
        for p in permissions_to_add:
            await session.execute(insert(user_page_permission).values(**p))
        print(f"✅ CEO ga barcha sahifa ruxsatlari berildi")

    await session.commit()

    # Email verification code
    code = email_service.generate_verification_code()
    await db_code_storage.set_code(session, user_id, code, "verify_email")

    background_tasks.add_task(email_service.send_verification_email, user_data.email, code)

    msg = (
        f"🎉 Birinchi CEO yaratildi! {user_data.email} ga tasdiqlash kodi yuborildi."
        if is_first_user
        else f"Ro'yxatdan o'tish muvaffaqiyatli! {user_data.email} ga tasdiqlash kodi yuborildi."
    )

    return SuccessResponse(message=msg)


# 2. EMAIL TASDIQLASH
@router.post("/verify-email", response_model=Token, summary="Email tasdiqlash")
async def verify_email(
    verification: EmailVerificationConfirm,
    session: AsyncSession = Depends(get_async_session)
):
    # 1. Foydalanuvchini topamiz
    result = await session.execute(select(user.c.id).where(user.c.email == verification.email))
    user_id = result.scalar()

    if not user_id:
        raise HTTPException(status_code=404, detail="Foydalanuvchi topilmadi")

    # 2. Bazadagi kodni olish
    saved_code = await db_code_storage.get_code(session, user_id, "verify_email")

    if not saved_code or saved_code != verification.code:
        raise HTTPException(status_code=400, detail="Tasdiqlash kodi noto‘g‘ri yoki topilmadi")

    # 3. Foydalanuvchini faollashtirish
    await session.execute(
        update(user).where(user.c.id == user_id).values(is_active=True)
    )
    await session.commit()

    # 4. Kodni 0 qilib qo‘yish
    await db_code_storage.invalidate_code(session, user_id, "verify_email")

    # 5. JWT token qaytarish
    access_token_expires = timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    access_token = create_access_token(
        data={"sub": verification.email},
        expires_delta=access_token_expires
    )

    return Token(access_token=access_token, token_type="bearer")



# 3. VERIFICATION KODNI QAYTA YUBORISH
@router.post("/resend-verification", response_model=SuccessResponse)
async def resend_verification_code(
    request: EmailVerificationRequest,
    background_tasks: BackgroundTasks,
    session: AsyncSession = Depends(get_async_session),
):
    result = await session.execute(select(user).where(user.c.email == request.email))
    user_data = result.fetchone()

    if not user_data:
        raise HTTPException(status_code=404, detail="Foydalanuvchi topilmadi")

    if user_data.is_active:
        raise HTTPException(status_code=400, detail="Email allaqachon tasdiqlangan")

    # user_id olish
    result_id = await session.execute(select(user.c.id).where(user.c.email == request.email))
    user_id = result_id.scalar()

    code = email_service.generate_verification_code()
    await db_code_storage.set_code(session, user_id, code, "verify_email")

    background_tasks.add_task(email_service.send_verification_email, request.email, code)

    return SuccessResponse(message="Yangi tasdiqlash kodi yuborildi")


# 4. LOGIN
@router.post("/login", response_model=Token, summary="Tizimga kirish")
async def login(
    form_data: OAuth2PasswordRequestForm = Depends(),
    session: AsyncSession = Depends(get_async_session),
):
    result = await session.execute(select(user).where(user.c.email == form_data.username))
    user_data = result.fetchone()

    if not user_data or not verify_password(form_data.password, user_data.password):
        raise HTTPException(
            status_code=401,
            detail="Email yoki parol noto'g'ri",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if not user_data.is_active:
        raise HTTPException(status_code=400, detail="Akkaunt faol emas. Email tasdiqlashni bajaring.")

    # Token
    access_token_expires = timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    access_token = create_access_token(
        data={"sub": user_data.email}, expires_delta=access_token_expires
    )

    return Token(access_token=access_token, token_type="bearer")


# 5. PAROLNI UNUTISH
@router.post("/forgot-password", response_model=SuccessResponse)
async def forgot_password(
    request: PasswordResetRequest,
    background_tasks: BackgroundTasks,
    session: AsyncSession = Depends(get_async_session),
):
    result = await session.execute(select(user.c.id).where(user.c.email == request.email))
    user_id = result.scalar()

    if user_id:
        code = email_service.generate_verification_code()
        await db_code_storage.set_code(session, user_id, code, "reset_password")

        background_tasks.add_task(email_service.send_password_reset_email, request.email, code)

    return SuccessResponse(message="Agar email mavjud bo'lsa, parol tiklash kodi yuborildi")


# 6. PAROLNI TIKLASH
@router.post("/reset-password", response_model=SuccessResponse)
async def reset_password(
    reset_data: PasswordResetConfirm,
    session: AsyncSession = Depends(get_async_session),
):
    result = await session.execute(select(user.c.id).where(user.c.email == reset_data.email))
    user_id = result.scalar()

    if not user_id:
        raise HTTPException(status_code=404, detail="Foydalanuvchi topilmadi")

    saved_code = await db_code_storage.get_code(session, user_id, "reset_password")

    if not saved_code or saved_code != reset_data.code:
        raise HTTPException(status_code=400, detail="Kod noto‘g‘ri yoki topilmadi")

    # Parolni yangilash
    hashed_password = get_password_hash(reset_data.new_password)
    await session.execute(update(user).where(user.c.id == user_id).values(password=hashed_password))
    await session.commit()

    # Kodni 0 ga o‘zgartirish
    await db_code_storage.invalidate_code(session, user_id, "reset_password")

    return SuccessResponse(message="Parol muvaffaqiyatli yangilandi")


@router.get("/me", response_model=UserResponse)
async def get_current_user_info(
    current_user=Depends(get_current_active_user),
    session: AsyncSession = Depends(get_async_session)
):
    """
    Joriy foydalanuvchi ma'lumotlari va sahifa ruxsatlari
    """
    # User permissions olish
    permissions_result = await session.execute(
        select(user_page_permission.c.page_name)
        .where(user_page_permission.c.user_id == current_user.id)
    )
    user_permissions = [perm.page_name.value for perm in permissions_result.fetchall()]

    # Barcha sahifalar uchun true/false obyekt yaratish
    permissions_object = {}
    for page in PageName:
        permissions_object[page.value] = page.value in user_permissions

    return UserResponse(
        id=current_user.id,
        email=current_user.email,
        name=current_user.name,
        surname=current_user.surname,
        company_code=current_user.company_code,
        role=current_user.role,
        is_active=current_user.is_active,
        permissions=permissions_object
    )

# 8. DASHBOARD REDIRECT
@router.get("/dashboard-redirect", response_model=RedirectResponse)
async def dashboard_redirect(
        current_user=Depends(get_current_active_user),
        session: AsyncSession = Depends(get_async_session)
):
    # Member check
    if current_user.role == UserRole.member:
        return RedirectResponse(redirect_url="/member_dashboard")

    # Company code check
    valid_company_codes = ['telegram', 'ceo', 'logistic', 'consulting', 'service']

    if current_user.company_code in valid_company_codes:
        redirect_map = {
            'telegram': '/index1',
            'ceo': '/ceo',
            'logistic': '/index',
            'consulting': '/consulting',
            'service': '/service_all'
        }
        return RedirectResponse(redirect_url=redirect_map[current_user.company_code])

    # Permissions check
    result = await session.execute(
        select(user_page_permission.c.page_name)
        .where(user_page_permission.c.user_id == current_user.id)
    )
    permissions = [row.page_name for row in result.fetchall()]

    if permissions:
        first_permission = permissions[0].value
        redirect_map = {
            'ceo': '/ceo',
            'crm': '/crm',
            'payment_list': '/payment_list',
            'finance_list': '/finance_list',
            'project_toggle': '/project_toggle',
            'dashboard': f'/user_dashboard/{current_user.company_code}',
        }

        redirect_url = redirect_map.get(first_permission)
        if redirect_url:
            return RedirectResponse(redirect_url=redirect_url)
        else:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=f"Noma'lum ruxsat: {first_permission}")
    else:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Sizda sahifaga kirish huquqi yo'q")