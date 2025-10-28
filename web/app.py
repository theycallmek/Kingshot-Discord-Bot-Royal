from fastapi import FastAPI, Request, Form, Depends, Body, UploadFile, File
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse, StreamingResponse
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
from paddleocr import PaddleOCR
import tempfile
from pathlib import Path
import aiohttp
import asyncio
import ssl
import hashlib
import cv2
import numpy as np

# --- Configuration ---
SECRET_KEY = os.environ.get("SECRET_KEY", "your-secret-key")
WEB_DASHBOARD_PASSWORD = os.environ.get("WEB_DASHBOARD_PASSWORD", "password")
API_SECRET = "mN4!pQs6JrYwV9"  # Secret for game API requests

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
cache_engine = create_engine(f"sqlite:///{os.path.join(DB_DIR, 'web_cache.sqlite')}", connect_args={"check_same_thread": False})

# Create only OCR tables in cache database
# Import OCR models to register them, but use selective table creation
from .ocr_models import OCRPlayerMapping, OCREventData, UserAvatarCache
from sqlalchemy import inspect

# Only create OCR tables if they don't exist
inspector = inspect(cache_engine)
existing_tables = inspector.get_table_names()
if 'ocr_player_mapping' not in existing_tables:
    OCRPlayerMapping.__table__.create(cache_engine)
if 'ocr_event_data' not in existing_tables:
    OCREventData.__table__.create(cache_engine)
if 'user_avatar_cache' not in existing_tables:
    UserAvatarCache.__table__.create(cache_engine)

# Initialize PaddleOCR reader (loaded once at startup)
ocr_reader = None

def get_ocr_reader():
    global ocr_reader
    if ocr_reader is None:
        ocr_reader = PaddleOCR(lang='en')
    return ocr_reader


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
with cache_engine.connect() as connection:
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

def get_cache_session():
    with Session(cache_engine) as session:
        yield session

# --- Helper Functions ---
def get_alliance_nicknames(alliance_session: Session) -> dict:
    "Get a mapping of alliance IDs to nicknames from the database."
    alliances = alliance_session.exec(select(Alliance)).all()
    return {str(alliance.alliance_id): alliance.name for alliance in alliances}

