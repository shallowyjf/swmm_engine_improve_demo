"""
Tests for the 2D surface flood solver.

Validates:
1. DEM generation and grid setup
2. Overflow hydrograph generation
3. Node-to-cell coupling
4. Solver physics: water flows downhill
5. Mass conservation
6. Bowl filling scenario (integration test)
7. CFL stability
"""

import sys
import os
import unittest
import tempfile
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.grid import (Grid2D, generate_synthetic_dem, DEPTH_MIN,
                       BC_OPEN, BC_WALL, BC_INTERIOR)
from src.solver import BatesSolver
from src.coupling import (SwmmOverflowSource, make_uniform_overflow_ts,
                           parse_swmm_rpt_overflow)
from src.io import compute_flood_statistics


class TestGridGeneration(unittest.TestCase):
    """Test DEM and Grid2D creation."""

    def test_flat_dem(self):
        grid = generate_synthetic_dem(20, 30, 10.0,
                                       [{'type': 'flat', 'params': {'elevation': 10.0}}])
        self.assertEqual((20, 30), grid.z.shape)
        self.assertAlmostEqual(10.0, grid.z.min())
        self.assertAlmostEqual(10.0, grid.z.max())
        self.assertEqual(10.0, grid.dx)
        self.assertAlmostEqual(0.0, grid.d.max())

    def test_bowl_dem(self):
        rows, cols = 50, 50
        cx, cy = cols // 2, rows // 2
        grid = generate_synthetic_dem(rows, cols, 5.0, [
            {'type': 'flat', 'params': {'elevation': 10.0}},
            {'type': 'bowl', 'params': {
                'center': (cy, cx), 'radius': 15, 'depth': 2.0,
            }},
        ])
        # Center should be lower than edges
        self.assertLess(grid.z[cy, cx], grid.z[0, 0])
        self.assertLess(grid.z[cy, cx], grid.z[0, cx])
        # Edge should be at base elevation
        self.assertAlmostEqual(10.0, grid.z[0, 0], delta=0.5)

    def test_slope_dem(self):
        rows, cols = 30, 30
        grid = generate_synthetic_dem(rows, cols, 10.0, [
            {'type': 'slope', 'params': {
                'direction': 'x', 'gradient': 0.02, 'base_elevation': 10.0,
            }},
        ])
        # Right (higher col) should be higher than left
        self.assertLess(grid.z[15, 0], grid.z[15, -1])

    def test_manning_uniform(self):
        grid = generate_synthetic_dem(20, 20, 10.0,
                                       [{'type': 'flat', 'params': {'elevation': 10.0}}])
        self.assertAlmostEqual(0.03, grid.n[0, 0])
        self.assertAlmostEqual(0.03, grid.n[10, 10])

    def test_boundary_default(self):
        grid = generate_synthetic_dem(20, 20, 10.0, [
            {'type': 'flat', 'params': {'elevation': 10.0}},
        ])
        # Default: interior cells are INTERIOR, edges are WALL
        self.assertEqual(BC_INTERIOR, np.int32(grid.bc[10, 10]))
        self.assertEqual(BC_WALL, np.int32(grid.bc[0, 0]))
        self.assertEqual(BC_WALL, np.int32(grid.bc[-1, -1]))
        grid.set_boundary_open('all')
        self.assertEqual(BC_OPEN, np.int32(grid.bc[0, 0]))
        self.assertEqual(BC_OPEN, np.int32(grid.bc[-1, -1]))


