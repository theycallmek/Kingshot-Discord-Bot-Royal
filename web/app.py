from fastapi import FastAPI, Request, Form, Depends, Body
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware
from sqlalchemy.orm import selectinload, Session
from sqlmodel import create_engine, Session, select, SQLModel
from .models import User, UserGiftCode, NicknameChange, FurnaceChange, AttendanceRecord, GiftCode, BearNotification, BearNotificationEmbed, NotificationDays, Alliance
import os
from datetime import datetime, date, timedelta, time
from collections import defaultdict
import calendar
from pydantic import BaseModel, field_validator
from typing import Optional, List, Any
import pytz
import re
import plotly.graph_objects as go
import plotly.utils
import json

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
alliance_engine = create_engine(f"sqlite:///{os.path.join(DB_DIR, 'alliance.sqlite')}", connect_args={"check_same_thread": False})


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
with alliance_engine.connect() as connection:
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

def get_alliance_session():
    with Session(alliance_engine) as session:
        yield session

# --- Helper Functions ---
def get_alliance_nicknames(alliance_session: Session) -> dict:
    """Get a mapping of alliance IDs to nicknames from the database."""
    alliances = alliance_session.exec(select(Alliance)).all()
    return {str(alliance.alliance_id): alliance.name for alliance in alliances}

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
async def read_root(request: Request, authenticated: bool = Depends(is_authenticated),
                   users_session: Session = Depends(get_users_session),
                   changes_session: Session = Depends(get_changes_session),
                   alliance_session: Session = Depends(get_alliance_session)):
    if not authenticated:
        return RedirectResponse(url="/login", status_code=303)
    
    # Get all users with their current furnace levels and alliances
    users = users_session.exec(select(User)).all()
    
    # Get all furnace changes
    furnace_changes = changes_session.exec(select(FurnaceChange)).all()
    
    # Create a map of users by fid for quick lookup
    user_map = {user.fid: user for user in users}
    
    # Process data for the graph
    # We need to track each user's furnace level over time to calculate alliance totals
    
    # Dictionary to store user's furnace level history: {fid: {date: level}}
    user_level_history = defaultdict(dict)
    
    # First, populate with all changes
    for change in furnace_changes:
        if change.fid in user_map:
            try:
                change_date = datetime.fromisoformat(change.change_date).date()
                user_level_history[change.fid][change_date] = change.new_furnace_lv
            except (ValueError, TypeError):
                continue
    
    # Add current levels for today
    today = date.today()
    for user in users:
        if user.furnace_lv:
            user_level_history[user.fid][today] = user.furnace_lv
    
    # Get all unique dates and sort them
    all_dates = set()
    for fid, date_levels in user_level_history.items():
        all_dates.update(date_levels.keys())
    sorted_dates = sorted(all_dates)
    
    # For each date, reconstruct each user's level and calculate alliance totals
    # Dictionary to store: {date: {alliance: total_level}}
    daily_alliance_totals = defaultdict(lambda: defaultdict(int))
    
    for current_date in sorted_dates:
        # Dictionary to track each user's level on this date: {fid: level}
        user_levels_on_date = {}
        
        # For each user, find their level on this date
        for fid, date_levels in user_level_history.items():
            # Find the most recent level at or before current_date
            level_on_date = None
            for check_date in sorted(date_levels.keys()):
                if check_date <= current_date:
                    level_on_date = date_levels[check_date]
                else:
                    break
            
            if level_on_date is not None:
                user_levels_on_date[fid] = level_on_date
        
        # Now calculate alliance totals for this date
        for fid, level in user_levels_on_date.items():
            if fid in user_map:
                user = user_map[fid]
                if user.alliance:
                    daily_alliance_totals[current_date][user.alliance] += level
    
    # Calculate totals and prepare data for Plotly

    # Get alliance nicknames from database
    alliance_nicknames = get_alliance_nicknames(alliance_session)

    # Filter to only include alliances 1, 2, 3
    target_alliances = ['1', '2', '3']

    # Prepare traces for each alliance (TOTAL graph)
    traces_total = []
    colors = {'1': '#FF6B6B', '2': '#4ECDC4', '3': '#45B7D1'}
    alliance_names = {aid: alliance_nicknames.get(aid, f'Alliance {aid}') for aid in target_alliances}

    for alliance in target_alliances:
        dates = []
        totals = []

        for date_key in sorted_dates:
            if alliance in daily_alliance_totals[date_key]:
                total_level = daily_alliance_totals[date_key][alliance]
                dates.append(date_key.strftime('%Y-%m-%d'))
                totals.append(total_level)

        if dates:  # Only add trace if we have data
            traces_total.append(go.Scatter(
                x=dates,
                y=totals,
                mode='lines+markers',
                name=alliance_names[alliance],
                line=dict(color=colors[alliance], width=3),
                marker=dict(size=8)
            ))

    # Create the total figure
    fig_total = go.Figure(data=traces_total)

    # Update layout for dark theme
    fig_total.update_layout(
        title='Town Center Level/Days',
        xaxis_title='Date',
        yaxis_title='Total Town Center Level',
        hovermode='x unified',
        template='plotly_dark',
        plot_bgcolor='#1e1e1e',
        paper_bgcolor='#1e1e1e',
        font=dict(color='#e0e0e0'),
        margin=dict(l=40, r=10, t=80, b=40),
        legend=dict(
            orientation="h",
            yanchor="bottom",
            y=1.02,
            xanchor="right",
            x=1
        )
    )

    # Convert to JSON for embedding in template
    graph_json = json.dumps(fig_total, cls=plotly.utils.PlotlyJSONEncoder)

    # --- NEW: Calculate average town level per alliance by day ---
    # Dictionary to store: {date: {alliance: {total: int, count: int}}}
    daily_alliance_stats = defaultdict(lambda: defaultdict(lambda: {'total': 0, 'count': 0}))

    for current_date in sorted_dates:
        # For each user, find their level on this date
        for fid, date_levels in user_level_history.items():
            # Find the most recent level at or before current_date
            level_on_date = None
            for check_date in sorted(date_levels.keys()):
                if check_date <= current_date:
                    level_on_date = date_levels[check_date]
                else:
                    break

            if level_on_date is not None and fid in user_map:
                user = user_map[fid]
                if user.alliance:
                    daily_alliance_stats[current_date][user.alliance]['total'] += level_on_date
                    daily_alliance_stats[current_date][user.alliance]['count'] += 1

    # Prepare traces for average graph
    traces_avg = []

    for alliance in target_alliances:
        dates = []
        averages = []

        for date_key in sorted_dates:
            if alliance in daily_alliance_stats[date_key]:
                stats = daily_alliance_stats[date_key][alliance]
                if stats['count'] > 0:
                    avg_level = stats['total'] / stats['count']
                    dates.append(date_key.strftime('%Y-%m-%d'))
                    averages.append(round(avg_level, 2))

        if dates:  # Only add trace if we have data
            traces_avg.append(go.Scatter(
                x=dates,
                y=averages,
                mode='lines+markers',
                name=alliance_names[alliance],
                line=dict(color=colors[alliance], width=3),
                marker=dict(size=8)
            ))

    # Create the average figure
    fig_avg = go.Figure(data=traces_avg)

    # Update layout for dark theme
    fig_avg.update_layout(
        title='Average Town Center Level/Days',
        xaxis_title='Date',
        yaxis_title='Average Town Center Level',
        hovermode='x unified',
        template='plotly_dark',
        plot_bgcolor='#1e1e1e',
        paper_bgcolor='#1e1e1e',
        font=dict(color='#e0e0e0'),
        margin=dict(l=40, r=10, t=80, b=40),
        legend=dict(
            orientation="h",
            yanchor="bottom",
            y=1.02,
            xanchor="right",
            x=1
        )
    )

    # Convert average graph to JSON
    graph_avg_json = json.dumps(fig_avg, cls=plotly.utils.PlotlyJSONEncoder)
    
    return templates.TemplateResponse("dashboard.html", {
        "request": request,
        "graph_json": graph_json,
        "graph_avg_json": graph_avg_json
    })

