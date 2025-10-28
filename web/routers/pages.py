"""
Routes for serving HTML pages.

This module defines the routes that render and return HTML templates
for the main pages of the web application, such as the dashboard,
members list, and event calendar.
"""

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session
from sqlmodel import select
from collections import defaultdict
import calendar
from datetime import date, datetime, timedelta, time
import pytz

from web.core.config import templates
from web.core.database import (
    get_users_session, get_changes_session, get_alliance_session,
    get_cache_session, get_beartime_session, get_giftcode_session
)
from web.models import User, FurnaceChange, Alliance, NicknameChange, GiftCode, UserGiftCode
from web.ocr_models import OCREventData, UserAvatarCache
from web.services.plotting import generate_town_center_graphs, generate_bear_trap_graph
from .auth import is_authenticated

router = APIRouter()

@router.get("/", response_class=HTMLResponse)
async def read_root(
    request: Request,
    authenticated: bool = Depends(is_authenticated),
    users_session: Session = Depends(get_users_session),
    changes_session: Session = Depends(get_changes_session),
    alliance_session: Session = Depends(get_alliance_session),
    cache_session: Session = Depends(get_cache_session)
):
    """Serves the main dashboard page with data visualizations."""
    if not authenticated:
        return RedirectResponse(url="/login", status_code=303)

    users = users_session.exec(select(User)).all()
    furnace_changes = changes_session.exec(select(FurnaceChange)).all()
    all_alliances = alliance_session.exec(select(Alliance)).all()
    alliance_nicknames = {str(a.alliance_id): a.name for a in all_alliances}

    tc_graphs = generate_town_center_graphs(users, furnace_changes, all_alliances, alliance_nicknames)

    thirty_days_ago = datetime.now() - timedelta(days=30)
    bear_trap_query = (
        select(OCREventData)
        .where(OCREventData.event_type == 'Bear Trap')
        .where(OCREventData.event_date >= thirty_days_ago)
        .where(OCREventData.damage_points.isnot(None))
    )
    bear_trap_data = cache_session.exec(bear_trap_query).all()
    graph_bear_trap_json = generate_bear_trap_graph(bear_trap_data)

    return templates.TemplateResponse("dashboard.html", {
        "request": request,
        **tc_graphs,
        "graph_bear_trap_json": graph_bear_trap_json
    })

@router.get("/members", response_class=HTMLResponse)
async def read_members(
    request: Request,
    authenticated: bool = Depends(is_authenticated),
    session: Session = Depends(get_users_session),
    alliance_session: Session = Depends(get_alliance_session),
    cache_session: Session = Depends(get_cache_session)
):
    """Serves the members page, listing all users."""
    if not authenticated:
        return RedirectResponse(url="/login", status_code=303)

    users = session.exec(select(User)).all()
    alliances = alliance_session.exec(select(Alliance)).all()
    alliance_nicknames = {str(a.alliance_id): a.name for a in alliances}
    avatar_cache = cache_session.exec(select(UserAvatarCache)).all()
    avatar_map = {cache.fid: cache.avatar_url for cache in avatar_cache}

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

@router.get("/bear-trap-map", response_class=HTMLResponse)
async def bear_trap_map(
    request: Request,
    authenticated: bool = Depends(is_authenticated),
    session: Session = Depends(get_users_session),
    alliance_session: Session = Depends(get_alliance_session)
):
    """Serves the bear trap map page."""
    if not authenticated:
        return RedirectResponse(url="/login", status_code=303)

    alliances = alliance_session.exec(select(Alliance)).all()
    users = session.exec(select(User)).all()

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

@router.get("/attendance", response_class=HTMLResponse)
async def attendance(request: Request, authenticated: bool = Depends(is_authenticated)):
    """Serves the attendance tracking page."""
    if not authenticated:
        return RedirectResponse(url="/login", status_code=303)
    return templates.TemplateResponse("attendance.html", {"request": request})

@router.get("/events", response_class=HTMLResponse)
async def read_events(
    request: Request,
    authenticated: bool = Depends(is_authenticated),
    beartime_session: Session = Depends(get_beartime_session),
    users_session: Session = Depends(get_users_session)
):
    """Serves the event calendar page."""
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