def parse_player_name(player_name: str) -> (str, str):
    "Parse player name to separate alliance tag from username."
    match = re.match(r'^\[(.*?)\](.*)$', player_name)
    if match:
        return match.group(1), match.group(2).strip()
    return "", player_name.strip()

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
                   alliance_session: Session = Depends(get_alliance_session),
                   cache_session: Session = Depends(get_cache_session)):
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

    # Get all alliances from database dynamically
    all_alliances = alliance_session.exec(select(Alliance)).all()
    target_alliances = [str(alliance.alliance_id) for alliance in all_alliances]
    alliance_nicknames = get_alliance_nicknames(alliance_session)

    # Generate colors dynamically for each alliance
    color_palette = ['#FF6B6B', '#4ECDC4', '#45B7D1', '#FFA07A', '#98D8C8', '#F7DC6F', '#BB8FCE', '#85C1E2', '#F8B195', '#C06C84']
    colors = {aid: color_palette[i % len(color_palette)] for i, aid in enumerate(target_alliances)}
    alliance_names = {aid: alliance_nicknames.get(aid, f'Alliance {aid}') for aid in target_alliances}

    # Prepare traces for each alliance (TOTAL graph)
    traces_total = []

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

    # --- NEW: Furnace Level Distribution Histogram ---
    # Get current furnace levels for all users in target alliances
    furnace_distribution = defaultdict(lambda: defaultdict(int))

    for user in users:
        if user.alliance in target_alliances and user.furnace_lv:
            furnace_distribution[user.alliance][user.furnace_lv] += 1

    # Create histogram traces for each alliance
    hist_traces = []

    for alliance in target_alliances:
        if furnace_distribution[alliance]:
            levels = sorted(furnace_distribution[alliance].keys())
            counts = [furnace_distribution[alliance][level] for level in levels]

            hist_traces.append(go.Bar(
                x=levels,
                y=counts,
                name=alliance_names[alliance],
                marker=dict(color=colors[alliance]),
                hovertemplate='<b>TC Level %{x}</b><br>Members: %{y}<extra></extra>'
            ))

    # Create the histogram figure
    fig_hist = go.Figure(data=hist_traces)

    # Update layout for dark theme
    fig_hist.update_layout(
        title='Town Center Level Distribution',
        xaxis_title='Town Center Level',
        yaxis_title='Number of Members',
        barmode='group',
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
        ),
        xaxis=dict(
            tickmode='linear',
            tick0=0,
            dtick=1
        )
    )

    # Convert histogram to JSON
    graph_hist_json = json.dumps(fig_hist, cls=plotly.utils.PlotlyJSONEncoder)

    # --- NEW: Town Center Level Heatmap ---
    # Create a heatmap showing TC level distribution across alliances
    # Group TC levels into ranges for better visualization
    tc_ranges = [
        (1, 10, 'TC 1-10'),
        (11, 15, 'TC 11-15'),
        (16, 20, 'TC 16-20'),
        (21, 25, 'TC 21-25'),
        (26, 30, 'TC 26-30'),
        (31, 35, 'TC 31-35'),
        (36, 40, 'TC 36-40'),
        (41, 50, 'TC 41-50')
    ]

    # Create matrix: rows = alliances, columns = TC ranges
    heatmap_data = []
    alliance_labels = []

    for alliance in target_alliances:
        alliance_labels.append(alliance_names[alliance])
        row_data = []

        for min_tc, max_tc, _ in tc_ranges:
            count = 0
            for user in users:
                if user.alliance == alliance and user.furnace_lv:
                    if min_tc <= user.furnace_lv <= max_tc:
                        count += 1
            row_data.append(count)

        heatmap_data.append(row_data)

    # Create column labels
    column_labels = [label for _, _, label in tc_ranges]

    # Create heatmap figure
    fig_heatmap = go.Figure(data=go.Heatmap(
        z=heatmap_data,
        x=column_labels,
        y=alliance_labels,
        colorscale='Viridis',
        hoverongaps=False,
        hovertemplate='<b>%{y}</b><br>%{x}<br>Members: %{z}<extra></extra>',
        colorbar=dict(
            title=dict(
                text='Members',
                side='right'
            ),
            tickmode='linear',
            tick0=0,
            dtick=1
        )
    ))

    # Update layout for dark theme
    fig_heatmap.update_layout(
        title='Town Center Level Heatmap',
        xaxis_title='Town Center Level Range',
        yaxis_title='Alliance',
        template='plotly_dark',
        plot_bgcolor='#1e1e1e',
        paper_bgcolor='#1e1e1e',
        font=dict(color='#e0e0e0'),
        margin=dict(l=40, r=10, t=80, b=40)
    )

    # Reverse y-axis to show alliances in order
    fig_heatmap.update_yaxes(autorange='reversed')

    # Convert heatmap to JSON
    graph_heatmap_json = json.dumps(fig_heatmap, cls=plotly.utils.PlotlyJSONEncoder)

    # --- NEW: Bear Trap Event Stats ---
    thirty_days_ago = datetime.now() - timedelta(days=30)
    bear_trap_query = (
        select(OCREventData)
        .where(OCREventData.event_type == 'Bear Trap')
        .where(OCREventData.event_date >= thirty_days_ago)
        .where(OCREventData.damage_points.isnot(None))
    )
    bear_trap_data = cache_session.exec(bear_trap_query).all()

    player_damage = defaultdict(int)
    for record in bear_trap_data:
        player_damage[record.player_name] += record.damage_points

    sorted_players = sorted(player_damage.items(), key=lambda item: item[1], reverse=True)[:15]
    player_names = [item[0] for item in sorted_players]
    damage_values = [item[1] for item in sorted_players]

    fig_bear_trap = go.Figure(data=[go.Bar(
        x=player_names,
        y=damage_values,
        marker=dict(
            color=damage_values,
            colorscale='Viridis',
            showscale=True
        ),
        hovertemplate='<b>%{x}</b><br>Damage: %{y}<extra></extra>'
    )])

    fig_bear_trap.update_layout(
        title='Top 15 Bear Trap Damage Dealers (Last 30 Days)',
        xaxis_title='Player',
        yaxis_title='Total Damage',
        template='plotly_dark',
        plot_bgcolor='#1e1e1e',
        paper_bgcolor='#1e1e1e',
        font=dict(color='#e0e0e0'),
        margin=dict(l=40, r=10, t=80, b=40),
        xaxis={'categoryorder':'total descending'}
    )
    graph_bear_trap_json = json.dumps(fig_bear_trap, cls=plotly.utils.PlotlyJSONEncoder)


    return templates.TemplateResponse("dashboard.html", {
        "request": request,
        "graph_json": graph_json,
        "graph_avg_json": graph_avg_json,
        "graph_hist_json": graph_hist_json,
        "graph_heatmap_json": graph_heatmap_json,
        "graph_bear_trap_json": graph_bear_trap_json
    })

@app.get("/members", response_class=HTMLResponse)
async def read_members(request: Request, authenticated: bool = Depends(is_authenticated),
                      session: Session = Depends(get_users_session),
                      alliance_session: Session = Depends(get_alliance_session),
                      cache_session: Session = Depends(get_cache_session)):
    if not authenticated:
        return RedirectResponse(url="/login", status_code=303)

    users = session.exec(select(User)).all()
    alliance_nicknames = get_alliance_nicknames(alliance_session)

    # Get cached avatar URLs
    avatar_cache = cache_session.exec(select(UserAvatarCache)).all()
    avatar_map = {cache.fid: cache.avatar_url for cache in avatar_cache}

    # Create list of users with their avatar URLs
    users_with_avatars = []
    for user in users:
        user_dict = {
            "fid": user.fid,
            "nickname": user.nickname,
            "furnace_lv": user.furnace_lv,
            "kid": user.kid,
            "alliance": user.alliance,
            "avatar_url": avatar_map.get(user.fid)
        }
        users_with_avatars.append(user_dict)

    return templates.TemplateResponse("members.html", {
        "request": request,
        "users": users_with_avatars,
        "alliance_nicknames": alliance_nicknames
    })

@app.get("/bear-trap-map", response_class=HTMLResponse)
async def bear_trap_map(request: Request, authenticated: bool = Depends(is_authenticated),
                        session: Session = Depends(get_users_session),
                        alliance_session: Session = Depends(get_alliance_session)):
    if not authenticated:
        return RedirectResponse(url="/login", status_code=303)

    # Get all alliances
    alliances = alliance_session.exec(select(Alliance)).all()

    # Get all users
    users = session.exec(select(User)).all()

    # Group users by alliance and convert to dictionaries
    users_by_alliance = {}
    for user in users:
        alliance_id = str(user.alliance)
        if alliance_id not in users_by_alliance:
            users_by_alliance[alliance_id] = []
        users_by_alliance[alliance_id].append({
            "nickname": user.nickname,
            "fid": user.fid,
            "furnace_lv": user.furnace_lv
        })

    return templates.TemplateResponse("bear_trap_map.html", {
        "request": request,
        "alliances": alliances,
        "users_by_alliance": users_by_alliance
    })


