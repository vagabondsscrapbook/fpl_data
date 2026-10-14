"""
Pulls Fantasy Premier League data (plus match stats and odds from
football-data.co.uk) and writes trimmed, easy-to-read files:

  players.csv       one row per player, key FPL fields only
  teams.csv         team strength ratings (for fixture difficulty)
  fixtures.csv      upcoming fixtures with difficulty ratings
  meta.json         current/next gameweek + last-updated timestamp
  history.csv       one row per player per match played this season
                    (team = the club the player played FOR in that match,
                    current_team = the club he is at now)
  past_seasons.csv  one row per player per previous season (goals, xG, ...)
  matches_fd.csv    played matches this season: goals, shots, shots on
                    target, corners, fouls, cards, bookmaker odds
  fixtures_odds.csv upcoming matches with bookmaker odds + implied win
                    probabilities (football-data.co.uk, current week only)

Run manually with: python fetch_fpl.py
Designed to be run automatically by the GitHub Actions workflow
in .github/workflows/update-fpl-data.yml

Every optional section fails soft: if a source is down, the existing file
is kept and the rest of the run carries on.
"""

import csv
import difflib
import io
import json
import re
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import requests

BASE = "https://fantasy.premierleague.com/api"
FD_BASE = "https://www.football-data.co.uk"
HEADERS = {"User-Agent": "Mozilla/5.0"}


def fetch(url, retries=3):
    """GET with retries/backoff. Returns the Response."""
    for attempt in range(retries):
        try:
            r = requests.get(url, headers=HEADERS, timeout=30)
            r.raise_for_status()
            return r
        except requests.RequestException:
            if attempt == retries - 1:
                raise
            time.sleep(2 * (attempt + 1))


def fetch_json(url, retries=3):
    return fetch(url, retries).json()


# ---------------------------------------------------------------- core files

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


# ------------------------------------------------- per-player history (FPL)

HISTORY_FIELDS = [
    "player_id", "web_name", "team", "current_team", "position", "gameweek",
    "kickoff_time", "opponent", "was_home", "team_scored", "team_conceded",
    "minutes", "starts", "total_points", "goals_scored", "assists",
    "clean_sheets", "bonus", "bps", "influence", "creativity", "threat",
    "ict_index", "expected_goals", "expected_assists",
    "expected_goal_involvements", "expected_goals_conceded", "value_m",
]

PAST_FIELDS = [
    "player_id", "web_name", "current_team", "position", "season",
    "start_cost_m", "end_cost_m", "total_points", "minutes", "starts",
    "goals_scored", "assists", "clean_sheets", "goals_conceded", "saves",
    "penalties_missed", "yellow_cards", "red_cards", "bonus", "bps",
    "influence", "creativity", "threat", "ict_index", "expected_goals",
    "expected_assists", "expected_goal_involvements", "expected_goals_conceded",
]


def fetch_player_summary(player):
    """element-summary for one player (history + history_past), or None."""
    try:
        return fetch_json(f"{BASE}/element-summary/{player['id']}/")
    except requests.RequestException:
        return None


def team_at_time(h, fixture_map):
    """Club the player actually played for in this match (handles transfers)."""
    fx = fixture_map.get(h.get("fixture"))
    if not fx:
        return None
    team_h, team_a = fx
    return team_a if h.get("opponent_team") == team_h else team_h


def history_row(player, h, teams, positions, fixture_map):
    was_home = bool(h.get("was_home"))
    h_score, a_score = h.get("team_h_score"), h.get("team_a_score")
    if h_score is None or a_score is None:
        scored = conceded = ""
    else:
        scored, conceded = (h_score, a_score) if was_home else (a_score, h_score)
    played_for = team_at_time(h, fixture_map)
    current = teams.get(player["team"], "")
    played_for_name = teams.get(played_for, current) if played_for else current
    return [
        player["id"], player["web_name"], played_for_name, current,
        positions.get(player["element_type"], ""), h.get("round"),
        h.get("kickoff_time"), teams.get(h.get("opponent_team"), ""),
        int(was_home), scored, conceded, h.get("minutes"), h.get("starts", ""),
        h.get("total_points"), h.get("goals_scored"), h.get("assists"),
        h.get("clean_sheets"), h.get("bonus"), h.get("bps"),
        h.get("influence"), h.get("creativity"), h.get("threat"),
        h.get("ict_index"), h.get("expected_goals", ""),
        h.get("expected_assists", ""), h.get("expected_goal_involvements", ""),
        h.get("expected_goals_conceded", ""), (h.get("value") or 0) / 10,
    ]


def past_row(player, s, teams, positions):
    def cost(x):
        return (x or 0) / 10
    g = s.get
    return [
        player["id"], player["web_name"], teams.get(player["team"], ""),
        positions.get(player["element_type"], ""), g("season_name"),
        cost(g("start_cost")), cost(g("end_cost")), g("total_points"),
        g("minutes"), g("starts", ""), g("goals_scored"), g("assists"),
        g("clean_sheets"), g("goals_conceded"), g("saves"),
        g("penalties_missed"), g("yellow_cards"), g("red_cards"), g("bonus"),
        g("bps"), g("influence"), g("creativity"), g("threat"), g("ict_index"),
        g("expected_goals", ""), g("expected_assists", ""),
        g("expected_goal_involvements", ""), g("expected_goals_conceded", ""),
    ]