class TestOverflowHydrograph(unittest.TestCase):
    """Test overflow time series generation."""

    def test_triangular_hydrograph(self):
        ts = make_uniform_overflow_ts(
            ['N1', 'N2'], start_time=0, end_time=3600,
            peak_rate=2.0, peak_time=1200, duration_ratio=0.3,
        )
        self.assertIn('N1', ts)
        self.assertIn('N2', ts)
        times, flows = ts['N1']
        self.assertEqual(len(times), len(flows))
        peak_idx = np.argmax(flows)
        self.assertAlmostEqual(1200, times[peak_idx], delta=100)
        self.assertAlmostEqual(2.0, np.max(flows), delta=0.1)
        self.assertAlmostEqual(0.0, flows[0])
        self.assertAlmostEqual(0.0, flows[-1])

    def test_zero_at_edges(self):
        ts = make_uniform_overflow_ts(
            ['A'], start_time=0, end_time=1000,
            peak_rate=1.0, peak_time=500, duration_ratio=0.2,
        )
        times, flows = ts['A']
        self.assertAlmostEqual(0.0, flows[0], delta=1e-10)
        self.assertAlmostEqual(0.0, flows[-1], delta=1e-10)


class TestCoupling(unittest.TestCase):
    """Test SWMM overflow -> 2D grid coupling."""

    def setUp(self):
        self.grid = generate_synthetic_dem(30, 30, 10.0, [
            {'type': 'flat', 'params': {'elevation': 10.0}},
            {'type': 'bowl', 'params': {
                'center': (15, 15), 'radius': 10, 'depth': 2.0,
            }},
        ])
        self.grid.set_boundary_open('all')

    def test_node_to_cell_mapping(self):
        node_xy = {'MH01': (15, 15), 'MH02': (5, 5)}
        ts = make_uniform_overflow_ts(
            ['MH01', 'MH02'], 0, 3600, peak_rate=1.0)
        coupling = SwmmOverflowSource(self.grid, node_xy, ts)
        self.assertIn('MH01', coupling.node_cell)
        self.assertIn('MH02', coupling.node_cell)
        self.assertEqual((15, 15), coupling.node_cell['MH01'])

    def test_source_at_peak_time(self):
        node_xy = {'MH01': (15, 15)}
        ts = make_uniform_overflow_ts(
            ['MH01'], 0, 3600, peak_rate=2.0, peak_time=1200)
        coupling = SwmmOverflowSource(self.grid, node_xy, ts)

        src = coupling.get_source_at_time(1200)
        self.assertGreater(src[15, 15], 0)
        self.assertAlmostEqual(0.0, src[0, 0])

        expected_rate = 2.0 / self.grid.cell_area
        self.assertAlmostEqual(expected_rate, src[15, 15], delta=expected_rate * 0.02)

    def test_source_zero_when_no_overflow(self):
        node_xy = {'MH01': (15, 15)}
        ts = make_uniform_overflow_ts(
            ['MH01'], 0, 3600, peak_rate=1.0, peak_time=1200,
            duration_ratio=0.2)
        coupling = SwmmOverflowSource(self.grid, node_xy, ts)

        src0 = coupling.get_source_at_time(0)
        self.assertAlmostEqual(0.0, src0[15, 15])

        src_end = coupling.get_source_at_time(3500)
        self.assertAlmostEqual(0.0, src_end[15, 15])