@app.get("/members", response_class=HTMLResponse)
async def read_members(request: Request, authenticated: bool = Depends(is_authenticated),
                      session: Session = Depends(get_users_session),
                      alliance_session: Session = Depends(get_alliance_session)):
    if not authenticated:
        return RedirectResponse(url="/login", status_code=303)

    users = session.exec(select(User)).all()
    alliance_nicknames = get_alliance_nicknames(alliance_session)
    return templates.TemplateResponse("members.html", {
        "request": request,
        "users": users,
        "alliance_nicknames": alliance_nicknames
    })

def dec_to_hex(decimal_color):
    if decimal_color is None:
        return None
    return f"#{decimal_color:06x}"

class BearNotificationEmbedPydantic(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    color: Optional[int] = None
    image_url: Optional[str] = None
    thumbnail_url: Optional[str] = None
    footer: Optional[str] = None
    author: Optional[str] = None
    mention_message: Optional[str] = None

    class Config:
        from_attributes = True

class BearNotificationWithNickname(BaseModel):
    id: Optional[int] = None
    description: Optional[str] = None
    channel_id: Optional[str] = None
    hour: Optional[int] = None
    minute: Optional[int] = None
    repeat_minutes: Optional[str] = None
    repeat_enabled: Optional[bool] = None
    is_enabled: Optional[int] = None
    mention_type: Optional[str] = None
    notification_type: Optional[int] = None
    next_notification: Optional[datetime] = None
    created_by: Optional[int] = None
    embeds: List[BearNotificationEmbedPydantic] = []
    notification_days: Optional[object] = None
    created_by_nickname: Optional[str] = None
    embed_title: Optional[str] = None

    @field_validator('channel_id', mode='before')
    @classmethod
    def validate_channel_id(cls, v: Any) -> Optional[str]:
        if v is None:
            return None
        return str(v)

    class Config:
        arbitrary_types_allowed = True
        from_attributes = True

@app.get("/api/thumbnails")
async def get_thumbnails(authenticated: bool = Depends(is_authenticated)):
    if not authenticated:
        return JSONResponse(status_code=401, content={"error": "Unauthorized"})

    thumbnails_dir = os.path.join("web", "static", "images", "thumbnails")
    thumbnails = []

    if os.path.exists(thumbnails_dir):
        for filename in os.listdir(thumbnails_dir):
            if filename.lower().endswith(('.png', '.jpg', '.jpeg', '.gif', '.webp', '.svg')):
                thumbnails.append({
                    "filename": filename,
                    "url": f"/static/images/thumbnails/{filename}"
                })

    # Sort alphabetically
    thumbnails.sort(key=lambda x: x['filename'].lower())

    return JSONResponse(content={"thumbnails": thumbnails})

@app.get("/events", response_class=HTMLResponse)
async def read_events(request: Request, authenticated: bool = Depends(is_authenticated), beartime_session: Session = Depends(get_beartime_session), users_session: Session = Depends(get_users_session)):
    if not authenticated:
        return RedirectResponse(url="/login", status_code=303)

    today = date.today()
    cal = calendar.Calendar()
    month_days = cal.monthdatescalendar(today.year, today.month)

    calendar_start_date = month_days[0][0]
    calendar_end_date = month_days[-1][-1]

    users = users_session.exec(select(User)).all()
    user_map = {user.fid: user.nickname for user in users}

    statement = select(BearNotification).options(selectinload(BearNotification.embeds), selectinload(BearNotification.notification_days))
    all_events_from_db = beartime_session.exec(statement).all()

    events_map = defaultdict(list)

    for event in all_events_from_db:
        if not event.next_notification:
            continue

        if isinstance(event.repeat_minutes, int):
            event.repeat_minutes = str(event.repeat_minutes)

        base_event_model = BearNotificationWithNickname.model_validate(event)
        base_event_model.created_by_nickname = user_map.get(event.created_by, "Unknown")
        base_event_model.embed_title = event.embeds[0].title if event.embeds else "No Title"

        occurrence = event.next_notification
        if occurrence.tzinfo is None:
            occurrence = pytz.utc.localize(occurrence)

        if event.repeat_enabled and occurrence.date() < calendar_start_date:
            if str(event.repeat_minutes).isdigit() and int(event.repeat_minutes) > 0:
                repeat_minutes = int(event.repeat_minutes)
                time_diff_minutes = (datetime.combine(calendar_start_date, time.min, tzinfo=pytz.utc) - occurrence).total_seconds() / 60
                if time_diff_minutes > 0:
                    periods_to_jump = int(time_diff_minutes / repeat_minutes)
                    occurrence += timedelta(minutes=repeat_minutes * periods_to_jump)
                while occurrence.date() < calendar_start_date:
                    occurrence += timedelta(minutes=repeat_minutes)

            elif event.repeat_minutes == "fixed" and event.notification_days:
                 weekdays = set(map(int, event.notification_days.weekday.split('|')))
                 day_iter = calendar_start_date
                 found = False
                 while day_iter <= calendar_end_date:
                     if day_iter.weekday() in weekdays and day_iter >= event.next_notification.date():
                         occurrence = datetime.combine(day_iter, event.next_notification.time(), tzinfo=occurrence.tzinfo)
                         found = True
                         break
                     day_iter += timedelta(days=1)
                 if not found:
                     continue

        while occurrence.date() <= calendar_end_date:
            clone = base_event_model.model_copy(deep=True)
            clone.next_notification = occurrence
            events_map[occurrence.date()].append(clone)

            if not event.repeat_enabled:
                break

            if str(event.repeat_minutes).isdigit() and int(event.repeat_minutes) > 0:
                occurrence += timedelta(minutes=int(event.repeat_minutes))
            elif event.repeat_minutes == "fixed" and event.notification_days:
                weekdays = set(map(int, event.notification_days.weekday.split('|')))
                next_day_iter = occurrence.date() + timedelta(days=1)
                found = False
                while next_day_iter <= calendar_end_date:
                    if next_day_iter.weekday() in weekdays:
                        occurrence = datetime.combine(next_day_iter, event.next_notification.time(), tzinfo=occurrence.tzinfo)
                        found = True
                        break
                    next_day_iter += timedelta(days=1)
                if not found:
                    break
            else:
                break

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

    all_codes = giftcode_session.exec(select(GiftCode)).all()
    user_giftcodes = giftcode_session.exec(select(UserGiftCode)).all()
    users = users_session.exec(select(User)).all()
    user_map = {user.fid: user.nickname for user in users}

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
    description: Optional[str] = None
    channel_id: str
    hour: int
    minute: int
    repeat_minutes: str
    mention_type: Optional[str] = None
    notification_type: int
    is_enabled: bool = True
    next_notification: datetime
    message_type: str
    weekdays: Optional[List[int]] = None
    custom_times: Optional[str] = None
    embed_title: Optional[str] = None
    embed_description: Optional[str] = None
    embed_color: Optional[str] = None
    embed_footer: Optional[str] = None
    embed_author: Optional[str] = None
    embed_image_url: Optional[str] = None
    embed_thumbnail_url: Optional[str] = None
    embed_mention_message: Optional[str] = None

class EventCreate(BaseModel):
    description: Optional[str] = None
    channel_id: str
    hour: int
    minute: int
    timezone: str
    repeat_minutes: str
    mention_type: str
    notification_type: int
    is_enabled: bool = True
    next_notification: datetime
    message_type: str
    weekdays: Optional[List[int]] = None
    custom_times: Optional[str] = None
    embed_title: Optional[str] = None
    embed_description: Optional[str] = None
    embed_color: Optional[str] = None
    embed_footer: Optional[str] = None
    embed_author: Optional[str] = None
    embed_image_url: Optional[str] = None
    embed_thumbnail_url: Optional[str] = None
    embed_mention_message: Optional[str] = None

@app.post("/create_event", response_class=JSONResponse)
async def create_event(
    request: Request,
    authenticated: bool = Depends(is_authenticated),
    beartime_session: Session = Depends(get_beartime_session),
    data: EventCreate = Body(...)
):
    if not authenticated:
        return JSONResponse(content={"success": False, "error": "Unauthorized"}, status_code=401)

    try:
        full_description = ""
        if data.notification_type == 6 and data.custom_times:
            full_description = f"CUSTOM_TIMES:{data.custom_times}|"

        if data.message_type == 'embed':
            full_description += "EMBED_MESSAGE:true"
        elif data.message_type == 'plain':
            full_description += f"PLAIN_MESSAGE:{data.description}"

        if data.next_notification.tzinfo is None:
            aware_dt = pytz.utc.localize(data.next_notification)
        else:
            aware_dt = data.next_notification

        new_notification = BearNotification(
            guild_id=1,
            channel_id=int(data.channel_id),
            hour=data.hour,
            minute=data.minute,
            timezone=data.timezone,
            description=full_description,
            notification_type=data.notification_type,
            mention_type=data.mention_type,
            repeat_enabled=1 if data.repeat_minutes and data.repeat_minutes != "0" else 0,
            repeat_minutes=data.repeat_minutes,
            is_enabled=1 if data.is_enabled else 0,
            created_by=0,
            next_notification=aware_dt # Pass datetime object directly
        )
        beartime_session.add(new_notification)
        beartime_session.flush()

        if data.message_type == 'embed':
            new_embed = BearNotificationEmbed(
                notification_id=new_notification.id,
                title=data.embed_title,
                description=data.embed_description,
                color=int(data.embed_color.lstrip('#'), 16) if data.embed_color else None,
                image_url=data.embed_image_url,
                thumbnail_url=data.embed_thumbnail_url,
                footer=data.embed_footer,
                author=data.embed_author,
                mention_message=data.embed_mention_message if data.embed_mention_message else None
            )
            beartime_session.add(new_embed)

        if data.repeat_minutes == "fixed" and data.weekdays:
            sorted_days = sorted(data.weekdays)
            weekday_str = "|".join(map(str, sorted_days))
            new_notification_days = NotificationDays(notification_id=new_notification.id, weekday=weekday_str)
            beartime_session.add(new_notification_days)

        beartime_session.commit()
        return JSONResponse(content={"success": True, "id": new_notification.id})

    except Exception as e:
        return JSONResponse(content={"success": False, "error": str(e)}, status_code=500)


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

    full_description = ""
    if data.notification_type == 6 and data.custom_times:
        full_description = f"CUSTOM_TIMES:{data.custom_times}|"

    if data.message_type == 'embed':
        full_description += "EMBED_MESSAGE:true"
    elif data.message_type == 'plain':
        full_description += f"PLAIN_MESSAGE:{data.description}"
    notification.description = full_description

    notification.channel_id = int(data.channel_id)
    notification.hour = data.hour
    notification.minute = data.minute
    notification.repeat_minutes = data.repeat_minutes
    notification.mention_type = data.mention_type or "none"
    notification.notification_type = data.notification_type
    notification.repeat_enabled = 1 if data.repeat_minutes and data.repeat_minutes != "0" else 0
    notification.is_enabled = 1 if data.is_enabled else 0

    if data.next_notification.tzinfo is None:
        aware_dt = pytz.utc.localize(data.next_notification)
    else:
        aware_dt = data.next_notification
    notification.next_notification = aware_dt

    if data.message_type == 'embed':
        if not notification.embeds:
            embed = BearNotificationEmbed(notification_id=notification.id)
            beartime_session.add(embed)
        else:
            embed = notification.embeds[0]

        embed.title = data.embed_title
        embed.description = data.embed_description
        embed.color = int(data.embed_color.lstrip('#'), 16) if data.embed_color else None
        embed.footer = data.embed_footer
        embed.author = data.embed_author
        embed.image_url = data.embed_image_url
        embed.thumbnail_url = data.embed_thumbnail_url
        embed.mention_message = data.embed_mention_message if data.embed_mention_message else None

    if data.repeat_minutes == "fixed":
        if not notification.notification_days:
            new_notification_days = NotificationDays(notification_id=notification.id, weekday="|".join(map(str, sorted(data.weekdays))))
            beartime_session.add(new_notification_days)
        elif data.weekdays:
            notification.notification_days.weekday = "|".join(map(str, sorted(data.weekdays)))
    elif notification.notification_days:
        beartime_session.delete(notification.notification_days)

    beartime_session.add(notification)
    beartime_session.commit()
    beartime_session.refresh(notification)

    return JSONResponse(content={"success": True})

@app.get("/logs", response_class=HTMLResponse)
async def read_logs(request: Request, authenticated: bool = Depends(is_authenticated), changes_session: Session = Depends(get_changes_session), users_session: Session = Depends(get_users_session)):
    if not authenticated:
        return RedirectResponse(url="/login", status_code=303)

    users = users_session.exec(select(User)).all()
    user_map = {user.fid: user.nickname for user in users}

    nickname_changes = changes_session.exec(select(NicknameChange)).all()
    furnace_changes = changes_session.exec(select(FurnaceChange)).all()

    try:
        nickname_changes.sort(key=lambda x: datetime.fromisoformat(x.change_date), reverse=True)
        furnace_changes.sort(key=lambda x: datetime.fromisoformat(x.change_date), reverse=True)
    except (ValueError, TypeError):
        pass

    return templates.TemplateResponse("logs.html", {
        "request": request,
        "nickname_changes": nickname_changes,
        "furnace_changes": furnace_changes,
        "user_map": user_map
    })