@router.get("/giftcodes", response_class=HTMLResponse)
async def read_giftcodes(
    request: Request,
    authenticated: bool = Depends(is_authenticated),
    giftcode_session: Session = Depends(get_giftcode_session),
    users_session: Session = Depends(get_users_session)
):
    """Serves the gift codes management page."""
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

@router.get("/logs", response_class=HTMLResponse)
async def read_logs(
    request: Request,
    authenticated: bool = Depends(is_authenticated),
    changes_session: Session = Depends(get_changes_session),
    users_session: Session = Depends(get_users_session)
):
    """Serves the logs page for nickname and furnace changes."""
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

@router.get("/data", response_class=HTMLResponse)
async def read_data(
    request: Request,
    authenticated: bool = Depends(is_authenticated),
    users_session: Session = Depends(get_users_session),
    alliance_session: Session = Depends(get_alliance_session),
    cache_session: Session = Depends(get_cache_session)
):
    """Serves the data analysis page with additional graphs."""
    if not authenticated:
        return RedirectResponse(url="/login", status_code=303)

    # --- Furnace Level Distribution Histogram ---
    users = users_session.exec(select(User)).all()
    furnace_levels = [user.furnace_lv for user in users if user.furnace_lv is not None]

    fig_hist = go.Figure(data=[go.Histogram(x=furnace_levels, nbinsx=20)])

    fig_hist.update_layout(
        title='Furnace Level Distribution',
        xaxis_title='Furnace Level',
        yaxis_title='Number of Players',
        template='plotly_dark',
        plot_bgcolor='#1e1e1e',
        paper_bgcolor='#1e1e1e',
        font=dict(color='#e0e0e0'),
        margin=dict(l=40, r=10, t=80, b=40),
    )

    graph_hist_json = json.dumps(fig_hist, cls=plotly.utils.PlotlyJSONEncoder)

    # --- Alliance Member Count ---
    alliance_nicknames = get_alliance_nicknames(alliance_session)
    alliance_counts = defaultdict(int)
    for user in users:
        if user.alliance:
            alliance_counts[user.alliance] += 1

    alliance_names = [alliance_nicknames.get(aid, f'Alliance {aid}') for aid in alliance_counts.keys()]
    member_counts = list(alliance_counts.values())

    fig_bar = go.Figure(data=[go.Bar(x=alliance_names, y=member_counts)])

    fig_bar.update_layout(
        title='Alliance Member Count',
        xaxis_title='Alliance',
        yaxis_title='Number of Members',
        template='plotly_dark',
        plot_bgcolor='#1e1e1e',
        paper_bgcolor='#1e1e1e',
        font=dict(color='#e0e0e0'),
        margin=dict(l=40, r=10, t=80, b=40),
    )

    graph_bar_json = json.dumps(fig_bar, cls=plotly.utils.PlotlyJSONEncoder)

    # --- Bear Trap Damage ---
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

    # --- Player Activity ---
    player_activity_query = select(OCRPlayerMapping)
    player_activity_data = cache_session.exec(player_activity_query).all()

    now = datetime.now()
    last_seen_days = [(now - record.last_seen).days for record in player_activity_data]

    fig_activity = go.Figure(data=[go.Histogram(x=last_seen_days, nbinsx=30)])

    fig_activity.update_layout(
        title='Player Activity (Days Since Last Seen)',
        xaxis_title='Days Since Last Seen',
        yaxis_title='Number of Players',
        template='plotly_dark',
        plot_bgcolor='#1e1e1e',
        paper_bgcolor='#1e1e1e',
        font=dict(color='#e0e0e0'),
        margin=dict(l=40, r=10, t=80, b=40),
    )

    graph_activity_json = json.dumps(fig_activity, cls=plotly.utils.PlotlyJSONEncoder)

    return templates.TemplateResponse("data.html", {
        "request": request,
        "graph_hist_json": graph_hist_json,
        "graph_bar_json": graph_bar_json,
        "graph_bear_trap_json": graph_bear_trap_json,
        "graph_activity_json": graph_activity_json
    })