class TestSolverPhysics(unittest.TestCase):
    """Test basic physics of the 2D solver."""

    def test_water_flows_downhill(self):
        """Water on a slope should flow from high to low elevation."""
        grid = generate_synthetic_dem(30, 30, 10.0, [
            {'type': 'slope', 'params': {
                'direction': 'x', 'gradient': 0.01, 'base_elevation': 10.0,
            }},
        ])
        grid.set_boundary_open('all')

        # Place initial water on the high side (right, high col index = high z)
        grid.d[14:17, 20:23] = 0.5

        solver = BatesSolver(grid, theta=0.3)
        # Center of mass of water in column direction
        initial_com = np.sum(grid.d * np.arange(grid.cols)) / max(np.sum(grid.d), 1e-10)

        for _ in range(120):
            solver.step()

        final_com = np.sum(grid.d * np.arange(grid.cols)) / max(np.sum(grid.d), 1e-10)
        # With positive x-gradient, z increases with col index.
        # Water at high col should flow toward low col (downhill = leftward).
        self.assertLess(final_com, initial_com,
                        f"Water should flow downhill (leftward on x-slope): "
                        f"COM {initial_com:.2f} -> {final_com:.2f}")

    def test_water_accumulates_in_bowl(self):
        """Water injected at the edge of a bowl should flow to the center."""
        grid = generate_synthetic_dem(50, 50, 5.0, [
            {'type': 'flat', 'params': {'elevation': 10.0}},
            {'type': 'bowl', 'params': {
                'center': (25, 25), 'radius': 20, 'depth': 3.0,
            }},
        ])
        grid.set_boundary_open('all')

        # Inject water at bowl edge (east side)
        grid.d[25, 35] = 0.3

        solver = BatesSolver(grid, theta=0.5)
        for _ in range(100):
            solver.step()

        # Center should now have water
        self.assertGreater(grid.d[25, 25], 0.0,
                           "Water should reach the bowl center")
        # Edge injection point should have less water than initial
        self.assertGreater(0.3, grid.d[25, 35],
                           "Water should drain from injection point")

    def test_no_flow_on_flat_dry(self):
        """No flow should occur on flat terrain with minimal water below threshold."""
        grid = generate_synthetic_dem(20, 20, 10.0, [
            {'type': 'flat', 'params': {'elevation': 10.0}},
        ])
        grid.set_boundary_open('all')

        grid.d[10, 10] = 1e-7

        solver = BatesSolver(grid, theta=0.5)
        d_before = grid.d[10, 10]
        solver.step()
        # Depth should remain essentially unchanged (below flow threshold)
        self.assertAlmostEqual(d_before, grid.d[10, 10], delta=1e-8)


class TestMassConservation(unittest.TestCase):
    """Test that mass is conserved with closed boundaries."""

    def test_mass_conservation_closed_boundary(self):
        """With all walls, mass should be conserved (no source)."""
        grid = generate_synthetic_dem(30, 30, 10.0, [
            {'type': 'flat', 'params': {'elevation': 10.0}},
        ])
        # Default WALL edges + INTERIOR cells

        initial_water = 0.1  # m
        grid.d[10:20, 10:20] = initial_water
        initial_volume = grid.water_volume

        solver = BatesSolver(grid, theta=0.5)
        for _ in range(20):
            solver.step()

        final_volume = grid.water_volume
        # Water spreads but stays in domain; small loss from wall cells at edges
        # of the initial patch touching domain walls, or depth below threshold
        self.assertAlmostEqual(initial_volume, final_volume,
                               delta=initial_volume * 0.02,
                               msg=f"Mass not conserved: {initial_volume:.6f} -> {final_volume:.6f}")

    def test_mass_conservation_with_source(self):
        """Volume added via source should accumulate in the domain."""
        grid = generate_synthetic_dem(30, 30, 10.0, [
            {'type': 'flat', 'params': {'elevation': 10.0}},
            {'type': 'bowl', 'params': {
                'center': (15, 15), 'radius': 12, 'depth': 2.0,
            }},
        ])
        grid.set_boundary_open('all')

        # Constant source at bowl center — water will accumulate in bowl
        source_rate = 0.01  # m/s
        sources = np.zeros_like(grid.d)
        sources[15, 15] = source_rate

        solver = BatesSolver(grid, theta=0.5)
        total_time = 0.0
        for _ in range(200):
            dt = solver.step(sources)
            total_time += dt

        expected_volume = source_rate * grid.cell_area * total_time
        actual_volume = grid.water_volume

        # Bowl retains water; some may escape through open boundaries
        self.assertGreater(actual_volume, expected_volume * 0.3,
                           "Too much water lost through open boundaries")
        self.assertLessEqual(actual_volume, expected_volume * 1.01)


