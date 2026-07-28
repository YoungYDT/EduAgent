import uvicorn
from fastapi import FastAPI
from backend.api.v1 import auth


app = FastAPI()

app.include_router(auth.router, prefix="/auth", tags=["auth"])

if __name__ == '__main__':
    uvicorn.run(app, host="127.0.0.1", port=8000)