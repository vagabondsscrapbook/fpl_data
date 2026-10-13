"""
Pulls the official Fantasy Premier League data and writes trimmed,
easy-to-read files:
  - players.csv   : one row per player, key FPL fields only
  - teams.csv     : team strength ratings (for fixture difficulty)
  - fixtures.csv  : upcoming fixtures with difficulty ratings
  - meta.json      : current/next gameweek + last-updated timestamp
  - history.csv    : one row per player per match played (home/away, minutes,
                     points, xG/xA, team score) for venue and with/without splits

Run manually with: python fetch_fpl.py
Designed to be run automatically by the GitHub Actions workflow
in .github/workflows/update-fpl-data.yml
"""

import csv
import json
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import requests

BASE = "https://fantasy.premierleague.com/api"
HEADERS = {"User-Agent": "Mozilla/5.0"}


def fetch_json(url, retries=3):
    for attempt in range(retries):
        try:
            r = requests.get(url, headers=HEADERS, timeout=30)
            r.raise_for_status()
            return r.json()
        except requests.RequestException:
            if attempt == retries - 1:
                raise
            time.sleep(2 * (attempt + 1))


def write_players(data, teams, positions):
    with open("players.csv", "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow([
            "id", "web_name", "first_name", "second_name", "team", "position",
            "now_cost_m", "total_points", "points_per_game", "form",
            "selected_by_percent", "status", "chance_of_playing_next_round",
            "news", "minutes", "goals_scored", "assists", "clean_sheets",
            "bonus", "ict_index", "expected_goals", "expected_assists",
            "expected_goal_involvements", "expected_goals_conceded",
        ])
        for p in data["elements"]:
            writer.writerow([
                p["id"], p["web_name"], p["first_name"], p["second_name"],
                teams.get(p["team"], ""), positions.get(p["element_type"], ""),
                p["now_cost"] / 10, p["total_points"], p["points_per_game"],
                p["form"], p["selected_by_percent"], p["status"],
                p["chance_of_playing_next_round"], p["news"],
                p["minutes"], p["goals_scored"], p["assists"],
                p["clean_sheets"], p["bonus"], p["ict_index"],
                p["expected_goals"], p["expected_assists"],
                p["expected_goal_involvements"], p["expected_goals_conceded"],
            ])


def write_teams(data):
    with open("teams.csv", "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow([
            "id", "name", "short_name",
            "strength_overall_home", "strength_overall_away",
            "strength_attack_home", "strength_attack_away",
            "strength_defence_home", "strength_defence_away",
        ])
        for t in data["teams"]:
            writer.writerow([
                t["id"], t["name"], t["short_name"],
                t["strength_overall_home"], t["strength_overall_away"],
                t["strength_attack_home"], t["strength_attack_away"],
                t["strength_defence_home"], t["strength_defence_away"],
            ])


def write_fixtures(teams):
    fixtures = fetch_json(f"{BASE}/fixtures/?future=1")
    with open("fixtures.csv", "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow([
            "event", "kickoff_time", "team_h", "team_a",
            "team_h_difficulty", "team_a_difficulty",
        ])
        for fx in fixtures:
            writer.writerow([
                fx["event"], fx["kickoff_time"],
                teams.get(fx["team_h"], ""), teams.get(fx["team_a"], ""),
                fx["team_h_difficulty"], fx["team_a_difficulty"],
            ])


def write_meta(data):
    current_event = next((e for e in data["events"] if e["is_current"]), None)
    next_event = next((e for e in data["events"] if e["is_next"]), None)
    meta = {
        "last_updated_utc": datetime.now(timezone.utc).isoformat(),
        "current_gameweek": current_event["id"] if current_event else None,
        "next_gameweek": next_event["id"] if next_event else None,
        "next_deadline": next_event["deadline_time"] if next_event else None,
    }
    with open("meta.json", "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2)


HISTORY_FIELDS = [
    "player_id", "web_name", "team", "position", "gameweek", "kickoff_time",
    "opponent", "was_home", "team_scored", "team_conceded", "minutes", "starts",
    "total_points", "goals_scored", "assists", "clean_sheets", "bonus", "bps",
    "influence", "creativity", "threat", "ict_index", "expected_goals",
    "expected_assists", "expected_goal_involvements", "expected_goals_conceded",
    "value_m",
]


def fetch_player_history(player):
    """Per-match history for one player, or [] if the request fails."""
    try:
        data = fetch_json(f"{BASE}/element-summary/{player['id']}/")
    except requests.RequestException:
        return None
    return data.get("history", [])


def history_rows(player, h, teams, positions):
    was_home = bool(h.get("was_home"))
    h_score, a_score = h.get("team_h_score"), h.get("team_a_score")
    if h_score is None or a_score is None:
        scored = conceded = ""
    else:
        scored, conceded = (h_score, a_score) if was_home else (a_score, h_score)
    return [
        player["id"], player["web_name"], teams.get(player["team"], ""),
        positions.get(player["element_type"], ""), h.get("round"),
        h.get("kickoff_time"), teams.get(h.get("opponent_team"), ""),
        int(was_home), scored, conceded, h.get("minutes"), h.get("starts", ""),
        h.get("total_points"), h.get("goals_scored"), h.get("assists"),
        h.get("clean_sheets"), h.get("bonus"), h.get("bps"),
        h.get("influence"), h.get("creativity"), h.get("threat"),
        h.get("ict_index"), h.get("expected_goals", ""),
        h.get("expected_assists", ""), h.get("expected_goal_involvements", ""),
        h.get("expected_goals_conceded", ""),
        (h.get("value") or 0) / 10,
    ]


def write_history(data, teams, positions):
    """Pull element-summary for everyone who has played; skip if it all fails."""
    players = [p for p in data["elements"] if p["minutes"] > 0]
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(fetch_player_history, players))

    failed = sum(1 for r in results if r is None)
    # If most requests failed, keep the previous history.csv rather than overwrite it
    if failed > len(players) * 0.2:
        print(f"history: {failed}/{len(players)} requests failed - keeping old file")
        return

    with open("history.csv", "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(HISTORY_FIELDS)
        for player, hist in zip(players, results):
            for h in hist or []:
                writer.writerow(history_rows(player, h, teams, positions))
    print(f"history: wrote {len(players) - failed} players ({failed} failed)")


def main():
    data = fetch_json(f"{BASE}/bootstrap-static/")
    teams = {t["id"]: t["short_name"] for t in data["teams"]}
    positions = {p["id"]: p["singular_name_short"] for p in data["element_types"]}

    write_players(data, teams, positions)
    write_teams(data)
    write_fixtures(teams)
    write_meta(data)
    write_history(data, teams, positions)


if __name__ == "__main__":
    main()
