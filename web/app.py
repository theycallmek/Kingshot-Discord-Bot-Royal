from fastapi import FastAPI, Request, Form, Depends, Body
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware
from sqlalchemy.orm import selectinload, Session
from sqlmodel import create_engine, Session, select, SQLModel
from .models import User, UserGiftCode, NicknameChange, FurnaceChange, AttendanceRecord, GiftCode, BearNotification, BearNotificationEmbed, NotificationDays
import os
from datetime import datetime, date, timedelta
from collections import defaultdict
import calendar
from pydantic import BaseModel
from typing import Optional, List

# --- Configuration ---
SECRET_KEY = os.environ.get("SECRET_KEY", "your-secret-key")
WEB_DASHBOARD_PASSWORD = os.environ.get("WEB_DASHBOARD_PASSWORD", "password")

app = FastAPI()

# Mount static files
app.mount("/static", StaticFiles(directory="web/static"), name="static")

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


def dec_to_hex(decimal_color):
    if decimal_color is None:
        return None
    return f"#{decimal_color:06x}"


# This is an API Model (also called a DTO), not a table model.
# It's used to structure data specifically for the frontend.
class BearNotificationWithNickname(BaseModel):
    # Copy all fields from BearNotification
    id: Optional[int] = None
    description: Optional[str] = None
    channel_id: Optional[int] = None
    hour: Optional[int] = None
    minute: Optional[int] = None
    repeat_minutes: Optional[str] = None
    repeat_enabled: Optional[bool] = None
    mention_type: Optional[str] = None
    notification_type: Optional[int] = None
    next_notification: Optional[datetime] = None
    created_by: Optional[int] = None

    # Relations (as lists of models, not Mapped)
    embeds: List = []
    notification_days: Optional[object] = None

    # Additional field
    created_by_nickname: Optional[str] = None
    embed_title: Optional[str] = None

    class Config:
        arbitrary_types_allowed = True
        from_attributes = True  # This allows model_validate to work with SQLModel objects


@app.get("/events", response_class=HTMLResponse)
async def read_events(request: Request, authenticated: bool = Depends(is_authenticated), beartime_session: Session = Depends(get_beartime_session), users_session: Session = Depends(get_users_session)):
    if not authenticated:
        return RedirectResponse(url="/login", status_code=303)

    today = date.today()
    cal = calendar.Calendar()
    month_days = cal.monthdatescalendar(today.year, today.month)

    users = users_session.exec(select(User)).all()
    user_map = {user.fid: user.nickname for user in users}

    statement = select(BearNotification).options(selectinload(BearNotification.embeds), selectinload(BearNotification.notification_days))
    events_query = beartime_session.exec(statement).all()

    events_map = defaultdict(list)
    for event in events_query:
        nickname = user_map.get(event.created_by, "Unknown")

        # Coerce repeat_minutes to string before validation to prevent type errors
        if isinstance(event.repeat_minutes, int):
            event.repeat_minutes = str(event.repeat_minutes)

        event_with_nickname = BearNotificationWithNickname.model_validate(event)
        event_with_nickname.created_by_nickname = nickname

        # Add a placeholder for embed_title for events that might not have one
        event_with_nickname.embed_title = event.embeds[0].title if event.embeds else "No Title"

        if event_with_nickname.next_notification and event_with_nickname.next_notification.date() >= today:
            events_map[event_with_nickname.next_notification.date()].append(event_with_nickname)

        if event_with_nickname.repeat_enabled and event_with_nickname.next_notification:
            if event_with_nickname.repeat_minutes.isdigit():
                repeat_minutes = int(event_with_nickname.repeat_minutes)
                if repeat_minutes > 0:
                    next_occurrence = event_with_nickname.next_notification + timedelta(minutes=repeat_minutes)
                    while next_occurrence.month == today.month:
                        if next_occurrence.date() >= today:
                            clone = BearNotificationWithNickname.model_validate(event_with_nickname)
                            clone.next_notification = next_occurrence
                            events_map[next_occurrence.date()].append(clone)
                        next_occurrence += timedelta(minutes=repeat_minutes)
            elif event_with_nickname.repeat_minutes == "fixed" and event.notification_days:
                weekdays = list(map(int, event.notification_days.weekday.split('|')))
                current_date = event_with_nickname.next_notification.date()
                while current_date.month <= today.month:
                    if current_date.weekday() in weekdays and current_date >= today:
                        clone = BearNotificationWithNickname.model_validate(event_with_nickname)
                        clone.next_notification = datetime.combine(current_date, event_with_nickname.next_notification.time())
                        events_map[current_date].append(clone)
                    current_date += timedelta(days=1)

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

