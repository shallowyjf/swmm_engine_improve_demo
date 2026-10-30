"""
Physical process demonstration for the 2D surface flood solver.

Visualizes four key physical behaviors:
1. Water flowing downhill on a slope (gravity-driven)
2. Water converging into a bowl depression (topographic control)
3. SWMM node overflow flooding (coupled scenario)
4. Mass conservation over time

Run: python demo_physics.py
Output: demo_physics.png (multi-panel figure)
"""

import sys
import os
from pathlib import Path
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from src.grid import generate_synthetic_dem
from src.solver import BatesSolver
from src.coupling import SwmmOverflowSource, make_uniform_overflow_ts
from src.io import compute_flood_statistics


def demo_slope_flow(ax_dem, ax_slice):
    """Demo 1: Water flows downhill on a 1% slope."""
    rows, cols = 40, 60
    dx = 10.0
    grid = generate_synthetic_dem(rows, cols, dx, [
        {'type': 'flat', 'params': {'elevation': 10.0}},
        {'type': 'slope', 'params': {
            'direction': 'x', 'gradient': 0.01, 'base_elevation': 0.0,
        }},
    ])
    grid.set_boundary_open('all')

    # Place water on the high side (right = high col = high z)
    grid.d[18:22, 40:45] = 0.5

    solver = BatesSolver(grid, theta=0.4)
    depths_t = [grid.d.copy()]
    times = [0.0]

    # Run and capture snapshots
    for snapshot_time in [30, 60, 120, 200]:
        while solver.sim_time < snapshot_time:
            solver.step()
        depths_t.append(grid.d.copy())
        times.append(snapshot_time)

    # Plot DEM with water depth overlay at final time
    im = ax_dem.contourf(grid.z, levels=15, cmap='terrain', alpha=0.7)
    water_mask = grid.d > 0.005
    if np.any(water_mask):
        ax_dem.contour(grid.d, levels=[0.01, 0.05, 0.1, 0.2],
                        colors=['lightblue', 'deepskyblue', 'dodgerblue', 'navy'],
                        linewidths=1.2)
    ax_dem.set_title('Slope flow: water moves downhill (leftward)', fontsize=10, fontweight='bold')
    ax_dem.set_xlabel('Column (east →)')
    ax_dem.set_ylabel('Row (south →)')
    # Arrow showing flow direction
    ax_dem.annotate('flow →', xy=(10, 20), xytext=(35, 20),
                     arrowprops=dict(arrowstyle='->', color='blue', lw=2),
                     fontsize=9, color='blue')

    # Cross-section: depth along center row at different times
    center_row = 20
    for i, (d, t) in enumerate(zip(depths_t, times)):
        alpha = 0.3 + 0.7 * i / len(depths_t)
        ax_slice.plot(np.arange(cols), d[center_row, :],
                       alpha=alpha, label=f't={t:.0f}s', linewidth=1.5)
    ax_slice.plot(np.arange(cols), grid.z[center_row, :] - grid.z[center_row, :].min(),
                   'k--', alpha=0.5, label='bed (rel)')
    ax_slice.set_title('Depth profile along center row', fontsize=9)
    ax_slice.set_xlabel('Column'); ax_slice.set_ylabel('Depth (m)')
    ax_slice.legend(fontsize=7)
    ax_slice.grid(True, alpha=0.3)


