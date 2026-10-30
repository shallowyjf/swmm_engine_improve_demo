"""
2D regular raster grid data structure for surface flood modeling.

Represents a DEM-aligned grid where each cell has:
- z: bed elevation (from DEM)
- d: current water depth
- n: Manning's roughness coefficient
- bc: boundary condition type (INTERIOR for regular cells, OPEN/WALL for edges)
"""

import numpy as np

BC_INTERIOR = -1   # interior domain cell (not a boundary)
BC_OPEN = 0        # open boundary (water can flow out freely)
BC_WALL = 1        # wall boundary (no flow at domain edge)
BC_CRITICAL = 2    # critical flow boundary (weir/normal depth)

# Minimum water depth threshold (m) below which flow is considered zero
DEPTH_MIN = 1e-6
# Gravity (m/s^2)
G = 9.81


class Grid2D:
    """2D regular raster grid for shallow water modeling."""

    def __init__(self, dem, dx, dy=None, manning_n=0.03):
        """
        Parameters
        ----------
        dem : np.ndarray (rows, cols)
            Digital Elevation Model - bed elevation per cell (m).
        dx : float
            Cell size in x-direction (m).
        dy : float or None
            Cell size in y-direction (m). Defaults to dx if None.
        manning_n : float or np.ndarray
            Manning's roughness coefficient. Can be uniform or per-cell.
        """
        self.z = np.asarray(dem, dtype=np.float64)
        self.rows, self.cols = self.z.shape
        self.dx = dx
        self.dy = dy if dy is not None else dx

        # Water depth (m)
        self.d = np.zeros_like(self.z, dtype=np.float64)
        # Manning's n
        if np.isscalar(manning_n):
            self.n = np.full_like(self.z, manning_n, dtype=np.float64)
        else:
            self.n = np.asarray(manning_n, dtype=np.float64)

        # Boundary condition: all interior by default, edges default to WALL
        self.bc = np.full_like(self.z, BC_INTERIOR, dtype=np.int32)
        self._set_edge_wall()

        # Pre-compute cell area
        self.cell_area = self.dx * self.dy

    def _set_edge_wall(self):
        """Set all domain edges to WALL boundary."""
        self.bc[0, :] = BC_WALL     # top row
        self.bc[-1, :] = BC_WALL    # bottom row
        self.bc[:, 0] = BC_WALL     # left column
        self.bc[:, -1] = BC_WALL    # right column

    @property
    def water_surface(self):
        """Water surface elevation: z + d."""
        return self.z + self.d

    def set_boundary_open(self, edge='all'):
        """Set boundary condition to OPEN on grid edges."""
        if edge in ('all', 'left'):
            self.bc[:, 0] = BC_OPEN
        if edge in ('all', 'right'):
            self.bc[:, -1] = BC_OPEN
        if edge in ('all', 'top'):
            self.bc[0, :] = BC_OPEN
        if edge in ('all', 'bottom'):
            self.bc[-1, :] = BC_OPEN

    def is_wet(self):
        """Return boolean mask of cells with water above threshold."""
        return self.d > DEPTH_MIN

    @property
    def water_volume(self):
        """Total water volume in the domain (m^3)."""
        return float(np.sum(self.d[self.d > DEPTH_MIN]) * self.cell_area)

    @property
    def max_depth(self):
        """Maximum water depth in the domain."""
        return float(np.max(self.d))

    @property
    def flooded_cells(self):
        """Number of cells with water depth > DEPTH_MIN."""
        return int(np.sum(self.is_wet()))


def generate_synthetic_dem(rows, cols, dx, features=None):
    """
    Generate a synthetic DEM for testing.

    Parameters
    ----------
    rows, cols : int
        Grid dimensions.
    dx : float
        Cell size (m).
    features : list of dict, optional
        Terrain features. Each dict has:
        - 'type': 'bowl' | 'channel' | 'slope' | 'flat'
        - params dict specific to type.
        Features are applied in order; flat sets a uniform base elevation,
        bowl/channel subtract from it.

    Returns
    -------
    Grid2D instance.
    """
    dem = np.zeros((rows, cols), dtype=np.float64)

    if features is None:
        features = [{'type': 'bowl', 'params': {'center': (rows // 2, cols // 2),
                                                  'radius': min(rows, cols) // 3,
                                                  'depth': 2.0}}]

    for feat in features:
        if feat['type'] == 'flat':
            elev = feat['params'].get('elevation', 10.0)
            dem = np.full((rows, cols), elev, dtype=np.float64)

        elif feat['type'] == 'slope':
            direction = feat['params'].get('direction', 'x')
            gradient = feat['params'].get('gradient', 0.01)
            base = feat['params'].get('base_elevation', 0.0)
            if direction == 'x':
                for j in range(cols):
                    dem[:, j] = base + gradient * j * dx
            else:
                for i in range(rows):
                    dem[i, :] = base + gradient * i * dx

        elif feat['type'] == 'bowl':
            cy, cx = feat['params']['center']
            radius = feat['params']['radius']
            depth = feat['params']['depth']
            for i in range(rows):
                for j in range(cols):
                    dist = np.sqrt(((i - cy) * dx) ** 2 + ((j - cx) * dx) ** 2)
                    if dist < radius:
                        dem[i, j] -= depth * (1 - (dist / radius) ** 2)

        elif feat['type'] == 'channel':
            from_i = feat['params'].get('from', (0, cols // 2))
            to_i = feat['params'].get('to', (rows - 1, cols // 2))
            width = feat['params'].get('width', 3)  # cells
            depth_c = feat['params'].get('depth', 1.0)
            r0, c0 = from_i
            r1, c1 = to_i
            for i in range(rows):
                for j in range(cols):
                    dist_to_line = _distance_to_line(i, j, r0, c0, r1, c1)
                    if dist_to_line < width:
                        dem[i, j] -= depth_c * (1 - (dist_to_line / width))

    # Ensure non-negative elevations
    if dem.min() < 0:
        dem -= dem.min()
    if dem.min() < 1.0:
        dem += 10.0

    return Grid2D(dem, dx)


def _distance_to_line(i, j, r0, c0, r1, c1):
    """Distance from point (i,j) to line segment (r0,c0)-(r1,c1)."""
    dx_seg = c1 - c0
    dy_seg = r1 - r0
    if dx_seg == 0 and dy_seg == 0:
        return np.sqrt((i - r0) ** 2 + (j - c0) ** 2)
    t = max(0, min(1, ((j - c0) * dx_seg + (i - r0) * dy_seg) /
                          (dx_seg * dx_seg + dy_seg * dy_seg)))
    proj_c = c0 + t * dx_seg
    proj_r = r0 + t * dy_seg
    return np.sqrt((j - proj_c) ** 2 + (i - proj_r) ** 2)


def load_dem_ascii(filepath):
    """
    Load DEM from ASCII grid format (.asc).

    Expected header:
    ncols N
    nrows M
    xllcorner X
    yllcorner Y
    cellsize S
    NODATA_value V
    """
    with open(filepath, 'r') as f:
        header = {}
        for _ in range(6):
            key, val = f.readline().strip().split()
            header[key.lower()] = float(val) if key.lower() not in ('ncols', 'nrows') else int(val)

        data = np.loadtxt(f, dtype=np.float64)

    nodata = header.get('nodata_value', -9999)
    data[data == nodata] = np.nan

    return Grid2D(data, header['cellsize']), header
