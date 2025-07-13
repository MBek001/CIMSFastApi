from fastapi import FastAPI
from main import cleaning_service

app = FastAPI()




app.include_router((cleaning_service.router), tags=["cleaning"])