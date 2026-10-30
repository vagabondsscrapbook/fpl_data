"""
Expected-points (xPts) model for FPL. Reads the CSVs produced by fetch_fpl.py
(from the current folder, or from the GitHub raw URL if --remote is given).

Usage:
  python xp_model.py --gws 9 10 11 12
  python xp_model.py --gws 9 10 11 12 --market market.json --pos MID --top 15
  python xp_model.py --gws 9 --names Mbeumo Cherki Foden

market.json (optional) = bookmaker win probabilities in PERCENT for matches in
the first listed gameweek, e.g.  {"CHE-MUN": [52.6, 23.4], "MCI-BHA": [58, 20.5]}
(home-away, home win %, away win %).  Those are converted to goal expectations
and replace the model's own estimate for that gameweek.

Optional overlays (the 'context layer'), also JSON:  --flags flags.json
  {"availability": {"Calafiori": 0.5},        # P(plays 60+) multiplier override
   "team_attack":  {"MUN": 0.9},              # multiply team goals-for expectation
   "team_defence": {"MUN": 1.1}}              # multiply goals-against expectation
Each flag should have a source and date in the weekly brief.

How it works (short version):
  * team attack/defence from per-match xG and xGC, shrunk toward the league mean
  * home/away factors 1.10/0.90
  * player attacking rate = this season's xGI/90 blended with the last three
    seasons (900-minute prior weight)
  * minutes: share of team games with 60+ minutes, or flag override
  * points: appearance + goals/assists + bonus + clean sheet - goals conceded
"""
import argparse, collections, csv, io, json, math, urllib.request

RAW = "https://raw.githubusercontent.com/vagabondsscrapbook/fpl_data/main/"