class TestCFLStability(unittest.TestCase):
    """Test CFL condition and solver stability."""

    def test_stable_dt_positive(self):
        grid = generate_synthetic_dem(30, 30, 10.0, [
            {'type': 'flat', 'params': {'elevation': 10.0}},
        ])
        grid.d[15, 15] = 0.5
        solver = BatesSolver(grid, theta=0.5)
        dt = solver.compute_stable_dt()
        self.assertGreater(dt, 0)
        # dx=10, h=0.5: dt ~ 0.5*10/sqrt(9.81*0.5) ≈ 2.25s
        self.assertGreater(dt, 0.1)
        self.assertLess(dt, 20.0)

    def test_no_nan_with_high_depth(self):
        grid = generate_synthetic_dem(30, 30, 10.0, [
            {'type': 'flat', 'params': {'elevation': 10.0}},
            {'type': 'bowl', 'params': {
                'center': (15, 15), 'radius': 10, 'depth': 2.0,
            }},
        ])
        grid.set_boundary_open('all')
        grid.d[13:18, 13:18] = 1.0

        solver = BatesSolver(grid, theta=0.5)
        for _ in range(50):
            solver.step()

        self.assertFalse(np.any(np.isnan(grid.d)), "NaN values in depth")
        self.assertFalse(np.any(np.isinf(grid.d)), "Inf values in depth")
        self.assertTrue(np.all(grid.d >= 0), "Negative depths detected")

    def test_stability_with_varying_theta(self):
        for theta in [0.3, 0.5, 0.7]:
            grid = generate_synthetic_dem(30, 30, 10.0, [
                {'type': 'flat', 'params': {'elevation': 10.0}},
                {'type': 'bowl', 'params': {
                    'center': (15, 15), 'radius': 10, 'depth': 2.0,
                }},
            ])
            grid.set_boundary_open('all')
            grid.d[15, 15] = 0.8

            solver = BatesSolver(grid, theta=theta)
            for _ in range(30):
                solver.step()

            self.assertFalse(np.any(np.isnan(grid.d)),
                             f"NaN at theta={theta}")


class TestIntegrationBowlFlooding(unittest.TestCase):
    """End-to-end test: overflow into a bowl depression."""

    def test_bowl_flooding_scenario(self):
        """SWMM node overflows into a bowl depression. Water accumulates."""
        rows, cols = 50, 50
        dx = 10.0
        grid = generate_synthetic_dem(rows, cols, dx, [
            {'type': 'flat', 'params': {'elevation': 10.0}},
            {'type': 'bowl', 'params': {
                'center': (25, 25), 'radius': 20, 'depth': 3.0,
            }},
        ])
        grid.set_boundary_open('all')

        # Place overflow node at bowl center
        node_xy = {'MH_overflow': (25, 25)}
        overflow_ts = make_uniform_overflow_ts(
            ['MH_overflow'],
            start_time=0, end_time=1800,
            peak_rate=2.0, peak_time=600, duration_ratio=0.5,
        )

        coupling = SwmmOverflowSource(grid, node_xy, overflow_ts)
        solver = BatesSolver(grid, theta=0.6)

        def src_func(t):
            return coupling.get_source_at_time(t)

        results = solver.run(1800, sources_func=src_func,
                             progress_interval=300)

        stats = compute_flood_statistics(grid, grid.d)

        # Water should be present
        self.assertGreater(stats['water_volume_m3'], 0,
                           "No water volume after flooding simulation")
        self.assertGreater(stats['n_cells_flooded'], 0,
                           "No cells flooded")

        # Deepest water should be near the overflow point
        max_idx = np.unravel_index(np.argmax(grid.d), grid.d.shape)
        dist_to_center = np.sqrt((max_idx[0] - 25)**2 + (max_idx[1] - 25)**2)
        self.assertLess(dist_to_center, 10,
                        f"Max depth at {max_idx}, expected near (25,25)")

        # Corners should be dry
        self.assertAlmostEqual(0.0, grid.d[0, 0], delta=DEPTH_MIN * 10)
        self.assertAlmostEqual(0.0, grid.d[0, -1], delta=DEPTH_MIN * 10)

        # Volume check against total input
        times, flows = overflow_ts['MH_overflow']
        # NumPy 2.x renamed trapz; retain compatibility with NumPy 1.21+.
        trapezoid = np.trapezoid if hasattr(np, 'trapezoid') else np.trapz
        total_overflow = trapezoid(flows, times)
        print(f"\n  Total overflow: {total_overflow:.1f} m3")
        print(f"  Surface water:  {stats['water_volume_m3']:.1f} m3")
        print(f"  Max depth:      {stats['max_depth_m']:.3f} m")
        print(f"  Flooded area:   {stats['flooded_area_ha']:.3f} ha")
        print(f"  Flooded cells:  {stats['n_cells_flooded']}")

        self.assertGreater(stats['water_volume_m3'], total_overflow * 0.3,
                           "Too little water retained in domain")
        self.assertGreater(len(results['times']), 0)
        self.assertGreater(len(results['water_volume']), 0)

    def test_multi_node_overflow(self):
        """Multiple nodes overflowing simultaneously."""
        rows, cols = 40, 40
        grid = generate_synthetic_dem(rows, cols, 10.0, [
            {'type': 'flat', 'params': {'elevation': 10.0}},
            {'type': 'bowl', 'params': {
                'center': (20, 20), 'radius': 15, 'depth': 2.0,
            }},
        ])
        grid.set_boundary_open('all')

        node_xy = {
            'N_left': (20, 10),
            'N_top': (10, 20),
            'N_center': (20, 20),
        }
        overflow_ts = make_uniform_overflow_ts(
            list(node_xy.keys()),
            start_time=0, end_time=1200,
            peak_rate=0.5, peak_time=400, duration_ratio=0.5,
        )

        coupling = SwmmOverflowSource(grid, node_xy, overflow_ts)
        solver = BatesSolver(grid, theta=0.5)

        def src_func(t):
            return coupling.get_source_at_time(t)

        solver.run(1200, sources_func=src_func, progress_interval=600)

        stats = compute_flood_statistics(grid, grid.d)
        self.assertGreater(stats['water_volume_m3'], 0)
        # At least some injection area should have water
        self.assertGreater(grid.d[10, 20] + grid.d[20, 10] + grid.d[20, 20], 0)