@app.get("/attendance", response_class=HTMLResponse)
async def attendance(request: Request, authenticated: bool = Depends(is_authenticated)):
    if not authenticated:
        return RedirectResponse(url="/login", status_code=303)

    return templates.TemplateResponse("attendance.html", {
        "request": request
    })


@app.post("/api/process-attendance")
async def process_attendance(
    files: List[UploadFile] = File(...),
    event_name: str = Form(...),
    event_type: str = Form("Bear Trap"),
    authenticated: bool = Depends(is_authenticated)
):
    "Process uploaded screenshots for attendance tracking"
    if not authenticated:
        return JSONResponse({"error": "Not authenticated"}, status_code=401)

    try:
        # Create temporary directory for uploaded files
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)

            # Save uploaded files
            saved_files = []
            for file in files:
                file_path = temp_path / file.filename
                with open(file_path, "wb") as f:
                    content = await file.read()
                    f.write(content)
                saved_files.append(str(file_path))

            # Initialize OCR reader
            reader = get_ocr_reader()

            # Create OCR session ID
            event_date = datetime.now()
            session_id = f"{event_name.replace(' ', '_')}_{event_date.strftime('%Y%m%d_%H%M%S')}"

            # Process each image
            all_player_data = []
            for file_path in saved_files:
                # Preprocess image for improved OCR accuracy
                preprocessed_path = preprocess_image_for_ocr(file_path)

                # Extract data from image using PaddleOCR
                results = reader.predict(str(preprocessed_path))

                # Extract player scores from PaddleOCR results
                player_data = extract_player_scores_from_ocr(results, Path(file_path).name)
                all_player_data.extend(player_data)

            # Remove duplicates (keep highest confidence)
            unique_players = {}
            for data in all_player_data:
                name = data['player_name']
                if name not in unique_players or data['confidence'] > unique_players[name]['confidence']:
                    unique_players[name] = data

            player_data_list = list(unique_players.values())

            # Match players and store scores
            matched_count, matched_players, unmatched = match_and_store_scores(
                player_data_list, session_id, event_name, event_type, event_date
            )

            # Mark attendance for both matched and ghost players
            attendance_marked = mark_attendance_from_scores(matched_players, unmatched, event_name, session_id)

            # Prepare response
            return JSONResponse({
                "success": True,
                "session_id": session_id,
                "summary": {
                    "images_processed": len(files),
                    "players_detected": len(player_data_list),
                    "players_matched": matched_count,
                    "attendance_marked": attendance_marked
                },
                "matched_players": [
                    {
                        "player_name": p['player_name'],
                        "player_fid": p['player_fid'],
                        "ranking": p.get('ranking'),
                        "damage_points": p.get('damage_points'),
                        "confidence": p['confidence']
                    }
                    for p in matched_players
                ],
                "unmatched_players": [
                    {
                        "player_name": p['player_name'],
                        "player_fid": p['player_fid'],  # Will be "0000000000"
                        "ranking": p.get('ranking'),
                        "damage_points": p.get('damage_points'),
                        "confidence": p['confidence'],
                        "image_source": p['image_source']
                    }
                    for p in unmatched
                ]
            })

    except Exception as e:
        import traceback
        error_trace = traceback.format_exc()
        print(f"[ERROR] Exception in process_attendance:")
        print(error_trace)
        return JSONResponse({"error": str(e), "traceback": error_trace}, status_code=500)


