"""The war page on the FMO tool port ("/war", "/war.json", "/war/tick").

What it shows is the stored war (fmo_war) as the City Control board reads
it: boardfmo's own readers (load_war, cities, phase, recent_battles,
connections, changed_hands), so the page and the board can never disagree
about who holds a city. On top of the board: the judged phases, the
frontline resets, the restarts archived under "wars", the state's own log
lines, every sector on file (?sectors=1) and the knobs the rules run on.

THE ONE ACTION is the phase clock: warstate._war_tick on the process's own
War, exactly what the server runs at start, on a sector query, after a
settled battle and on a service record. It judges every phase whose
judgement time has passed and is not on file, and starts the current phase
(the frontline reset and the loser's fortress Deadlock) if that has not
run. Each runs once (War.tick keeps the record), so the button only spares
an operator waiting for the next pilot to trigger it, and it is refused
when nothing is due.

LEFT OUT: restarting the war. FMO_WAR_RESTART (read when the War loads) and
`python fmowar.py --restart` with the service stopped are its two paths;
a restart from inside the running service would race the sessions' own
writes of the same document, and it archives every judged phase.
"""
import time

from .deps import fmosectors, fmowar
from .wirelog import log

try:
    import boardfmo                          # the City Control board's readers
except ImportError:                          # pragma: no cover
    boardfmo = None

_NATION = {1: "O.C.U.", 2: "U.S.N."}


def due(data, phase1, now):
    """{"judge": [phase numbers], "start": [phase numbers]} that War.tick
    would run at `now` on the state `data`, by its own rules. Pure."""
    n, _start, _judge, _nxt, cease = fmowar.phase_at(now, phase1)
    phases = data.get("phases") or {}
    resets = data.get("resets") or {}
    return {"judge": [pn for pn in range(1, n + (1 if cease else 0)) if str(pn) not in phases],
            "start": ([pn for pn in range(2, n + 1) if str(pn) not in resets][-1:]
                      if n >= 2 else [])}


def _tile_zones():
    """tile -> [selector] from the sector table, for naming a sector."""
    out = {}
    for sel, rows in (getattr(fmosectors, "SECTORS", None) or {}).items():
        for t in rows:
            out.setdefault(int(t), []).append(int(sel))
    return out


