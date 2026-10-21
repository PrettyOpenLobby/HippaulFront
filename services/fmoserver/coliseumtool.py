"""The Coliseum page on the FMO tool port ("/coliseum", "/coliseum.json").

What it shows is what coliseum.py keeps, read and never written:

  - THE ARENAS the Registration Officer lists: the official ones under
    fmoarena's schedule (or the Clash of Iron regulation on a preliminary
    night), a Clash of Iron final while its window is open, and the
    player-hosted ones from the stored document (fmo_coliseum).
  - WHO IS REGISTERED, the MATCHES under way and the winners deciding on
    the streak's next battle. These live in the running process only
    (Coliseum.entries / .matches / .returning): a restart drops them with
    the clients' waiting windows, so they are read off the one Coliseum
    this process serves (coliseum._COLISEUM) and never off the database.
  - THE STREAK RECORDS per arena, the game day's hosting count and the
    Clash of Iron standings, which are in the stored document.

No action is offered. Everything that changes the Coliseum's state is a
pilot's own request (host, register, cancel, the next battle) or the tick
that every online pilot's keepalive already runs (coliseum_pushes_due:
the day roll, the end of a hosted arena, pairing, judging). Ending an arena
early or withdrawing somebody's entry has no code path of its own, and the
0x01BA text a pilot would be shown for it would be ours to choose.

THE ONE TRAP: Coliseum.list_arenas() and open_slots() call tick(), which
rolls the game day and ends hosted arenas, and writes the document. This
module only calls arenas() and view(), both pure, and works the open slots
out itself.
"""
import time

import fmoarena

#: coliseum.STATE_* -> a word for the page
_STATE = {0: "out", 1: "in", 3: "in"}


def _copy(fn):
    """fn() over the live Coliseum's dicts, which the sessions' threads change
    under us: a 'changed size during iteration' is tried again, not shown."""
    for _ in range(4):
        try:
            return fn()
        except RuntimeError:
            time.sleep(0.01)
    return fn()


def _online(account):
    try:
        return trade.session_for_account(account) is not None
    except Exception:
        return False


def _arena(col, a, now):
    v = col.view(a, now)
    aid = int(a["id"])
    fmt = int(a.get("format") or fmoarena.FORMAT_NORMAL)
    ents = []
    for e in list(col.entries.get(aid, [])):
        members = list(e.get("members") or [])
        ents.append({"name": e.get("name") or "", "leader": e.get("leader"),
                     "members": [{"account": m, "online": _online(m)} for m in members],
                     "streak": int(e.get("streak") or 0), "rounds": int(e.get("rounds") or 0),
                     "state": _STATE.get(e.get("state"), "in"), "match": e.get("match"),
                     "at": int(e.get("at") or 0)})
    return {"id": aid, "kind": a.get("kind") or "", "name": a.get("name") or "",
            "promoter": a.get("promoter") or "", "host": a.get("host"),
            "format": "tournament" if fmt == fmoarena.FORMAT_TOURNAMENT else "normal",
            "headcount": int(a.get("headcount") or 1), "req_bgs": int(a.get("req_bgs") or 0),
            "bg_cost": int(v.get("bg_cost") or 0), "total_cost": int(v.get("total_cost") or 0),
            "tile": int(a.get("tile") or 0), "fee": int(a.get("fee") or 0),
            "prize": coliseum.prize_per_win(a), "password": bool(a.get("password")),
            "start": int(a.get("start") or 0), "end": int(a.get("end") or 0),
            "created": int(a.get("created") or 0),
            "closed": bool(v.get("closed")),
            "ended": bool(a.get("end") and now >= a["end"]),
            "schedule": bool(a.get("schedule")), "coi": v.get("coi"),
            "rules": fmoarena.rule_text(v), "entries": ents,
            "records": [{"name": r[0], "streak": int(r[1])}
                        for r in col.records.get(str(aid), []) if len(r) >= 2]}