def load(name, remote):
    if remote:
        txt = urllib.request.urlopen(RAW + name, timeout=30).read().decode("utf-8")
        return list(csv.DictReader(io.StringIO(txt)))
    with open(name, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return 0.0


def pois(l, k):
    return math.exp(-l) * l ** k / math.factorial(k)


def solve(pH, pA):
    """Goal expectations (home, away) that reproduce the win probabilities."""
    best = None
    for i in range(20, 320, 4):
        for j in range(20, 320, 4):
            lh, la = i / 100, j / 100
            h = a = 0.0
            for x in range(10):
                for y in range(10):
                    pr = pois(lh, x) * pois(la, y)
                    if x > y: h += pr
                    elif y > x: a += pr
            e = (h - pH) ** 2 + (a - pA) ** 2
            if best is None or e < best[0]:
                best = (e, lh, la)
    return best[1], best[2]


class Model:
    HOME, AWAY, K, PRIOR_K = 1.10, 0.90, 6.0, 900.0
    GPTS = {"FWD": 4, "MID": 5, "DEF": 6, "GKP": 6}
    POS_AVG = {"FWD": 0.40, "MID": 0.22, "DEF": 0.07, "GKP": 0.0}

    def __init__(self, remote=False, market=None, market_gw=None, flags=None):
        self.P = {p["id"]: p for p in load("players.csv", remote)}
        self.H = load("history.csv", remote)
        self.PS = load("past_seasons.csv", remote)
        self.F = load("fixtures.csv", remote)
        self.flags = flags or {}
        att, dfn = collections.defaultdict(float), collections.defaultdict(float)
        games, gk = collections.defaultdict(set), collections.defaultdict(float)
        for r in self.H:
            t, gw = r["team"], int(r["gameweek"])
            games[t].add(gw); att[(t, gw)] += f(r["expected_goals"])
            if r["position"] == "GKP" and f(r["minutes"]) >= gk[(t, gw)]:
                gk[(t, gw)] = f(r["minutes"]); dfn[(t, gw)] = f(r["expected_goals_conceded"])
        self.games = games
        teams = sorted(games)
        self.A = {t: sum(att[(t, g)] for g in games[t]) / len(games[t]) for t in teams}
        self.D = {t: sum(dfn[(t, g)] for g in games[t]) / len(games[t]) for t in teams}
        self.lg = sum(self.A.values()) / len(self.A)
        for t in teams:
            n = len(games[t])
            self.A[t] = (self.A[t] * n + self.lg * self.K) / (n + self.K)
            self.D[t] = (self.D[t] * n + self.lg * self.K) / (n + self.K)
        self.fx = collections.defaultdict(dict)
        for x in self.F:
            try: e = int(x["event"])
            except ValueError: continue
            self.fx[x["team_h"]][e] = (x["team_a"], True)
            self.fx[x["team_a"]][e] = (x["team_h"], False)
        self.hist = collections.defaultdict(list)
        for r in self.H: self.hist[r["player_id"]].append(r)
        self.prior = collections.defaultdict(lambda: [0.0, 0.0])
        for r in self.PS:
            if r["season"] in ("2023/24", "2024/25", "2025/26") and r["expected_goals"] != "":
                self.prior[r["player_id"]][0] += f(r["expected_goal_involvements"])
                self.prior[r["player_id"]][1] += f(r["minutes"])
        self.market = {}
        if market and market_gw is not None:
            for key, (pH, pA) in market.items():
                h, a = key.split("-")
                lh, la = solve(pH / 100, pA / 100)
                self.market[(market_gw, h)] = (lh, la)
                self.market[(market_gw, a)] = (la, lh)

    def lam(self, team, opp, home, gw):
        if (gw, team) in self.market:
            fl, ag = self.market[(gw, team)]
        else:
            fl = (self.A[team] + self.D[opp]) / 2 * (self.HOME if home else self.AWAY)
            ag = (self.A[opp] + self.D[team]) / 2 * (self.AWAY if home else self.HOME)
        fl *= self.flags.get("team_attack", {}).get(team, 1.0)
        ag *= self.flags.get("team_defence", {}).get(team, 1.0)
        return fl, ag

    def rates(self, pid):
        p, rs = self.P[pid], self.hist.get(pid, [])
        mins = sum(f(r["minutes"]) for r in rs)
        xgi = sum(f(r["expected_goal_involvements"]) for r in rs)
        xg = sum(f(r["expected_goals"]) for r in rs)
        pri_x, pri_m = self.prior[pid]
        pr = (pri_x / pri_m * 90) if pri_m >= 900 else self.POS_AVG[p["position"]]
        k = self.PRIOR_K
        rate = (xgi + pr * k / 90) / ((mins + k) / 90)      # xGI per 90, shrunk
        gshare = (xg / xgi) if xgi > 0 else 0.7
        bon = 0.5 * (sum(f(r["bonus"]) for r in rs) / max(mins, 1) * 90) + 0.5 * 0.25
        p60 = sum(1 for r in rs if f(r["minutes"]) >= 60) / max(len(self.games[p["team"]]), 1)
        return rate, gshare, bon, p60

    @staticmethod
    def pgc(l):
        return sum(pois(l, k) * (k // 2) for k in range(12))

    def xp(self, pid, gw):
        p = self.P[pid]; t = p["team"]
        if gw not in self.fx[t]: return 0.0
        opp, home = self.fx[t][gw]
        fl, ag = self.lam(t, opp, home, gw)
        rate, gshare, bon, p60 = self.rates(pid)
        p60 = self.flags.get("availability", {}).get(p["web_name"], p60)
        ch = p["chance_of_playing_next_round"]
        avail = 1.0 if ch in ("", "None", None) else float(ch) / 100
        if p["status"] in ("i", "s", "u"): avail = 0.0
        xgi = rate * (fl / self.lg)
        g = xgi * gshare; a = xgi - g
        pos = p["position"]
        pts = 2 * p60 + p60 * (g * self.GPTS[pos] + a * 3) + bon * p60
        cs = math.exp(-ag)
        if pos in ("GKP", "DEF"): pts += p60 * (4 * cs - self.pgc(ag))
        elif pos == "MID": pts += p60 * cs
        if pos == "GKP": pts += 0.9 * p60
        return pts * avail

    def run(self, pid, gws): return [self.xp(pid, g) for g in gws]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--gws", type=int, nargs="+", required=True)
    ap.add_argument("--remote", action="store_true")
    ap.add_argument("--market"); ap.add_argument("--flags")
    ap.add_argument("--pos"); ap.add_argument("--top", type=int, default=15)
    ap.add_argument("--names", nargs="*")
    a = ap.parse_args()
    market = json.load(open(a.market)) if a.market else None
    flags = json.load(open(a.flags)) if a.flags else None
    m = Model(a.remote, market, a.gws[0], flags)
    rows = []
    for pid, p in m.P.items():
        if a.names and p["web_name"] not in a.names: continue
        if not a.names and (f(p["minutes"]) < 200 or p["status"] in ("i", "s", "u")): continue
        if a.pos and p["position"] != a.pos: continue
        v = m.run(pid, a.gws)
        rows.append((sum(v), p, v))
    for tot, p, v in sorted(rows, key=lambda r: -r[0])[: (None if a.names else a.top)]:
        print(f"{p['web_name']:15}{p['team']} {p['position']} £{p['now_cost_m']:>5} "
              f"own {p['selected_by_percent']:>5}% | " + " ".join(f"{x:4.1f}" for x in v)
              + f" | total {tot:5.1f}")


if __name__ == "__main__":
    main()