class TestRptParser(unittest.TestCase):
    """Test SWMM .rpt parsing."""

    def test_parse_empty_rpt(self):
        """Parse a minimal valid rpt with no flooding."""
        fd, fpath = tempfile.mkstemp(suffix='.rpt')
        os.close(fd)
        try:
            with open(fpath, 'w') as f:
                f.write("""
  EPA STORM WATER MANAGEMENT MODEL - VERSION 5.2 (Build 5.2.4)
  -----------------------------------------------------------

  ****************
  No flooding occurred.
  ****************
""")
            result = parse_swmm_rpt_overflow(fpath)
            self.assertEqual({}, result)
        finally:
            os.unlink(fpath)

    def test_parse_flooding_summary(self):
        """Parse rpt with a flooding summary section."""
        fd, fpath = tempfile.mkstemp(suffix='.rpt')
        os.close(fd)
        try:
            with open(fpath, 'w') as f:
                f.write("""
  ****************************
  Node Flooding Summary
  ****************************
  --------------------------------------------
    Node    Max Rate   Day of    Time of    Total     Maximum
             (CMS)    Max Rate  Max Rate   Volume     Ponded
  --------------------------------------------
    J10       0.523     0.15     03:30      45.2      0.30
    J25       1.201     0.20     04:42     102.8      0.50
    J33       0.089     0.10     02:15       7.3      0.10
  --------------------------------------------
""")
            result = parse_swmm_rpt_overflow(fpath)
            self.assertIn('J10', result)
            self.assertIn('J25', result)
            self.assertIn('J33', result)
            self.assertAlmostEqual(0.523, result['J10']['max_rate'])
            self.assertAlmostEqual(45.2, result['J10']['total_volume'])
        finally:
            os.unlink(fpath)


if __name__ == '__main__':
    unittest.main(verbosity=2)