def _team(e):
    return {"name": e.get("name") or "", "leader": e.get("leader"),
            "members": list(e.get("members") or [])}


def _match(m):
    v = m.get("verdict")
    out = {"id": m["id"], "arena": m["arena"], "name": m.get("name") or "",
           "mapno": m.get("mapno"),
           "format": "tournament" if m.get("format") == fmoarena.FORMAT_TOURNAMENT else "normal",
           "teams": [_team(e) for e in m.get("teams") or ()],
           "pushed": float(m.get("pushed") or 0), "sortied": len(m.get("go") or {}),
           "pilots": len(m.get("sides") or {}), "spectators": len(m.get("spectators") or ()),
           "verdict": None}
    if v is not None:
        w = v[0]
        out["verdict"] = {"winner": w, "winner_name": (out["teams"][w]["name"]
                                                       if w is not None and w < len(out["teams"]) else None),
                          "why": v[1], "at": float(v[2])}
    return out


def _coi(col, now):
    night = fmoarena.coi_night(now)
    fin = fmoarena.coi_final_window(now)
    out = []
    for s in fmoarena.COI_SEASONS:
        doc = col.coi.get(str(s["name"])) or {}
        reg = fmoarena.REGULATIONS.get(s.get("regulation")) or {}
        nights = []
        for d, lo, hi in s.get("nights") or ():
            rows = fmoarena.coi_standings((doc.get("nights") or {}).get(d))
            nights.append({"date": d, "start": lo, "end": hi,
                           "on": bool(night and night[0] is s and night[1] == d),
                           "standings": [{"account": a, "name": n, "wins": w, "reached": t}
                                         for a, n, w, t in rows]})
        out.append({"name": s["name"], "regulation": s.get("regulation"),
                    "title": reg.get("title") or s.get("regulation"),
                    "final_at": s.get("final_at"), "final_open": fin is s,
                    "nights": nights,
                    "qualified": [{"account": a, "name": n, "night": d}
                                  for a, n, d in fmoarena.coi_qualifiers(doc)],
                    "final_wins": dict(doc.get("final") or {})})
    return out


def snapshot(col, now, live):
    """The page's whole state from Coliseum `col` at unix `now`. `live` says
    whether `col` is the one this process serves (else it is a read of the
    stored document, with no registrations)."""
    today = coliseum.game_day(now)
    rolled = col.day != today
    hosted_today = 0 if rolled else int(col.hosted_today)
    cap = coliseum.HOST_CAP
    turns = (today + 1) * 86400 + coliseum.DAY_HOUR_UTC * 3600

    def build():
        return {"arenas": [_arena(col, a, now) for a in col.arenas(now)],
                "matches": sorted((_match(m) for m in list(col.matches.values())),
                                  key=lambda m: -m["id"]),
                "returning": [{"account": acct, "name": e.get("name") or "",
                               "arena": e.get("arena"), "streak": int(e.get("streak") or 0),
                               "deadline": float(dl)}
                              for acct, (e, dl) in list(col.returning.items())],
                "spectators": len(col.spectators),
                "coi": _coi(col, now)}
    body = _copy(build)
    # every arena's records, the ended hosted ones too (their names are gone
    # with them, so they go by id)
    names = {a["id"]: a["name"] for a in body["arenas"]}
    body["records"] = [{"arena": int(k), "name": names.get(int(k), ""),
                        "rows": [{"name": r[0], "streak": int(r[1])} for r in v if len(r) >= 2]}
                       for k, v in sorted(dict(col.records).items(), key=lambda kv: int(kv[0]))]
    body.update({
        "ok": True, "now": int(now), "live": bool(live), "present": bool(col.present),
        "enabled": bool(coliseum.COLISEUM),
        "hosting": {"cap": cap, "today": hosted_today,
                    "open": max(0, cap - hosted_today) if cap > 0 else 0,
                    "day_turns": int(turns), "day_hour_utc": coliseum.DAY_HOUR_UTC,
                    "rank": coliseum.HOST_RANK, "mp": coliseum.HOST_MP,
                    "lead": coliseum.HOST_LEAD, "hours": coliseum.HOST_HOURS,
                    "next_id": int(col.next_id)},
        "official_next_change": fmoarena.next_change(now),
        "settings": {"match_time": coliseum.MATCH_TIME, "countdown": coliseum.COUNTDOWN,
                     "go_wait": coliseum.GO_WAIT, "return_wait": coliseum.RETURN_WAIT,
                     "spectator_seats": coliseum.SPECTATE_SLOTS,
                     "spectate_fee": coliseum.SPECTATE_FEE, "records_kept": coliseum.RECORDS_KEPT},
        "errors": [x for x in (fmoarena.OFFICIAL_ERR, fmoarena.COI_ERR) if x]})
    return body


