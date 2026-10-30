"""
Build self-contained HTML prototype with Three.js 3D rendering
demonstrating 4 physical processes of the 2D surface flood solver.

Terrain is rendered as a 3D mesh with vertex colors and lighting.
Water is a semi-transparent dynamic surface updated per simulation frame.
OrbitControls allow rotate/zoom/pan.

Output: prototype_viewer.html (requires internet for Three.js CDN)
"""

import json
from pathlib import Path
import numpy as np
from src.grid import generate_synthetic_dem
from src.solver import BatesSolver
from src.coupling import SwmmOverflowSource, make_uniform_overflow_ts

SPARSE_THRESHOLD = 0.001


def sparse_frame(t, grid):
    d = grid.d
    wet = np.where(d > SPARSE_THRESHOLD)
    sparse = [[int(r), int(c), round(float(d[r, c]), 4)]
              for r, c in zip(wet[0], wet[1])]
    return {
        't': float(t), 'd': sparse,
        'max_d': round(float(d.max()), 4),
        'vol': round(float(grid.water_volume), 2),
        'cells': int(grid.flooded_cells),
    }


# ═══════════════════════════════════════════════════════════════════════
# Scenario builders
# ═══════════════════════════════════════════════════════════════════════

def build_slope():
    rows, cols, dx = 50, 80, 10.0
    grid = generate_synthetic_dem(rows, cols, dx, [
        {'type': 'flat', 'params': {'elevation': 10.0}},
        {'type': 'slope', 'params': {'direction': 'x', 'gradient': 0.006,
                                       'base_elevation': 0.0}},
    ])
    grid.set_boundary_open('all')
    grid.d[22:28, 56:66] = 0.5
    solver = BatesSolver(grid, theta=0.35)
    frames = [sparse_frame(0.0, grid)]
    n, dur = 55, 400.0
    iv = dur / n; ns = iv
    while solver.sim_time < dur:
        solver.step()
        if solver.sim_time >= ns:
            frames.append(sparse_frame(solver.sim_time, grid)); ns += iv
    return {
        'title': '坡面流 — 重力驱动水向下坡扩散',
        'desc': '0.6% 坡度，初始水体 0.5m。水沿坡面重力方向扩散流动。',
        'rows': rows, 'cols': cols, 'dx': dx,
        'z_flat': [round(float(v), 2) for v in grid.z.flat],
        'z_min': round(float(grid.z.min()), 2),
        'z_max': round(float(grid.z.max()), 2),
        'frames': frames,
    }


def build_bowl():
    rows, cols, dx = 65, 65, 5.0
    grid = generate_synthetic_dem(rows, cols, dx, [
        {'type': 'flat', 'params': {'elevation': 10.0}},
        {'type': 'bowl', 'params': {'center': (32, 32), 'radius': 27, 'depth': 3.5}},
    ])
    grid.set_boundary_open('all')
    # Two injection points on opposite sides of the bowl rim
    grid.d[32, 50] = 0.4
    grid.d[50, 32] = 0.4
    solver = BatesSolver(grid, theta=0.35)
    frames = [sparse_frame(0.0, grid)]
    n, dur = 60, 500.0
    iv = dur / n; ns = iv
    while solver.sim_time < dur:
        solver.step()
        if solver.sim_time >= ns:
            frames.append(sparse_frame(solver.sim_time, grid)); ns += iv
    return {
        'title': '洼地汇流 — 地形控制水流方向',
        'desc': '两股水流从洼地边缘注入，受重力驱动汇聚到中心最低点。',
        'rows': rows, 'cols': cols, 'dx': dx,
        'z_flat': [round(float(v), 2) for v in grid.z.flat],
        'z_min': round(float(grid.z.min()), 2),
        'z_max': round(float(grid.z.max()), 2),
        'frames': frames,
    }