def demo_bowl_convergence(ax_dem, ax_slice):
    """Demo 2: Water converges into a bowl depression."""
    rows, cols = 50, 50
    dx = 5.0
    grid = generate_synthetic_dem(rows, cols, dx, [
        {'type': 'flat', 'params': {'elevation': 10.0}},
        {'type': 'bowl', 'params': {
            'center': (25, 25), 'radius': 20, 'depth': 3.0,
        }},
    ])
    grid.set_boundary_open('all')

    # Inject water at bowl rim (northeast edge)
    grid.d[25, 38] = 0.4

    solver = BatesSolver(grid, theta=0.4)
    depths_t = [grid.d.copy()]
    times = [0.0]

    for snapshot_time in [20, 50, 100, 200]:
        while solver.sim_time < snapshot_time:
            solver.step()
        depths_t.append(grid.d.copy())
        times.append(solver.sim_time)

    # Plot DEM contours + water
    ax_dem.contour(grid.z, levels=12, cmap='terrain', alpha=0.6)
    water_mask = grid.d > 0.005
    if np.any(water_mask):
        ax_dem.contourf(grid.d, levels=[0.01, 0.05, 0.1, 0.2, 0.3],
                         cmap='Blues', alpha=0.5)
    ax_dem.plot(38, 25, 'r*', markersize=10, label='Injection point')
    ax_dem.plot(25, 25, 'y*', markersize=8, label='Bowl center')
    ax_dem.set_title('Bowl convergence: water seeks lowest point', fontsize=10, fontweight='bold')
    ax_dem.legend(fontsize=7)

    # Time series of depth at center vs injection point
    center_depths = [d[25, 25] for d in depths_t]
    inject_depths = [d[25, 38] for d in depths_t]
    ax_slice.plot(times, center_depths, 'b-o', label='Bowl center', markersize=4)
    ax_slice.plot(times, inject_depths, 'r-s', label='Injection point', markersize=4)
    ax_slice.set_title('Depth at center vs injection point', fontsize=9)
    ax_slice.set_xlabel('Time (s)'); ax_slice.set_ylabel('Depth (m)')
    ax_slice.legend(fontsize=7); ax_slice.grid(True, alpha=0.3)


def demo_swmm_overflow(ax_map, ax_hydro):
    """Demo 3: Complete SWMM overflow → 2D flood coupled scenario."""
    rows, cols = 60, 60
    dx = 10.0
    grid = generate_synthetic_dem(rows, cols, dx, [
        {'type': 'flat', 'params': {'elevation': 10.0}},
        {'type': 'bowl', 'params': {
            'center': (30, 30), 'radius': 25, 'depth': 3.0,
        }},
        {'type': 'channel', 'params': {
            'from': (30, 30), 'to': (55, 30),
            'width': 3, 'depth': 0.5,
        }},
    ])
    grid.set_boundary_open('all')

    # Two overflow nodes
    node_xy = {
        'MH_center': (30, 30),
        'MH_east': (30, 45),
    }
    overflow_ts = make_uniform_overflow_ts(
        ['MH_center', 'MH_east'],
        start_time=0, end_time=2400,
        peak_rate=1.5, peak_time=600, duration_ratio=0.4,
    )

    coupling = SwmmOverflowSource(grid, node_xy, overflow_ts)
    solver = BatesSolver(grid, theta=0.4)

    # Track max depth envelope
    max_depth = np.zeros_like(grid.d)

    def src_func(t):
        return coupling.get_source_at_time(t)

    results = solver.run(2400, sources_func=src_func, progress_interval=300)
    max_depth = np.maximum(max_depth, grid.d)

    # Left: max depth map
    ax_map.contour(grid.z, levels=10, cmap='terrain', alpha=0.5)
    flood = ax_map.contourf(max_depth, levels=[0.01, 0.1, 0.2, 0.3, 0.5, 0.7],
                              cmap='YlOrRd', alpha=0.7)
    plt.colorbar(flood, ax=ax_map, label='Max depth (m)', shrink=0.8)
    # Plot nodes
    for name, (c, r) in node_xy.items():
        ax_map.plot(c, r, 's', markersize=8, markerfacecolor='cyan',
                     markeredgecolor='black', markeredgewidth=1)
        ax_map.annotate(name, (c + 1, r - 2), fontsize=7, color='white',
                         bbox=dict(boxstyle='round', facecolor='black', alpha=0.6))
    ax_map.set_title('SWMM overflow → 2D flood: max depth envelope', fontsize=10, fontweight='bold')

    # Right: hydrograph + water volume
    times_h, flows_h = overflow_ts['MH_center']
    ax_hydro.fill_between(times_h, flows_h, alpha=0.3, color='steelblue')
    ax_hydro.plot(times_h, flows_h, 'b-', linewidth=1.5, label='Overflow rate (m3/s)')
    ax_hydro_twin = ax_hydro.twinx()
    ax_hydro_twin.plot(results['times'], results['water_volume'],
                        'r-', linewidth=2, label='Surface water volume (m3)')
    ax_hydro.set_xlabel('Time (s)')
    ax_hydro.set_ylabel('Overflow rate (m3/s)', color='blue')
    ax_hydro_twin.set_ylabel('Water volume (m3)', color='red')
    ax_hydro.set_title('Overflow hydrograph & flood volume', fontsize=9)
    lines1, labels1 = ax_hydro.get_legend_handles_labels()
    lines2, labels2 = ax_hydro_twin.get_legend_handles_labels()
    ax_hydro.legend(lines1 + lines2, labels1 + labels2, fontsize=7, loc='upper left')
    ax_hydro.grid(True, alpha=0.3)