def state(query, actor=None):
    """GET coliseum.json -> (status, body)."""
    now = time.time()
    col = coliseum._COLISEUM
    live = col is not None
    if col is None:
        # Nobody has touched a desk since the start: there are no
        # registrations or matches to show, and coliseum.coliseum() is not
        # called, so looking does not create the process's Coliseum.
        col = coliseum.Coliseum()
    return 200, snapshot(col, now, live)


def routes():
    """The Coliseum's page and endpoint on the FMO tool port."""
    return {("GET", "/coliseum"): lambda _q, _a: (200, PAGE, "text/html"),
            ("GET", "/coliseum.json"): state}


PAGE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Coliseum</title>
<style>
:root{
  --bg:#0f1317;--panel:#161c23;--sunk:#0b0f13;--ink:#e5eaf1;--ink2:#98a4b4;
  --ink3:#66727f;--rule:#232d38;--accent:#74a8ea;--warn:#d59450;--good:#59ab80;
  --m:ui-monospace,"IBM Plex Mono",Consolas,monospace;
}
@media (prefers-color-scheme:light){
  :root{--bg:#edeff3;--panel:#fafbfd;--sunk:#e3e7ee;--ink:#171b21;--ink2:#4b5563;
        --ink3:#7b8798;--rule:#d5dae3;--accent:#2b5ea6;--warn:#96551a;--good:#256b4c;}
}
*{box-sizing:border-box}
[hidden]{display:none!important}
body{margin:0;background:var(--bg);color:var(--ink);font:14px/1.5 system-ui,sans-serif}
.wrap{max-width:1180px;margin:0 auto;padding:16px 14px 60px;display:grid;gap:14px;grid-template-columns:minmax(0,1fr)}
h1{font-size:1.1rem;margin:0;font-weight:600}
.card{background:var(--panel);border:1px solid var(--rule);border-radius:5px;padding:13px 15px;min-width:0}
.hd{display:flex;justify-content:space-between;align-items:baseline;gap:10px;margin-bottom:9px;flex-wrap:wrap}
.hd h2{font-size:.95rem;margin:0;font-weight:600}
.sub{font-size:12px;color:var(--ink3)}
.mono{font-family:var(--m);font-size:12.5px}
.top{display:flex;flex-wrap:wrap;gap:10px 16px;align-items:center}
.pill{display:inline-block;border:1px solid var(--rule);border-radius:10px;padding:0 8px;font-size:12px;color:var(--ink2)}
.pill.on{border-color:var(--good);color:var(--good)}
.pill.warn{border-color:var(--warn);color:var(--warn)}
.msg{font-size:13px;border-radius:4px;padding:6px 9px;border:1px solid var(--warn);color:var(--warn)}
.facts{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:1px;background:var(--rule);
  border:1px solid var(--rule);border-radius:4px;overflow:hidden}
.facts div{background:var(--panel);padding:6px 10px}
.facts dt{font-size:10.5px;letter-spacing:.08em;text-transform:uppercase;color:var(--ink3)}
.facts dd{margin:1px 0 0;font-family:var(--m);font-size:13px}
.grid2{display:grid;grid-template-columns:minmax(0,1fr);gap:14px}
@media(min-width:1000px){.grid2{grid-template-columns:minmax(0,3fr) minmax(0,2fr)}}
.tw{overflow-x:auto}
table{border-collapse:collapse;width:100%}
th,td{text-align:left;padding:5px 7px;border-bottom:1px solid var(--rule);vertical-align:top}
th{font-size:11.5px;font-weight:600;color:var(--ink3);text-transform:uppercase;letter-spacing:.05em}
td.n{font-family:var(--m);font-size:12.5px;white-space:nowrap}
tr.dim td{color:var(--ink2)}
.why{font-size:12px;color:var(--ink3)}
.ents{margin:4px 0 0;padding:0;list-style:none;display:grid;gap:2px;font-size:12.5px}
.ents .acct{font-family:var(--m);font-size:12px;color:var(--ink2)}
.ents .acct.on{color:var(--good)}
.night{border-bottom:1px solid var(--rule);padding:6px 0}
.night:last-child{border-bottom:0}
</style></head><body>
<div class="wrap">
  <div class="card">
    <div class="top">
      <h1>Coliseum</h1>
      <span id="badges"></span>
      <span id="clock" class="sub"></span>
    </div>
    <div id="msg" class="msg" style="margin-top:10px" hidden></div>
    <div class="facts" id="facts" style="margin-top:10px"></div>
  </div>

  <div class="card">
    <div class="hd"><h2>Arenas</h2><span class="sub" id="arsub"></span></div>
    <div class="tw"><table><thead><tr><th>Id</th><th>Arena</th><th>Format</th><th>B.G.Cost</th><th>Fee / prize</th><th>Open</th><th>Registered</th></tr></thead>
    <tbody id="arenas"></tbody></table></div>
  </div>

  <div class="grid2">
    <div class="card">
      <div class="hd"><h2>Matches</h2><span class="sub" id="msub"></span></div>
      <div class="tw"><table><thead><tr><th>Match</th><th>Arena</th><th>Teams</th><th>Sortied</th><th>Result</th></tr></thead>
      <tbody id="matches"></tbody></table></div>
      <div id="retwrap" style="margin-top:12px" hidden>
        <div class="hd"><h2>Deciding on the next battle</h2></div>
        <div class="tw"><table><thead><tr><th>Pilot</th><th>Entry</th><th>Arena</th><th>Streak</th><th>Until</th></tr></thead>
        <tbody id="returning"></tbody></table></div>
      </div>
    </div>
    <div class="card">
      <div class="hd"><h2>Streak records</h2><span class="sub" id="recsub"></span></div>
      <div id="records"></div>
    </div>
  </div>

  <div class="card">
    <div class="hd"><h2>Clash of Iron</h2><span class="sub" id="coisub"></span></div>
    <div id="coi"></div>
  </div>
</div>
<script>
const T = new URLSearchParams(location.search).get('t') || '';
const qs = p => { p = p.replace(/^\//, ''); return T ? p + (p.includes('?') ? '&' : '?') + 't=' + encodeURIComponent(T) : p; };
const q = s => document.querySelector(s);
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const pad = n => String(n).padStart(2, '0');
function utc(t){ if(!t) return '-'; const d = new Date(t * 1000);
  return `${d.getUTCFullYear()}-${pad(d.getUTCMonth()+1)}-${pad(d.getUTCDate())} ${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())}`; }
function ago(t, now){ const s = Math.max(0, Math.round(now - t));
  return s < 90 ? s + ' s ago' : s < 5400 ? Math.round(s / 60) + ' min ago' : Math.round(s / 3600) + ' h ago'; }
function money(n){ return 'H$ ' + Number(n || 0).toLocaleString('en-US'); }
function setHTML(el, html){ if(el.dataset.h !== html){ el.dataset.h = html; el.innerHTML = html; } }
function facts(o){ return Object.entries(o).map(([k, v]) => `<div><dt>${esc(k)}</dt><dd>${esc(v)}</dd></div>`).join(''); }
let LAST = '';

async function load(){
  try{
    const r = await fetch(qs('coliseum.json'));
    const t = await r.text();
    const j = JSON.parse(t);
    if(!r.ok) throw new Error(j.msg || j.error || r.statusText);
    q('#msg').hidden = true;
    if(t === LAST) return;
    LAST = t; render(j);
  }catch(e){ q('#msg').hidden = false; q('#msg').textContent = e.message; }
}

function render(S){
  const h = S.hosting, now = S.now;
  q('#badges').innerHTML = `<span class="pill ${S.enabled ? 'on' : 'warn'}">${S.enabled ? 'Desks open' : 'Desks off (FMO_COLISEUM=0)'}</span> `
    + (S.live ? '' : '<span class="pill">No pilot has used the Coliseum since the server started</span>');
  q('#clock').textContent = 'As of ' + utc(now) + ' UTC';
  if(S.errors && S.errors.length){ q('#msg').hidden = false; q('#msg').textContent = S.errors.join(' / '); }
  setHTML(q('#facts'), facts({
    'Hosting slots today': h.cap > 0 ? `${h.open} of ${h.cap} open` : 'hosting off',
    'Day turns': utc(h.day_turns) + ' UTC',
    'Host needs': `rank ${h.rank}, ${h.mp} MP`,
    'Hosted arena runs': `${Math.round(h.lead / 60)} min after filing, for ${h.hours} h`,
    'Match time limit': Math.round(S.settings.match_time / 60) + ' min',
    'Spectator seats': S.settings.spectator_seats + ' per match',
    'Watching now': S.spectators,
    'Official rules change': utc(S.official_next_change) + ' UTC',
  }));
  const ar = S.arenas || [];
  q('#arsub').textContent = ar.length + ' listed, ' + ar.filter(a => a.kind === 'hosted').length + ' hosted by players';
  setHTML(q('#arenas'), ar.map(a => {
    const kind = a.kind === 'official' ? 'Official' : a.kind === 'coi' ? 'Clash of Iron final' : 'Hosted';
    const fmt = a.format === 'tournament' ? `Tournament of ${a.req_bgs}` : 'Normal';
    const open = a.ended ? '<span class="pill warn">time is up</span>'
      : a.closed ? '<span class="pill">bracket under way</span>'
      : a.start && a.start > now ? 'battles from ' + utc(a.start) : (a.end ? 'until ' + utc(a.end) : 'always');
    const ents = a.entries.length ? `<ul class="ents">` + a.entries.map(e =>
      `<li>${esc(e.name || '(unnamed)')} <span class="why">${e.state === 'out' ? 'knocked out' : e.match ? 'in match ' + e.match : 'waiting'}`
      + `${e.streak ? ', streak ' + e.streak : ''}${a.format === 'tournament' ? ', round ' + (e.rounds + 1) : ''}</span><br>`
      + e.members.map(m => `<span class="acct ${m.online ? 'on' : ''}">${esc(m.account)}${m.account === e.leader && e.members.length > 1 ? ' (leader)' : ''}</span>`).join(' ')
      + `</li>`).join('') + `</ul>` : '<span class="sub">nobody</span>';
    return `<tr class="${a.ended ? 'dim' : ''}"><td class="n">${a.id}</td>`
      + `<td>${esc(a.name)}<div class="why">${kind}${a.promoter ? ' by ' + esc(a.promoter) : ''}${a.host ? ' <span class="mono">' + esc(a.host) + '</span>' : ''}`
      + `${a.password ? ', password' : ''}</div><div class="why">${esc(a.rules)}</div></td>`
      + `<td>${fmt}<div class="why">${a.headcount} per BG</div></td>`
      + `<td class="n">${a.bg_cost ? a.bg_cost + ' / ' + a.total_cost : 'any'}</td>`
      + `<td class="n">${a.fee ? money(a.fee) + '<div class="why">' + money(a.prize) + ' per win</div>' : 'free'}</td>`
      + `<td>${open}</td><td>${ents}</td></tr>`;
  }).join('') || '<tr><td colspan="7" class="sub">No arenas.</td></tr>');

  const ms = S.matches || [];
  q('#msub').textContent = ms.filter(m => !m.verdict).length + ' under way';
  setHTML(q('#matches'), ms.map(m => {
    const res = m.verdict ? (m.verdict.winner === null ? 'Draw' : esc(m.verdict.winner_name) + ' won')
        + `<div class="why">${esc(m.verdict.why)}, ${ago(m.verdict.at, now)}</div>` : '<span class="pill on">running</span>';
    return `<tr class="${m.verdict ? 'dim' : ''}"><td class="n">${m.id}<div class="why">map ${esc(m.mapno)}</div></td>`
      + `<td>${esc(m.name)} <span class="why">#${m.arena}</span></td>`
      + `<td>${m.teams.map(t => esc(t.name) + ' <span class="why mono">' + esc(t.members.join(', ')) + '</span>').join('<br>vs ')}</td>`
      + `<td class="n">${m.sortied} of ${m.pilots}${m.spectators ? '<div class="why">' + m.spectators + ' watching</div>' : ''}</td>`
      + `<td>${res}</td></tr>`;
  }).join('') || '<tr><td colspan="5" class="sub">No matches since the server started.</td></tr>');
  const rt = S.returning || [];
  q('#retwrap').hidden = !rt.length;
  setHTML(q('#returning'), rt.map(r => `<tr><td class="n">${esc(r.account)}</td><td>${esc(r.name)}</td><td class="n">${r.arena}</td>`
    + `<td class="n">${r.streak}</td><td class="n">${utc(r.deadline)}</td></tr>`).join(''));

  const withRec = (S.records || []).filter(a => a.rows.length);
  q('#recsub').textContent = `top ${S.settings.records_kept} per arena`;
  setHTML(q('#records'), withRec.map(a => `<div class="night"><b>${esc(a.name || 'Arena ' + a.arena + ' (ended)')}</b> <span class="why">#${a.arena}</span>`
    + `<ol class="ents" style="padding-left:20px;list-style:decimal">${a.rows.map(r => `<li>${esc(r.name)} <span class="why">${r.streak} win${r.streak === 1 ? '' : 's'}</span></li>`).join('')}</ol></div>`).join('')
    || '<span class="sub">No streak on record yet.</span>');

  const cs = S.coi || [];
  q('#coisub').textContent = cs.length ? cs.length + ' season' + (cs.length === 1 ? '' : 's') + ' on the calendar' : 'no seasons (FMO_COI_SEASONS=0)';
  setHTML(q('#coi'), cs.map(s => {
    const nights = s.nights.map(n => `<div class="why">${esc(n.date)}${n.on ? ' <span class="pill on">on now</span>' : ''}: `
      + (n.standings.length ? n.standings.slice(0, 5).map(r => `${esc(r.name)} ${r.wins}`).join(', ') : 'no wins') + `</div>`).join('');
    const quals = s.qualified.length ? s.qualified.map(x => `${esc(x.name)} <span class="why">(${esc(x.night)})</span>`).join(', ') : 'none yet';
    const fw = Object.entries(s.final_wins || {});
    return `<div class="night"><b>${esc(s.name)}</b> <span class="why">${esc(s.title)}, final ${utc(s.final_at)} UTC</span>`
      + `${s.final_open ? ' <span class="pill on">final listed</span>' : ''}`
      + `<div>Qualified: ${quals}</div>${nights}`
      + (fw.length ? `<div class="why">Final wins: ${fw.map(([a, w]) => esc(a) + ' ' + w).join(', ')}</div>` : '') + `</div>`;
  }).join('') || '<span class="sub">None.</span>');
}

load(); setInterval(() => { if(!document.hidden) load(); }, 5000);
</script></body></html>
"""


# Called at run time only; imported last so that import cycles resolve.
from . import coliseum, trade  # noqa: E402
