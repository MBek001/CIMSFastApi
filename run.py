from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse


from routers.auth import router as auth_router

from routers.users import router as users_router

from routers.crm import router as crm_router

from routers.wordpress import router as wordpress_router

app = FastAPI(
    title="CIMS Table-Based Auth API",
    version="1.0.0",
    description="Table-based SQLAlchemy bilan Auth Sistema",

)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth_router)
app.include_router(users_router)
app.include_router(crm_router)
app.include_router(wordpress_router)
@app.exception_handler(404)
async def custom_404_handler(request: Request, exc: HTTPException):
    return RedirectResponse(url="/auth/login")


@app.get("/")
async def root():
    return {
        "message": "🚀 CIMS Table-Based Auth API",
        "approach": "Table-based SQLAlchemy",
        "docs": "/docs"
    }



if __name__ == "__run__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)