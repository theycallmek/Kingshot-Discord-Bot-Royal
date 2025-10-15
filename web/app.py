from fastapi import FastAPI, Request, Form, Depends
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware
from sqlmodel import create_engine, Session, select
from .models import User
import os

app = FastAPI()

# This is a temporary secret key. In a real application, this should be
# loaded from a secure configuration.
app.add_middleware(SessionMiddleware, secret_key="your-secret-key")

templates = Jinja2Templates(directory="web/templates")

# This will be replaced with a value from the bot's configuration.
WEB_DASHBOARD_PASSWORD = "password"

# --- Database Setup ---
DATABASE_URL = "sqlite:///db/users.sqlite"
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})

# Enable WAL mode
with engine.connect() as connection:
    connection.exec_driver_sql("PRAGMA journal_mode=WAL;")

def get_session():
    with Session(engine) as session:
        yield session

# --- Authentication ---
def is_authenticated(request: Request):
    return "authenticated" in request.session

# --- Routes ---
@app.get("/login", response_class=HTMLResponse)
async def login_get(request: Request):
    return templates.TemplateResponse("login.html", {"request": request})

@app.post("/login", response_class=HTMLResponse)
async def login_post(request: Request, password: str = Form(...)):
    if password == WEB_DASHBOARD_PASSWORD:
        request.session["authenticated"] = True
        return RedirectResponse(url="/", status_code=303)
    else:
        return templates.TemplateResponse("login.html", {"request": request, "error": "Invalid password"})

@app.get("/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse(url="/login", status_code=303)

@app.get("/", response_class=HTMLResponse)
async def read_root(request: Request, authenticated: bool = Depends(is_authenticated)):
    if not authenticated:
        return RedirectResponse(url="/login", status_code=303)
    return templates.TemplateResponse("dashboard.html", {"request": request})

@app.get("/members", response_class=HTMLResponse)
async def read_members(request: Request, authenticated: bool = Depends(is_authenticated), session: Session = Depends(get_session)):
    if not authenticated:
        return RedirectResponse(url="/login", status_code=303)

    users = session.exec(select(User)).all()
    return templates.TemplateResponse("members.html", {"request": request, "users": users})

@app.get("/events", response_class=HTMLResponse)
async def read_events(request: Request, authenticated: bool = Depends(is_authenticated)):
    if not authenticated:
        return RedirectResponse(url="/login", status_code=303)
    return templates.TemplateResponse("events.html", {"request": request})
