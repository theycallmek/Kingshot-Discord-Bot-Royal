"""
Plotting services for generating graphs for the web dashboard.

This module uses the Plotly library to create various data visualizations,
such as line charts, bar charts, histograms, and heatmaps, styled for a
dark theme.
"""

import json
import plotly.graph_objects as go
import plotly.utils
from collections import defaultdict
from datetime import date, datetime

def generate_town_center_graphs(users, furnace_changes, all_alliances, alliance_nicknames):
    """
    Generates all Town Center level related graphs for the main dashboard.

    Args:
        users (list): A list of User model objects.
        furnace_changes (list): A list of FurnaceChange model objects.
        all_alliances (list): A list of Alliance model objects.
        alliance_nicknames (dict): A mapping of alliance IDs to nicknames.

    Returns:
        A dictionary containing the JSON representations of the generated
        graphs for total, average, distribution, and heatmap of TC levels.
    """
    user_map = {user.fid: user for user in users}
    user_level_history = defaultdict(dict)

    for change in furnace_changes:
        if change.fid in user_map:
            try:
                change_date = datetime.fromisoformat(change.change_date).date()
                user_level_history[change.fid][change_date] = change.new_furnace_lv
            except (ValueError, TypeError):
                continue

    today = date.today()
    for user in users:
        if user.furnace_lv:
            user_level_history[user.fid][today] = user.furnace_lv

    all_dates = set()
    for fid, date_levels in user_level_history.items():
        all_dates.update(date_levels.keys())
    sorted_dates = sorted(all_dates)

    daily_alliance_totals = defaultdict(lambda: defaultdict(int))
    daily_alliance_stats = defaultdict(lambda: defaultdict(lambda: {'total': 0, 'count': 0}))

    for current_date in sorted_dates:
        user_levels_on_date = {}
        for fid, date_levels in user_level_history.items():
            level_on_date = None
            for check_date in sorted(date_levels.keys()):
                if check_date <= current_date:
                    level_on_date = date_levels[check_date]
                else:
                    break
            if level_on_date is not None:
                user_levels_on_date[fid] = level_on_date

        for fid, level in user_levels_on_date.items():
            if fid in user_map:
                user = user_map[fid]
                if user.alliance:
                    daily_alliance_totals[current_date][user.alliance] += level
                    daily_alliance_stats[current_date][user.alliance]['total'] += level
                    daily_alliance_stats[current_date][user.alliance]['count'] += 1

    target_alliances = [str(alliance.alliance_id) for alliance in all_alliances]
    color_palette = ['#FF6B6B', '#4ECDC4', '#45B7D1', '#FFA07A', '#98D8C8', '#F7DC6F', '#BB8FCE', '#85C1E2', '#F8B195', '#C06C84']
    colors = {aid: color_palette[i % len(color_palette)] for i, aid in enumerate(target_alliances)}
    alliance_names = {aid: alliance_nicknames.get(aid, f'Alliance {aid}') for aid in target_alliances}

    # --- Total TC Level Graph ---
    traces_total = []
    for alliance in target_alliances:
        dates = [d.strftime('%Y-%m-%d') for d in sorted_dates if alliance in daily_alliance_totals[d]]
        totals = [daily_alliance_totals[d][alliance] for d in sorted_dates if alliance in daily_alliance_totals[d]]
        if dates:
            traces_total.append(go.Scatter(x=dates, y=totals, mode='lines+markers', name=alliance_names[alliance], line=dict(color=colors[alliance], width=3), marker=dict(size=8)))

    fig_total = go.Figure(data=traces_total)
    fig_total.update_layout(title='Town Center Level/Days', xaxis_title='Date', yaxis_title='Total Town Center Level', hovermode='x unified', template='plotly_dark', plot_bgcolor='#1e1e1e', paper_bgcolor='#1e1e1e', font=dict(color='#e0e0e0'), margin=dict(l=40, r=10, t=80, b=40), legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1))

    # --- Average TC Level Graph ---
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
        if dates:
            traces_avg.append(go.Scatter(x=dates, y=averages, mode='lines+markers', name=alliance_names[alliance], line=dict(color=colors[alliance], width=3), marker=dict(size=8)))

    fig_avg = go.Figure(data=traces_avg)
    fig_avg.update_layout(title='Average Town Center Level/Days', xaxis_title='Date', yaxis_title='Average Town Center Level', hovermode='x unified', template='plotly_dark', plot_bgcolor='#1e1e1e', paper_bgcolor='#1e1e1e', font=dict(color='#e0e0e0'), margin=dict(l=40, r=10, t=80, b=40), legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1))

    # --- TC Level Distribution Histogram ---
    furnace_distribution = defaultdict(lambda: defaultdict(int))
    for user in users:
        if user.alliance in target_alliances and user.furnace_lv:
            furnace_distribution[user.alliance][user.furnace_lv] += 1

    hist_traces = []
    for alliance in target_alliances:
        if furnace_distribution[alliance]:
            levels = sorted(furnace_distribution[alliance].keys())
            counts = [furnace_distribution[alliance][level] for level in levels]
            hist_traces.append(go.Bar(x=levels, y=counts, name=alliance_names[alliance], marker=dict(color=colors[alliance]), hovertemplate='<b>TC Level %{x}</b><br>Members: %{y}<extra></extra>'))

    fig_hist = go.Figure(data=hist_traces)
    fig_hist.update_layout(title='Town Center Level Distribution', xaxis_title='Town Center Level', yaxis_title='Number of Members', barmode='group', template='plotly_dark', plot_bgcolor='#1e1e1e', paper_bgcolor='#1e1e1e', font=dict(color='#e0e0e0'), margin=dict(l=40, r=10, t=80, b=40), legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1), xaxis=dict(tickmode='linear', tick0=0, dtick=1))

    # --- TC Level Heatmap ---
    tc_ranges = [(1, 10, 'TC 1-10'), (11, 15, 'TC 11-15'), (16, 20, 'TC 16-20'), (21, 25, 'TC 21-25'), (26, 30, 'TC 26-30'), (31, 35, 'TC 31-35'), (36, 40, 'TC 36-40'), (41, 50, 'TC 41-50')]
    heatmap_data = []
    alliance_labels = []
    for alliance in target_alliances:
        alliance_labels.append(alliance_names[alliance])
        row_data = [sum(1 for user in users if user.alliance == alliance and user.furnace_lv and min_tc <= user.furnace_lv <= max_tc) for min_tc, max_tc, _ in tc_ranges]
        heatmap_data.append(row_data)

    column_labels = [label for _, _, label in tc_ranges]
    fig_heatmap = go.Figure(data=go.Heatmap(z=heatmap_data, x=column_labels, y=alliance_labels, colorscale='Viridis', hoverongaps=False, hovertemplate='<b>%{y}</b><br>%{x}<br>Members: %{z}<extra></extra>', colorbar=dict(title=dict(text='Members', side='right'), tickmode='linear', tick0=0, dtick=1)))
    fig_heatmap.update_layout(title='Town Center Level Heatmap', xaxis_title='Town Center Level Range', yaxis_title='Alliance', template='plotly_dark', plot_bgcolor='#1e1e1e', paper_bgcolor='#1e1e1e', font=dict(color='#e0e0e0'), margin=dict(l=40, r=10, t=80, b=40))
    fig_heatmap.update_yaxes(autorange='reversed')

    return {
        "graph_json": json.dumps(fig_total, cls=plotly.utils.PlotlyJSONEncoder),
        "graph_avg_json": json.dumps(fig_avg, cls=plotly.utils.PlotlyJSONEncoder),
        "graph_hist_json": json.dumps(fig_hist, cls=plotly.utils.PlotlyJSONEncoder),
        "graph_heatmap_json": json.dumps(fig_heatmap, cls=plotly.utils.PlotlyJSONEncoder),
    }