def write_history(data, teams, positions):
    """One request per player gives both this season's matches and past seasons."""
    fixtures = fetch_json(f"{BASE}/fixtures/")
    fixture_map = {fx["id"]: (fx["team_h"], fx["team_a"]) for fx in fixtures}

    # Everyone who has played, plus anyone not marked 'unavailable/left'
    players = [p for p in data["elements"] if p["minutes"] > 0 or p["status"] != "u"]
    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(fetch_player_summary, players))

    failed = sum(1 for r in results if r is None)
    if failed > len(players) * 0.2:
        print(f"history: {failed}/{len(players)} requests failed - keeping old files")
        return

    with open("history.csv", "w", newline="", encoding="utf-8") as f, \
            open("past_seasons.csv", "w", newline="", encoding="utf-8") as g:
        hw, pw = csv.writer(f), csv.writer(g)
        hw.writerow(HISTORY_FIELDS)
        pw.writerow(PAST_FIELDS)
        for player, summary in zip(players, results):
            if not summary:
                continue
            for h in summary.get("history", []):
                hw.writerow(history_row(player, h, teams, positions, fixture_map))
            for s in summary.get("history_past", []):
                pw.writerow(past_row(player, s, teams, positions))
    print(f"history: {len(players) - failed} players ok, {failed} failed")


# ------------------------------------------- football-data.co.uk (optional)

# football-data.co.uk name (after _norm) -> FPL-style key
FD_ALIASES = {
    "manunited": "manutd", "tottenham": "spurs", "nottinghamforest": "nottmforest",
    "manchestercity": "mancity", "manchesterunited": "manutd",
    "wolverhampton": "wolves", "newcastleunited": "newcastle",
    "brightonhovealbion": "brighton", "leedsunited": "leeds",
}

FD_MATCH_COLS = [
    "FTHG", "FTAG", "FTR", "HTHG", "HTAG", "HS", "AS", "HST", "AST", "HF",
    "AF", "HC", "AC", "HY", "AY", "HR", "AR",
]
FD_ODDS_COLS = ["B365H", "B365D", "B365A", "AvgH", "AvgD", "AvgA", "MaxH", "MaxD", "MaxA"]


def _norm(name):
    return re.sub(r"[^a-z0-9]", "", name.lower())


def build_name_map(data):
    """Map normalised team names to FPL short names."""
    keys = {}
    for t in data["teams"]:
        keys[_norm(t["name"])] = t["short_name"]
        keys[_norm(t["short_name"])] = t["short_name"]
    return keys


def to_short(fd_name, keys):
    n = _norm(fd_name)
    n = FD_ALIASES.get(n, n)
    if n in keys:
        return keys[n]
    close = difflib.get_close_matches(n, keys.keys(), n=1, cutoff=0.75)
    return keys[close[0]] if close else fd_name


def implied_probs(row):
    """Normalised (margin-removed) probabilities from the best odds columns."""
    for h, d, a in (("AvgH", "AvgD", "AvgA"), ("B365H", "B365D", "B365A")):
        try:
            oh, od, oa = float(row[h]), float(row[d]), float(row[a])
        except (KeyError, ValueError, TypeError):
            continue
        inv = [1 / oh, 1 / od, 1 / oa]
        total = sum(inv)
        return [round(x / total, 3) for x in inv]
    return ["", "", ""]


def season_code(now=None):
    now = now or datetime.now(timezone.utc)
    start = now.year if now.month >= 7 else now.year - 1
    return f"{start % 100:02d}{(start + 1) % 100:02d}"


def read_fd_csv(url):
    text = fetch(url).content.decode("utf-8-sig", errors="replace")
    return list(csv.DictReader(io.StringIO(text)))


def write_fd(rows, filename, keys, extra_cols):
    present = [c for c in extra_cols if rows and c in rows[0]]
    header = ["Date", "Time", "home", "away"] + present + ["pH", "pD", "pA"]
    with open(filename, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(header)
        for r in rows:
            if not r.get("HomeTeam"):
                continue
            writer.writerow(
                [r.get("Date", ""), r.get("Time", ""),
                 to_short(r["HomeTeam"], keys), to_short(r["AwayTeam"], keys)]
                + [r.get(c, "") for c in present] + implied_probs(r)
            )


def write_football_data(data):
    keys = build_name_map(data)
    try:
        rows = [r for r in read_fd_csv(f"{FD_BASE}/mmz4281/{season_code()}/E0.csv")
                if r.get("FTHG") not in (None, "")]
        write_fd(rows, "matches_fd.csv", keys, FD_MATCH_COLS + FD_ODDS_COLS)
        print(f"football-data: {len(rows)} played matches")
    except requests.RequestException as e:
        print(f"football-data matches skipped: {e}")
    try:
        rows = [r for r in read_fd_csv(f"{FD_BASE}/fixtures.csv") if r.get("Div") == "E0"]
        write_fd(rows, "fixtures_odds.csv", keys, FD_ODDS_COLS)
        print(f"football-data: {len(rows)} upcoming matches with odds")
    except requests.RequestException as e:
        print(f"football-data fixtures skipped: {e}")


# ----------------------------------------------------------------------- main

def main():
    data = fetch_json(f"{BASE}/bootstrap-static/")
    teams = {t["id"]: t["short_name"] for t in data["teams"]}
    positions = {p["id"]: p["singular_name_short"] for p in data["element_types"]}

    write_players(data, teams, positions)
    write_teams(data)
    write_fixtures(teams)
    write_meta(data)

    # Optional extras: never let these break the core run
    for step in (lambda: write_history(data, teams, positions),
                 lambda: write_football_data(data)):
        try:
            step()
        except Exception as e:  # noqa: BLE001
            print(f"optional step failed: {e!r}")


if __name__ == "__main__":
    main()
