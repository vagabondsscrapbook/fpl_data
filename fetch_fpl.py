"""
Pulls the official Fantasy Premier League data and writes trimmed,
easy-to-read files:
  - players.csv   : one row per player, key FPL fields only
  - teams.csv     : team strength ratings (for fixture difficulty)
  - fixtures.csv  : upcoming fixtures with difficulty ratings
  - meta.json      : current/next gameweek + last-updated timestamp

Run manually with: python fetch_fpl.py
Designed to be run automatically by the GitHub Actions workflow
in .github/workflows/update-fpl-data.yml
"""

import csv
import json
from datetime import datetime, timezone

import requests

BASE = "https://fantasy.premierleague.com/api"
HEADERS = {"User-Agent": "Mozilla/5.0"}


def fetch_json(url):
    r = requests.get(url, headers=HEADERS, timeout=30)
    r.raise_for_status()
    return r.json()


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


def main():
    data = fetch_json(f"{BASE}/bootstrap-static/")
    teams = {t["id"]: t["short_name"] for t in data["teams"]}
    positions = {p["id"]: p["singular_name_short"] for p in data["element_types"]}

    write_players(data, teams, positions)
    write_teams(data)
    write_fixtures(teams)
    write_meta(data)


if __name__ == "__main__":
    main()