def build_overflow():
    rows, cols, dx = 65, 65, 10.0
    grid = generate_synthetic_dem(rows, cols, dx, [
        {'type': 'flat', 'params': {'elevation': 10.0}},
        {'type': 'bowl', 'params': {'center': (32, 32), 'radius': 26, 'depth': 3.5}},
        {'type': 'channel', 'params': {'from': (32, 32), 'to': (58, 32),
                                        'width': 3, 'depth': 0.7}},
    ])
    grid.set_boundary_open('all')
    node_xy = {'J-01': (32, 32), 'J-02': (32, 48)}
    ots = make_uniform_overflow_ts(
        ['J-01', 'J-02'], start_time=0, end_time=1800,
        peak_rate=2.0, peak_time=500, duration_ratio=0.45,
    )
    coupling = SwmmOverflowSource(grid, node_xy, ots)
    solver = BatesSolver(grid, theta=0.35)
    frames = [sparse_frame(0.0, grid)]
    n, dur = 60, 1800.0
    iv = dur / n; ns = iv
    while solver.sim_time < dur:
        solver.step(coupling.get_source_at_time(solver.sim_time))
        if solver.sim_time >= ns:
            frames.append(sparse_frame(solver.sim_time, grid)); ns += iv
    times_h, flows_h = ots['J-01']
    return {
        'title': 'SWMM 溢流耦合 — 检查井溢流地表淹没',
        'desc': '两座检查井溢流（峰值 2.0 m³/s），水在洼地中积聚并沿排水通道流动。',
        'rows': rows, 'cols': cols, 'dx': dx,
        'z_flat': [round(float(v), 2) for v in grid.z.flat],
        'z_min': round(float(grid.z.min()), 2),
        'z_max': round(float(grid.z.max()), 2),
        'nodes': [{'name': 'J-01', 'row': 32, 'col': 32},
                   {'name': 'J-02', 'row': 32, 'col': 48}],
        'hydrograph': {'t': [round(float(x), 1) for x in times_h],
                        'q': [round(float(x), 3) for x in flows_h]},
        'frames': frames,
    }


def build_mass():
    rows, cols, dx = 50, 50, 10.0
    grid = generate_synthetic_dem(rows, cols, dx, [
        {'type': 'flat', 'params': {'elevation': 10.0}},
    ])
    grid.d[16:34, 16:34] = 0.12
    initial_vol = grid.water_volume
    solver = BatesSolver(grid, theta=0.4)
    frames = [sparse_frame(0.0, grid)]
    n, dur = 50, 500.0
    iv = dur / n; ns = iv
    while solver.sim_time < dur:
        solver.step()
        if solver.sim_time >= ns:
            frames.append(sparse_frame(solver.sim_time, grid)); ns += iv
    drift = abs(grid.water_volume - initial_vol) / initial_vol * 100
    return {
        'title': '质量守恒 — 封闭系统水量无损失',
        'desc': f'平坦地形 + 全封闭边界。体积漂移仅 {drift:.4f}%。',
        'rows': rows, 'cols': cols, 'dx': dx,
        'z_flat': [round(float(v), 2) for v in grid.z.flat],
        'z_min': round(float(grid.z.min()), 2),
        'z_max': round(float(grid.z.max()), 2),
        'frames': frames,
    }


# ═══════════════════════════════════════════════════════════════════════
# Build data
# ═══════════════════════════════════════════════════════════════════════

print("Generating simulation data...")
scenarios = {
    'slope': build_slope(),
    'bowl': build_bowl(),
    'overflow': build_overflow(),
    'mass': build_mass(),
}
for key, s in scenarios.items():
    n = len(s['frames'])
    wet = sum(len(f['d']) for f in s['frames'])
    print(f"  {key}: {n} frames, {s['rows']}x{s['cols']}, {wet} wet entries")

# Store terrain as compact array-of-arrays for JS
for key, s in scenarios.items():
    rows, cols = s['rows'], s['cols']
    zf = s['z_flat']
    s['z_grid'] = [[zf[i*cols+j] for j in range(cols)] for i in range(rows)]
    del s['z_flat']  # remove flat copy to save space

data_json = json.dumps(scenarios, separators=(',', ':'))
print(f"JSON: {len(data_json)/1024:.0f} KB")

# ═══════════════════════════════════════════════════════════════════════
# HTML template — Three.js 3D rendering engine
# ═══════════════════════════════════════════════════════════════════════

