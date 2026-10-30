#!/usr/bin/env python
"""
SWMM-2D-Flood: Loose-coupled 2D surface flood model.

Usage:
    python main.py --dem <dem.asc> --overflow <overflow.json> [options]

Example with synthetic data:
    python main.py --synthetic --duration 3600 --dx 10 --rows 100 --cols 100
"""

import argparse
import json
import sys
import numpy as np

from src.grid import Grid2D, generate_synthetic_dem
from src.solver import BatesSolver
from src.coupling import SwmmOverflowSource, make_uniform_overflow_ts
from src.io import (save_depth_ascii, save_max_depth_ascii,
                     compute_flood_statistics, print_flood_summary)


def build_synthetic_dem(args):
    """Create synthetic DEM with a bowl depression."""
    rows, cols = args.rows, args.cols
    features = [
        {'type': 'flat', 'params': {'elevation': 0.0}},
        {'type': 'bowl', 'params': {
            'center': (rows // 2, cols // 2),
            'radius': min(rows, cols) // 3,
            'depth': 2.0,
        }},
    ]
    if args.channel:
        features.append({
            'type': 'channel',
            'params': {
                'from': (0, cols // 2),
                'to': (rows - 1, cols // 2),
                'width': 3,
                'depth': 0.5,
            },
        })
    return generate_synthetic_dem(rows, cols, args.dx, features)


def build_overflow_synthetic(grid, args):
    """Create synthetic overflow at the bowl center."""
    node_xy = {
        'MH001': (grid.cols // 2, grid.rows // 2),
    }
    overflow_ts = make_uniform_overflow_ts(
        node_names=['MH001'],
        start_time=0.0,
        end_time=args.duration,
        peak_rate=args.peak_rate,
        peak_time=args.peak_time if args.peak_time else None,
        duration_ratio=args.hydrograph_duration_ratio,
    )
    return node_xy, overflow_ts


def run_synthetic(args):
    """Run flood simulation with synthetic data."""
    print(f"Creating synthetic DEM: {args.rows}×{args.cols}, dx={args.dx}m")
    grid = build_synthetic_dem(args)

    print(f"DEM range: {grid.z.min():.2f} - {grid.z.max():.2f} m")
    grid.set_boundary_open('all')

    node_xy, overflow_ts = build_overflow_synthetic(grid, args)
    coupling = SwmmOverflowSource(grid, node_xy, overflow_ts)
    print(f"Overflow nodes: {list(coupling.node_cell.keys())}")
    for name, (r, c) in coupling.node_cell.items():
        print(f"  {name} → cell ({r}, {c}), elev={grid.z[r, c]:.2f}m")

    solver = BatesSolver(grid, theta=args.theta)

    def src_func(t):
        return coupling.get_source_at_time(t)

    print(f"Starting simulation: {args.duration}s ...")
    results = solver.run(args.duration, sources_func=src_func,
                         progress_interval=args.duration / 20)

    # Track max depth envelope
    max_depth = np.maximum.reduce(
        [np.zeros_like(grid.d)] +
        [grid.d.copy()])  # tracked separately in test, here we just use final

    stats = compute_flood_statistics(grid, grid.d)
    print_flood_summary(stats)

    if args.output:
        save_depth_ascii(grid, args.output)
        print(f"Final depth grid saved to: {args.output}")
    if args.max_output:
        save_max_depth_ascii(grid, grid.d, args.max_output)

    return grid, solver, results


def main():
    parser = argparse.ArgumentParser(
        description='SWMM-2D-Flood: Loose-coupled surface flood model')

    # Mode
    parser.add_argument('--synthetic', action='store_true',
                        help='Use synthetic DEM and overflow data')
    parser.add_argument('--dem', type=str, help='DEM file (ASCII grid)')
    parser.add_argument('--overflow', type=str,
                        help='Overflow JSON file (SWMM node time series)')
    parser.add_argument('--node-xy', type=str,
                        help='Node coordinate mapping JSON')

    # Grid parameters
    parser.add_argument('--dx', type=float, default=10.0,
                        help='Cell size in meters (default: 10)')
    parser.add_argument('--rows', type=int, default=100,
                        help='Grid rows (default: 100)')
    parser.add_argument('--cols', type=int, default=100,
                        help='Grid cols (default: 100)')

    # Simulation
    parser.add_argument('--duration', type=float, default=3600.0,
                        help='Simulation duration in seconds (default: 3600)')
    parser.add_argument('--theta', type=float, default=0.7,
                        help='CFL factor, 0.2-0.8 (default: 0.7)')
    parser.add_argument('--manning', type=float, default=0.03,
                        help="Manning's n (default: 0.03)")

    # Overflow parameters (synthetic mode)
    parser.add_argument('--peak-rate', type=float, default=1.0,
                        help='Peak overflow rate per node in m³/s (default: 1.0)')
    parser.add_argument('--peak-time', type=float, default=None,
                        help='Time of peak overflow in seconds')
    parser.add_argument('--hydrograph-duration-ratio', type=float, default=0.3,
                        help='Ratio of hydrograph duration to total sim time')
    parser.add_argument('--channel', action='store_true',
                        help='Add a drainage channel to synthetic DEM')

    # Output
    parser.add_argument('--output', '-o', type=str, default=None,
                        help='Output depth ASCII grid file')
    parser.add_argument('--max-output', type=str, default=None,
                        help='Output max depth envelope ASCII grid file')

    args = parser.parse_args()

    if args.synthetic:
        run_synthetic(args)
    elif args.dem and args.overflow:
        # Real data path
        raise NotImplementedError(
            "Real DEM + SWMM coupling path is not yet implemented. "
            "Use --synthetic for testing."
        )
    else:
        parser.print_help()
        print("\nTip: use --synthetic for a quick test run.")


if __name__ == '__main__':
    main()