def _sectors(sectors):
    zones = _tile_zones()
    out = []
    for key, s in sectors.items():
        if not isinstance(s, dict):
            continue
        tile = int(key)
        try:
            kind = int(str(s.get("seeded") or "").split()[-1])
        except (ValueError, IndexError):
            kind = None
        sels = zones.get(tile) or []
        sel = next((z for z in sels if kind is not None and z // 100 == kind), sels[0] if sels else None)
        city = fmowar.CITIES.get(tile)
        out.append({"tile": tile, "zone": sel,
                    "area": boardfmo.zone_label(sel) if sel is not None else "",
                    "sector": boardfmo.sector_row(sel, tile) if sel is not None else None,
                    "name": city[0] if city else (fmowar.FACILITY.get(tile, "").split(" ", 1)[-1]
                                                  if tile in fmowar.FACILITY else ""),
                    "nation": boardfmo.holder(s), "control": int(s.get("control") or 0),
                    "cap": fmowar.control_cap(tile),
                    "counter": {"ocu": int((s.get("counter") or {}).get("1") or 0),
                                "usn": int((s.get("counter") or {}).get("2") or 0)},
                    "wins": {"ocu": int((s.get("wins") or {}).get("1") or 0),
                             "usn": int((s.get("wins") or {}).get("2") or 0)},
                    "seeded": s.get("seeded") or "", "changed": boardfmo.changed_hands(s, tile),
                    "updated": int(s.get("updated") or 0)})
    out.sort(key=lambda r: (r["zone"] if r["zone"] is not None else 1 << 30, r["sector"] or 0, r["tile"]))
    return out


def _judged(pn, rec):
    pen = rec.get("penalty") or {}
    return {"n": int(pn), "winner": int(rec.get("winner") or 0),
            "ocu": int(rec.get("ocu") or 0), "usn": int(rec.get("usn") or 0),
            "judged_at": int(rec.get("judged_at") or 0),
            "penalty": ({"nation": int(pen.get("nation") or 0), "tile": int(pen.get("tile") or 0)}
                        if pen else None),
            "reward": {_NATION.get(int(k), k): (r or {}).get("name")
                       for k, r in (rec.get("reward") or {}).items()},
            "reward_from": int(rec.get("reward_from") or 0)}


def snapshot(now, with_sectors=False):
    """The page's whole state at unix `now`, from the stored war. Never
    writes: War(autosave=False), and none of War.tick / War.sector."""
    war, present, mtime = boardfmo.load_war()
    data = war.data
    sectors = data.get("sectors") or {}
    score = war.score()
    held = {1: 0, 2: 0}
    for s in sectors.values():
        if isinstance(s, dict) and boardfmo.holder(s):
            held[boardfmo.holder(s)] += 1
    body = {"ok": True, "now": int(now),
            "war": {"present": present, "mtime": int(mtime) if mtime else None,
                    "sectors": len(sectors), "phase1": war.phase1(),
                    "held": {"ocu": held[1], "usn": held[2]},
                    "changed": sum(1 for t, s in sectors.items()
                                   if isinstance(s, dict) and boardfmo.changed_hands(s, t)),
                    "restarted_to": data.get("restarted_to"),
                    "restarted_at": data.get("restarted_at")},
            "phase": boardfmo.phase(war, now),
            "score": {"ocu": score[fmowar.OCU], "usn": score[fmowar.USN],
                      "total": sum(p for _n, p, _w in fmowar.CITIES.values())},
            "cities": boardfmo.cities(war),
            "recent": boardfmo.recent_battles(),
            "connections": boardfmo.connections(now),
            "due": due(data, war.phase1(), now),
            "judged": [_judged(k, v) for k, v in sorted((data.get("phases") or {}).items(),
                                                        key=lambda kv: int(kv[0]))
                       if isinstance(v, dict)],
            "resets": [dict(v, n=int(k)) for k, v in sorted((data.get("resets") or {}).items(),
                                                             key=lambda kv: int(kv[0]))
                       if isinstance(v, dict)],
            "wars": [{"phase1": w.get("phase1"), "phases": len(w.get("phases") or {}),
                      "sectors": w.get("sectors"), "ended_at": w.get("ended_at")}
                     for w in data.get("wars") or [] if isinstance(w, dict)],
            "log": [str(x) for x in (data.get("log") or [])][-40:],
            "knobs": {"FMO_WAR": warstate.WAR, "FMO_WAR_PHASE1": fmowar.PHASE1_START,
                      "FMO_WAR_COUNTER_CAP": fmowar.COUNTER_CAP,
                      "FMO_WAR_RATE_STEP": fmowar.RATE_STEP,
                      "FMO_WAR_FACILITY_CAP": fmowar.FACILITY_CAP,
                      "FMO_WAR_LOSS": fmowar.LOSS_WEIGHT,
                      "FMO_WAR_NEUTRAL": int(fmowar.NEUTRAL),
                      "FMO_WAR_PHASE_RESET": int(fmowar.PHASE_RESET),
                      "FMO_WAR_RESTART": fmowar.RESTART or "",
                      "FMO_WAR_MAP": warstate.WAR_MAP or ""}}
    if with_sectors:
        body["sector_rows"] = _sectors(sectors)
    return body


def state(query, actor=None):
    """GET war.json[?sectors=1] -> (status, body)."""
    if fmowar is None or boardfmo is None:
        return 503, {"ok": False, "msg": "The war module is missing."}
    return 200, snapshot(time.time(), with_sectors=bool(query.get("sectors")))


def tick(body, actor=None):
    """POST war/tick -> (status, body): run the phase clock, as the server
    does, on the process's own War."""
    if fmowar is None:
        return 503, {"ok": False, "msg": "The war module is missing."}
    st = warstate.war_state()
    now = time.time()
    d = due(st.data, st.phase1(), now)
    if not d["judge"] and not d["start"]:
        n = st.phase_at(now)[0]
        return 200, {"ok": False, "msg": "Nothing is due: phase %d is %s." % (
            n, "judged" if st.phase_at(now)[4] else "running")}
    before = set((st.data.get("phases") or {}).keys())
    warstate._war_tick(st)
    judged = sorted(int(k) for k in set((st.data.get("phases") or {}).keys()) - before)
    started = [pn for pn, _rec in getattr(st, "resets_now", ())]
    what = ["phase %d judged (%s)" % (pn, {1: "O.C.U. wins", 2: "U.S.N. wins"}.get(
        int(st.data["phases"][str(pn)].get("winner") or 0), "a tie"))
        for pn in judged] + ["phase %d started (frontline reset)" % pn for pn in started]
    if not what:
        return 200, {"ok": False, "msg": "The phase clock ran and changed nothing."}
    log(f"war tool: {actor or 'tool'} ran the phase clock: {'; '.join(what)}")
    t = "; ".join(what)
    return 200, {"ok": True, "msg": t[:1].upper() + t[1:] + "."}


def routes():
    """The war's page and endpoints on the FMO tool port."""
    return {("GET", "/war"): lambda _q, _a: (200, PAGE, "text/html"),
            ("GET", "/war.json"): state, ("POST", "/war/tick"): tick}


PAGE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>War</title>
<style>
:root{
  --bg:#0f1317;--panel:#161c23;--sunk:#0b0f13;--ink:#e5eaf1;--ink2:#98a4b4;
  --ink3:#66727f;--rule:#232d38;--accent:#74a8ea;--warn:#d59450;--good:#59ab80;
  --ocu:#6292ff;--usn:#ff6e58;
  --m:ui-monospace,"IBM Plex Mono",Consolas,monospace;
}
@media (prefers-color-scheme:light){
  :root{--bg:#edeff3;--panel:#fafbfd;--sunk:#e3e7ee;--ink:#171b21;--ink2:#4b5563;
        --ink3:#7b8798;--rule:#d5dae3;--accent:#2b5ea6;--warn:#96551a;--good:#256b4c;
        --ocu:#2556c4;--usn:#c23a24;}
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
button{font:inherit;font-size:13px;cursor:pointer;color:var(--ink);background:var(--sunk);
       border:1px solid var(--rule);border-radius:4px;padding:4px 10px}
button:hover:not(:disabled){border-color:var(--accent);color:var(--accent)}
button:disabled{opacity:.4;cursor:not-allowed}
button.go{border-color:var(--good);color:var(--good)}
input{font:inherit;font-size:13px;background:var(--sunk);color:var(--ink);
  border:1px solid var(--rule);border-radius:4px;padding:4px 7px}
input[type=checkbox]{accent-color:var(--accent)}
.top{display:flex;flex-wrap:wrap;gap:10px 16px;align-items:center}
.pill{display:inline-block;border:1px solid var(--rule);border-radius:10px;padding:0 8px;font-size:12px;color:var(--ink2)}
.pill.on{border-color:var(--good);color:var(--good)}
.pill.warn{border-color:var(--warn);color:var(--warn)}
.msg{font-size:13px;border-radius:4px;padding:6px 9px;border:1px solid var(--rule)}
.msg.ok{border-color:var(--good);color:var(--good)}
.msg.bad{border-color:var(--warn);color:var(--warn)}
.facts{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:1px;background:var(--rule);
  border:1px solid var(--rule);border-radius:4px;overflow:hidden}
.facts div{background:var(--panel);padding:6px 10px}
.facts dt{font-size:10.5px;letter-spacing:.08em;text-transform:uppercase;color:var(--ink3)}
.facts dd{margin:1px 0 0;font-family:var(--m);font-size:13px}
.score{display:flex;align-items:center;gap:12px;font-family:var(--m);font-size:15px;flex-wrap:wrap}
.bar{flex:1;min-width:160px;height:10px;border-radius:5px;background:var(--sunk);overflow:hidden;display:flex}
.bar i{display:block;height:100%}
.ocu{color:var(--ocu)} .usn{color:var(--usn)} .dl{color:var(--ink3)}
.grid2{display:grid;grid-template-columns:minmax(0,1fr);gap:14px}
@media(min-width:1000px){.grid2{grid-template-columns:minmax(0,3fr) minmax(0,2fr)}}
.tw{overflow-x:auto}
table{border-collapse:collapse;width:100%}
th,td{text-align:left;padding:5px 7px;border-bottom:1px solid var(--rule);vertical-align:top}
th{font-size:11.5px;font-weight:600;color:var(--ink3);text-transform:uppercase;letter-spacing:.05em}
td.n{font-family:var(--m);font-size:12.5px;white-space:nowrap}
td.r{text-align:right}
.why{font-size:12px;color:var(--ink3)}
.row{display:flex;gap:8px;flex-wrap:wrap;align-items:center}
.log{font-family:var(--m);font-size:12px;color:var(--ink2);max-height:220px;overflow:auto;white-space:pre-wrap}
.duebox{border-left:3px solid var(--warn);padding:4px 0 4px 11px;margin-top:10px}
</style></head><body>
<div class="wrap">
  <div class="card">
    <div class="top">
      <h1>War</h1>
      <span id="badges"></span>
      <span id="clock" class="sub"></span>
    </div>
    <div class="score" style="margin-top:10px">
      <span class="ocu" id="socu"></span>
      <div class="bar" id="sbar"></div>
      <span class="usn" id="susn"></span>
    </div>
    <div class="facts" id="facts" style="margin-top:10px"></div>
    <div class="duebox" id="due" hidden>
      <div class="row"><span id="duetext"></span><button class="go" id="tick">Run the phase clock now</button></div>
      <div class="why">The server runs it itself on the next sector query, settled battle or service record; this runs the same step now.</div>
    </div>
    <div id="msg" class="msg" style="margin-top:10px" hidden></div>
  </div>

  <div class="grid2">
    <div class="card">
      <div class="hd"><h2>City control</h2><span class="sub" id="csub"></span></div>
      <div class="tw"><table><thead><tr><th>City</th><th>Sector</th><th>Control</th><th class="r">Rate</th><th class="r">B.G.Cost</th><th class="r">Rank</th><th class="r">Wins</th></tr></thead>
      <tbody id="cities"></tbody></table></div>
    </div>
    <div style="display:grid;gap:14px;align-content:start">
      <div class="card">
        <div class="hd"><h2>Recent sector wins</h2><span class="sub">kept two days</span></div>
        <div class="tw"><table><tbody id="recent"></tbody></table></div>
      </div>
      <div class="card">
        <div class="hd"><h2>Judged phases</h2></div>
        <div class="tw"><table><thead><tr><th>Phase</th><th>Result</th><th>Reward</th></tr></thead>
        <tbody id="judged"></tbody></table></div>
        <div id="resets" class="why" style="margin-top:8px"></div>
      </div>
      <div class="card">
        <div class="hd"><h2>State log</h2></div>
        <div class="log" id="log"></div>
      </div>
    </div>
  </div>

  <div class="card">
    <details id="secdet"><summary class="hd" style="cursor:pointer"><h2>Every sector on file</h2><span class="sub" id="secsub"></span></summary>
      <div class="row" style="margin:6px 0"><input id="secq" placeholder="Filter" aria-label="Filter sectors">
        <label class="sub"><input type="checkbox" id="secchg"> Only sectors that changed hands</label></div>
      <div class="tw"><table><thead><tr><th>Tile</th><th>Sector</th><th>Name</th><th>Control</th><th class="r">Rate</th><th class="r">Counter</th><th class="r">Wins</th><th>Updated</th></tr></thead>
      <tbody id="sectors"></tbody></table></div>
    </details>
  </div>

  <div class="card">
    <details><summary class="hd" style="cursor:pointer"><h2>Rules in force</h2><span class="sub">the FMO_WAR knobs</span></summary>
      <div class="facts" id="knobs"></div>
    </details>
  </div>
</div>
<script>
const T = new URLSearchParams(location.search).get('t') || '';
const qs = p => { p = p.replace(/^\//, ''); return T ? p + (p.includes('?') ? '&' : '?') + 't=' + encodeURIComponent(T) : p; };
const q = s => document.querySelector(s);
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const pad = n => String(n).padStart(2, '0');
const NAT = {1: ['O.C.U.', 'ocu'], 2: ['U.S.N.', 'usn'], 0: ['Deadlock', 'dl']};
function utc(t){ if(!t) return '-'; const d = new Date(t * 1000);
  return `${d.getUTCFullYear()}-${pad(d.getUTCMonth()+1)}-${pad(d.getUTCDate())} ${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())}`; }
function nat(n){ const x = NAT[n] || NAT[0]; return `<span class="${x[1]}">${x[0]}</span>`; }
function setHTML(el, html){ if(el.dataset.h !== html){ el.dataset.h = html; el.innerHTML = html; } }
function facts(o){ return Object.entries(o).map(([k, v]) => `<div><dt>${esc(k)}</dt><dd>${esc(v)}</dd></div>`).join(''); }
function say(text, ok){ const m = q('#msg'); m.hidden = !text; m.textContent = text || ''; m.className = 'msg ' + (ok ? 'ok' : 'bad'); }
let LAST = '', SECT = null, BUSY = false;

async function load(){
  if(BUSY) return;
  try{
    const want = !q('#secdet').open ? '' : '?sectors=1';
    const r = await fetch(qs('war.json' + want));
    const t = await r.text();
    const j = JSON.parse(t);
    if(!r.ok) throw new Error(j.msg || j.error || r.statusText);
    if(j.sector_rows) SECT = j.sector_rows;
    if(t === LAST) return;
    LAST = t; render(j);
  }catch(e){ say(e.message, false); }
}

function render(S){
  const p = S.phase, w = S.war, sc = S.score;
  q('#badges').innerHTML = (w.present ? '' : '<span class="pill warn">No war state on file</span> ')
    + (p.n === 0 ? '<span class="pill">Before the war</span>' : p.ceasefire ? '<span class="pill warn">Ceasefire</span>' : '<span class="pill on">Phase ' + p.n + ' running</span>')
    + (S.connections != null ? ` <span class="pill">${S.connections} connection${S.connections === 1 ? '' : 's'}</span>` : '');
  q('#clock').textContent = 'As of ' + utc(S.now) + ' UTC';
  q('#socu').textContent = 'O.C.U. ' + sc.ocu;
  q('#susn').textContent = sc.usn + ' U.S.N.';
  const free = Math.max(0, sc.total - sc.ocu - sc.usn);
  setHTML(q('#sbar'), `<i style="width:${100*sc.ocu/sc.total}%;background:var(--ocu)"></i>`
    + `<i style="width:${100*free/sc.total}%"></i><i style="width:${100*sc.usn/sc.total}%;background:var(--usn)"></i>`);
  setHTML(q('#facts'), facts({
    'Phase': p.n || 'none yet',
    'Started': utc(p.start),
    'Judged at': utc(p.judge),
    'Next phase': utc(p.next),
    'Judgement': p.judged ? (p.judged.winner ? NAT[p.judged.winner][0] + ' won' : 'a tie') : (p.ceasefire && p.n ? 'not run yet' : '-'),
    'Phase 1 of this war': w.phase1,
    'Sectors on file': w.sectors,
    'Held': `O.C.U. ${w.held.ocu}, U.S.N. ${w.held.usn}`,
    'Changed hands': w.changed,
    'State written': w.mtime ? utc(w.mtime) : '-',
  }));
  const d = S.due, items = d.judge.map(n => 'phase ' + n + ' is past its judgement').concat(d.start.map(n => 'phase ' + n + ' has not been started'));
  q('#due').hidden = !items.length;
  q('#duetext').textContent = items.length ? items.join('; ').replace(/^./, c => c.toUpperCase()) + '.' : '';

  const cs = S.cities || [];
  q('#csub').textContent = cs.filter(c => c.nation).length + ' of ' + cs.length + ' held';
  setHTML(q('#cities'), cs.map(c => `<tr><td>${esc(c.name)}</td><td class="n">${esc(c.area)} / ${pad(c.sector)}</td>`
    + `<td>${nat(c.nation)}</td><td class="n r">${c.nation ? c.control + '%' : '-'}</td>`
    + `<td class="n r">${c.bg_max} / ${c.bg_min}</td><td class="n r">${c.rank}</td>`
    + `<td class="n r"><span class="ocu">${c.wins.ocu}</span> / <span class="usn">${c.wins.usn}</span></td></tr>`).join(''));
  setHTML(q('#recent'), (S.recent || []).map(b => `<tr><td>${nat(b.nation)}</td><td>${esc(b.area)}${b.sector != null ? ' / ' + pad(b.sector) : ''}`
    + `${b.city ? ' <span class="why">' + esc(b.city) + '</span>' : ''}</td><td class="n">${utc(b.t)}</td></tr>`).join('')
    || '<tr><td class="sub">None in the last two days.</td></tr>');
  setHTML(q('#judged'), (S.judged || []).map(j => `<tr><td class="n">${j.n}<div class="why">${utc(j.judged_at)}</div></td>`
    + `<td>${j.winner ? nat(j.winner) + ' won' : 'A tie'} <span class="mono">${j.ocu} : ${j.usn}</span>`
    + `${j.penalty ? '<div class="why">' + NAT[j.penalty.nation][0] + ' fortress ' + j.penalty.tile + ' opens Deadlock</div>' : ''}</td>`
    + `<td>${Object.entries(j.reward).map(([k, v]) => esc(k) + ': ' + esc(v)).join('<br>') || '-'}`
    + `${j.reward_from ? '<div class="why">on sale from ' + utc(j.reward_from) + '</div>' : ''}</td></tr>`).join('')
    || '<tr><td colspan="3" class="sub">No phase judged yet.</td></tr>');
  q('#resets').textContent = (S.resets || []).filter(r => !r.superseded_by).map(r => `Phase ${r.n} started ${utc(r.at)}: ${r.sectors} sector(s) reset.`)
    .concat((S.wars || []).length ? [`Restarted ${(S.wars || []).length} time(s); this war's phase 1 is ${w.phase1}.`] : []).join(' ');
  q('#log').textContent = (S.log || []).join('\n') || 'Nothing logged.';
  setHTML(q('#knobs'), facts(S.knobs || {}));
  renderSectors();
}

function renderSectors(){
  const rows = SECT || [];
  q('#secsub').textContent = SECT ? rows.length + ' sectors' : 'open to load';
  const f = q('#secq').value.trim().toLowerCase(), chg = q('#secchg').checked;
  const list = rows.filter(r => (!chg || r.changed) && (!f || JSON.stringify(r).toLowerCase().includes(f) || (NAT[r.nation][0].toLowerCase().includes(f))));
  setHTML(q('#sectors'), list.slice(0, 400).map(r => `<tr><td class="n">${r.tile}</td><td class="n">${esc(r.area)}${r.sector != null ? ' / ' + pad(r.sector) : ''}</td>`
    + `<td>${esc(r.name)}<div class="why">${esc(r.seeded)}</div></td><td>${nat(r.nation)}${r.changed ? ' <span class="pill warn">changed</span>' : ''}</td>`
    + `<td class="n r">${r.nation ? r.control + '%' : '-'}<div class="why">cap ${r.cap}%</div></td>`
    + `<td class="n r"><span class="ocu">${r.counter.ocu}</span> / <span class="usn">${r.counter.usn}</span></td>`
    + `<td class="n r"><span class="ocu">${r.wins.ocu}</span> / <span class="usn">${r.wins.usn}</span></td>`
    + `<td class="n">${r.updated ? utc(r.updated) : '-'}</td></tr>`).join('')
    + (list.length > 400 ? `<tr><td colspan="8" class="sub">${list.length - 400} more; filter to narrow.</td></tr>` : ''));
}

q('#tick').addEventListener('click', async () => {
  if(!confirm('Run the phase clock now? Judged phases and phase starts are written to the war state.')) return;
  BUSY = true;
  try{
    const r = await fetch(qs('war/tick'), {method:'POST', headers:{'Content-Type':'application/json'}, body:'{}'});
    const j = await r.json();
    say(j.msg || (j.ok ? 'Done' : 'Refused'), !!j.ok);
  }catch(e){ say(e.message, false); }
  BUSY = false; LAST = ''; load();
});
q('#secdet').addEventListener('toggle', () => { if(q('#secdet').open){ LAST = ''; load(); } });
q('#secq').addEventListener('input', renderSectors); q('#secchg').addEventListener('change', renderSectors);
load(); setInterval(() => { if(!document.hidden && !document.activeElement.matches('input')) load(); }, 10000);
</script></body></html>
"""


# Called at run time only; imported last so that import cycles resolve.
from . import warstate  # noqa: E402
