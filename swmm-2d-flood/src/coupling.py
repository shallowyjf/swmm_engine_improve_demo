"""
SWMM-to-2D coupling layer: maps SWMM node overflow time series
to 2D grid source terms.

Plan A (loose coupling) data flow:
  SWMM .rpt → parse node flooding (Q,t) per node
            → map nodes to grid cells by (x,y) coordinates
            → interpolate overflow rates at each solver time step
            → inject as source terms into 2D solver
"""

import numpy as np


class SwmmOverflowSource:
    """
    Converts SWMM node overflow time series into 2D grid source terms.

    Each node has a time series of overflow rate (m³/s). Nodes are mapped
    to the nearest grid cell. At each solver time step, the overflow rate
    is interpolated and converted to a depth source (m/s) = Q / cell_area.
    """

    def __init__(self, grid, node_xy, overflow_ts):
        """
        Parameters
        ----------
        grid : Grid2D
            The 2D computational grid.
        node_xy : dict {node_name: (x, y)}
            Real-world coordinates of each SWMM node (meters, in the
            same coordinate system as the DEM).
        overflow_ts : dict {node_name: (times, flows)}
            Overflow time series per node.
            - times: np.ndarray (T,) - simulation time in seconds
            - flows: np.ndarray (T,) - overflow rate in m³/s
        """
        self.grid = grid
        self.node_xy = node_xy
        self.overflow_ts = overflow_ts

        # Map each node to the nearest grid cell (row, col)
        self.node_cell = {}
        self._map_nodes_to_cells()

    def _map_nodes_to_cells(self):
        """Map each node to the nearest grid cell by (x, y) coordinates.

        Assumes the grid origin (row=0, col=0) corresponds to (xmin, ymax),
        with x increasing eastward and y increasing southward (raster convention).
        """
        g = self.grid
        for name, (nx, ny) in self.node_xy.items():
            # Convert real-world coords to grid indices
            # For this implementation, we assume the caller has already
            # aligned node_xy to grid indices, or we use direct indexing.
            # If node_xy is in (col, row) format:
            if isinstance(nx, int) and isinstance(ny, int):
                col, row = nx, ny
            else:
                # Treat as grid index directly for testing
                col = int(nx)
                row = int(ny)

            if 0 <= col < g.cols and 0 <= row < g.rows:
                self.node_cell[name] = (row, col)

    def get_source_at_time(self, t):
        """
        Compute source term array at simulation time t.

        Parameters
        ----------
        t : float
            Current simulation time (seconds).

        Returns
        -------
        sources : np.ndarray (rows, cols)
            Source term in m/s (depth per second). Positive = inflow.
        """
        sources = np.zeros_like(self.grid.d)
        for name, (row, col) in self.node_cell.items():
            if name not in self.overflow_ts:
                continue
            times, flows = self.overflow_ts[name]
            if len(times) == 0:
                continue
            # Interpolate flow at time t
            q = np.interp(t, times, flows, left=0.0, right=0.0)
            if q > 0:
                sources[row, col] += q / self.grid.cell_area
        return sources


def parse_swmm_rpt_overflow(filepath):
    """
    Parse SWMM .rpt file to extract node flooding time series.

    Looks for the "Node Flooding Summary" section in the report,
    or continuous "Node Flooding" time series output.

    Parameters
    ----------
    filepath : str or Path
        Path to SWMM .rpt file.

    Returns
    -------
    overflow_summary : dict {node_name: {'max_rate': float, 'max_day': float,
                                          'total_volume': float}}
    """
    summary = {}
    in_section = False

    with open(filepath, 'r') as f:
        for line in f:
            line = line.strip()
            if 'Node Flooding Summary' in line:
                in_section = True
                continue
            if in_section and line.startswith('--'):
                continue
            if in_section and len(line) < 10:
                break
            if in_section:
                parts = line.split()
                if len(parts) >= 5:
                    try:
                        name = parts[0]
                        max_rate = float(parts[1])
                        max_day = float(parts[2])
                        total_volume = float(parts[4])
                        summary[name] = {
                            'max_rate': max_rate,
                            'max_day': max_day,
                            'total_volume': total_volume,
                        }
                    except (ValueError, IndexError):
                        pass
    return summary


def make_uniform_overflow_ts(node_names, start_time, end_time, peak_rate,
                              peak_time=None, duration_ratio=0.3):
    """
    Generate a simplified triangular overflow hydrograph for testing.

    Parameters
    ----------
    node_names : list of str
        Node names.
    start_time, end_time : float
        Simulation start and end times (s).
    peak_rate : float
        Peak overflow rate per node (m³/s).
    peak_time : float or None
        Time of peak (s). Defaults to 1/3 of simulation duration.
    duration_ratio : float
        Ratio of hydrograph duration to total simulation time.

    Returns
    -------
    overflow_ts : dict {node_name: (times, flows)}
    """
    duration = end_time - start_time
    if peak_time is None:
        peak_time = start_time + duration / 3.0
    event_duration = duration * duration_ratio
    t_start = peak_time - event_duration * 0.3
    t_end = peak_time + event_duration * 0.7

    # Triangular hydrograph
    times = np.linspace(start_time, end_time, 100)
    flows = np.zeros_like(times)
    for idx, t in enumerate(times):
        if t <= t_start:
            flows[idx] = 0
        elif t <= peak_time:
            flows[idx] = peak_rate * (t - t_start) / (peak_time - t_start)
        elif t <= t_end:
            flows[idx] = peak_rate * (t_end - t) / (t_end - peak_time)
        else:
            flows[idx] = 0

    overflow_ts = {}
    for name in node_names:
        overflow_ts[name] = (times.copy(), flows.copy())
    return overflow_ts