class EventUpdate(BaseModel):
    id: int
    description: str
    channel_id: int
    hour: int
    minute: int
    repeat_minutes: str
    repeat_enabled: bool
    mention_type: str
    notification_type: int
    next_notification: datetime
    weekdays: Optional[List[int]] = None

    # Embed fields
    embed_title: str
    embed_description: str
    embed_color: str
    embed_footer: str
    embed_author: str
    embed_image_url: str
    embed_thumbnail_url: str
    embed_mention_message: str

@app.post("/update_event", response_class=JSONResponse)
async def update_event(
    request: Request,
    authenticated: bool = Depends(is_authenticated),
    beartime_session: Session = Depends(get_beartime_session),
    data: EventUpdate = Body(...)
):
    if not authenticated:
        return JSONResponse(content={"success": False, "error": "Unauthorized"}, status_code=401)

    notification = beartime_session.get(BearNotification, data.id)
    if not notification:
        return JSONResponse(content={"success": False, "error": "Event not found"}, status_code=404)

    # Update notification fields
    notification.description = data.description
    notification.channel_id = data.channel_id
    notification.hour = data.hour
    notification.minute = data.minute
    notification.repeat_minutes = data.repeat_minutes
    notification.mention_type = data.mention_type
    notification.notification_type = data.notification_type
    notification.next_notification = data.next_notification
    notification.repeat_enabled = data.repeat_enabled

    if not notification.repeat_enabled:
        notification.repeat_minutes = ""

    # Update embed fields
    if notification.embeds:
        embed = notification.embeds[0]
        embed.title = data.embed_title
        embed.description = data.embed_description
        embed.color = int(data.embed_color.lstrip('#'), 16) if data.embed_color else None
        embed.footer = data.embed_footer
        embed.author = data.embed_author
        embed.image_url = data.embed_image_url
        embed.thumbnail_url = data.embed_thumbnail_url
        embed.mention_message = data.embed_mention_message

    # Handle weekdays for fixed repeat
    if data.repeat_minutes == "fixed":
        if notification.notification_days:
            beartime_session.delete(notification.notification_days)

        if data.weekdays:
            sorted_days = sorted(data.weekdays)
            weekday_str = "|".join(map(str, sorted_days))
            new_notification_days = NotificationDays(notification_id=notification.id, weekday=weekday_str)
            beartime_session.add(new_notification_days)
    else:
        if notification.notification_days:
            beartime_session.delete(notification.notification_days)

    beartime_session.add(notification)
    beartime_session.commit()
    beartime_session.refresh(notification)

    return JSONResponse(content={"success": True})

@app.get("/logs", response_class=HTMLResponse)
async def read_logs(request: Request, authenticated: bool = Depends(is_authenticated), changes_session: Session = Depends(get_changes_session), users_session: Session = Depends(get_users_session)):
    if not authenticated:
        return RedirectResponse(url="/login", status_code=303)

    # Fetch all users and create a FID -> nickname map
    users = users_session.exec(select(User)).all()
    user_map = {user.fid: user.nickname for user in users}

    nickname_changes = changes_session.exec(select(NicknameChange)).all()
    furnace_changes = changes_session.exec(select(FurnaceChange)).all()

    # Sort logs by date
    try:
        nickname_changes.sort(key=lambda x: datetime.fromisoformat(x.change_date), reverse=True)
        furnace_changes.sort(key=lambda x: datetime.fromisoformat(x.change_date), reverse=True)
    except (ValueError, TypeError): # Handle cases where change_date might not be a valid ISO format string
        pass # Or add more robust error handling/logging

    return templates.TemplateResponse("logs.html", {
        "request": request,
        "nickname_changes": nickname_changes,
        "furnace_changes": furnace_changes,
        "user_map": user_map
    })