html = r'''<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>二维地表淹没求解器 — 3D 物理过程验证</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:-apple-system,BlinkMacSystemFont,'Microsoft YaHei','PingFang SC',sans-serif;
     background:#0a0f14;color:#d5e8f5;overflow:hidden;height:100vh}
#app{display:flex;flex-direction:column;height:100vh}
#header{background:linear-gradient(135deg,#0d1520,#16283a);padding:12px 24px;
        display:flex;align-items:center;justify-content:space-between;border-bottom:1px solid #1a3147;
        z-index:10}
#header h1{font-size:17px;font-weight:600;letter-spacing:.5px}
#header .sub{font-size:10.5px;color:#6a8da8;margin-top:2px}
#badge{background:#1a4668;color:#72b4dc;padding:4px 12px;border-radius:12px;font-size:11px}

#tabs{display:flex;background:#0d1520;border-bottom:2px solid #1a3147;padding:0 12px;z-index:10}
.tab{padding:10px 20px;cursor:pointer;font-size:12px;font-weight:500;border:none;
     background:none;color:#6a8da8;border-bottom:2px solid transparent;
     margin-bottom:-2px;transition:all .2s}
.tab:hover{color:#a0c0d8;background:rgba(255,255,255,.02)}
.tab.active{color:#5dade2;border-bottom-color:#5dade2}
.tab .n{display:inline-block;background:#1a4668;color:#72b4dc;font-size:10px;
        width:17px;height:17px;line-height:17px;text-align:center;
        border-radius:50%;margin-right:5px}
.tab.active .n{background:#2e86c1;color:#fff}

#view{flex:1;position:relative;overflow:hidden}
#three-canvas{display:block;width:100%;height:100%}

/* Overlay panels */
#panel{position:absolute;top:14px;right:14px;width:230px;
       background:rgba(13,21,32,.93);border:1px solid #1a3147;
       border-radius:8px;padding:13px;font-size:10.5px;
       backdrop-filter:blur(8px);z-index:5;pointer-events:auto}
#panel h3{font-size:12px;margin-bottom:3px;color:#5dade2;font-weight:600}
#panel .desc{font-size:10px;color:#6a8da8;margin-bottom:9px;line-height:1.45}
.stat{display:flex;justify-content:space-between;padding:3.5px 0;
      border-bottom:1px solid rgba(255,255,255,.035)}
.stat .l{color:#6a8da8}.stat .v{color:#d5e8f5;font-weight:600;
      font-family:'SF Mono',Consolas,monospace;font-size:10px}
.stat .hl{color:#f0a050}
#hydro-plot{width:100%;height:52px;margin-top:8px;border-radius:4px;overflow:hidden}
#legend{position:absolute;bottom:16px;left:16px;background:rgba(13,21,32,.92);
        border:1px solid #1a3147;border-radius:6px;padding:7px 12px;
        font-size:10px;display:flex;align-items:center;gap:10px;z-index:5}
.leg-bar{width:100px;height:9px;border-radius:2px;
         background:linear-gradient(90deg,#0a2d4a,#1565c0,#219ebc,#48cae4,#90e0ef)}
#tips{position:absolute;bottom:16px;right:16px;font-size:9px;color:#2a4d68;
      text-align:right;line-height:1.6;z-index:5}
#loading{position:absolute;top:50%;left:50%;transform:translate(-50%,-50%);
         font-size:14px;color:#5dade2;z-index:20}

#controls{background:#0d1520;padding:9px 18px;display:flex;align-items:center;
          gap:10px;border-top:1px solid #1a3147;z-index:10}
#controls button{background:#16283a;color:#b0c8d8;border:1px solid #1d3f5a;
                 border-radius:5px;padding:5px 14px;cursor:pointer;font-size:11.5px;
                 font-weight:500;transition:all .15s}
#controls button:hover{background:#1d3f5a;border-color:#2a6080}
#controls button.on{background:#2e86c1;border-color:#5dade2;color:#fff}
#progress{flex:1;display:flex;align-items:center;gap:9px}
#slider{flex:1;accent-color:#5dade2;height:5px;cursor:pointer}
#time-label{font-size:10.5px;color:#6a8da8;min-width:80px;text-align:right;
            font-family:'SF Mono',Consolas,monospace}
#speed-select{background:#16283a;color:#b0c8d8;border:1px solid #1d3f5a;
              border-radius:4px;padding:4px 7px;font-size:10.5px;cursor:pointer}
</style>
</head>
<body>
<div id="app">
<div id="header">
  <div>
    <h1>二维地表淹没求解器 · 3D 物理过程验证</h1>
    <div class="sub">方案A：SWMM 节点溢流 → 二维栅格浅水方程求解器（Bates 2010 简化惯性格式）&nbsp;|&nbsp;Three.js 3D 渲染</div>
  </div>
  <div id="badge">Plan A · 松散耦合</div>
</div>
<div id="tabs">
  <button class="tab active" data-scene="slope"><span class="n">1</span>坡面流</button>
  <button class="tab" data-scene="bowl"><span class="n">2</span>洼地汇流</button>
  <button class="tab" data-scene="overflow"><span class="n">3</span>SWMM 溢流耦合</button>
  <button class="tab" data-scene="mass"><span class="n">4</span>质量守恒</button>
</div>
<div id="view">
  <canvas id="three-canvas"></canvas>
  <div id="loading">加载中...</div>
  <div id="panel">
    <h3 id="scene-title"></h3>
    <div class="desc" id="scene-desc"></div>
    <div class="stat"><span class="l">模拟时间</span><span class="v" id="st-time">--</span></div>
    <div class="stat"><span class="l">最大水深</span><span class="v" id="st-maxd">--</span></div>
    <div class="stat"><span class="l">淹没单元</span><span class="v" id="st-cells">--</span></div>
    <div class="stat"><span class="l">地表水量</span><span class="v hl" id="st-vol">--</span></div>
    <div id="hydro-plot"><canvas id="hydro-canvas"></canvas></div>
  </div>
  <div id="legend">
    <span style="color:#6a8da8">水深</span>
    <div><div class="leg-bar"></div>
    <div style="display:flex;justify-content:space-between;font-size:9px;color:#6a8da8;margin-top:1px">
      <span>0m</span><span>浅</span><span>深</span></div></div>
    <span style="color:#6a8da8">max</span>
  </div>
  <div id="tips">🖱 拖拽旋转 &nbsp;|&nbsp; 滚轮缩放 &nbsp;|&nbsp; 右键平移 &nbsp;|&nbsp; 空格 播放 &nbsp;|&nbsp; 1-4 切换场景</div>
</div>
<div id="controls">
  <button id="btn-play">▶ 播放</button>
  <button id="btn-pause">⏸ 暂停</button>
  <button id="btn-reset">↺ 重置</button>
  <div id="progress">
    <span style="font-size:10px;color:#6a8da8">帧</span>
    <input type="range" id="slider" min="0" max="100" value="0">
    <span id="time-label">0.0 s</span>
  </div>
  <span style="font-size:10px;color:#6a8da8">速度:</span>
  <select id="speed-select">
    <option value="0.5">0.5x</option><option value="1" selected>1x</option>
    <option value="2">2x</option><option value="4">4x</option>
  </select>
</div>
</div>

<script type="importmap">
{"imports":{
  "three":"https://unpkg.com/three@0.157.0/build/three.module.js",
  "three/addons/":"https://unpkg.com/three@0.157.0/examples/jsm/"
}}
</script>

<script type="module">
import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';

const SCENARIOS = __DATA_PLACEHOLDER__;

// ── State ──────────────────────────────────────────────────────────
let currentScene = 'slope';
let currentFrame = 0;
let playing = false;
let playSpeed = 1;
let animationId = null;

// Three.js objects
let renderer, scene3d, camera, controls;
let terrainMesh, waterMesh, nodeMarkers = [];
let terrainGeo, waterGeo; // shared geometries rebuilt per scene

const V_EXAG = 3.0;   // vertical exaggeration
const WATER_OFFSET = 0.03;

// ── Init Three.js ──────────────────────────────────────────────────

function initThree() {
    const canvas = document.getElementById('three-canvas');
    const view = document.getElementById('view');

    renderer = new THREE.WebGLRenderer({canvas, antialias:true, alpha:true});
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    renderer.setSize(view.clientWidth, view.clientHeight);
    renderer.shadowMap.enabled = true;
    renderer.toneMapping = THREE.ACESFilmicToneMapping;
    renderer.toneMappingExposure = 1.2;

    scene3d = new THREE.Scene();
    scene3d.background = new THREE.Color('#0d1722');
    scene3d.fog = new THREE.Fog('#0d1722', 30, 200);

    camera = new THREE.PerspectiveCamera(48, view.clientWidth/view.clientHeight, 0.5, 500);
    camera.position.set(35, 22, 42);
    camera.lookAt(0, -2, 0);

    controls = new OrbitControls(camera, renderer.domElement);
    controls.target.set(0, -1, 0);
    controls.enableDamping = true;
    controls.dampingFactor = 0.12;
    controls.minDistance = 5;
    controls.maxDistance = 150;
    controls.maxPolarAngle = Math.PI * 0.48;
    controls.update();

    // Lighting
    const ambient = new THREE.AmbientLight('#8899bb', 1.8);
    scene3d.add(ambient);

    const sun = new THREE.DirectionalLight('#fff8e8', 5.5);
    sun.position.set(30, 40, 20);
    sun.castShadow = true;
    sun.shadow.mapSize.set(1024, 1024);
    sun.shadow.camera.near = 0.5;
    sun.shadow.camera.far = 200;
    sun.shadow.camera.left = -60; sun.shadow.camera.right = 60;
    sun.shadow.camera.top = 60; sun.shadow.camera.bottom = -60;
    sun.shadow.bias = -0.0001;
    scene3d.add(sun);

    const fill = new THREE.DirectionalLight('#8899cc', 1.2);
    fill.position.set(-15, 5, -20);
    scene3d.add(fill);

    // Ground reference grid
    const gridHelper = new THREE.PolarGridHelper(40, 32, 24, 128, '#1a3048', '#1a3048');
    gridHelper.position.y = -8;
    scene3d.add(gridHelper);

    document.getElementById('loading').style.display = 'none';
}

// ── Build terrain mesh ─────────────────────────────────────────────

function terrainVertexColor(z, zMin, zRange) {
    const t = zRange > 0 ? (z - zMin) / zRange : 0.5;
    let r, g, b;
    if (t < 0.3) { const s=t/0.3; r=0.18+s*0.28; g=0.22+s*0.20; b=0.10+s*0.10; }
    else if (t < 0.65) { const s=(t-0.3)/0.35; r=0.46+s*0.30; g=0.42+s*0.18; b=0.20+s*0.18; }
    else { const s=(t-0.65)/0.35; r=0.76+s*0.18; g=0.60+s*0.25; b=0.38+s*0.25; }
    return {r,g,b};
}

function buildTerrainMesh(scene) {
    const rows = scene.rows, cols = scene.cols;
    const zGrid = scene.z_grid;
    const zMin = scene.z_min, zMax = scene.z_max, zRange = zMax - zMin;
    const dx = scene.dx;

    const geo = new THREE.PlaneGeometry(
        (cols-1) * dx / 10, (rows-1) * dx / 10, cols-1, rows-1);
    geo.rotateX(-Math.PI / 2);

    const pos = geo.attributes.position;
    const colors = new Float32Array(pos.count * 3);

    for (let i = 0; i < pos.count; i++) {
        const x = pos.getX(i), z = pos.getZ(i);
        // Map from 3D coordinates back to grid indices
        const col = Math.round((x / (dx/10)) + (cols-1)/2);
        const row = Math.round(-(z / (dx/10)) + (rows-1)/2);
        const zVal = (row>=0&&row<rows&&col>=0&&col<cols) ? zGrid[row][col] : zMin;
        const y = (zVal - (zMin+zMax)/2) * V_EXAG / 10;
        pos.setY(i, y);
        const c = terrainVertexColor(zVal, zMin, zRange);
        colors[i*3]=c.r; colors[i*3+1]=c.g; colors[i*3+2]=c.b;
    }

    geo.setAttribute('color', new THREE.BufferAttribute(colors, 3));
    geo.computeVertexNormals();

    const mat = new THREE.MeshStandardMaterial({
        vertexColors: true,
        roughness: 0.75,
        metalness: 0.05,
        flatShading: false,
    });

    const mesh = new THREE.Mesh(geo, mat);
    mesh.castShadow = true;
    mesh.receiveShadow = true;
    return {mesh, geo};
}

// ── Build / update water mesh ─────────────────────────────────────

function buildWaterMesh(scene) {
    const rows = scene.rows, cols = scene.cols;
    const dx = scene.dx;
    const zMin = scene.z_min, zMax = scene.z_max;

    const geo = new THREE.PlaneGeometry(
        (cols-1) * dx / 10, (rows-1) * dx / 10, cols-1, rows-1);
    geo.rotateX(-Math.PI / 2);

    const mat = new THREE.MeshPhongMaterial({
        color: '#2196F3',
        specular: '#ffffff',
        shininess: 80,
        transparent: true,
        opacity: 0.65,
        side: THREE.DoubleSide,
        depthWrite: false,
    });

    const mesh = new THREE.Mesh(geo, mat);
    mesh.renderOrder = 1;
    mesh.position.y = WATER_OFFSET;
    return {mesh, geo};
}

function updateWaterMesh(scene, frameIdx) {
    const rows = scene.rows, cols = scene.cols;
    const zGrid = scene.z_grid;
    const zMin = scene.z_min, zMax = scene.z_max;
    const dx = scene.dx;

    const frame = scene.frames[frameIdx];
    // Rebuild dense depth
    if (!frame._dense) {
        const d = new Float32Array(rows * cols);
        for (const [r, c, v] of frame.d) d[r * cols + c] = v;
        frame._dense = d;
    }
    const dArr = frame._dense;

    const pos = waterGeo.attributes.position;
    for (let i = 0; i < pos.count; i++) {
        const x = pos.getX(i), z = pos.getZ(i);
        const col = Math.round((x / (dx/10)) + (cols-1)/2);
        const row = Math.round(-(z / (dx/10)) + (rows-1)/2);
        const zVal = (row>=0&&row<rows&&col>=0&&col<cols) ? zGrid[row][col] : zMin;
        const dVal = (row>=0&&row<rows&&col>=0&&col<cols) ? (dArr[row*cols+col]||0) : 0;
        const ws = zVal + dVal;
        const y = (ws - (zMin+zMax)/2) * V_EXAG / 10 + WATER_OFFSET;
        pos.setY(i, y);
    }
    pos.needsUpdate = true;
    waterGeo.computeVertexNormals();

    // Dynamic opacity based on max depth
    const maxD = frame.max_d || 0.01;
    waterMesh.material.opacity = 0.3 + Math.min(0.55, maxD * 1.2);
}

// ── Node markers ───────────────────────────────────────────────────

function buildNodeMarkers(scene) {
    // Remove old
    for (const m of nodeMarkers) scene3d.remove(m);
    nodeMarkers = [];

    if (!scene.nodes) return;

    const rows = scene.rows, cols = scene.cols;
    const zGrid = scene.z_grid;
    const zMin = scene.z_min, zMax = scene.z_max, zMid = (zMin+zMax)/2;
    const dx = scene.dx;

    for (const node of scene.nodes) {
        const zVal = zGrid[node.row][node.col];
        const x = (node.col - (cols-1)/2) * dx / 10;
        const z = -(node.row - (rows-1)/2) * dx / 10;
        const y = (zVal - zMid) * V_EXAG / 10;

        // Glow sphere
        const glowGeo = new THREE.SphereGeometry(0.35, 16, 16);
        const glowMat = new THREE.MeshBasicMaterial({color:'#ff6633',transparent:true,opacity:0.5});
        const glow = new THREE.Mesh(glowGeo, glowMat);
        glow.position.set(x, y, z);
        scene3d.add(glow);
        nodeMarkers.push(glow);

        // Core sphere
        const coreGeo = new THREE.SphereGeometry(0.18, 16, 16);
        const coreMat = new THREE.MeshStandardMaterial({color:'#ff4422',emissive:'#ff3311',emissiveIntensity:0.6});
        const core = new THREE.Mesh(coreGeo, coreMat);
        core.position.set(x, y, z);
        scene3d.add(core);
        nodeMarkers.push(core);
    }
}

function updateNodeMarkers(scene, frameIdx) {
    if (!scene.nodes) return;
    const rows=scene.rows, cols=scene.cols;
    const zGrid=scene.z_grid;
    const zMin=scene.z_min, zMax=scene.z_max, zMid=(zMin+zMax)/2;
    const dx=scene.dx;
    const frame=scene.frames[frameIdx];

    let mi = 0;
    for (const node of scene.nodes) {
        const zVal = zGrid[node.row][node.col];
        const dVal = frame._dense ? (frame._dense[node.row*cols+node.col]||0) : 0;
        const ws = zVal + dVal;
        const x = (node.col - (cols-1)/2) * dx / 10;
        const z = -(node.row - (rows-1)/2) * dx / 10;
        const y = (ws - zMid) * V_EXAG / 10 + WATER_OFFSET;
        if (mi < nodeMarkers.length) {
            nodeMarkers[mi].position.set(x,y,z);
            nodeMarkers[mi+1].position.set(x,y,z);
        }
        mi += 2;
    }
}

// ── Scene switching ────────────────────────────────────────────────

function switchScene(name) {
    currentScene = name; currentFrame = 0;
    const scene = SCENARIOS[name];

    // Clear old
    if (terrainMesh) { terrainGeo.dispose(); terrainMesh.material.dispose(); scene3d.remove(terrainMesh); }
    if (waterMesh) { waterGeo.dispose(); waterMesh.material.dispose(); scene3d.remove(waterMesh); }
    for (const m of nodeMarkers) { m.geometry?.dispose(); m.material?.dispose(); scene3d.remove(m); }
    nodeMarkers = [];
    for (const f of scene.frames) f._dense = null;

    // Build new
    const t = buildTerrainMesh(scene);
    terrainMesh = t.mesh; terrainGeo = t.geo;
    scene3d.add(terrainMesh);

    const w = buildWaterMesh(scene);
    waterMesh = w.mesh; waterGeo = w.geo;
    updateWaterMesh(scene, 0);
    scene3d.add(waterMesh);

    buildNodeMarkers(scene);

    // Camera
    const w2 = (scene.cols-1) * scene.dx / 10;
    const h2 = (scene.rows-1) * scene.dx / 10;
    const diag = Math.sqrt(w2*w2 + h2*h2);
    camera.position.set(diag*0.7, diag*0.55, diag*0.85);
    controls.target.set(0, -0.5, 0);
    controls.update();

    // UI
    document.getElementById('slider').max = scene.frames.length - 1;
    document.getElementById('slider').value = 0;
    document.getElementById('scene-title').textContent = scene.title;
    document.getElementById('scene-desc').textContent = scene.desc;
    document.getElementById('hydro-plot').style.display = scene.hydrograph ? 'block' : 'none';
    document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
    document.querySelector('[data-scene="' + name + '"]').classList.add('active');

    updateStats();
}

function updateStats() {
    const scene = SCENARIOS[currentScene];
    const frame = scene.frames[currentFrame];
    const t = frame.t;
    document.getElementById('st-time').textContent =
        t < 60 ? t.toFixed(1) + ' s' :
        t < 3600 ? (t/60).toFixed(1) + ' min' : (t/3600).toFixed(2) + ' h';
    document.getElementById('st-maxd').textContent = frame.max_d.toFixed(3) + ' m';
    document.getElementById('st-cells').textContent = frame.cells + ' / ' + (scene.rows*scene.cols);
    document.getElementById('st-vol').textContent = frame.vol.toFixed(1) + ' m3';
    document.getElementById('slider').value = currentFrame;
    document.getElementById('time-label').textContent = frame.t.toFixed(0) + ' s';
    if (scene.hydrograph) drawH(scene);
}

// ── Hydrograph ─────────────────────────────────────────────────────

function drawH(scene) {
    const ctr = document.getElementById('hydro-plot');
    ctr.style.display = 'block';
    const w = ctr.clientWidth, h = ctr.clientHeight;
    if (w<=0||h<=0) return;
    const hc = document.getElementById('hydro-canvas');
    hc.width = w; hc.height = h;
    const ctx = hc.getContext('2d');
    const hg = scene.hydrograph;
    const m = {t:4,r:5,b:14,l:30}, pw = w-m.l-m.r, ph = h-m.t-m.b;
    ctx.clearRect(0,0,w,h);
    ctx.fillStyle = 'rgba(0,0,0,0.25)'; ctx.fillRect(m.l,m.t,pw,ph);
    ctx.strokeStyle = '#1a3f58'; ctx.lineWidth = 0.5;
    ctx.beginPath(); ctx.moveTo(m.l,m.t); ctx.lineTo(m.l,m.t+ph);
    ctx.lineTo(m.l+pw,m.t+ph); ctx.stroke();

    const tM = hg.t[hg.t.length-1], qM = Math.max(...hg.q) * 1.15;
    const frame = scene.frames[currentFrame];
    const tNow = frame.t;

    // Fill under curve
    const grad = ctx.createLinearGradient(0,m.t,0,m.t+ph);
    grad.addColorStop(0,'rgba(93,173,226,.8)'); grad.addColorStop(1,'rgba(93,173,226,.1)');
    ctx.fillStyle = grad; ctx.strokeStyle = '#5dade2'; ctx.lineWidth = 1.5;
    ctx.beginPath();
    const x0=m.l+(hg.t[0]/tM)*pw, y0=m.t+ph-(hg.q[0]/qM)*ph;
    ctx.moveTo(x0,y0);
    for(let k=1;k<hg.t.length;k++){
        const x=m.l+(hg.t[k]/tM)*pw, y=m.t+ph-(hg.q[k]/qM)*ph;
        ctx.lineTo(x,y);
    }
    ctx.stroke();
    const lastX=m.l+(hg.t[hg.t.length-1]/tM)*pw;
    ctx.lineTo(lastX,m.t+ph); ctx.lineTo(x0,m.t+ph); ctx.closePath(); ctx.fill();

    // Time marker
    const mx = m.l + (tNow / tM) * pw;
    ctx.strokeStyle = '#f0a050'; ctx.lineWidth = 1; ctx.setLineDash([3,3]);
    ctx.beginPath(); ctx.moveTo(mx,m.t); ctx.lineTo(mx,m.t+ph); ctx.stroke();
    ctx.setLineDash([]);

    ctx.fillStyle = '#6a8da8'; ctx.font = '8px "Microsoft YaHei",sans-serif';
    ctx.textAlign = 'right'; ctx.fillText('Q',m.l-2,m.t+10);
    ctx.textAlign = 'center'; ctx.fillText('t(s)',m.l+pw/2,m.t+ph+12);
}

// ── Animation ──────────────────────────────────────────────────────

let lastTick = 0, accum = 0;
const FRAME_INT = 0.09;

function animate(ts) {
    animationId = requestAnimationFrame(animate);
    controls.update();

    if (playing) {
        if (lastTick === 0) lastTick = ts;
        accum += (ts - lastTick) / 1000 * playSpeed;
        lastTick = ts;

        const scene = SCENARIOS[currentScene];
        const n = Math.max(1, Math.floor(accum / FRAME_INT));
        if (n > 0) {
            accum -= n * FRAME_INT;
            currentFrame = Math.min(currentFrame + n, scene.frames.length - 1);
            updateWaterMesh(scene, currentFrame);
            updateNodeMarkers(scene, currentFrame);
            updateStats();
            if (currentFrame >= scene.frames.length - 1) stopAnim();
        }
    }

    renderer.render(scene3d, camera);
}

function stopAnim() {
    playing = false;
    const b = document.getElementById('btn-play');
    b.classList.remove('on'); b.textContent = '▶ 播放';
}

function togglePlay() {
    playing = !playing;
    const b = document.getElementById('btn-play');
    if (playing) {
        b.classList.add('on'); b.textContent = '⏸ 播放中...';
        lastTick = 0; accum = 0;
    } else {
        b.classList.remove('on'); b.textContent = '▶ 播放';
    }
}

function resetAnim() {
    stopAnim();
    currentFrame = 0; lastTick = 0; accum = 0;
    const scene = SCENARIOS[currentScene];
    updateWaterMesh(scene, 0);
    updateNodeMarkers(scene, 0);
    updateStats();
}

// ── Events ─────────────────────────────────────────────────────────

document.querySelectorAll('.tab').forEach(t => {
    t.addEventListener('click', () => switchScene(t.dataset.scene));
});
document.getElementById('btn-play').addEventListener('click', togglePlay);
document.getElementById('btn-pause').addEventListener('click', stopAnim);
document.getElementById('btn-reset').addEventListener('click', resetAnim);
document.getElementById('slider').addEventListener('input', e => {
    currentFrame = parseInt(e.target.value);
    const scene = SCENARIOS[currentScene];
    updateWaterMesh(scene, currentFrame);
    updateNodeMarkers(scene, currentFrame);
    updateStats();
});
document.getElementById('speed-select').addEventListener('change', e => {
    playSpeed = parseFloat(e.target.value);
});

document.addEventListener('keydown', e => {
    switch (e.key) {
        case ' ': e.preventDefault(); togglePlay(); break;
        case 'ArrowLeft':
            currentFrame = Math.max(0, currentFrame - 1);
            document.getElementById('slider').value = currentFrame;
            updateWaterMesh(SCENARIOS[currentScene], currentFrame);
            updateNodeMarkers(SCENARIOS[currentScene], currentFrame);
            updateStats(); break;
        case 'ArrowRight':
            const max = SCENARIOS[currentScene].frames.length - 1;
            currentFrame = Math.min(max, currentFrame + 1);
            document.getElementById('slider').value = currentFrame;
            updateWaterMesh(SCENARIOS[currentScene], currentFrame);
            updateNodeMarkers(SCENARIOS[currentScene], currentFrame);
            updateStats(); break;
        case '1': switchScene('slope'); break;
        case '2': switchScene('bowl'); break;
        case '3': switchScene('overflow'); break;
        case '4': switchScene('mass'); break;
    }
});

window.addEventListener('resize', () => {
    const view = document.getElementById('view');
    camera.aspect = view.clientWidth / view.clientHeight;
    camera.updateProjectionMatrix();
    renderer.setSize(view.clientWidth, view.clientHeight);
});

// ── Init ───────────────────────────────────────────────────────────

initThree();
switchScene('slope');
animate(0);
</script>
</body>
</html>'''

output = html.replace('__DATA_PLACEHOLDER__', data_json)
out_path = Path(__file__).resolve().parent / 'prototype_viewer.html'
with open(out_path, 'w', encoding='utf-8') as f:
    f.write(output)
print(f"\nPrototype: {out_path}  ({len(output)/1024:.0f} KB)")
print("Open in browser (requires internet for Three.js CDN).")
