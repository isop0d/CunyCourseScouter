from fastapi import FastAPI
from starlette.middleware.sessions import SessionMiddleware

from cuny_scouter.config import settings
from cuny_scouter.web.routes import router

app = FastAPI(title="CUNY Course Scouter")
app.add_middleware(SessionMiddleware, secret_key=settings.secret_key)
app.include_router(router)
app.state.settings = settings
