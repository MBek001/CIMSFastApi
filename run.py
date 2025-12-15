from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from starlette.responses import JSONResponse

# Routers
from routers.auth import router as auth_router
from routers.users import router as users_router
from routers.crm import router as crm_router
from routers.crm_sales_manager import router as crm_sales_manager_router
from routers.crm_dynamic_status import router as crm_dynamic_status_router
from routers.wordpress import router as wordpress_router
from routers.finance import router as finance_router
from routers.finance_advanced import advanced_router as advanced_router
from routers.updates import router as updates_router
from routers.management import router as management_router
from routers.instagram import router as instagram_router

from fastapi.responses import JSONResponse

# --------------------------------------------------
# FASTAPI APP CONFIG
# --------------------------------------------------
app = FastAPI(
    title="CIMS Table-Based Auth API",
    version="1.0.0",
    description="Table-based SQLAlchemy bilan Auth Sistema",
)


# --------------------------------------------------
# CORS (handled only here, not in nginx)
# --------------------------------------------------
origins = [
    # "https://cims-two.vercel.app",  # ✅ Frontend domain (single origin)
    "https://cims.cognilabs.org"
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# --------------------------------------------------
# ROUTERS
# --------------------------------------------------
app.include_router(auth_router)
app.include_router(users_router)
app.include_router(wordpress_router)
app.include_router(crm_router)
app.include_router(crm_sales_manager_router)
app.include_router(crm_dynamic_status_router)
app.include_router(finance_router)
app.include_router(advanced_router)
app.include_router(updates_router)
app.include_router(management_router)
app.include_router(instagram_router)


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


# --------------------------------------------------
# ENTRYPOINT
# --------------------------------------------------
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("run:app", host="0.0.0.0", port=8000, reload=True)
