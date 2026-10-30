"""
I/O utilities for the 2D flood model: save depth rasters, export to GeoTIFF,
generate summary reports.
"""

import numpy as np


def save_depth_ascii(grid, filepath):
    """
    Save current water depth as ASCII grid (.asc) file.

    Parameters
    ----------
    grid : Grid2D
        The grid with current depth state.
    filepath : str
        Output file path.
    """
    header = (
        f"ncols         {grid.cols}\n"
        f"nrows         {grid.rows}\n"
        f"xllcorner     0.0\n"
        f"yllcorner     0.0\n"
        f"cellsize      {grid.dx}\n"
        f"NODATA_value  -9999\n"
    )
    data = grid.d.copy()
    data[data < 1e-6] = -9999
    np.savetxt(filepath, data, fmt='%.4f', header=header.rstrip('\n'),
               comments='')


def save_depth_geotiff(grid, filepath):
    """Save current water depth as GeoTIFF (requires rasterio/gdal)."""
    try:
        import rasterio
        transform = rasterio.transform.from_origin(
            0, 0, grid.dx, grid.dy)
        with rasterio.open(
            filepath, 'w',
            driver='GTiff',
            height=grid.rows,
            width=grid.cols,
            count=1,
            dtype='float32',
            crs='EPSG:4326',
            transform=transform,
        ) as dst:
            dst.write(grid.d.astype(np.float32), 1)
    except ImportError:
        raise ImportError(
            "rasterio is required for GeoTIFF export. "
            "Install with: pip install rasterio"
        )


def save_max_depth_ascii(grid, max_depth, filepath):
    """Save maximum water depth envelope as ASCII grid."""
    header = (
        f"ncols         {grid.cols}\n"
        f"nrows         {grid.rows}\n"
        f"xllcorner     0.0\n"
        f"yllcorner     0.0\n"
        f"cellsize      {grid.dx}\n"
        f"NODATA_value  -9999\n"
    )
    data = max_depth.copy()
    data[data < 1e-6] = -9999
    np.savetxt(filepath, data, fmt='%.4f', header=header.rstrip('\n'),
               comments='')


def compute_flood_statistics(grid, max_depth):
    """
    Compute flood summary statistics.

    Returns
    -------
    dict with keys:
        max_depth: deepest point (m)
        mean_flood_depth: average depth in flooded cells (m)
        flooded_area_m2: area where depth > threshold (m2)
        flooded_area_ha: flooded area in hectares
        water_volume_m3: total water volume (m3)
        pct_area_flooded: percentage of domain flooded
    """
    flooded = grid.d > 0.01  # 1 cm threshold
    flooded_max = max_depth > 0.01

    area = grid.cell_area
    flooded_cells = int(np.sum(flooded_max))
    flooded_area = flooded_cells * area

    return {
        'max_depth_m': float(np.max(max_depth)),
        'mean_flood_depth_m': float(np.mean(max_depth[flooded_max]))
                              if flooded_cells > 0 else 0.0,
        'flooded_area_m2': flooded_area,
        'flooded_area_ha': flooded_area / 10000.0,
        'water_volume_m3': float(np.sum(grid.d[flooded]) * area),
        'pct_area_flooded': 100.0 * flooded_cells / (grid.rows * grid.cols),
        'n_cells_flooded': flooded_cells,
        'n_cells_total': grid.rows * grid.cols,
    }


def print_flood_summary(stats):
    """Print flood statistics in a formatted block."""
    print("=" * 50)
    print("  2D Flood Simulation Results")
    print("=" * 50)
    print(f"  Max flood depth:        {stats['max_depth_m']:.3f} m")
    print(f"  Mean flood depth:       {stats['mean_flood_depth_m']:.3f} m")
    print(f"  Flooded area:           {stats['flooded_area_m2']:.1f} m2 "
          f"({stats['flooded_area_ha']:.2f} ha)")
    print(f"  Water volume on surface:{stats['water_volume_m3']:.1f} m3")
    print(f"  Cells flooded:          {stats['n_cells_flooded']} / "
          f"{stats['n_cells_total']} "
          f"({stats['pct_area_flooded']:.1f}%)")
    print("=" * 50)