@app.get("/api/dashboard-data")
async def get_dashboard_data(authenticated: bool = Depends(is_authenticated)):
    "Get dashboard statistics for attendance page"
    if not authenticated:
        return JSONResponse({"error": "Not authenticated"}, status_code=401)

    try:
        with Session(cache_engine) as cache_session, Session(attendance_engine) as att_session:
            # Get recent OCR processing sessions (last 10)
            recent_events_query = select(OCREventData).order_by(OCREventData.extracted_at.desc()).limit(100)
            recent_events_data = cache_session.exec(recent_events_query).all()

            # Group by processing_session
            sessions_dict = {}
            for event in recent_events_data:
                session_id = event.processing_session
                if session_id not in sessions_dict:
                    sessions_dict[session_id] = {
                        'session_id': session_id,
                        'event_name': event.event_name,
                        'event_type': event.event_type,
                        'event_date': event.event_date.isoformat(),
                        'player_count': 0,
                        'verified_count': 0,
                        'total_verifications': 0,
                        'extracted_at': event.extracted_at.isoformat()
                    }
                sessions_dict[session_id]['player_count'] += 1
                if event.verification_count > 1:
                    sessions_dict[session_id]['verified_count'] += 1
                sessions_dict[session_id]['total_verifications'] += event.verification_count

            recent_sessions = sorted(sessions_dict.values(),
                                    key=lambda x: x['extracted_at'],
                                    reverse=True)[:10]

            # Get top players by damage (across all events in last 30 days)
            thirty_days_ago = datetime.now() - timedelta(days=30)
            top_players_query = (
                select(OCREventData)
                .where(OCREventData.event_date >= thirty_days_ago)
                .where(OCREventData.damage_points.isnot(None))
                .order_by(OCREventData.damage_points.desc())
                .limit(50)
            )
            top_players_data = cache_session.exec(top_players_query).all()

            # Group by event to calculate ranks
            event_players = defaultdict(list)
            for record in top_players_data:
                event_players[record.processing_session].append(record)

            # Calculate ranks and stats
            player_stats = {}
            for records in event_players.values():
                sorted_by_damage = sorted(records, key=lambda p: -(p.damage_points or 0))
                for rank_idx, player_record in enumerate(sorted_by_damage, 1):
                    name = player_record.player_name
                    alliance_tag, username = parse_player_name(name)
                    if name not in player_stats:
                        player_stats[name] = {
                            'player_name': username,
                            'alliance_tag': alliance_tag,
                            'player_fid': player_record.player_fid or "0000000000",
                            'total_damage': 0,
                            'event_count': 0,
                            'avg_verification': 0,
                            'best_rank': None,
                            'total_verifications': 0
                        }
                    if player_stats[name]['best_rank'] is None or rank_idx < player_stats[name]['best_rank']:
                        player_stats[name]['best_rank'] = rank_idx

            # Sum totals
            player_events = defaultdict(set)
            for record in top_players_data:
                name = record.player_name
                player_stats[name]['total_damage'] += record.damage_points or 0
                player_stats[name]['total_verifications'] += record.verification_count
                player_events[name].add(record.processing_session)
            
            for name in player_stats:
                player_stats[name]['event_count'] = len(player_events[name])

            # Calculate averages and sort
            for name in player_stats:
                if player_stats[name]['event_count'] > 0:
                    player_stats[name]['avg_verification'] = player_stats[name]['total_verifications'] / player_stats[name]['event_count']

            top_players = sorted(player_stats.values(),
                               key=lambda x: x['total_damage'],
                               reverse=True)[:10]

            # Query UserAvatarCache to get avatar URLs for top players
            avatar_cache_data = cache_session.exec(select(UserAvatarCache)).all()
            avatar_map = {str(cache.fid): cache.avatar_url for cache in avatar_cache_data}
            
            # Add avatar URLs to each top player
            for player in top_players:
                player_fid = str(player.get('player_fid', ''))
                if player_fid != "0000000000":
                    # Matched player - get avatar from cache
                    player['avatar_url'] = avatar_map.get(player_fid, '/static/images/user-icon.svg')
                else:
                    # Ghost player - use generic avatar
                    player['avatar_url'] = '/static/images/user-icon.svg'

            # Get verification statistics
            all_event_data = cache_session.exec(select(OCREventData)).all()
            verification_stats = {
                'high_confidence': 0,  # 3+ verifications
                'medium_confidence': 0,  # 2 verifications
                'low_confidence': 0,  # 1 verification
                'total_records': len(all_event_data),
                'avg_confidence': 0
            }

            total_confidence = 0
            for record in all_event_data:
                if record.verification_count >= 3:
                    verification_stats['high_confidence'] += 1
                elif record.verification_count == 2:
                    verification_stats['medium_confidence'] += 1
                else:
                    verification_stats['low_confidence'] += 1
                total_confidence += record.data_confidence

            if len(all_event_data) > 0:
                verification_stats['avg_confidence'] = total_confidence / len(all_event_data)

            return JSONResponse({
                "success": True,
                "recent_events": recent_sessions,
                "top_players": top_players,
                "verification_stats": verification_stats
            })

    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/event-details/{session_id}")
async def get_event_details(session_id: str, authenticated: bool = Depends(is_authenticated)):
    "Get detailed player data for a specific event processing session"
    if not authenticated:
        return JSONResponse({"error": "Not authenticated"}, status_code=401)

    try:
        # Use sessions in context managers like get_dashboard_data does
        with Session(cache_engine) as cache_session, Session(users_engine) as users_session:
            # Get all event data for the given session
            event_data_query = select(OCREventData).where(OCREventData.processing_session == session_id)
            event_players = cache_session.exec(event_data_query).all()

            if not event_players:
                return JSONResponse({"error": "Event session not found"}, status_code=404)

            # Get all users for nickname mapping
            users = users_session.exec(select(User)).all()
            user_map = {user.fid: user.nickname for user in users}

            # Prepare player details
            players_details = []
            for player in event_players:
                is_matched = player.player_fid and player.player_fid != "0000000000"
                nickname = "N/A"
                if is_matched:
                    # FID is stored as an integer in User model, but string in OCREventData
                    try:
                        nickname = user_map.get(int(player.player_fid), "Unknown")
                    except (ValueError, TypeError):
                        nickname = "Invalid FID"

                alliance_tag, username = parse_player_name(player.player_name)
                players_details.append({
                    "player_name": username,
                    "alliance_tag": alliance_tag,
                    "player_fid": player.player_fid,
                    "is_matched": is_matched,
                    "nickname": nickname,
                    "ranking": player.ranking,
                    "damage_points": player.damage_points,
                    "ocr_confidence": round(player.ocr_confidence * 100, 1),
                    "verification_count": player.verification_count,
                    "image_source": player.image_source,
                })

            # Sort players by damage points (highest first) for rank calculation
            players_details.sort(key=lambda p: -(p.get('damage_points') or 0))
            
            # Assign calculated ranks based on damage (highest damage = rank 1)
            for idx, player in enumerate(players_details, 1):
                player['calculated_rank'] = idx

            # Query UserAvatarCache to get avatar URLs for matched players
            avatar_cache_data = cache_session.exec(select(UserAvatarCache)).all()
            avatar_map = {str(cache.fid): cache.avatar_url for cache in avatar_cache_data}
            
            # Add avatar URLs to each player
            for player in players_details:
                if player.get('is_matched'):
                    # Look up avatar from cache using player FID
                    player_fid = str(player.get('player_fid', ''))
                    player['avatar_url'] = avatar_map.get(player_fid, '/static/images/user-icon.svg')
                else:
                    # Use generic avatar for ghost players
                    player['avatar_url'] = '/static/images/user-icon.svg'

            # Get event metadata from the first player record
            first_player = event_players[0]
            event_info = {
                "session_id": first_player.processing_session,
                "event_name": first_player.event_name,
                "event_date": first_player.event_date.isoformat(),
                "player_count": len(players_details)
            }

            return JSONResponse({
                "success": True,
                "event_info": event_info,
                "players": players_details
            })

    except Exception as e:
        import traceback
        error_trace = traceback.format_exc()
        print(f"[ERROR] Exception in get_event_details for session {session_id}:")
        print(error_trace)
        return JSONResponse({"error": str(e), "trace": error_trace}, status_code=500)


