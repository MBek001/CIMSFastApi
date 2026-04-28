from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.responses import JSONResponse

# Routers
from routers.auth import router as auth_router
from routers.users import router as users_router
from routers.crm import router as crm_router
from routers.crm_sales_manager import router as crm_sales_manager_router
from routers.crm_dynamic_status import router as crm_dynamic_status_router
from routers.sales_stats import router as sales_stats_router
from routers.wordpress import router as wordpress_router
from routers.finance import router as finance_router
from routers.finance_advanced import advanced_router as advanced_router
from routers.updates import router as updates_router
from routers.update_tracking import router as update_tracking_router
from routers.management import router as management_router
from routers.instagram import router as instagram_router
from routers.recall_bot import router as recall_bot_router
from routers.projects import router as projects_router
from routers.ai_chat import router as ai_chat_router
from routers.attendance import router as attendance_router
from routers.audit import router as audit_router
from cognilabsai.router import router as cognilabsai_router
from cognilabsai.service import shutdown_cognilabsai, startup_cognilabsai
from utils.file_storage import IMAGES_ROOT, ensure_image_directories
from uuid import uuid4

from fastapi.responses import JSONResponse

# --------------------------------------------------
# FASTAPI APP CONFIG
# --------------------------------------------------
app = FastAPI(
    title="CIMS Table-Based Auth API",
    version="1.0.0",
    description="Table-based SQLAlchemy bilan Auth Sistema",
)

ensure_image_directories()
app.mount("/images", StaticFiles(directory=str(IMAGES_ROOT)), name="images")


# --------------------------------------------------
# CORS (handled only here, not in nginx)
# --------------------------------------------------
origins = [
    "http://localhost:3000",
    "http://localhost:5173",
    "https://cims.cognilabs.org"
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def attach_request_id(request: Request, call_next):
    request.state.request_id = str(uuid4())
    response = await call_next(request)
    response.headers["X-Request-ID"] = request.state.request_id
    return response


# --------------------------------------------------
# ROUTERS
# --------------------------------------------------
app.include_router(auth_router)
app.include_router(users_router)
app.include_router(wordpress_router)
app.include_router(crm_router)
app.include_router(crm_sales_manager_router)
app.include_router(crm_dynamic_status_router)
app.include_router(sales_stats_router)
# app.include_router(finance_router)
app.include_router(advanced_router)
app.include_router(updates_router)
app.include_router(update_tracking_router)
app.include_router(management_router)
# app.include_router(instagram_router)
app.include_router(recall_bot_router)
app.include_router(projects_router)
app.include_router(ai_chat_router)
app.include_router(attendance_router)
app.include_router(audit_router)
app.include_router(cognilabsai_router)


# --------------------------------------------------
# ERROR & ROOT HANDLERS
# --------------------------------------------------
# @app.exception_handler(404)
# async def custom_404_handler(request: Request, exc: HTTPException):
#     return RedirectResponse(url="/auth/login")

# @app.exception_handler(404)
# async def custom_404_handler(request: Request, exc: HTTPException) -> JSONResponse:
#     return JSONResponse(
#         status_code=404,
#         content={"detail": "Sahifa topilmadi yoki endpoint mavjud emas"}
#     )



@app.get("/")
async def root():
    return {
        "message": "🚀 CIMS Table-Based Auth API",
        "approach": "Table-based SQLAlchemy",
        "docs": "/docs",
    }


@app.on_event("startup")
async def app_startup():
    await startup_cognilabsai()


@app.on_event("shutdown")
async def app_shutdown():
    await shutdown_cognilabsai()


# --------------------------------------------------
# ENTRYPOINT
# --------------------------------------------------
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("run:app", host="0.0.0.0", port=8000, reload=True)
