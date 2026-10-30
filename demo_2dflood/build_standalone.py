"""
Build self-contained demo_viewer.html with:
- DEM heatmap + cascade arrows + rainfall context
- Node-subcatchment flood coupling (highlight contributing areas)
- 3D DEM modal on subcatchment click with runoff animation
"""
import json
import math
from pathlib import Path

BASE = Path(__file__).resolve().parent

# Load data
data = json.loads((BASE / "data_compact.json").read_text(encoding="utf-8"))

# Parse INP for rainfall hyetograph
def parse_rainfall(path):
    text = path.read_text(encoding="utf-8")
    sec = None
    rt, rv = [], []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith(";;"): continue
        if line.startswith("[") and line.endswith("]"): sec = line[1:-1].upper().strip(); continue
        if sec == "TIMESERIES":
            p = line.split()
            if len(p) >= 4 and p[0] != ";;Name":
                try:
                    hm = p[2].split(":")
                    rt.append(int(hm[0])*60 + int(hm[1]))
                    rv.append(float(p[3]))
                except: pass
    return {"rain_times": rt, "rain_vals": rv}

rain_data = parse_rainfall(BASE / "demo_valley.inp")

# ---- Build DEM grids ----
nodes = data["topology"]["nodes"]
polys = {s["name"]: s["polygon"] for s in data["topology"]["subcatchments"]}

def bbox_all():
    xs, ys = [], []
    for n in nodes: xs.append(n["x"]); ys.append(n["y"])
    for pts in polys.values():
        for p in pts: xs.append(p[0]); ys.append(p[1])
    return min(xs)-30, max(xs)+30, min(ys)-30, max(ys)+30

xmin, xmax, ymin, ymax = bbox_all()
xc = (xmin+xmax)/2

def dem_z(x, y):
    zc = 38 + 14*((x-xc)/(xmax-xc))**2
    zt = -2*(y-ymin)/(ymax-ymin)
    return round(zc+zt, 1)

def build_dem_grid(res):
    nx = int((xmax-xmin)/res)+1; ny = int((ymax-ymin)/res)+1
    grid = []; zvals = []
    for iy in range(ny):
        row = []
        for ix in range(nx):
            x = xmin+ix*res; y = ymin+iy*res
            z = dem_z(x, y)
            row.append(z); zvals.append(z)
        grid.append(row)
    return {"xmin":xmin,"ymax":ymax,"res":res,"nx":nx,"ny":ny,"grid":grid,
            "zmin":min(zvals),"zmax":max(zvals)}

# Map DEM (8m res)
dem_map = build_dem_grid(8)

# 3D DEM (15m res) with subcatchment assignment via point-in-polygon
dem3d = build_dem_grid(15)

def point_in_polygon(px, py, polygon):
    """Ray casting algorithm"""
    inside = False
    n = len(polygon)
    j = n-1
    for i in range(n):
        xi, yi = polygon[i][0], polygon[i][1]
        xj, yj = polygon[j][0], polygon[j][1]
        if ((yi > py) != (yj > py)) and (px < (xj-xi)*(py-yi)/(yj-yi)+xi):
            inside = not inside
        j = i
    return inside

# Assign DEM cells to subcatchments
sc_names = sorted(polys.keys())
dem_sc_assign = []  # flattened: [sc_index, ...] for each cell
for iy in range(dem3d["ny"]):
    for ix in range(dem3d["nx"]):
        x = dem3d["xmin"]+ix*dem3d["res"]
        y = dem3d["ymax"]-iy*dem3d["res"]  # grid y decreases with row
        best = -1
        for si, sn in enumerate(sc_names):
            if polys[sn] and point_in_polygon(x, y, polys[sn]):
                best = si; break
        dem_sc_assign.append(best)  # -1 if outside all polygons

# ---- Node-to-subcatchment coupling (upstream trace) ----
sc_to_outlet = {}
for s in data["topology"]["subcatchments"]:
    sc_to_outlet[s["name"]] = s["outlet"]

# Build reverse mapping: subcatchment -> list of upstream subcatchments
upstream_of = {s["name"]: [] for s in data["topology"]["subcatchments"]}
for s in data["topology"]["subcatchments"]:
    outlet = s["outlet"]
    if outlet in upstream_of:
        upstream_of[outlet].append(s["name"])

def get_all_upstream(sc_name, visited=None):
    """Recursively find all upstream subcatchments"""
    if visited is None: visited = set()
    if sc_name in visited: return []
    visited.add(sc_name)
    result = []
    for up in upstream_of.get(sc_name, []):
        result.append(up)
        result.extend(get_all_upstream(up, visited))
    return result

# For each node, find all subcatchments that ultimately drain to it
node_upstream = {}
for node in nodes:
    node_name = node["name"]
    contributors = []
    for s in data["topology"]["subcatchments"]:
        if s["outlet"] == node_name:
            contributors.append(s["name"])
            contributors.extend(get_all_upstream(s["name"]))
    node_upstream[node_name] = sorted(set(contributors))

# ---- Cascade arrows ----
cascade_arrows = []
sc_objs = {s["name"]:s for s in data["topology"]["subcatchments"]}
node_coords = {n["name"]:[n["x"],n["y"]] for n in nodes}

for cas in data["topology"]["cascades"]:
    for i in range(len(cas["chain"])-1):
        a, b = cas["chain"][i], cas["chain"][i+1]
        sa, sb = sc_objs.get(a), sc_objs.get(b)
        if sa and sb and sa["polygon"] and sb["polygon"]:
            cx1 = sum(p[0] for p in sa["polygon"])/len(sa["polygon"])
            cy1 = sum(p[1] for p in sa["polygon"])/len(sa["polygon"])
            cx2 = sum(p[0] for p in sb["polygon"])/len(sb["polygon"])
            cy2 = sum(p[1] for p in sb["polygon"])/len(sb["polygon"])
            cascade_arrows.append({"from":[cx1,cy1],"to":[cx2,cy2],"chain":cas["name"],"source":a,"target":b,"kind":"surface"})
    # Terminal: last -> outlet node
    last = cas["chain"][-1]; node = cas["outlet_node"]
    sa = sc_objs.get(last); nc = node_coords.get(node)
    if sa and nc and sa["polygon"]:
        cx = sum(p[0] for p in sa["polygon"])/len(sa["polygon"])
        cy = sum(p[1] for p in sa["polygon"])/len(sa["polygon"])
        cascade_arrows.append({"from":[cx,cy],"to":nc,"chain":cas["name"]+" outlet","source":last,"target":node,"kind":"inlet"})

# ---- Derived coupling metrics for synchronized surface-pipe playback ----
def _max_abs_series(series_by_name):
    vals = [abs(v) for vals in series_by_name.values() for v in vals]
    return max(vals) if vals else 0.0

series = data.setdefault("series", {})
subcatch_runoff = series.get("subcatch_runoff", {})
node_depth = series.get("node_depth", {})
node_overflow = series.get("node_overflow", {})
link_flow = series.get("link_flow", {})

max_runoff = _max_abs_series(subcatch_runoff)
max_link_flow = _max_abs_series(link_flow)
max_overflow = _max_abs_series(node_overflow)

if link_flow and max_link_flow > 0:
    series["link_flow_ratio"] = {
        name: [round(min(1.0, abs(v) / max_link_flow), 5) for v in vals]
        for name, vals in link_flow.items()
    }
else:
    series["link_flow_ratio"] = {name: [0.0 for v in vals] for name, vals in link_flow.items()}

pond_depth = {}
for node in nodes:
    vals = node_depth.get(node["name"], [])
    rim = node.get("maxDepth", 0) or 0
    pond_depth[node["name"]] = [round(max(0.0, v - rim), 5) for v in vals]
series["pond_depth"] = pond_depth

node_surface_inflow = {}
frame_count = len(data.get("time", []))
for node_name, contributors in node_upstream.items():
    node_surface_inflow[node_name] = []
    for i in range(frame_count):
        total = 0.0
        for sc_name in contributors:
            vals = subcatch_runoff.get(sc_name)
            if vals and i < len(vals):
                total += vals[i]
        node_surface_inflow[node_name].append(round(total, 6))
series["node_surface_inflow"] = node_surface_inflow

def _frame_sum(series_by_name, i):
    total = 0.0
    for vals in series_by_name.values():
        if vals and i < len(vals):
            total += abs(vals[i])
    return total

def _frame_max(series_by_name, i):
    best = 0.0
    for vals in series_by_name.values():
        if vals and i < len(vals):
            best = max(best, abs(vals[i]))
    return best

def _idx_max(vals):
    if not vals:
        return 0
    return max(range(len(vals)), key=lambda i: vals[i])

time_vals = data.get("time", [])
rain_times = rain_data.get("rain_times", [])
rain_vals = rain_data.get("rain_vals", [])
rain_peak_min = rain_times[_idx_max(rain_vals)] if rain_times and rain_vals else 0
rain_peak_idx = min(range(frame_count), key=lambda i: abs(time_vals[i] - rain_peak_min)) if frame_count and time_vals else 0
total_runoff_series = [_frame_sum(subcatch_runoff, i) for i in range(frame_count)]
surface_inflow_series = [_frame_sum(node_surface_inflow, i) for i in range(frame_count)]
pipe_loading_series = [_frame_max(series.get("link_flow_ratio", {}), i) for i in range(frame_count)]
overflow_series = [_frame_sum(node_overflow, i) for i in range(frame_count)]

data.setdefault("metadata", {})["visual_metrics"] = {
    "max_runoff": round(max_runoff, 6),
    "max_link_flow": round(max_link_flow, 6),
    "max_overflow": round(max_overflow, 6),
    "max_node_surface_inflow": round(_max_abs_series(node_surface_inflow), 6),
    "max_total_runoff": round(max(total_runoff_series) if total_runoff_series else 0.0, 6),
    "max_surface_inflow_total": round(max(surface_inflow_series) if surface_inflow_series else 0.0, 6),
    "max_pipe_loading": round(max(pipe_loading_series) if pipe_loading_series else 0.0, 6),
    "max_overflow_total": round(max(overflow_series) if overflow_series else 0.0, 6),
    "key_frames": {
        "rain_peak": rain_peak_idx,
        "surface_peak": _idx_max(total_runoff_series),
        "inlet_peak": _idx_max(surface_inflow_series),
        "pipe_peak": _idx_max(pipe_loading_series),
        "overflow_peak": _idx_max(overflow_series),
    },
}