@app.get("/api/past-events")
async def get_past_events(authenticated: bool = Depends(is_authenticated), beartime_session: Session = Depends(get_beartime_session)):
    if not authenticated:
        return JSONResponse({"error": "Not authenticated"}, status_code=401)

    try:
        now = datetime.now(pytz.utc)
        past_events_from_db = beartime_session.exec(
            select(BearNotification)
            .options(selectinload(BearNotification.embeds))
            .where(BearNotification.next_notification < now)
            .order_by(BearNotification.next_notification.desc())
            .limit(20)
        ).all()

        unique_events = {}
        for event in past_events_from_db:
            if event.embeds and event.embeds[0].title:
                title = event.embeds[0].title
                if title not in unique_events:
                    unique_events[title] = event.next_notification
        
        sorted_events = sorted(unique_events.items(), key=lambda item: item[1], reverse=True)
        
        recent_events = sorted_events[:5]

        events_list = [
            {"name": title, "date": dt.isoformat()}
            for title, dt in recent_events
        ]

        return JSONResponse({"success": True, "events": events_list})

    except Exception as e:
        import traceback
        print(traceback.format_exc())
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/refresh-avatars")
async def refresh_avatars(authenticated: bool = Depends(is_authenticated)):
    "Stream progress updates while fetching and caching avatar URLs for all users"
    if not authenticated:
        return JSONResponse({"error": "Not authenticated"}, status_code=401)

    async def generate_progress():
        try:
            with Session(users_engine) as users_session, Session(cache_engine) as cache_session:
                # Get all users
                users = users_session.exec(select(User)).all()

                # Track progress
                total_users = len(users)
                updated_count = 0
                skipped_count = 0
                error_count = 0
                current_index = 0

                # Send initial status
                yield f"data: {json.dumps({'type': 'init', 'total': total_users, 'message': f'Starting avatar refresh for {total_users} users...'})}\n\n"

                # Get existing cache entries
                cached_avatars = {}
                cached_entries = cache_session.exec(select(UserAvatarCache)).all()
                for entry in cached_entries:
                    cached_avatars[entry.fid] = entry

                yield f"data: {json.dumps({'type': 'status', 'message': 'Loaded cache entries. Checking which avatars need updating...'})}\n\n"

                # SSL context for API requests
                ssl_context = ssl.create_default_context()
                ssl_context.check_hostname = False
                ssl_context.verify_mode = ssl.CERT_NONE

                async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(ssl=ssl_context)) as session:
                    for user in users:
                        current_index += 1

                        # Check if we need to update this user's avatar
                        cached_entry = cached_avatars.get(user.fid)
                        if cached_entry:
                            # Skip if updated within last 24 hours
                            time_since_update = datetime.now() - cached_entry.last_updated
                            if time_since_update < timedelta(hours=24):
                                skipped_count += 1
                                progress = int((current_index / total_users) * 100)
                                yield f"data: {json.dumps({'type': 'progress', 'current': current_index, 'total': total_users, 'percent': progress, 'updated': updated_count, 'skipped': skipped_count, 'errors': error_count, 'message': f'Skipped {user.nickname} (cached recently)'})}\n\n"
                                continue

                        try:
                            # Send status update
                            yield f"data: {json.dumps({'type': 'status', 'message': f'Fetching avatar for {user.nickname}...'})}\n\n"

                            # Prepare API request (same logic as id_channel.py)
                            current_time = int(asyncio.get_event_loop().time() * 1000)
                            form = f"fid={user.fid}&time={current_time}"
                            sign = hashlib.md5((form + API_SECRET).encode('utf-8')).hexdigest()
                            form = f"sign={sign}&{form}"
                            headers = {'Content-Type': 'application/x-www-form-urlencoded'}

                            # Make API request
                            async with session.post(
                                'https://kingshot-giftcode.centurygame.com/api/player',
                                headers=headers,
                                data=form
                            ) as response:
                                if response.status == 200:
                                    data = await response.json()
                                    if data.get('data'):
                                        avatar_url = data['data'].get('avatar_image')

                                        # Update or create cache entry
                                        if cached_entry:
                                            cached_entry.avatar_url = avatar_url
                                            cached_entry.last_updated = datetime.now()
                                            cache_session.add(cached_entry)
                                        else:
                                            new_entry = UserAvatarCache(
                                                fid=user.fid,
                                                avatar_url=avatar_url,
                                                last_updated=datetime.now(),
                                                created_at=datetime.now()
                                            )
                                            cache_session.add(new_entry)

                                        updated_count += 1
                                        cache_session.commit()

                                        progress = int((current_index / total_users) * 100)
                                        yield f"data: {json.dumps({'type': 'progress', 'current': current_index, 'total': total_users, 'percent': progress, 'updated': updated_count, 'skipped': skipped_count, 'errors': error_count, 'message': f'Updated avatar for {user.nickname}'})}\n\n"
                                else:
                                    error_count += 1
                                    progress = int((current_index / total_users) * 100)
                                    yield f"data: {json.dumps({'type': 'progress', 'current': current_index, 'total': total_users, 'percent': progress, 'updated': updated_count, 'skipped': skipped_count, 'errors': error_count, 'message': f'Error fetching {user.nickname} (status {response.status})'})}\n\n"

                            # Rate limiting: wait 2 seconds between requests
                            yield f"data: {json.dumps({'type': 'status', 'message': 'Waiting 2 seconds (rate limit)...'})}\n\n"
                            await asyncio.sleep(2)

                        except Exception as e:
                            error_count += 1
                            progress = int((current_index / total_users) * 100)
                            yield f"data: {json.dumps({'type': 'progress', 'current': current_index, 'total': total_users, 'percent': progress, 'updated': updated_count, 'skipped': skipped_count, 'errors': error_count, 'message': f'Error processing {user.nickname}: {str(e)}'})}\n\n"
                            continue

                # Send completion message
                yield f"data: {json.dumps({'type': 'complete', 'updated': updated_count, 'skipped': skipped_count, 'errors': error_count, 'message': 'Avatar refresh completed!'})}\n\n"

        except Exception as e:
            yield f"data: {json.dumps({'type': 'error', 'message': str(e)})}\n\n"

    return StreamingResponse(generate_progress(), media_type="text/event-stream")

