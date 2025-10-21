from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse

# Routers
from routers.auth import router as auth_router
from routers.users import router as users_router
from routers.crm import router as crm_router
from routers.wordpress import router as wordpress_router
from routers.finance import router as finance_router
from routers.finance_advanced import advanced_router as advanced_router


app = FastAPI(
    title="CIMS Table-Based Auth API",
    version="1.0.0",
    description="Table-based SQLAlchemy bilan Auth Sistema",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://cims-two.vercel.app"],  # takror yo‘q
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)



# ✅ Routers
app.include_router(auth_router)
app.include_router(users_router)
app.include_router(crm_router)
app.include_router(wordpress_router)
app.include_router(finance_router)
app.include_router(advanced_router)


# ✅ Custom 404 redirect
@app.exception_handler(404)
async def custom_404_handler(request: Request, exc: HTTPException):
    return RedirectResponse(url="/auth/login")


# ✅ Root endpoint
@app.get("/")
async def root():
    return {
        "message": "🚀 CIMS Table-Based Auth API",
        "approach": "Table-based SQLAlchemy",
        "docs": "/docs",
    }


# ✅ Fix main entrypoint (was '__run__', should be '__main__')
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("run:app", host="0.0.0.0", port=8000, reload=True)