# ---- Serialize for embedding ----
data_json = json.dumps(data, separators=(',',':'), ensure_ascii=True)
rain_json = json.dumps(rain_data, separators=(',',':'))
dem_map_json = json.dumps(dem_map, separators=(',',':'))
dem3d_json = json.dumps(dem3d, separators=(',',':'))
dem_sc_json = json.dumps(dem_sc_assign, separators=(',',':'))
arrows_json = json.dumps(cascade_arrows, separators=(',',':'))
nu_json = json.dumps(node_upstream, separators=(',',':'))

html = f'''<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>SWMM 2D Flood Demo - Valley Cascading</title>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css" />
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js">
</script>
<script src="https://cdn.plot.ly/plotly-2.35.2.min.js">
</script>
<style>
*{{margin:0;padding:0;box-sizing:border-box}}
body{{font-family:'Segoe UI',system-ui,sans-serif;background:#1a1a2e;color:#e0e0e0;overflow:hidden;height:100vh}}
#app{{display:grid;grid-template-rows:auto 1fr auto;height:100vh}}
#header{{background:#16213e;padding:8px 16px;display:flex;align-items:center;gap:24px;border-bottom:2px solid #0f3460;flex-wrap:wrap;z-index:10}}
#header h1{{font-size:18px;color:#e94560;white-space:nowrap}}
#mode-badge{{font-size:10px;color:#8de8ff;border:1px solid rgba(141,232,255,0.35);padding:3px 7px;border-radius:999px;background:rgba(64,192,255,0.08)}}
#stats{{display:flex;gap:18px;font-size:12px;flex-wrap:wrap}}
.stat-item{{text-align:center}}
.stat-val{{font-weight:bold;font-size:15px;color:#f0c040}}
.stat-label{{color:#aaa;font-size:10px}}
#main{{display:grid;grid-template-columns:1fr 450px;gap:0;overflow:hidden;min-height:0}}
#map-panel{{position:relative;background:#111;min-height:0}}
#map{{width:100%;height:100%}}
#charts-panel{{display:flex;flex-direction:column;gap:0;background:#16213e;overflow-y:auto;padding:4px}}
.chart-card{{background:#1a1a2e;border-radius:6px;padding:6px;margin:1px 0}}
.chart-card h3{{font-size:11px;color:#e94560;margin-bottom:2px}}
.chart-card .plot-container{{height:180px}}
#footer{{background:#16213e;padding:6px 16px;display:flex;align-items:center;gap:12px;border-top:2px solid #0f3460;z-index:10}}
#footer button{{background:#e94560;color:#fff;border:none;padding:6px 16px;border-radius:4px;cursor:pointer;font-size:12px;font-weight:bold}}
#footer button:hover{{background:#ff6b81}}
#footer button.paused{{background:#0f3460}}
#time-slider{{flex:1;height:20px;accent-color:#e94560}}
#time-label{{font-size:13px;font-weight:bold;min-width:85px;text-align:center;color:#f0c040}}
#speed-select{{background:#0f3460;color:#fff;border:1px solid #333;padding:4px 8px;border-radius:3px;font-size:11px}}
.leaflet-container{{background:#1a1a2e!important}}
.legend-card{{position:absolute;bottom:12px;left:12px;background:rgba(22,33,62,0.92);padding:8px 14px;border-radius:6px;font-size:10px;z-index:1000;pointer-events:none;border:1px solid rgba(255,255,255,0.1)}}
.legend-row{{display:flex;align-items:center;gap:8px;margin:3px 0}}
.legend-swatch{{width:15px;height:15px;border-radius:3px;border:1px solid rgba(255,255,255,0.25)}}
#story-card{{position:absolute;top:12px;left:12px;width:310px;background:linear-gradient(145deg,rgba(8,19,39,0.95),rgba(22,33,62,0.92));border:1px solid rgba(141,232,255,0.22);box-shadow:0 12px 30px rgba(0,0,0,0.28);border-radius:10px;padding:12px;z-index:1000;font-size:11px;pointer-events:auto}}
.story-eyebrow{{color:#8de8ff;text-transform:uppercase;letter-spacing:0.12em;font-size:9px;margin-bottom:5px}}
#stage-name{{font-size:15px;color:#fff;font-weight:700;margin-bottom:4px}}
#stage-desc{{color:#b9c6d8;line-height:1.35;min-height:30px}}
#stage-meter{{height:6px;background:rgba(255,255,255,0.08);border-radius:99px;overflow:hidden;margin:9px 0}}
#stage-meter-fill{{height:100%;width:0;background:linear-gradient(90deg,#75e7ff,#ffb347,#ff5a3d);border-radius:99px;transition:width .18s ease}}
#stage-metrics{{display:grid;grid-template-columns:repeat(3,1fr);gap:6px;margin:8px 0}}
.stage-metric{{background:rgba(255,255,255,0.05);border-radius:6px;padding:5px;text-align:center}}
.stage-metric b{{display:block;color:#f0c040;font-size:12px}}
.stage-metric span{{color:#8f9db4;font-size:9px}}
#stage-steps{{display:grid;grid-template-columns:1fr 1fr;gap:5px;margin-top:8px}}
.stage-chip{{border:1px solid rgba(141,232,255,0.22);background:rgba(64,192,255,0.08);color:#d7f7ff;border-radius:6px;padding:5px 6px;font-size:10px;cursor:pointer;text-align:left}}
.stage-chip:hover{{background:rgba(64,192,255,0.18);border-color:rgba(141,232,255,0.48)}}
.stage-chip.active{{background:rgba(255,179,71,0.16);border-color:rgba(255,179,71,0.65);color:#ffe4b2}}
#highlight-info{{position:absolute;top:10px;right:10px;background:rgba(22,33,62,0.94);padding:8px 12px;border-radius:4px;font-size:10px;z-index:1000;display:none;max-width:280px}}
.elev-label{{position:absolute;color:#fff;font-size:9px;font-weight:bold;text-shadow:0 0 4px #000;pointer-events:none;z-index:500}}

/* 3D Modal */
#modal-overlay{{position:fixed;top:0;left:0;width:100%;height:100%;background:rgba(0,0,0,0.75);z-index:2000;display:none;align-items:center;justify-content:center}}
#modal-overlay.active{{display:flex}}
#modal-box{{background:#16213e;border-radius:10px;padding:16px;width:90vw;max-width:900px;height:80vh;position:relative;display:flex;flex-direction:column}}
#modal-close{{position:absolute;top:10px;right:16px;background:none;border:none;color:#fff;font-size:24px;cursor:pointer;z-index:10}}
#modal-close:hover{{color:#e94560}}
#modal-title{{font-size:14px;color:#e94560;margin-bottom:4px;text-align:center}}
#modal-3d{{flex:1;min-height:0}}
#modal-footer{{display:flex;justify-content:center;gap:16px;margin-top:8px;align-items:center}}
#modal-footer button{{background:#e94560;color:#fff;border:none;padding:5px 14px;border-radius:4px;cursor:pointer;font-size:11px}}
#modal-footer button:hover{{background:#ff6b81}}
#modal-time{{color:#f0c040;font-size:13px;min-width:60px;text-align:center}}

/* Ponding flash animation */
@keyframes pondPulse{{0%,100%{{opacity:0.3}}50%{{opacity:0.7}}}}
.pond-flash{{animation:pondPulse 1.2s ease-in-out infinite}}
</style>
</head>
<body>
<div id="app">
<div id="header">
  <h1>SWMM 2D Flood Demo &mdash; Valley Cascading</h1>
  <span id="mode-badge">surface-pipe coupled playback</span>
  <div id="stats">
    <div class="stat-item"><div class="stat-val" id="st-time">0:00</div><div class="stat-label">Sim Time</div></div>
    <div class="stat-item"><div class="stat-val" id="st-rain">0</div><div class="stat-label">Rain (mm/hr)</div></div>
    <div class="stat-item"><div class="stat-val" id="st-runoff">-</div><div class="stat-label">Total Runoff (L/s)</div></div>
    <div class="stat-item"><div class="stat-val" id="st-flooded">-</div><div class="stat-label">Ponded Nodes</div></div>
    <div class="stat-item"><div class="stat-val" id="st-surcharge">-</div><div class="stat-label">Loaded Pipes</div></div>
    <div class="stat-item"><div class="stat-val" id="st-stage">-</div><div class="stat-label">Coupling Stage</div></div>
    <div class="stat-item"><div class="stat-val" id="st-errors">-0.05% / -0.24%</div><div class="stat-label">Continuity (Runoff / Flow)</div></div>
  </div>
</div>
<div id="main">
  <div id="map-panel">
    <div id="map"></div>
    <div id="highlight-info"></div>
    <div id="story-card">
      <div class="story-eyebrow">Coupling timeline</div>
      <div id="stage-name">Waiting for rainfall</div>
      <div id="stage-desc">Rainfall, overland runoff, inlet exchange, pipe loading, and overflow are synchronized on the same time slider.</div>
      <div id="stage-meter"><div id="stage-meter-fill"></div></div>
      <div id="stage-metrics">
        <div class="stage-metric"><b id="stage-rain">0</b><span>rain</span></div>
        <div class="stage-metric"><b id="stage-inlet">0</b><span>inlet L/s</span></div>
        <div class="stage-metric"><b id="stage-pipe">0%</b><span>pipe load</span></div>
      </div>
      <div id="stage-steps">
        <button class="stage-chip" data-jump="rain_peak">Rain peak</button>
        <button class="stage-chip" data-jump="surface_peak">Surface peak</button>
        <button class="stage-chip" data-jump="pipe_peak">Pipe peak</button>
        <button class="stage-chip" data-jump="overflow_peak">Overflow peak</button>
      </div>
    </div>
    <div class="legend-card">
      <div style="font-weight:bold;margin-bottom:5px;color:#fff">Land Use &amp; Network</div>
      <div class="legend-row"><div class="legend-swatch" style="background:#707070"></div> ROAD (90-95% imp)</div>
      <div class="legend-row"><div class="legend-swatch" style="background:#e07040"></div> BUILDING (80-85% imp)</div>
      <div class="legend-row"><div class="legend-swatch" style="background:#e0b040"></div> MIXED (35-60% imp)</div>
      <div class="legend-row"><div class="legend-swatch" style="background:#5b9b4c"></div> GREEN (15-25% imp)</div>
      <div style="border-top:1px solid rgba(255,255,255,0.15);margin:4px 0"></div>
      <div class="legend-row"><div style="width:16px;height:3px;background:#75e7ff;border-radius:2px"></div> Surface flow particles</div>
      <div class="legend-row"><div class="legend-swatch" style="background:#8de8ff;border-radius:50%"></div> Inlet exchange pulse</div>
      <div class="legend-row"><div style="width:16px;height:3px;background:#ffb347;border-radius:2px"></div> Pipe flow particles</div>
      <div class="legend-row"><div class="legend-swatch" style="background:#ff5a3d;border-radius:50%"></div> Overflow / backwater</div>
      <div class="legend-row"><div class="legend-swatch" style="background:#3a7eb0;border-radius:50%"></div> Junction</div>
      <div class="legend-row"><div class="legend-swatch" style="background:#fff;border-radius:50%"></div> Outfall</div>
      <div class="legend-row"><div class="legend-swatch" style="background:#40c0ff;border-radius:50%"></div> Ponding node</div>
      <div class="legend-row"><div style="width:16px;height:3px;background:#f0c040;border-radius:2px"></div> Cascade flow path</div>
    </div>
  </div>
  <div id="charts-panel">
    <div class="chart-card"><h3>Cascade Runoff &mdash; Chain W-B (BUILDING &rarr; GREEN &rarr; ROAD)</h3><div id="chart-cascade" class="plot-container"></div></div>
    <div class="chart-card"><h3>Node Water Depth &amp; Surface Ponding (J_W rim=2.8m)</h3><div id="chart-depth" class="plot-container"></div></div>
    <div class="chart-card"><h3>Coupled Surface Inflow, Pipe Flow &amp; Overflow at J_W</h3><div id="chart-coupling" class="plot-container"></div></div>
    <div class="chart-card"><h3>Pipe Flow Loading (visual ratio)</h3><div id="chart-capacity" class="plot-container"></div></div>
    <div class="chart-card"><h3>Infiltration Rate by Land Use</h3><div id="chart-infil" class="plot-container"></div></div>
  </div>
</div>
<div id="footer">
  <button id="btn-play">&#9654; Play Animation</button>
  <span id="time-label">00:00</span>
  <input type="range" id="time-slider" min="0" max="71" value="0" step="1">
  <span style="font-size:10px;color:#aaa">Speed:</span>
  <select id="speed-select">
    <option value="0.5">0.5x</option>
    <option value="1" selected>1x</option>
    <option value="2">2x</option>
    <option value="4">4x</option>
    <option value="8">8x</option>
  </select>
  <span style="font-size:9px;color:#888;margin-left:auto">Click subcatchments &rarr; 3D DEM view | Drag to pan | Scroll to zoom</span>
</div>
</div>

<!-- 3D DEM Modal -->
<div id="modal-overlay">
  <div id="modal-box">
    <button id="modal-close">&times;</button>
    <div id="modal-title">3D Terrain &amp; Runoff View</div>
    <div id="modal-3d"></div>
    <div id="modal-footer">
      <button id="modal-play">&#9654; Animate Runoff</button>
      <span id="modal-time">00:00</span>
      <input type="range" id="modal-slider" min="0" max="71" value="0" step="1" style="flex:1;accent-color:#e94560">
    </div>
  </div>
</div>

<script id="data-json" type="application/json">{data_json}</script>
<script>
(function() {{
"use strict";

var DATA = JSON.parse(document.getElementById("data-json").textContent);
var RAIN = {rain_json};
var DEM = {dem_map_json};
var DEM3D = {dem3d_json};
var DEM_SC = {dem_sc_json};
var CASCADE_ARROWS = {arrows_json};
var NODE_UPSTREAM = {nu_json};

var currentFrame = 0, playing = false, playTimer = null, playSpeed = 1;
var map = null;
var scLayers = [], nodeLayers = [], linkLayers = [], arrowLayers = [], pondCircles = [];
var surfaceParticleLayers = [], pipeParticleLayers = [], exchangeLayers = [];
var upstreamHighlighted = {{}}; // {{nodeName: [scNames]}} currently highlighted

var LU_COLORS = {{BUILDING:"#e07040",MIXED:"#e0b040",GREEN:"#5b9b4c",ROAD:"#707070"}};
var TERRAIN_CMAP = ["#2d5a27","#3d7a33","#5b9b4c","#8bb860","#c4c46c","#e0c070","#d4a050","#c08040","#a06030","#8b5a2b"];

// 3D Modal state
var modalOpen = false, modalScName = null, modalPlaying = false, modalTimer = null;
var modalFrame = 0;

// ============ MAP INIT ============
function initMap() {{
  var xs = DATA.topology.nodes.map(function(n){{return n.x}});
  var ys = DATA.topology.nodes.map(function(n){{return n.y}});
  var m = 100;
  var xMin = Math.min.apply(null,xs)-m, xMax = Math.max.apply(null,xs)+m;
  var yMin = Math.min.apply(null,ys)-m, yMax = Math.max.apply(null,ys)+m;

  map = L.map("map", {{
    crs: L.CRS.Simple, minZoom: -2, maxZoom: 5, zoomControl: true, attributionControl: false,
    renderer: L.canvas()
  }});
  map.fitBounds([[yMin,xMin],[yMax,xMax]]);
  map.setMaxBounds([[yMin-60,xMin-60],[yMax+60,xMax+60]]);

  map.createPane("dem-pane"); map.getPane("dem-pane").style.zIndex = 200;
  map.createPane("pond-pane"); map.getPane("pond-pane").style.zIndex = 390;
  map.createPane("sc-pane"); map.getPane("sc-pane").style.zIndex = 400;
  map.createPane("link-pane"); map.getPane("link-pane").style.zIndex = 410;
  map.createPane("arrow-pane"); map.getPane("arrow-pane").style.zIndex = 415;
  map.createPane("particle-pane"); map.getPane("particle-pane").style.zIndex = 416;
  map.createPane("exchange-pane"); map.getPane("exchange-pane").style.zIndex = 430;
  map.createPane("node-pane"); map.getPane("node-pane").style.zIndex = 420;

  buildDEMOverlay();
  drawSubcatchments();
  drawCascadeArrows();
  drawLinks();
  drawNodes();
  drawElevationLabels();
}}

// ---- DEM heatmap ----
function buildDEMOverlay() {{
  var bounds = [[DEM.ymax-DEM.ny*DEM.res,DEM.xmin],[DEM.ymax,DEM.xmin+DEM.nx*DEM.res]];
  var canvas = document.createElement("canvas");
  canvas.width = DEM.nx; canvas.height = DEM.ny;
  var ctx = canvas.getContext("2d");
  var imgData = ctx.createImageData(DEM.nx, DEM.ny);

  function terrainColor(z) {{
    var t = Math.max(0,Math.min(1,(z-DEM.zmin)/(DEM.zmax-DEM.zmin)));
    var idx = t*(TERRAIN_CMAP.length-1);
    var lo=Math.floor(idx), hi=Math.ceil(idx);
    if(lo===hi) hi=Math.min(lo+1,TERRAIN_CMAP.length-1);
    var f=idx-lo;
    var cLo=TERRAIN_CMAP[lo], cHi=TERRAIN_CMAP[hi];
    var r=parseInt(cLo.substr(1,2),16)*(1-f)+parseInt(cHi.substr(1,2),16)*f;
    var g=parseInt(cLo.substr(3,2),16)*(1-f)+parseInt(cHi.substr(3,2),16)*f;
    var b=parseInt(cLo.substr(5,2),16)*(1-f)+parseInt(cHi.substr(5,2),16)*f;
    return [r,g,b];
  }}

  for(var iy=0;iy<DEM.ny;iy++) {{
    for(var ix=0;ix<DEM.nx;ix++) {{
      var rgb=terrainColor(DEM.grid[iy][ix]);
      var idx=(iy*DEM.nx+ix)*4;
      imgData.data[idx]=rgb[0]; imgData.data[idx+1]=rgb[1];
      imgData.data[idx+2]=rgb[2]; imgData.data[idx+3]=120;
    }}
  }}
  ctx.putImageData(imgData,0,0);
  L.imageOverlay(canvas.toDataURL(),bounds,{{opacity:0.5,pane:"dem-pane"}}).addTo(map);
}}

// ---- Subcatchments ----
function drawSubcatchments() {{
  DATA.topology.subcatchments.forEach(function(sc) {{
    if(!sc.polygon||!sc.polygon.length) return;
    var latlngs=sc.polygon.map(function(p){{return[p[1],p[0]]}});
    var lu=sc.landuse, color=LU_COLORS[lu]||"#888";
    var poly=L.polygon(latlngs,{{
      color:color,weight:1.5,fillColor:color,fillOpacity:0.22,pane:"sc-pane",
    }}).addTo(map);
    poly.bindTooltip("<b>"+sc.name+"</b> ("+lu+")<br>Area:"+sc.area_ha+"ha Imp:"+sc.imp_pct+"%<br>Slope:"+sc.slope_pct+"% Outlet:"+sc.outlet+"<br><i style='color:#f0c040'>Click for 3D DEM view</i>",{{sticky:true}});
    poly.on("click",function(){{open3DModal(sc.name)}});
    scLayers.push({{layer:poly,sc:sc,origColor:color,origOpacity:0.22}});
  }});
}}

// ---- Cascade arrows ----
function drawCascadeArrows() {{
  CASCADE_ARROWS.forEach(function(arr) {{
    var f=[arr.from[1],arr.from[0]], t=[arr.to[1],arr.to[0]];
    var line=L.polyline([f,t],{{color:"#f0c040",weight:1.5,opacity:0.5,dashArray:"6 4",pane:"arrow-pane"}}).addTo(map);
    var mx=(arr.from[0]+arr.to[0])/2, my=(arr.from[1]+arr.to[1])/2;
    var mk=L.circleMarker([my,mx],{{radius:2.5,color:"#f0c040",fillColor:"#f0c040",fillOpacity:0.6,weight:0,pane:"arrow-pane"}}).addTo(map);
    arrowLayers.push({{line:line,marker:mk,arr:arr}});
  }});
}}

// ---- Links ----
function drawLinks() {{
  var nodeMap={{}};
  DATA.topology.nodes.forEach(function(n){{nodeMap[n.name]=n}});
  DATA.topology.links.forEach(function(link) {{
    var fromNode=nodeMap[link.from], toNode=nodeMap[link.to];
    if(!fromNode||!toNode) return;
    var f=[fromNode.y,fromNode.x], t=[toNode.y,toNode.x];
    link._fromXY=[fromNode.x,fromNode.y];
    link._toXY=[toNode.x,toNode.y];
    var line=L.polyline([f,t],{{color:"#3a7eb0",weight:3,opacity:0.85,pane:"link-pane"}}).addTo(map);
    line.bindTooltip("<b>"+link.name+"</b><br>"+link.from+" &rarr; "+link.to+"<br>L="+link.length+"m D="+link.diameter.toFixed(2)+"m<br><span style='color:#ffb347'>Animated by simulated link_flow</span>",{{sticky:true}});
    linkLayers.push({{layer:line,link:link}});
  }});
}}

// ---- Nodes ----
function drawNodes() {{
  DATA.topology.nodes.forEach(function(node) {{
    var isOutfall=node.type==="outfall";
    var marker=L.circleMarker([node.y,node.x],{{
      radius:isOutfall?9:6.5,fillColor:isOutfall?"#fff":"#3a7eb0",
      color:isOutfall?"#ccc":"#fff",weight:2,fillOpacity:0.9,pane:"node-pane",
    }}).addTo(map);
    var tip="<b>"+node.name+"</b> ("+node.type+")<br>Elev:"+node.elev+"m MaxDepth:"+node.maxDepth+"m";
    var upScs=NODE_UPSTREAM[node.name]||[];
    if(upScs.length) tip+="<br><span style='color:#f0c040'>Drains "+upScs.length+" subcatchments</span>";
    marker.bindTooltip(tip,{{sticky:true}});
    nodeLayers.push({{layer:marker,node:node}});
  }});
}}

// ---- Elevation labels ----
function drawElevationLabels() {{
  var labels=[
    {{x:20,y:280,text:"West Ridge ~52m"}},{{x:460,y:270,text:"East Ridge ~50m"}},
    {{x:240,y:10,text:"Valley Bottom ~38m"}},{{x:240,y:200,text:"Valley ~42m"}},
  ];
  var mapEl=map.getContainer();
  labels.forEach(function(l) {{
    var div=document.createElement("div"); div.className="elev-label";
    div.textContent=l.text;
    var point=map.latLngToContainerPoint([l.y,l.x]);
    div.style.left=point.x+"px"; div.style.top=point.y+"px"; div.style.transform="translate(-50%,-50%)";
    mapEl.appendChild(div);
    map.on("move",function(){{var p=map.latLngToContainerPoint([l.y,l.x]);div.style.left=p.x+"px";div.style.top=p.y+"px";}});
  }});
}}

// ============ FLOW ANIMATION HELPERS ============
function clearLayerList(list) {{
  list.forEach(function(layer){{map.removeLayer(layer)}});
  list.length = 0;
}}

function interpPoint(from, to, f) {{
  return [from[0] + (to[0]-from[0])*f, from[1] + (to[1]-from[1])*f];
}}

function metric(name, fallback) {{
  var m = DATA.metadata && DATA.metadata.visual_metrics ? DATA.metadata.visual_metrics : {{}};
  return Math.max(fallback || 0.000001, m[name] || fallback || 0.000001);
}}

var KEY_FRAMES = (DATA.metadata && DATA.metadata.visual_metrics && DATA.metadata.visual_metrics.key_frames) ? DATA.metadata.visual_metrics.key_frames : {{}};

function rainAtFrame(idx) {{
  var simMin = DATA.time[idx] || 0;
  var val = 0;
  for(var i=0;i<RAIN.rain_times.length;i++) {{
    if(RAIN.rain_times[i] <= simMin + 0.001) val = RAIN.rain_vals[i];
    else break;
  }}
  return val;
}}

function sumAt(seriesByName, idx) {{
  var total = 0;
  Object.keys(seriesByName || {{}}).forEach(function(name) {{
    var vals = seriesByName[name] || [];
    total += Math.abs(vals[idx] || 0);
  }});
  return total;
}}

function maxAt(seriesByName, idx) {{
  var best = 0;
  Object.keys(seriesByName || {{}}).forEach(function(name) {{
    var vals = seriesByName[name] || [];
    best = Math.max(best, Math.abs(vals[idx] || 0));
  }});
  return best;
}}

function couplingState(idx) {{
  var rainRatio = Math.min(1, rainAtFrame(idx) / 110);
  var runoffRatio = Math.min(1, sumAt(DATA.series.subcatch_runoff, idx) / metric("max_total_runoff", 0.001));
  var inlet = sumAt(DATA.series.node_surface_inflow, idx);
  var inletRatio = Math.min(1, inlet / metric("max_surface_inflow_total", 0.001));
  var pipeRatio = Math.min(1, maxAt(DATA.series.link_flow_ratio, idx) / metric("max_pipe_loading", 0.001));
  var overflowRatio = Math.min(1, sumAt(DATA.series.node_overflow, idx) / metric("max_overflow_total", 0.001));
  var pondRatio = Math.min(1, maxAt(DATA.series.pond_depth, idx) / 1.3);
  var state = {{
    rain: rainAtFrame(idx), inlet: inlet, pipeRatio: pipeRatio,
    score: Math.max(rainRatio, runoffRatio, inletRatio, pipeRatio, overflowRatio, pondRatio),
    name: "Dry / waiting", shortName: "Dry",
    desc: "The catchment is mostly dry. Watch for cyan surface particles as rainfall begins to generate runoff."
  }};
  if(overflowRatio > 0.03 || pondRatio > 0.08) {{
    state.name = "Overflow and surface ponding";
    state.shortName = "Overflow";
    state.desc = "Pipe capacity is no longer enough locally: red pulses show water returning to the surface while ponding expands around the node.";
  }} else if(pipeRatio > 0.70) {{
    state.name = "Pipe network surcharge";
    state.shortName = "Surcharge";
    state.desc = "The downstream pipes are heavily loaded. Amber particles speed through conduits while node depth approaches the rim.";
  }} else if(inletRatio > 0.18 || pipeRatio > 0.25) {{
    state.name = "Surface runoff entering pipes";
    state.shortName = "Coupled";
    state.desc = "Runoff from subcatchments is being captured by junctions; blue inlet pulses and amber pipe particles move together.";
  }} else if(runoffRatio > 0.08) {{
    state.name = "Overland runoff forming";
    state.shortName = "Runoff";
    state.desc = "Rainfall has exceeded losses on impervious areas. Cyan particles trace the surface flow paths toward inlets.";
  }} else if(rainRatio > 0.03) {{
    state.name = "Rainfall builds storage";
    state.shortName = "Rain";
    state.desc = "Rainfall is active, with infiltration and surface storage filling before strong runoff reaches the drainage network.";
  }} else if(pipeRatio > 0.04 || runoffRatio > 0.02) {{
    state.name = "Drainage recession";
    state.shortName = "Recession";
    state.desc = "Rain has eased, but water is still draining through the pipe network and residual surface flow paths.";
  }}
  return state;
}}

function updateStoryPanel(idx) {{
  var state = couplingState(idx);
  document.getElementById("stage-name").textContent = state.name;
  document.getElementById("stage-desc").textContent = state.desc;
  document.getElementById("stage-meter-fill").style.width = Math.round(state.score * 100) + "%";
  document.getElementById("stage-rain").textContent = state.rain.toFixed(0);
  document.getElementById("stage-inlet").textContent = (state.inlet * 1000).toFixed(0);
  document.getElementById("stage-pipe").textContent = Math.round(state.pipeRatio * 100) + "%";
  document.getElementById("st-rain").textContent = state.rain.toFixed(0);
  document.getElementById("st-stage").textContent = state.shortName;
  document.querySelectorAll(".stage-chip").forEach(function(btn) {{
    var key = btn.getAttribute("data-jump");
    var frame = KEY_FRAMES[key];
    btn.classList.toggle("active", typeof frame === "number" && Math.abs(frame - idx) <= 1);
  }});
}}

function updateSurfaceParticles(idx, maxRunoff) {{
  clearLayerList(surfaceParticleLayers);
  CASCADE_ARROWS.forEach(function(arr, i) {{
    var vals = DATA.series.subcatch_runoff[arr.source] || [];
    var val = Math.max(0, vals[idx] || 0);
    var ratio = Math.min(1, val / Math.max(0.000001, maxRunoff * 0.65));
    if(arrowLayers[i]) {{
      arrowLayers[i].line.setStyle({{
        color: arr.kind === "inlet" ? "#9ef7ff" : "#75e7ff",
        weight: 1.2 + ratio * 4.5,
        opacity: 0.22 + ratio * 0.72
      }});
      arrowLayers[i].marker.setStyle({{radius: 2 + ratio * 4, fillOpacity: 0.35 + ratio * 0.55}});
    }}
    if(ratio < 0.035) return;
    var count = Math.max(1, Math.ceil(ratio * 5));
    for(var p=0;p<count;p++) {{
      var phase = (idx * (0.11 + ratio * 0.16) + p / count) % 1;
      var xy = interpPoint(arr.from, arr.to, phase);
      var marker = L.circleMarker([xy[1], xy[0]], {{
        radius: 2.5 + ratio * 3.5,
        color: "#d8fbff", fillColor: arr.kind === "inlet" ? "#9ef7ff" : "#75e7ff",
        weight: 1, fillOpacity: 0.72, opacity: 0.9, pane: "particle-pane"
      }}).addTo(map);
      surfaceParticleLayers.push(marker);
    }}
  }});
}}

function updatePipeParticles(idx) {{
  clearLayerList(pipeParticleLayers);
  var linkFlow = DATA.series.link_flow || {{}};
  var linkRatio = DATA.series.link_flow_ratio || {{}};
  linkLayers.forEach(function(item) {{
    var vals = linkFlow[item.link.name] || [];
    var flow = vals[idx] || 0;
    var ratioVals = linkRatio[item.link.name] || [];
    var ratio = Math.min(1, ratioVals[idx] || 0);
    var hot = Math.round(80 + ratio * 175);
    item.layer.setStyle({{
      color: ratio > 0.78 ? "#ff5a3d" : ratio > 0.35 ? "#ffb347" : "#3aa7d8",
      weight: 2.4 + ratio * 5.6,
      opacity: 0.55 + ratio * 0.42
    }});
    if(ratio < 0.025) return;
    var from = [item.link._fromXY[0], item.link._fromXY[1]];
    var to = [item.link._toXY[0], item.link._toXY[1]];
    if(flow < 0) {{ var tmp = from; from = to; to = tmp; }}
    var count = Math.max(1, Math.ceil(ratio * 7));
    for(var p=0;p<count;p++) {{
      var phase = (idx * (0.08 + ratio * 0.18) + p / count) % 1;
      var xy = interpPoint(from, to, phase);
      var marker = L.circleMarker([xy[1], xy[0]], {{
        radius: 2.4 + ratio * 3.1,
        color: "#ffe7b5", fillColor: ratio > 0.75 ? "#ff5a3d" : "#ffb347",
        weight: 1, fillOpacity: 0.78, opacity: 0.95, pane: "particle-pane"
      }}).addTo(map);
      pipeParticleLayers.push(marker);
    }}
  }});
}}

function updateNodeExchange(idx) {{
  clearLayerList(exchangeLayers);
  var inflowSeries = DATA.series.node_surface_inflow || {{}};
  var overflowSeries = DATA.series.node_overflow || {{}};
  var maxInflow = metric("max_node_surface_inflow", 0.001);
  var maxOverflow = metric("max_overflow", 0.001);
  nodeLayers.forEach(function(item) {{
    if(item.node.type === "outfall") return;
    var inflow = (inflowSeries[item.node.name] || [])[idx] || 0;
    var overflow = (overflowSeries[item.node.name] || [])[idx] || 0;
    var inRatio = Math.min(1, inflow / maxInflow);
    var outRatio = Math.min(1, overflow / maxOverflow);
    if(inRatio > 0.04) {{
      var intake = L.circle([item.node.y, item.node.x], {{
        radius: 6 + inRatio * 20, color: "#8de8ff", fillColor: "#8de8ff",
        fillOpacity: 0.06 + inRatio * 0.10, opacity: 0.35 + inRatio * 0.35,
        weight: 1.5, pane: "exchange-pane"
      }}).addTo(map);
      intake.bindTooltip("<b>"+item.node.name+" surface inflow</b><br>"+(inflow*1000).toFixed(0)+" L/s entering drainage node");
      exchangeLayers.push(intake);
    }}
    if(outRatio > 0.02) {{
      var spill = L.circle([item.node.y, item.node.x], {{
        radius: 14 + outRatio * 42, color: "#ff5a3d", fillColor: "#ff5a3d",
        fillOpacity: 0.12 + outRatio * 0.16, opacity: 0.75, weight: 2.2,
        className: "pond-flash", pane: "exchange-pane"
      }}).addTo(map);
      spill.bindTooltip("<b>"+item.node.name+" overflow / backwater</b><br>"+(overflow*1000).toFixed(0)+" L/s returning to surface");
      exchangeLayers.push(spill);
    }}
  }});
}}

// ============ NODE-SUBCATCHMENT FLOOD COUPLING ============
function updateFloodCoupling(idx) {{
  var nodeDepth = DATA.series.node_depth;

  // Clear previous pond circles
  pondCircles.forEach(function(c){{map.removeLayer(c)}});
  pondCircles=[];

  // Reset upstream highlights
  Object.keys(upstreamHighlighted).forEach(function(nodeName) {{
    upstreamHighlighted[nodeName].forEach(function(scName) {{
      var item = scLayers.find(function(sl){{return sl.sc.name===scName}});
      if(item) {{
        item.layer.setStyle({{fillColor:item.origColor,fillOpacity:item.origOpacity}});
      }}
    }});
  }});
  upstreamHighlighted={{}};

  // Check each node for ponding
  nodeLayers.forEach(function(item) {{
    var vals=nodeDepth[item.node.name], val=vals?vals[idx]:0;
    var pondVals = DATA.series.pond_depth && DATA.series.pond_depth[item.node.name];
    var pondDepth = pondVals ? pondVals[idx] : Math.max(0, val-item.node.maxDepth);
    if(pondDepth>0 && item.node.type!=="outfall") {{
      // Node is ponding - highlight upstream subcatchments
      var upScs=NODE_UPSTREAM[item.node.name]||[];
      upstreamHighlighted[item.node.name]=upScs;
      upScs.forEach(function(scName) {{
        var sl=scLayers.find(function(s){{return s.sc.name===scName}});
        if(sl) {{
          sl.layer.setStyle({{fillColor:"#40c0ff",fillOpacity:0.5}});
        }}
      }});

      // Add ponding circle around node (size proportional to ponded volume)
      var radius=8+pondDepth*15; // scale for visibility
      var circle=L.circle([item.node.y,item.node.x],{{
        radius:radius,color:"#40c0ff",fillColor:"#40c0ff",fillOpacity:0.2,weight:2,
        className:"pond-flash",pane:"pond-pane",
      }}).addTo(map);
      circle.bindTooltip("<b>"+item.node.name+" ponding</b><br>"+pondDepth.toFixed(2)+"m above rim<br>"+upScs.length+" subcatchments contributing");
      pondCircles.push(circle);
    }}
  }});
}}

// ============ MAP FRAME UPDATE ============
function updateMapFrame(idx) {{
  var series=DATA.series;
  var scRunoff=series.subcatch_runoff;
  var allVals=[];
  Object.keys(scRunoff).forEach(function(k){{allVals=allVals.concat(scRunoff[k])}});
  var maxRunoff=Math.max(metric("max_runoff", 0.001), Math.max(0.001,Math.max.apply(null,allVals)));

  // Update subcatchment colors (only non-highlighted ones)
  scLayers.forEach(function(item) {{
    if(upstreamHighlighted[item.sc.outlet]||Object.values(upstreamHighlighted).some(function(arr){{return arr.indexOf(item.sc.name)>=0}})) return; // skip highlighted
    var vals=scRunoff[item.sc.name], val=vals?vals[idx]:0;
    var ratio=Math.min(1,val/(maxRunoff*0.5));
    var r=Math.round(120+135*ratio), g=Math.round(180-130*ratio), b=Math.round(220-180*ratio);
    item.layer.setStyle({{fillColor:"rgb("+r+","+g+","+b+")",fillOpacity:0.15+0.6*ratio}});
  }});

  // Links are animated from actual link_flow, not the coarse capacity flag.
  updatePipeParticles(idx);

  // Nodes
  var nodeDepth=series.node_depth;
  nodeLayers.forEach(function(item) {{
    var vals=nodeDepth[item.node.name], val=vals?vals[idx]:0;
    var ratio=item.node.maxDepth>0?val/item.node.maxDepth:0, ponding=val>item.node.maxDepth;
    var fc=ponding?"#ff2020":ratio>0.8?"#ff9920":ratio>0.4?"#40c0ff":"#3a7eb0";
    item.layer.setStyle({{fillColor:fc,radius:(item.node.type==="outfall"?9:6.5)+(ponding?6:0)+ratio*3,fillOpacity:0.75+0.25*(ponding?1:0)}});
  }});

  // Surface/pipe exchange coupling
  updateFloodCoupling(idx);
  updateSurfaceParticles(idx, maxRunoff);
  updateNodeExchange(idx);

  var t=DATA.time[idx],hrs=Math.floor(t/60),mm=Math.floor(t%60),ts=hrs+":"+String(mm).padStart(2,"0");
  document.getElementById("st-time").textContent=ts;
  document.getElementById("time-label").textContent=ts;
  updateStoryPanel(idx);

  var totalRunoff=0;
  Object.keys(scRunoff).forEach(function(k){{totalRunoff+=scRunoff[k][idx]||0}});
  document.getElementById("st-runoff").textContent=(totalRunoff*1000).toFixed(0);

  var ponded=0;
  nodeLayers.forEach(function(item){{var v=nodeDepth[item.node.name]?nodeDepth[item.node.name][idx]:0;if(v>item.node.maxDepth)ponded++;}});
  document.getElementById("st-flooded").textContent=ponded+"/"+nodeLayers.length;

  var full=0;
  var flowRatio=series.link_flow_ratio || {{}};
  linkLayers.forEach(function(item){{var v=flowRatio[item.link.name]?flowRatio[item.link.name][idx]:0;if(v>=0.75)full++;}});
  document.getElementById("st-surcharge").textContent=full+"/"+linkLayers.length;
}}

// ============ 3D DEM MODAL ============
function open3DModal(scName) {{
  modalScName = scName;
  modalFrame = currentFrame;
  modalOpen = true;
  document.getElementById("modal-overlay").classList.add("active");
  document.getElementById("modal-title").textContent = "3D Terrain + Runoff: " + scName;
  document.getElementById("modal-slider").value = currentFrame;
  render3DSurface(scName, currentFrame);
}}

function closeModal() {{
  modalOpen = false;
  if(modalPlaying) toggleModalPlay();
  document.getElementById("modal-overlay").classList.remove("active");
  Plotly.purge("modal-3d");
}}

function setModalFrame(val) {{
  modalFrame = parseInt(val);
  document.getElementById("modal-slider").value = modalFrame;
  var t = DATA.time[modalFrame];
  var hrs=Math.floor(t/60),mm=Math.floor(t%60);
  document.getElementById("modal-time").textContent = hrs+":"+String(mm).padStart(2,"0");
  if(modalScName) render3DSurface(modalScName, modalFrame);
}}

function toggleModalPlay() {{
  modalPlaying = !modalPlaying;
  var btn = document.getElementById("modal-play");
  if(modalPlaying) {{
    btn.innerHTML = "&#9208; Pause";
    if(modalFrame >= DATA.time.length-1) setModalFrame(0);
    modalPlayTick();
  }} else {{
    btn.innerHTML = "&#9654; Animate Runoff";
    if(modalTimer) clearTimeout(modalTimer);
  }}
}}

function modalPlayTick() {{
  if(!modalPlaying || !modalOpen) return;
  if(modalFrame >= DATA.time.length-1) {{
    setModalFrame(0);
    modalPlaying = false;
    document.getElementById("modal-play").innerHTML = "&#8634; Replay";
    return;
  }}
  setModalFrame(modalFrame+1);
  modalTimer = setTimeout(modalPlayTick, Math.max(80, Math.round(120/playSpeed)));
}}

function render3DSurface(scName, frameIdx) {{
  var sc = DATA.topology.subcatchments.find(function(s){{return s.name===scName}});
  if(!sc||!sc.polygon||!sc.polygon.length) return;

  // ---- Which cascade chain does this SC belong to? ----
  var myCas=null;
  DATA.topology.cascades.forEach(function(c){{if(c.chain.indexOf(scName)>=0) myCas=c;}});
  if(!myCas){{myCas={{chain:[scName],landuses:[sc.landuse]}}}}

  // ---- Zoom to this cascade chain only ----
  var chainSCs=[];
  myCas.chain.forEach(function(name) {{
    var s=DATA.topology.subcatchments.find(function(ss){{return ss.name===name}});
    if(s) chainSCs.push(s);
  }});
  var drainNodes=[];
  if(myCas.outlet_node) {{
    var cursor=myCas.outlet_node, guard=0;
    while(cursor && guard < DATA.topology.links.length + 2) {{
      var dn=DATA.topology.nodes.find(function(n){{return n.name===cursor}});
      if(dn) drainNodes.push(dn);
      var nextLink=DATA.topology.links.find(function(l){{return l.from===cursor}});
      if(!nextLink) break;
      cursor=nextLink.to;
      guard++;
    }}
  }}
  var padXY=55, padZ=3;
  var allXs=[],allYs=[],allZs=[];
  chainSCs.forEach(function(s) {{
    s.polygon.forEach(function(p){{allXs.push(p[0]);allYs.push(p[1]);allZs.push(demZ(p[0],p[1]));}});
  }});
  drainNodes.forEach(function(n){{allXs.push(n.x);allYs.push(n.y);allZs.push(demZ(n.x,n.y));}});
  if(!allXs.length) return;
  var x0=Math.min.apply(null,allXs)-padXY, x1=Math.max.apply(null,allXs)+padXY;
  var y0=Math.min.apply(null,allYs)-padXY, y1=Math.max.apply(null,allYs)+padXY;
  var zLo=Math.min.apply(null,allZs)-padZ, zHi=Math.max.apply(null,allZs)+padZ+6;

  var res=DEM3D.res;
  var ix0=Math.max(0,Math.floor((x0-DEM3D.xmin)/res));
  var ix1=Math.min(DEM3D.nx-1,Math.ceil((x1-DEM3D.xmin)/res));
  var iy0=Math.max(0,Math.floor((DEM3D.ymax-y1)/res));
  var iy1=Math.min(DEM3D.ny-1,Math.ceil((DEM3D.ymax-y0)/res));

  var scRunoff=DATA.series.subcatch_runoff;
  var scAreas={{}};
  DATA.topology.subcatchments.forEach(function(s){{scAreas[s.name]=s.area_ha*10000}});

  // ---- Water depth & slope per subcatchment ----
  var maxWD=0.001, wdBySc={{}}, slopeBySc={{}};
  chainSCs.forEach(function(s) {{
    var d=(scRunoff[s.name][frameIdx]||0)*60/(scAreas[s.name]||5000)*600;
    wdBySc[s.name]=d; if(d>maxWD) maxWD=d;
    slopeBySc[s.name]=s.slope_pct;
  }});

  // ---- Build terrain grid ----
  var surfX=[],surfY=[],surfZ=[],surfW=[]; // surfW: 0=dry..1=wet
  for(var iy=iy0;iy<=iy1;iy++) {{
    var rowX=[],rowY=[],rowZ=[],rowW=[];
    for(var ix=ix0;ix<=ix1;ix++) {{
      var x=DEM3D.xmin+ix*res, y=DEM3D.ymax-iy*res, z=demZ(x,y);
      rowX.push(x); rowY.push(y); rowZ.push(z);
      var scIdx=DEM_SC[iy*DEM3D.nx+ix];
      var wf=0;
      if(scIdx>=0&&scIdx<sc_names.length&&myCas.chain.indexOf(sc_names[scIdx])>=0) {{
        var d=wdBySc[sc_names[scIdx]]||0;
        wf=Math.min(1,d/Math.max(0.01,maxWD*0.6));
      }}
      rowW.push(wf);
    }}
    surfX.push(rowX); surfY.push(rowY); surfZ.push(rowZ); surfW.push(rowW);
  }}

  // ---- Chain boundary polygon (highlight all chain SCs) ----
  var bndTraces=[];
  chainSCs.forEach(function(cs) {{
    var bx=[],by=[],bz=[];
    cs.polygon.forEach(function(p){{bx.push(p[0]);by.push(p[1]);bz.push(demZ(p[0],p[1])+1.2);}});
    bx.push(cs.polygon[0][0]); by.push(cs.polygon[0][1]); bz.push(demZ(cs.polygon[0][0],cs.polygon[0][1])+1.2);
    bndTraces.push({{
      type:"scatter3d",mode:"lines",x:bx,y:by,z:bz,
      line:{{color:cs.name===scName?"#ff3355":"#888888",width:cs.name===scName?5:2}},
      name:cs.name===scName?scName+" (selected)":"",showlegend:cs.name===scName,
      hoverinfo:"skip",
    }});
  }});

  // ---- Slope arrows: small arrows at each SC centroid showing flow direction ----
  var arrowX=[],arrowY=[],arrowZ=[],arrowU=[],arrowV=[],arrowW=[];
  for(var i=0;i<myCas.chain.length-1;i++) {{
    var sUp=DATA.topology.subcatchments.find(function(ss){{return ss.name===myCas.chain[i]}});
    var sDn=DATA.topology.subcatchments.find(function(ss){{return ss.name===myCas.chain[i+1]}});
    if(!sUp||!sDn||!sUp.polygon.length||!sDn.polygon.length) continue;
    var ux=sUp.polygon.reduce(function(a,p){{return a+p[0]}},0)/sUp.polygon.length;
    var uy=sUp.polygon.reduce(function(a,p){{return a+p[1]}},0)/sUp.polygon.length;
    var dx=sDn.polygon.reduce(function(a,p){{return a+p[0]}},0)/sDn.polygon.length;
    var dy=sDn.polygon.reduce(function(a,p){{return a+p[1]}},0)/sDn.polygon.length;
    var uz=demZ(ux,uy)+Math.max(3,maxWD*1.2);
    var dz=demZ(dx,dy)+Math.max(3,maxWD*1.2);
    // Arrow line
    bndTraces.push({{
      type:"scatter3d",mode:"lines+markers",x:[ux,dx],y:[uy,dy],z:[uz,dz],
      line:{{color:"#40d0ff",width:4}},
      marker:{{size:[0,6],color:["rgba(0,0,0,0)","#40d0ff"],symbol:["circle","triangle-right"]}},
      name:"",showlegend:false,hoverinfo:"skip",
    }});
    // Slope label at midpoint
    var midx=(ux+dx)/2,midy=(uy+dy)/2,midz=(uz+dz)/2+2;
    var slp=sUp.slope_pct.toFixed(0);
    arrowX.push(midx); arrowY.push(midy); arrowZ.push(midz);
    arrowU.push(0); arrowV.push(0); arrowW.push(0);
  }}

  // ---- Flow particles down the cascade path ----
  var particleTraces=[];
  var path=[];
  myCas.chain.forEach(function(name) {{
    var s=DATA.topology.subcatchments.find(function(ss){{return ss.name===name}});
    if(s&&s.polygon&&s.polygon.length) {{
      var mx=s.polygon.reduce(function(a,p){{return a+p[0]}},0)/s.polygon.length;
      var my=s.polygon.reduce(function(a,p){{return a+p[1]}},0)/s.polygon.length;
      path.push([mx,my,demZ(mx,my)+Math.max(3.5,maxWD*1.3)]);
    }}
  }});
  var outletNode = DATA.topology.nodes.find(function(n){{return myCas.outlet_node && n.name===myCas.outlet_node}});
  if(outletNode) {{
    path.push([outletNode.x,outletNode.y,demZ(outletNode.x,outletNode.y)+Math.max(3.5,maxWD*1.3)]);
  }}
  if(path.length>=2) {{
    var N=40, px=[],py=[],pz=[];
    var spd=0.1+maxWD*0.3;
    for(var p=0;p<N;p++) {{
      var phase=((frameIdx*spd+p/N*1.5)%1.0+1.0)%1.0;
      var sc=path.length-1, sf=phase*sc, seg=Math.min(sc-1,Math.floor(sf)), frac=sf-seg;
      px.push(path[seg][0]+(path[seg+1][0]-path[seg][0])*frac);
      py.push(path[seg][1]+(path[seg+1][1]-path[seg][1])*frac);
      pz.push(path[seg][2]+(path[seg+1][2]-path[seg][2])*frac);
    }}
    particleTraces.push({{
      type:"scatter3d",mode:"markers",x:px,y:py,z:pz,
      marker:{{size:7,color:"#b0e0ff",symbol:"circle",opacity:0.88,line:{{color:"#fff",width:1}}}},
      name:"Surface runoff particles",showlegend:true,
    }});
  }}

  // ---- SC centroid labels with slope info ----
  var lblX=[],lblY=[],lblZ=[],lblT=[];
  chainSCs.forEach(function(s) {{
    if(!s.polygon||!s.polygon.length) return;
    var mx=s.polygon.reduce(function(a,p){{return a+p[0]}},0)/s.polygon.length;
    var my=s.polygon.reduce(function(a,p){{return a+p[1]}},0)/s.polygon.length;
    lblX.push(mx); lblY.push(my);
    lblZ.push(demZ(mx,my)+Math.max(6,maxWD+3));
    var rv=scRunoff[s.name]?scRunoff[s.name][frameIdx]:0;
    lblT.push(s.name+"<br>slope:"+s.slope_pct+"% imp:"+s.imp_pct+"%<br>runoff:"+(rv*1000).toFixed(1)+" L/s");
  }});

  // ---- Slope legend arrows ----
  // Show cascade flow direction with slope percentage
  var slopeLegends=[];
  for(var i=0;i<myCas.chain.length-1;i++) {{
    var nm=myCas.chain[i]; var sUp=DATA.topology.subcatchments.find(function(ss){{return ss.name===nm}});
    if(sUp) slopeLegends.push("<span style='color:#40d0ff'>flow</span> "+nm+" slope:"+sUp.slope_pct+"%");
  }}
  var lastNm=myCas.chain[myCas.chain.length-1];
  var lastSc=DATA.topology.subcatchments.find(function(ss){{return ss.name===lastNm}});
  if(lastSc) slopeLegends.push(lastNm+" slope:"+lastSc.slope_pct+"%");

  // ---- Rain ----
  var rainNow=0,simMin=DATA.time[frameIdx];
  for(var ri=0;ri<RAIN.rain_times.length;ri++) {{
    if(Math.abs(RAIN.rain_times[ri]-simMin)<3){{rainNow=RAIN.rain_vals[ri];break;}}
  }}

  // ---- Assemble ----
  var traces=[{{
      // Terrain: numeric surfacecolor (dry earth to wet blue)
    type:"surface",x:surfX,y:surfY,z:surfZ,
    surfacecolor:surfW,
    colorscale:[
      [0.00,"rgb(45,88,32)"],
      [0.08,"rgb(65,112,42)"],
      [0.18,"rgb(105,145,55)"],
      [0.30,"rgb(155,165,65)"],
      [0.45,"rgb(185,150,60)"],
      [0.60,"rgb(200,130,55)"],
      [0.73,"rgb(210,140,100)"],
      [0.82,"rgb(50,120,210)"],
      [0.92,"rgb(30,80,225)"],
      [1.00,"rgb(15,35,230)"],
    ],
    cmin:0,cmax:1,showscale:false,opacity:0.93,
    name:"Terrain (green/brown=dry, blue=surface runoff)",
    hoverinfo:"z",
    lighting:{{ambient:0.55,diffuse:0.75,specular:0.12,roughness:0.45}},
    contours:{{z:{{show:true,usecolormap:false,highlightcolor:"rgba(255,255,255,0.2)",project:{{z:true}},width:1}}}},
  }}];

  // Boundaries
  bndTraces.forEach(function(t){{traces.push(t)}});
  // Flow particles
  particleTraces.forEach(function(t){{traces.push(t)}});

  // Underground pipe network and node exchange markers
  var nodeByName={{}};
  DATA.topology.nodes.forEach(function(n){{nodeByName[n.name]=n;}});
  var linkFlow=DATA.series.link_flow || {{}}, linkRatio=DATA.series.link_flow_ratio || {{}};
  DATA.topology.links.forEach(function(link) {{
    var nf=nodeByName[link.from], nt=nodeByName[link.to];
    if(!nf||!nt) return;
    var visible = Math.max(nf.x,nt.x)>=x0 && Math.min(nf.x,nt.x)<=x1 && Math.max(nf.y,nt.y)>=y0 && Math.min(nf.y,nt.y)<=y1;
    if(!visible) return;
    var zf=demZ(nf.x,nf.y)-4.5, zt=demZ(nt.x,nt.y)-4.5;
    var ratio=(linkRatio[link.name]||[])[frameIdx] || 0;
    var color=ratio>0.75?"#ff5a3d":ratio>0.35?"#ffb347":"#4aa8d8";
    traces.push({{type:"scatter3d",mode:"lines",x:[nf.x,nt.x],y:[nf.y,nt.y],z:[zf,zt],
      line:{{color:color,width:4+ratio*7}},name:link.name+" pipe flow",showlegend:false,hoverinfo:"skip"}});
    var flow=(linkFlow[link.name]||[])[frameIdx] || 0;
    var from=[nf.x,nf.y,zf], to=[nt.x,nt.y,zt];
    if(flow<0){{var tmp=from;from=to;to=tmp;}}
    if(ratio>0.03) {{
      var px=[],py=[],pz=[],count=Math.max(2,Math.ceil(ratio*8));
      for(var p=0;p<count;p++) {{
        var phase=(frameIdx*(0.07+ratio*0.13)+p/count)%1;
        px.push(from[0]+(to[0]-from[0])*phase);
        py.push(from[1]+(to[1]-from[1])*phase);
        pz.push(from[2]+(to[2]-from[2])*phase);
      }}
      traces.push({{type:"scatter3d",mode:"markers",x:px,y:py,z:pz,
        marker:{{size:5+ratio*4,color:color,opacity:0.9,line:{{color:"#fff",width:0.6}}}},
        name:"Pipe flow particles",showlegend:false,hoverinfo:"skip"}});
    }}
  }});

  DATA.topology.nodes.forEach(function(n) {{
    if(n.x<x0||n.x>x1||n.y<y0||n.y>y1||n.type==="outfall") return;
    var surfaceZ=demZ(n.x,n.y)+0.4, pipeZ=surfaceZ-5.0;
    traces.push({{type:"scatter3d",mode:"lines",x:[n.x,n.x],y:[n.y,n.y],z:[pipeZ,surfaceZ],
      line:{{color:"#c8d6e5",width:3}},name:"Node shaft",showlegend:false,hoverinfo:"skip"}});
    var ov=((DATA.series.node_overflow||{{}})[n.name]||[])[frameIdx] || 0;
    if(ov>0) {{
      var oRatio=Math.min(1, ov/metric("max_overflow", 0.001));
      traces.push({{type:"scatter3d",mode:"markers",x:[n.x],y:[n.y],z:[surfaceZ+2+oRatio*4],
        marker:{{size:10+oRatio*18,color:"#ff5a3d",opacity:0.55,line:{{color:"#ffd0c2",width:2}}}},
        name:n.name+" overflow",showlegend:true,hoverinfo:"skip"}});
    }}
  }});

  // Labels
  traces.push({{
    type:"scatter3d",mode:"text",x:lblX,y:lblY,z:lblZ,
    text:lblT,textfont:{{size:8.5,color:"#fff"}},name:"",showlegend:false,
  }});

  var layout={{
    title:{{
      text:"<b>"+scName+"</b>  Cascade: "+myCas.chain.join(" -> ")+
           "  t="+DATA.time[frameIdx].toFixed(0)+"min  Rain:"+rainNow.toFixed(0)+"mm/hr"+
           "  <span style='color:#2080d0'>surface</span> runoff  <span style='color:#40d0ff'>cascade</span> path  <span style='color:#ffb347'>pipe</span> flow",
      font:{{size:12,color:"#ccc"}}
    }},
    paper_bgcolor:"#16213e",
    scene:{{
      xaxis:{{title:"",gridcolor:"#2a2a3a",showline:false,showticklabels:false}},
      yaxis:{{title:"",gridcolor:"#2a2a3a",showline:false,showticklabels:false}},
      zaxis:{{title:"Elev (m)",gridcolor:"#2a2a3a",showline:true,linecolor:"#555"}},
      aspectmode:"data",
      camera:{{eye:{{x:0.85,y:-0.85,z:1.1}}}},  // Isometric-ish view
    }},
    margin:{{l:0,r:0,t:40,b:0}},
    showlegend:true,
    legend:{{font:{{size:8,color:"#ccc"}},bgcolor:"rgba(0,0,0,0.5)",y:0.96,x:0.01}},
  }};

  // Add slope annotation
  layout.annotations=[{{
    text:"<i>Slope-driven surface runoff: water flows downhill<br>"+slopeLegends.join("<br>")+"</i>",
    x:0.01,y:0.02,xref:"paper",yref:"paper",showarrow:false,
    font:{{size:7,color:"#aaa"}},align:"left",bgcolor:"rgba(0,0,0,0.4)",
  }}];

  Plotly.react("modal-3d",traces,layout,{{responsive:true,displayModeBar:true,modeBarButtons:[["zoom3d","pan3d","orbitRotation","resetCameraDefault3d"]]}});
}}

// Helper: DEM z at any point
var sc_names = {json.dumps(sc_names, separators=(',',':'))};
var _xc={xc},_xmax={xmax},_ymin={ymin},_ymax={ymax};
function demZ(x,y) {{
  var zc=38+14*Math.pow((x-_xc)/(_xmax-_xc),2);
  var zt=-2*(y-_ymin)/(_ymax-_ymin);
  return zc+zt;
}}

// ============ CHARTS ============
function initCharts() {{
  var tHr=DATA.time.map(function(v){{return v/60}});
  var maxT=tHr[tHr.length-1];
  var plotBg="#1a1a2e",fontColor="#ccc",gridColor="#333";

  var rainX=RAIN.rain_times.map(function(v){{return v/60}});
  var rainY=RAIN.rain_vals;
  var rainTrace={{x:rainX,y:rainY,type:"bar",name:"Rainfall",marker:{{color:"rgba(31,119,180,0.2)",line:{{color:"rgba(31,119,180,0.35)",width:0.5}}}},yaxis:"y2",hoverinfo:"skip",showlegend:true}};

  function baseLayout() {{
    return {{margin:{{l:45,r:50,t:5,b:30}},paper_bgcolor:plotBg,plot_bgcolor:plotBg,font:{{color:fontColor,size:10}},xaxis:{{title:"Time (hr)",gridcolor:gridColor,range:[0,maxT],fixedrange:true,showline:true,linecolor:"#555"}},yaxis:{{gridcolor:gridColor,showline:true,linecolor:"#555",fixedrange:true}},yaxis2:{{overlaying:"y",side:"right",range:[0,140],showgrid:false,showticklabels:false,fixedrange:true}},showlegend:true,legend:{{font:{{size:8.5}},orientation:"h",y:1.12,bgcolor:"rgba(0,0,0,0)"}}}};
  }}

  // Cascade chart
  var casNames=["S_WB1","S_WB2","S_WB3"],casColors=["#e07040","#5b9b4c","#707070"];
  var casLabels=["S_WB1 (BUILDING,85% imp)","S_WB2 (GREEN,15% imp)","S_WB3 (ROAD,90% imp)"];
  var casTraces=[rainTrace];
  casNames.forEach(function(name,i) {{
    var y=DATA.series.subcatch_runoff[name].map(function(v){{return v*1000}});
    var peakIdx=y.indexOf(Math.max.apply(null,y));
    casTraces.push({{x:tHr,y:y,type:"scatter",mode:"lines",name:casLabels[i],line:{{color:casColors[i],width:2.2}},hovertemplate:"%{{x:.2f}}h %{{y:.1f}} L/s<extra>"+name+"</extra>"}});
    casTraces.push({{x:[tHr[peakIdx]],y:[y[peakIdx]],type:"scatter",mode:"markers+text",name:"",marker:{{color:casColors[i],size:8,symbol:"circle"}},text:[" peak "+y[peakIdx].toFixed(0)+" L/s"],textposition:"top right",textfont:{{color:casColors[i],size:7}},showlegend:false,hoverinfo:"skip"}});
  }});
  Plotly.newPlot("chart-cascade",casTraces,baseLayout(),{{responsive:true,displayModeBar:false}});

  // Node depth with ponding
  var ndNames=["J_W","J_MID","J_OUT"],ndColors=["#d62728","#3a7eb0","#2ca02c"],ndFD={{J_W:2.8,J_MID:3.2,J_OUT:3.5}};
  var ndTraces=[rainTrace];
  ndNames.forEach(function(name,i) {{
    ndTraces.push({{x:tHr,y:DATA.series.node_depth[name],type:"scatter",mode:"lines",name:name,line:{{color:ndColors[i],width:2.2}},hovertemplate:"%{{x:.2f}}h %{{y:.2f}}m<extra>"+name+"</extra>"}});
    ndTraces.push({{x:[0,maxT],y:[ndFD[name],ndFD[name]],type:"scatter",mode:"lines",name:name+" rim",line:{{color:ndColors[i],width:1,dash:"dot"}},showlegend:false,hoverinfo:"skip"}});
  }});
  // J_W ponding fill
  var jwDepth=DATA.series.node_depth["J_W"],jwFD=2.8,pondX=[],pondY=[];
  for(var i=0;i<jwDepth.length;i++) {{if(jwDepth[i]>jwFD){{pondX.push(tHr[i]);pondY.push(jwDepth[i])}}else{{if(pondX.length>0){{pondX.push(tHr[i]);pondY.push(jwFD)}}}}}}
  if(pondX.length>0) {{
    ndTraces.push({{x:pondX,y:pondY,type:"scatter",mode:"none",fill:"tozeroy",fillcolor:"rgba(214,39,40,0.15)",name:"J_W ponding zone",showlegend:true,hoverinfo:"skip"}});
    ndTraces.push({{x:pondX,y:pondX.map(function(){{return jwFD}}),type:"scatter",mode:"lines",line:{{color:"rgba(0,0,0,0)",width:0}},showlegend:false,hoverinfo:"skip"}});
  }}
  Plotly.newPlot("chart-depth",ndTraces,baseLayout(),{{responsive:true,displayModeBar:false}});

  // Coupled exchange chart at J_W
  var jwIn=(DATA.series.node_surface_inflow["J_W"]||[]).map(function(v){{return v*1000}});
  var jwOverflow=(DATA.series.node_overflow["J_W"]||[]).map(function(v){{return v*1000}});
  var cwFlow=(DATA.series.link_flow["C_W"]||[]).map(function(v){{return v*1000}});
  var jwDepth=DATA.series.node_depth["J_W"]||[];
  var coupTraces=[
    {{x:tHr,y:jwIn,type:"scatter",mode:"lines",name:"Surface inflow to J_W",line:{{color:"#75e7ff",width:2.3}},hovertemplate:"%{{x:.2f}}h %{{y:.0f}} L/s<extra>surface inflow</extra>"}},
    {{x:tHr,y:cwFlow,type:"scatter",mode:"lines",name:"C_W pipe flow",line:{{color:"#ffb347",width:2.3}},hovertemplate:"%{{x:.2f}}h %{{y:.0f}} L/s<extra>C_W</extra>"}},
    {{x:tHr,y:jwOverflow,type:"scatter",mode:"lines",name:"J_W overflow",line:{{color:"#ff5a3d",width:2.3}},hovertemplate:"%{{x:.2f}}h %{{y:.0f}} L/s<extra>overflow</extra>"}},
    {{x:tHr,y:jwDepth,type:"scatter",mode:"lines",name:"J_W depth",yaxis:"y2",line:{{color:"#e94560",width:1.7,dash:"dot"}},hovertemplate:"%{{x:.2f}}h %{{y:.2f}} m<extra>J_W depth</extra>"}},
    {{x:[0,maxT],y:[2.8,2.8],type:"scatter",mode:"lines",name:"J_W rim",yaxis:"y2",line:{{color:"rgba(233,69,96,0.55)",width:1,dash:"dash"}},hoverinfo:"skip"}}
  ];
  var coupLayout=baseLayout();
  coupLayout.yaxis.title="Flow (L/s)";
  coupLayout.yaxis2={{overlaying:"y",side:"right",title:"Depth (m)",range:[0,4.6],showgrid:false,fixedrange:true,showline:true,linecolor:"#555"}};
  Plotly.newPlot("chart-coupling",coupTraces,coupLayout,{{responsive:true,displayModeBar:false}});

  // Pipe flow loading, normalized from actual link_flow for clearer playback.
  var lkNames=["C_W","C_MID","C_OUT"],lkColors=["#d62728","#3a7eb0","#2ca02c"];
  var lkTraces=[rainTrace];
  lkNames.forEach(function(name,i) {{lkTraces.push({{x:tHr,y:DATA.series.link_flow_ratio[name].map(function(v){{return v*100}}),type:"scatter",mode:"lines",name:name,line:{{color:lkColors[i],width:2.2}},hovertemplate:"%{{x:.2f}}h %{{y:.0f}}% visual load<extra>"+name+"</extra>"}});}});
  lkTraces.push({{x:[0,maxT],y:[75,75],type:"scatter",mode:"lines",name:"High loading",line:{{color:"#ff3333",width:1,dash:"dot"}},hoverinfo:"skip"}});
  var lkLayout=baseLayout(); lkLayout.yaxis.title="Flow loading (%)"; lkLayout.yaxis.range=[-5,105];
  Plotly.newPlot("chart-capacity",lkTraces,lkLayout,{{responsive:true,displayModeBar:false}});

  // Infiltration
  var infData=[{{name:"S_WB1",label:"BUILDING (Horton 50->3 mm/hr)",color:"#e07040"}},{{name:"S_WB2",label:"GREEN (Horton 120->15 mm/hr)",color:"#5b9b4c"}},{{name:"S_WB3",label:"ROAD (Horton 8->1 mm/hr)",color:"#707070"}}];
  var infTraces=[rainTrace];
  infData.forEach(function(item){{infTraces.push({{x:tHr,y:DATA.series.subcatch_infil[item.name].map(function(v){{return v*3600}}),type:"scatter",mode:"lines",name:item.label,line:{{color:item.color,width:2.2}},hovertemplate:"%{{x:.2f}}h %{{y:.1f}} mm/hr<extra>"+item.label+"</extra>"}});}});
  var infLayout=baseLayout(); infLayout.yaxis.title="Infiltration (mm/hr)";
  Plotly.newPlot("chart-infil",infTraces,infLayout,{{responsive:true,displayModeBar:false}});
}}

// ============ FRAME CURSOR ============
function addFrameLine() {{
  var tVal=DATA.time[currentFrame]/60;
  ["chart-cascade","chart-depth","chart-coupling","chart-capacity","chart-infil"].forEach(function(id){{Plotly.relayout(id,{{shapes:[{{type:"line",x0:tVal,x1:tVal,y0:0,y1:1,yref:"paper",line:{{color:"#e94560",width:1.5,dash:"dash"}}}}]}});}});
}}

// ============ TIME CONTROL ============
function setFrame(idx) {{
  currentFrame=Math.max(0,Math.min(DATA.time.length-1,parseInt(idx)));
  document.getElementById("time-slider").value=currentFrame;
  var t=DATA.time[currentFrame],hrs=Math.floor(t/60),mm=Math.floor(t%60);
  document.getElementById("time-label").textContent=hrs+":"+String(mm).padStart(2,"0");
  updateMapFrame(currentFrame);
  addFrameLine();
}}
function onSlider(val){{setFrame(val);}}
function onSpeedChange(val){{playSpeed=parseFloat(val);}}
function togglePlay() {{
  playing=!playing;
  var btn=document.getElementById("btn-play");
  if(playing){{btn.innerHTML="&#9208; Pause";btn.classList.add("paused");if(currentFrame>=DATA.time.length-1)setFrame(0);playTick();}}
  else{{btn.innerHTML="&#9654; Play Animation";btn.classList.remove("paused");if(playTimer)clearTimeout(playTimer);}}
}}
function playTick() {{
  if(!playing) return;
  if(currentFrame>=DATA.time.length-1){{setFrame(0);playing=false;document.getElementById("btn-play").innerHTML="&#8634; Replay";document.getElementById("btn-play").classList.remove("paused");return;}}
  setFrame(currentFrame+1);
  playTimer=setTimeout(playTick,Math.max(50,Math.round(100/playSpeed)));
}}
function highlightCascade(scName) {{
  var found=DATA.topology.cascades.filter(function(c){{return c.chain.indexOf(scName)>=0}});
  if(!found.length) return;
  var cas=found[0],info=document.getElementById("highlight-info");
  info.innerHTML="<b>"+cas.name+"</b><br>"+cas.chain.join(" &rarr; ")+" &rarr; "+cas.outlet_node+"<br><span style='color:#aaa'>"+cas.landuses.join(" &rarr; ")+"</span><br><i style='color:#f0c040'>Click for 3D view</i>";
  info.style.display="block";
  clearTimeout(info._timeout);
  info._timeout=setTimeout(function(){{info.style.display="none"}},5000);
}}

// ============ INIT ============
function initAll() {{
  document.getElementById("time-slider").max=DATA.time.length-1;
  document.getElementById("btn-play").addEventListener("click",togglePlay);
  document.getElementById("time-slider").addEventListener("input",function(){{onSlider(this.value)}});
  document.getElementById("speed-select").addEventListener("change",function(){{onSpeedChange(this.value)}});
  document.getElementById("modal-slider").addEventListener("input",function(){{setModalFrame(this.value)}});
  document.getElementById("modal-play").addEventListener("click",toggleModalPlay);
  document.getElementById("modal-close").addEventListener("click",closeModal);
  document.getElementById("modal-overlay").addEventListener("click",function(e){{if(e.target===this)closeModal()}});
  document.querySelectorAll(".stage-chip").forEach(function(btn) {{
    btn.addEventListener("click",function() {{
      var key=this.getAttribute("data-jump");
      if(typeof KEY_FRAMES[key] === "number") setFrame(KEY_FRAMES[key]);
    }});
  }});
  initMap();
  initCharts();
  setFrame(0);
}}
initAll();
}})();
</script>
</body>
</html>'''

out_path = BASE / "demo_viewer.html"
out_path.write_text(html, encoding="utf-8")
print(f"Written: {out_path} ({len(html)} bytes)")