def preprocess_image_for_ocr(image_path):
    """
    Preprocess image using adaptive thresholding for improved OCR accuracy.
    This method significantly improves damage point detection (+40% improvement).
    It reads the image into memory to handle non-ASCII file paths correctly.

    Args:
        image_path: Path to the image file

    Returns:
        Path to the preprocessed image

    Raises:
        FileNotFoundError: If the image file doesn't exist
        ValueError: If the image couldn't be processed
    """
    # Check if file exists
    if not os.path.exists(image_path):
        raise FileNotFoundError(f"Image file not found: {image_path}")

    try:
        # Read the file into a numpy array to handle non-ASCII paths
        with open(image_path, 'rb') as f:
            file_bytes = np.frombuffer(f.read(), dtype=np.uint8)
        
        # Decode the image from the numpy array
        image = cv2.imdecode(file_bytes, cv2.IMREAD_COLOR)

        # Check if image was successfully loaded
        if image is None:
            raise ValueError(f"Could not decode image from path: {image_path}")

        # Convert to grayscale
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)

        # Apply adaptive thresholding for high contrast text
        thresh = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                                       cv2.THRESH_BINARY, 11, 2)

        # Convert back to BGR for PaddleOCR compatibility
        result = cv2.cvtColor(thresh, cv2.COLOR_GRAY2BGR)

        # Encode the processed image to a buffer
        # Use .png for lossless compression to preserve OCR quality
        ext = os.path.splitext(image_path)[1] or '.png'
        is_success, buffer = cv2.imencode(ext, result)
        if not is_success:
            raise ValueError(f"Could not encode preprocessed image: {image_path}")

        # Write the buffer back to the original file path
        with open(image_path, 'wb') as f:
            f.write(buffer)

    except Exception as e:
        # Re-raise exceptions with more context
        raise ValueError(f"Failed to preprocess image at {image_path}: {e}") from e

    return image_path