def demo_mass_conservation(ax):
    """Demo 4: Mass conservation validation."""
    grid = generate_synthetic_dem(30, 30, 10.0, [
        {'type': 'flat', 'params': {'elevation': 10.0}},
    ])
    # Default WALL boundaries (closed system)
    grid.d[10:20, 10:20] = 0.1
    initial_volume = grid.water_volume

    solver = BatesSolver(grid, theta=0.5)
    volumes = [initial_volume]
    max_depths = [grid.d.max()]
    times = [0.0]

    for _ in range(60):
        dt = solver.step()
        volumes.append(grid.water_volume)
        max_depths.append(grid.d.max())
        times.append(solver.sim_time)

    ax1 = ax
    ax2 = ax1.twinx()
    ax1.plot(times, volumes, 'b-', linewidth=2, label='Water volume (m3)')
    ax1.axhline(y=initial_volume, color='gray', linestyle='--', alpha=0.7, label=f'Initial ({initial_volume:.0f} m3)')
    ax2.plot(times, max_depths, 'r-', linewidth=1.5, alpha=0.7, label='Max depth (m)')
    ax1.set_xlabel('Time (s)')
    ax1.set_ylabel('Water volume (m3)', color='blue')
    ax2.set_ylabel('Max depth (m)', color='red')
    ax1.set_title('Mass conservation (closed boundaries, no sources)', fontsize=10, fontweight='bold')
    lines1, labels1 = ax1.get_legend_handles_labels()
    lines2, labels2 = ax2.get_legend_handles_labels()
    ax1.legend(lines1 + lines2, labels1 + labels2, fontsize=8, loc='center right')
    ax1.grid(True, alpha=0.3)

    # Compute error
    final_volume = volumes[-1]
    error_pct = abs(final_volume - initial_volume) / initial_volume * 100
    ax1.text(0.5, 0.05, f'Volume drift: {error_pct:.4f}%',
             transform=ax1.transAxes, fontsize=11, fontweight='bold',
             ha='center', va='bottom',
             bbox=dict(boxstyle='round', facecolor='lightgreen', alpha=0.8))


def main():
    print("Running physics demos...")
    fig = plt.figure(figsize=(16, 14))

    # Layout: 2x3 grid
    # Row 1: Slope flow (DEM + cross-section), Bowl convergence (DEM + time series)
    # Row 2: SWMM overflow (map + hydrograph), Mass conservation (time series)

    gs = fig.add_gridspec(3, 3, hspace=0.35, wspace=0.35,
                           height_ratios=[1, 1, 0.9])

    # ---- Row 1: Slope flow ----
    ax_slope_dem = fig.add_subplot(gs[0, 0])
    ax_slope_slice = fig.add_subplot(gs[0, 1:])
    demo_slope_flow(ax_slope_dem, ax_slope_slice)

    # ---- Row 2: Bowl convergence ----
    ax_bowl_dem = fig.add_subplot(gs[1, 0])
    ax_bowl_ts = fig.add_subplot(gs[1, 1:])
    demo_bowl_convergence(ax_bowl_dem, ax_bowl_ts)

    # ---- Row 3 left: SWMM overflow ----
    ax_swmm_map = fig.add_subplot(gs[2, :2])
    ax_swmm_hydro = fig.add_subplot(gs[2, 2])
    demo_swmm_overflow(ax_swmm_map, ax_swmm_hydro)

    # ---- Mass conservation (separate figure for clarity) ----
    fig2, ax_mass = plt.subplots(1, 1, figsize=(10, 4))
    demo_mass_conservation(ax_mass)

    fig.suptitle('2D Surface Flood Solver — Physical Process Validation\n'
                 'Plan A: Loose-coupled SWMM → raster shallow water solver',
                 fontsize=13, fontweight='bold', y=0.995)

    base = Path(__file__).resolve().parent
    out1 = base / 'demo_physics_scenarios.png'
    out2 = base / 'demo_physics_mass.png'
    fig.savefig(out1, dpi=150, bbox_inches='tight')
    fig2.savefig(out2, dpi=150, bbox_inches='tight')
    print(f"Saved: {out1}")
    print(f"Saved: {out2}")
    plt.close('all')


if __name__ == '__main__':
    main()
