"""
Simplified 2D shallow water solver (diffusive wave / simplified inertial).

Based on Bates et al. (2010) "A simple inertial formulation of the shallow
water equations for efficient two-dimensional flood inundation modelling."
Journal of Hydrology, 387(1-2), 33-45.

Key equations per cell face:
  S_f = (ws_left - ws_right) / dx    -- water surface slope (>0 drives left->right)
  h_flow = max(ws_i, ws_j) - max(z_i, z_j)
  q = sign(S_f) * h_flow^(5/3) / n * sqrt(|S_f|)   (per unit width)

where q > 0 means flow from left/top cell to right/bottom cell.
"""

import numpy as np
from .grid import DEPTH_MIN, G, BC_OPEN, BC_WALL, BC_INTERIOR

MAX_DT = 30.0
MIN_H_FOR_CFL = 0.01
MAX_DEPTH = 100.0


class BatesSolver:
    """Simplified inertial (diffusive wave) 2D flood solver."""

    def __init__(self, grid, theta=0.5, depth_lim=1e-4):
        self.grid = grid
        self.theta = theta
        self.depth_lim = depth_lim

        self._nx = np.zeros((grid.rows, grid.cols - 1), dtype=np.float64)
        self._ny = np.zeros((grid.rows - 1, grid.cols), dtype=np.float64)
        self._compute_face_roughness()

        self._wall_x = np.zeros((grid.rows, grid.cols - 1), dtype=bool)
        self._wall_y = np.zeros((grid.rows - 1, grid.cols), dtype=bool)
        self._update_wall_masks()

        self.step_count = 0
        self.sim_time = 0.0

    def _compute_face_roughness(self):
        g = self.grid
        self._nx = 2.0 / (1.0 / g.n[:, :-1] + 1.0 / g.n[:, 1:])
        self._ny = 2.0 / (1.0 / g.n[:-1, :] + 1.0 / g.n[1:, :])

    def _update_wall_masks(self):
        g = self.grid
        self._wall_x = (g.bc[:, :-1] == BC_WALL) | (g.bc[:, 1:] == BC_WALL)
        self._wall_y = (g.bc[:-1, :] == BC_WALL) | (g.bc[1:, :] == BC_WALL)

    def _flux_dt_limit(self, q_x, q_y):
        """Compute time step limit so no cell drains beyond its volume in one step.

        For each cell, total outflow rate = sum of outflow through all 4 faces.
        dt must be ≤ available_volume / total_outflow_rate.
        """
        g = self.grid
        # Outflow from each cell (m³/s) through each of 4 faces
        out = np.zeros_like(g.d)

        # Right face: q_x[i,j] > 0 → outflow from cell (i,j) to right
        out[:, :-1] += np.maximum(0, q_x) * g.dy
        # Left face: q_x[i,j-1] < 0 → outflow from cell (i,j) to left
        out[:, 1:] += np.maximum(0, -q_x) * g.dy
        # Bottom face: q_y[i,j] > 0 → outflow from cell (i,j) downward
        out[:-1, :] += np.maximum(0, q_y) * g.dx
        # Top face: q_y[i-1,j] < 0 → outflow from cell (i,j) upward
        out[1:, :] += np.maximum(0, -q_y) * g.dx

        # Available volume per cell
        avail = g.d * g.cell_area

        # Where outflow > 0, compute max dt
        mask = out > 0
        if not np.any(mask):
            return MAX_DT
        dt_lim = np.min(avail[mask] / out[mask])
        return max(dt_lim, 0.001)  # floor at 1ms

    def compute_stable_dt(self):
        g = self.grid
        h_max = max(float(np.max(g.d)), MIN_H_FOR_CFL)
        dt_cfl = self.theta * min(g.dx, g.dy) / np.sqrt(G * h_max)
        return min(dt_cfl, MAX_DT)

    def step(self, sources=None):
        """Advance one time step. Returns dt used."""
        g = self.grid
        ws = g.water_surface

        # ---- X-direction fluxes ----
        s_x = (ws[:, :-1] - ws[:, 1:]) / g.dx  # + = flow left->right

        z_max_x = np.maximum(g.z[:, :-1], g.z[:, 1:])
        ws_max_x = np.maximum(ws[:, :-1], ws[:, 1:])
        h_flow_x = ws_max_x - z_max_x

        q_x = np.zeros_like(s_x)
        wet = (h_flow_x > self.depth_lim) & ~self._wall_x
        h_safe = np.clip(h_flow_x[wet], 0, MAX_DEPTH)
        s_abs = np.clip(np.abs(s_x[wet]), 1e-20, 1e10)
        q_x[wet] = np.sign(s_x[wet]) * (h_safe ** (5.0 / 3.0)) / self._nx[wet] * np.sqrt(s_abs)
        q_x = np.clip(q_x, -1e4, 1e4)

        # ---- Y-direction fluxes ----
        s_y = (ws[:-1, :] - ws[1:, :]) / g.dy

        z_max_y = np.maximum(g.z[:-1, :], g.z[1:, :])
        ws_max_y = np.maximum(ws[:-1, :], ws[1:, :])
        h_flow_y = ws_max_y - z_max_y

        q_y = np.zeros_like(s_y)
        wet = (h_flow_y > self.depth_lim) & ~self._wall_y
        h_safe = np.clip(h_flow_y[wet], 0, MAX_DEPTH)
        s_abs = np.clip(np.abs(s_y[wet]), 1e-20, 1e10)
        q_y[wet] = np.sign(s_y[wet]) * (h_safe ** (5.0 / 3.0)) / self._ny[wet] * np.sqrt(s_abs)
        q_y = np.clip(q_y, -1e4, 1e4)

        # ---- Adaptive time step with flux-based constraint ----
        dt_cfl = self.compute_stable_dt()
        dt_flux = self._flux_dt_limit(q_x, q_y)
        dt = min(dt_cfl, dt_flux)

        # ---- Continuity ----
        dqdx = np.zeros_like(g.d)
        dqdx[:, 1:-1] = (q_x[:, :-1] - q_x[:, 1:]) * g.dy
        dqdx[:, 0] = (0 - q_x[:, 0]) * g.dy
        dqdx[:, -1] = (q_x[:, -1] - 0) * g.dy

        dqdy = np.zeros_like(g.d)
        dqdy[1:-1, :] = (q_y[:-1, :] - q_y[1:, :]) * g.dx
        dqdy[0, :] = (0 - q_y[0, :]) * g.dx
        dqdy[-1, :] = (q_y[-1, :] - 0) * g.dx

        dh = dt * (dqdx + dqdy) / g.cell_area
        if sources is not None:
            dh += dt * sources

        g.d += dh
        g.d = np.clip(g.d, 0, MAX_DEPTH)
        g.d[g.bc == BC_WALL] = 0.0

        self.step_count += 1
        self.sim_time += dt
        return dt

    def run(self, duration, sources_func=None, progress_interval=60.0):
        g = self.grid
        results = {'times': [], 'max_depth': [], 'flooded_cells': [],
                    'water_volume': [], 'dt_history': []}
        next_report = progress_interval
        t = 0.0

        while t < duration:
            src = sources_func(t) if sources_func else None
            dt = self.step(src)
            t += dt

            if t >= next_report:
                results['times'].append(t)
                results['max_depth'].append(g.max_depth)
                results['flooded_cells'].append(g.flooded_cells)
                results['water_volume'].append(g.water_volume)
                results['dt_history'].append(dt)
                next_report += progress_interval

        return results