def extract_player_scores_from_ocr(ocr_results, image_name):
    "Extract player names, rankings, and scores from PaddleOCR results"
    player_data = []

    # PaddleOCR returns a list with one dict containing rec_texts and rec_scores
    if not ocr_results or not isinstance(ocr_results, list) or len(ocr_results) == 0:
        return player_data

    page_result = ocr_results[0]

    if 'rec_texts' not in page_result or 'rec_scores' not in page_result:
        return player_data

    texts = page_result['rec_texts']
    scores = page_result['rec_scores']
    polys = page_result.get('rec_polys', [])

    # Create list of (text, confidence, polygon) tuples for easier processing
    text_items = []
    for i, (text, confidence) in enumerate(zip(texts, scores)):
        poly = polys[i] if i < len(polys) else None
        text_items.append((text, confidence, poly))

    # Process each text item
    for i, (text, confidence, poly) in enumerate(text_items):
        player_name = text.strip()

        # PaddleOCR reads brackets correctly, but we keep validation logic
        # Fix any remaining OCR misreading patterns
        if player_name.startswith('[DOAJ'):
            player_name = '[DOA]' + player_name[5:]
        elif player_name.startswith('[DOA') and len(player_name) > 4 and player_name[4] != ']':
            # Missing closing bracket after DOA
            player_name = '[DOA]' + player_name[4:]

        # Check if this is a player name (starts with [DOA] and has good confidence)
        if player_name.startswith('[DOA]') and confidence > 0.55:
            try:
                # First, check if there are additional text elements on the same line (for multi-word names)
                full_player_name = player_name
                if poly is not None and len(poly) > 0:
                    try:
                        player_y = float(poly[:, 1].min())  # Top edge of player name
                        player_x_end = float(poly[:, 0].max())  # Right edge of player name

                        # Look for text elements to the right on the same line
                        for t, c, p in text_items:
                            if p is not None and len(p) > 0 and t != text:
                                try:
                                    text_y = float(p[:, 1].min())
                                    text_x_start = float(p[:, 0].min())

                                    # Check if on same line (within 30px vertically) and to the right (allow small overlap, within 200px)
                                    if abs(text_y - player_y) < 30 and text_x_start >= player_x_end - 10 and text_x_start < player_x_end + 200:
                                        # Make sure it's not a number or "Damage Points" text
                                        if not t.isdigit() and 'damage' not in t.lower() and 'point' not in t.lower():
                                            full_player_name += " " + t
                                            break
                                except (ValueError, TypeError):
                                    continue
                    except (ValueError, TypeError, IndexError):
                        pass

                player_name = full_player_name

                # Find ranking (should be to the left of the player name)
                ranking = None
                if poly is not None and len(poly) > 0:
                    try:
                        player_y = float(poly[:, 1].min())  # Top edge of player name
                        player_x = float(poly[:, 0].min())  # Left edge of player name

                        for t, c, p in text_items:
                            if p is not None and len(p) > 0 and t.isdigit():
                                try:
                                    if 1 <= int(t) <= 50:
                                        text_y = float(p[:, 1].min())
                                        text_x_max = float(p[:, 0].max())  # Right edge of ranking number

                                        # Ranking should be on same line and to the left
                                        if abs(text_y - player_y) < 50 and text_x_max < player_x:
                                            ranking = int(t)
                                            break
                                except (ValueError, TypeError):
                                    continue
                    except (ValueError, TypeError, IndexError):
                        pass

                # Find damage score (should be below the player name)
                damage_points = None
                if poly is not None and len(poly) > 0:
                    try:
                        player_y_bottom = float(poly[:, 1].max())  # Bottom edge of player name

                        for t, c, p in text_items:
                            if p is not None and len(p) > 0:
                                try:
                                    text_y_top = float(p[:, 1].min())

                                    # Look for "Damage Points:" text below player name
                                    if text_y_top > player_y_bottom and text_y_top < player_y_bottom + 100:
                                        if 'damage' in t.lower() and ('point' in t.lower() or ':' in t):
                                            numbers = re.findall(r'[\d,]+', t)
                                            if numbers:
                                                try:
                                                    damage_points = int(numbers[-1].replace(',', ''))
                                                except (ValueError, TypeError):
                                                    pass
                                            break
                                except (ValueError, TypeError, IndexError):
                                    continue
                    except (ValueError, TypeError, IndexError):
                        pass

                player_data.append({
                    'player_name': player_name,
                    'raw_name': text,
                    'ranking': ranking,
                    'damage_points': damage_points,
                    'confidence': confidence,
                    'image_source': image_name
                })
            except Exception as e:
                print(f"[ERROR] Failed to process {player_name}: {e}")

    return player_data

