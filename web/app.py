from fastapi import FastAPI, Request, Form, Depends
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware
from sqlmodel import create_engine, Session, select
from .models import User, UserGiftCode, NicknameChange, FurnaceChange, AttendanceRecord, GiftCode, BearNotification, BearNotificationWithNickname
import os
from datetime import datetime, date
from collections import defaultdict
import calendar

app = FastAPI()

# Mount static files
app.mount("/static", StaticFiles(directory="web/static"), name="static")

# --- Configuration ---
SECRET_KEY = os.environ.get("SECRET_KEY", "your-secret-key")
WEB_DASHBOARD_PASSWORD = os.environ.get("WEB_DASHBOARD_PASSWORD", "password")

app.add_middleware(SessionMiddleware, secret_key=SECRET_KEY)

templates = Jinja2Templates(directory="web/templates")

# --- Database Setup ---
DB_DIR = os.path.abspath("db")
os.makedirs(DB_DIR, exist_ok=True)
users_engine = create_engine(f"sqlite:///{os.path.join(DB_DIR, 'users.sqlite')}", connect_args={"check_same_thread": False})
giftcode_engine = create_engine(f"sqlite:///{os.path.join(DB_DIR, 'giftcode.sqlite')}", connect_args={"check_same_thread": False})
changes_engine = create_engine(f"sqlite:///{os.path.join(DB_DIR, 'changes.sqlite')}", connect_args={"check_same_thread": False})
attendance_engine = create_engine(f"sqlite:///{os.path.join(DB_DIR, 'attendance.sqlite')}", connect_args={"check_same_thread": False})
beartime_engine = create_engine(f"sqlite:///{os.path.join(DB_DIR, 'beartime.sqlite')}", connect_args={"check_same_thread": False})


# Enable WAL mode
with users_engine.connect() as connection:
    connection.exec_driver_sql("PRAGMA journal_mode=WAL;")
with giftcode_engine.connect() as connection:
    connection.exec_driver_sql("PRAGMA journal_mode=WAL;")
with changes_engine.connect() as connection:
    connection.exec_driver_sql("PRAGMA journal_mode=WAL;")
with attendance_engine.connect() as connection:
    connection.exec_driver_sql("PRAGMA journal_mode=WAL;")
with beartime_engine.connect() as connection:
    connection.exec_driver_sql("PRAGMA journal_mode=WAL;")

def get_users_session():
    with Session(users_engine) as session:
        yield session

def get_giftcode_session():
    with Session(giftcode_engine) as session:
        yield session

def get_changes_session():
    with Session(changes_engine) as session:
        yield session

def get_attendance_session():
    with Session(attendance_engine) as session:
        yield session

def get_beartime_session():
    with Session(beartime_engine) as session:
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
async def read_members(request: Request, authenticated: bool = Depends(is_authenticated), session: Session = Depends(get_users_session)):
    if not authenticated:
        return RedirectResponse(url="/login", status_code=303)

    users = session.exec(select(User)).all()
    return templates.TemplateResponse("members.html", {"request": request, "users": users})

@app.get("/events", response_class=HTMLResponse)
async def read_events(request: Request, authenticated: bool = Depends(is_authenticated), beartime_session: Session = Depends(get_beartime_session), users_session: Session = Depends(get_users_session)):
    if not authenticated:
        return RedirectResponse(url="/login", status_code=303)

    today = date.today()
    cal = calendar.Calendar()
    month_days = cal.monthdatescalendar(today.year, today.month)

    users = users_session.exec(select(User)).all()
    user_map = {user.fid: user.nickname for user in users}

    events_query = beartime_session.exec(select(BearNotification)).all()
    events_map = defaultdict(list)
    for event in events_query:
        nickname = user_map.get(event.created_by, "Unknown")
        event_with_nickname = BearNotificationWithNickname(
            **event.model_dump(),
            created_by_nickname=nickname
        )
        if event_with_nickname.next_notification:
            events_map[event_with_nickname.next_notification.date()].append(event_with_nickname)

    return templates.TemplateResponse("events.html", {
        "request": request,
        "month_days": month_days,
        "events_map": events_map,
        "today": today
    })

@app.get("/giftcodes", response_class=HTMLResponse)
async def read_giftcodes(request: Request, authenticated: bool = Depends(is_authenticated), giftcode_session: Session = Depends(get_giftcode_session), users_session: Session = Depends(get_users_session)):
    if not authenticated:
        return RedirectResponse(url="/login", status_code=303)

    # Fetch all gift codes
    all_codes = giftcode_session.exec(select(GiftCode)).all()

    # Fetch all user redemption statuses
    user_giftcodes = giftcode_session.exec(select(UserGiftCode)).all()

    # Fetch all users for nickname mapping
    users = users_session.exec(select(User)).all()
    user_map = {user.fid: user.nickname for user in users}

    # Create a map of giftcode -> list of users who have redeemed it
    redemption_map = defaultdict(list)
    for ugc in user_giftcodes:
        redemption_map[ugc.giftcode].append({
            "fid": ugc.fid,
            "nickname": user_map.get(ugc.fid, "N/A"),
            "status": ugc.status
        })

    return templates.TemplateResponse("giftcodes.html", {
        "request": request,
        "all_codes": all_codes,
        "redemption_map": redemption_map
    })

@app.get("/logs", response_class=HTMLResponse)
async def read_logs(request: Request, authenticated: bool = Depends(is_authenticated), changes_session: Session = Depends(get_changes_session), users_session: Session = Depends(get_users_session)):
    if not authenticated:
        return RedirectResponse(url="/login", status_code=303)

    # Fetch all users and create a FID -> nickname map
    users = users_session.exec(select(User)).all()
    user_map = {user.fid: user.nickname for user in users}

    nickname_changes = changes_session.exec(select(NicknameChange)).all()
    furnace_changes = changes_session.exec(select(FurnaceChange)).all()

    logs = nickname_changes + furnace_changes

    # Sort logs by date
    try:
        logs.sort(key=lambda x: datetime.fromisoformat(x.change_date), reverse=True)
    except (ValueError, TypeError): # Handle cases where change_date might not be a valid ISO format string
        pass # Or add more robust error handling/logging

    return templates.TemplateResponse("logs.html", {"request": request, "logs": logs, "user_map": user_map})