def generate_bear_trap_graph(bear_trap_data):
    """
    Generates the Bear Trap damage dealers bar chart.

    Args:
        bear_trap_data (list): A list of OCREventData model objects.

    Returns:
        A string containing the JSON representation of the graph.
    """
    player_damage = defaultdict(int)
    for record in bear_trap_data:
        player_damage[record.player_name] += record.damage_points

    sorted_players = sorted(player_damage.items(), key=lambda item: item[1], reverse=True)[:15]
    player_names = [item[0] for item in sorted_players]
    damage_values = [item[1] for item in sorted_players]

    fig_bear_trap = go.Figure(data=[go.Bar(x=player_names, y=damage_values, marker=dict(color=damage_values, colorscale='Viridis', showscale=True), hovertemplate='<b>%{x}</b><br>Damage: %{y}<extra></extra>')])
    fig_bear_trap.update_layout(title='Top 15 Bear Trap Damage Dealers (Last 30 Days)', xaxis_title='Player', yaxis_title='Total Damage', template='plotly_dark', plot_bgcolor='#1e1e1e', paper_bgcolor='#1e1e1e', font=dict(color='#e0e0e0'), margin=dict(l=40, r=10, t=80, b=40), xaxis={'categoryorder':'total descending'})

    return json.dumps(fig_bear_trap, cls=plotly.utils.PlotlyJSONEncoder)