def match_and_store_scores(player_data_list, session_id, event_name, event_type, event_date):
    "Match players and store scores in cache using new streamlined models"
    matched_players = []
    unmatched = []

    with Session(users_engine) as user_session, Session(cache_engine) as cache_session:
        all_users = user_session.exec(select(User)).all()
        # Create lookup by nickname only (without alliance tag)
        users_by_nickname = {}
        for user in all_users:
            # Extract nickname after [DOA] or any alliance tag
            nickname_only = re.sub(r'\[.*?\]', '', user.nickname).strip()
            users_by_nickname[nickname_only.lower()] = user

        for player_data in player_data_list:
            player_name = player_data['player_name']
            matched = False
            player_fid = "0000000000"  # Default FID for unmatched
            user_obj = None
            match_confidence = player_data['confidence']

            # Extract nickname after [DOA] tag from OCR result
            extracted_nickname = re.sub(r'\[.*?\]', '', player_name).strip()

            # Try to match by nickname only
            if extracted_nickname.lower() in users_by_nickname:
                user_obj = users_by_nickname[extracted_nickname.lower()]
                player_fid = str(user_obj.fid)
                matched = True

            # Update or create player mapping
            existing_mapping = cache_session.exec(
                select(OCRPlayerMapping)
                .where(OCRPlayerMapping.player_name == player_name)
            ).first()

            if existing_mapping:
                existing_mapping.last_seen = datetime.now()
                existing_mapping.times_seen += 1
                existing_mapping.updated_at = datetime.now()
                # Update FID if we have a match and it was previously unmatched
                if matched and existing_mapping.player_fid == "0000000000":
                    existing_mapping.player_fid = player_fid
                    existing_mapping.confidence = match_confidence
                cache_session.add(existing_mapping)
            else:
                mapping = OCRPlayerMapping(
                    player_name=player_name,
                    player_fid=player_fid,
                    confidence=match_confidence,
                    first_seen=datetime.now(),
                    last_seen=datetime.now(),
                    times_seen=1,
                    created_at=datetime.now(),
                    updated_at=datetime.now()
                )
                cache_session.add(mapping)

            # Store event data (check for duplicates across ALL sessions for same event/date)
            # This handles multiple users uploading screenshots from the same event
            event_date_str = event_date.strftime('%Y-%m-%d')
            existing_event = cache_session.exec(
                select(OCREventData)
                .where(OCREventData.event_name == event_name)
                .where(OCREventData.player_name == player_name)
                .where(OCREventData.event_date >= datetime.strptime(event_date_str, '%Y-%m-%d'))
                .where(OCREventData.event_date < datetime.strptime(event_date_str, '%Y-%m-%d') + timedelta(days=1))
            ).first()

            if not existing_event:
                # Create new event record (first verification)
                event_record = OCREventData(
                    event_name=event_name,
                    event_type=event_type,
                    event_date=event_date,
                    player_name=player_name,
                    player_fid=player_fid if matched else None,
                    ranking=player_data.get('ranking'),
                    rank_inferred=False,  # Web uploads don't use rank inference
                    score=None,
                    damage_points=player_data.get('damage_points'),
                    time_value=None,
                    ocr_confidence=player_data['confidence'],
                    image_source=player_data['image_source'],
                    processing_session=session_id,
                    verification_count=1,
                    verified_sessions=session_id,
                    data_confidence=1.0,
                    extracted_at=datetime.now(),
                    created_at=datetime.now()
                )
                cache_session.add(event_record)
            else:
                # Player already detected in a previous upload for this event/date
                # Check for verification and update if we have better data
                new_damage = player_data.get('damage_points', 0)
                new_confidence = player_data['confidence']
                new_ranking = player_data.get('ranking')
                should_update = False

                # Check if this session already verified this player (don't count twice)
                verified_sessions_list = existing_event.verified_sessions.split(',') if existing_event.verified_sessions else []
                is_new_verification = session_id not in verified_sessions_list

                # VERIFICATION: Check if data matches (within tolerance)
                damage_tolerance = 1000  # Allow 1k difference (OCR errors)
                damage_matches = False
                if new_damage and existing_event.damage_points:
                    damage_diff = abs(new_damage - existing_event.damage_points)
                    damage_matches = damage_diff <= damage_tolerance

                ranking_matches = new_ranking == existing_event.ranking if new_ranking and existing_event.ranking else False

                # If data matches from a new session, increment verification count
                if is_new_verification and (damage_matches or ranking_matches):
                    verified_sessions_list.append(session_id)
                    existing_event.verified_sessions = ','.join(verified_sessions_list)
                    existing_event.verification_count = len(verified_sessions_list)
                    # Increase data confidence: 1.0 + (0.2 * additional verifications)
                    # Max confidence bonus is 2.0 (at 5+ verifications)
                    existing_event.data_confidence = min(2.0, 1.0 + (0.2 * (existing_event.verification_count - 1)))
                    should_update = True

                # Update if we have a higher damage score
                if new_damage and new_damage > (existing_event.damage_points or 0):
                    existing_event.damage_points = new_damage
                    existing_event.processing_session = session_id  # Track which session had best damage
                    should_update = True

                # Update if we have a ranking and didn't have one before
                if new_ranking and not existing_event.ranking:
                    existing_event.ranking = new_ranking
                    should_update = True

                # Update if we have a better ranking (lower number = better rank)
                if new_ranking and existing_event.ranking and new_ranking < existing_event.ranking:
                    existing_event.ranking = new_ranking
                    should_update = True

                # Update if OCR confidence is higher
                if new_confidence > existing_event.ocr_confidence:
                    existing_event.ocr_confidence = new_confidence
                    existing_event.image_source = player_data['image_source']
                    should_update = True

                if should_update:
                    cache_session.add(existing_event)

            if matched:
                player_data['player_fid'] = player_fid
                player_data['user'] = user_obj
                matched_players.append(player_data)
            else:
                player_data['player_fid'] = player_fid
                unmatched.append(player_data)

        cache_session.commit()

    return len(matched_players), matched_players, unmatched

def mark_attendance_from_scores(matched_players, unmatched_players, event_name, ocr_session_id):
    """
    Mark attendance for matched players only (unmatched players are tracked in OCR cache).
    Handles duplicates by keeping the highest damage score when a player appears multiple times.
    """
    event_date = datetime.now()
    session_id = f"{event_name}_{event_date.strftime('%Y%m%d')}"
    marked_count = 0
    updated_count = 0

    with Session(attendance_engine) as att_session:
        # Mark attendance for matched players (real users)
        for player_data in matched_players:
            user = player_data['user']
            new_damage = player_data.get('damage_points') or 0

            # Check if already exists
            existing = att_session.exec(
                select(AttendanceRecord)
                .where(AttendanceRecord.player_id == str(user.fid))
                .where(AttendanceRecord.session_id == session_id)
            ).first()

            if not existing:
                # Create new attendance record
                attendance = AttendanceRecord(
                    session_id=session_id,
                    session_name=event_name,
                    event_type="OCR Import",
                    event_date=event_date,
                    player_id=str(user.fid),
                    player_name=user.nickname,
                    alliance_id=str(user.alliance),
                    alliance_name=f"Alliance {user.alliance}",
                    status="present",
                    points=new_damage,
                    marked_at=datetime.now(),
                    marked_by="OCR_System",
                    marked_by_username="Automated OCR",
                    created_at=datetime.now()
                )
                att_session.add(attendance)
                marked_count += 1
            else:
                # Update if new damage score is higher (player appeared in multiple screenshots)
                if new_damage and new_damage > (existing.points or 0):
                    existing.points = new_damage
                    existing.marked_at = datetime.now()  # Update timestamp
                    att_session.add(existing)
                    updated_count += 1

        att_session.commit()

    # Return total marked (new + updated)
    return marked_count + updated_count

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
