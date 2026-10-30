from __future__ import annotations

import argparse
import json
import math
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


BASE_DIR = Path(__file__).resolve().parent
ENGINE_DIR = BASE_DIR.parent / "swmm524_engine"
EXE_NAME = "runswmm.exe" if os.name == "nt" else "runswmm"
EXE_CANDIDATES = [
    ENGINE_DIR / "build_vs" / "Release" / EXE_NAME,
    ENGINE_DIR / "build" / "Release" / EXE_NAME,
    ENGINE_DIR / "build" / EXE_NAME,
]
SWMM_EXE = next((path for path in EXE_CANDIDATES if path.is_file()), EXE_CANDIDATES[0])

GRID_SIZE = 101  # 101 x 101 = 10,201 junctions
CELL_SIZE_M = 80.0
AREA_HA = (CELL_SIZE_M * CELL_SIZE_M) / 10000.0
INFLOW_STRIDE = 5

START_DATE = "04/28/2026"
END_DATE = "04/28/2026"
START_TIME = "00:00:00"
END_TIME = "06:00:00"

RAIN_SERIES = [
    ("00:00", 28.13),
    ("00:05", 29.675),
    ("00:10", 31.422),
    ("00:15", 33.416),
    ("00:20", 35.713),
    ("00:25", 38.393),
    ("00:30", 41.564),
    ("00:35", 45.382),
    ("00:40", 50.076),
    ("00:45", 56.005),
    ("00:50", 63.755),
    ("00:55", 74.37),
    ("01:00", 89.913),
    ("01:05", 115.147),
    ("01:10", 164.37),
    ("01:15", 164.37),
    ("01:20", 127.56),
    ("01:25", 105.131),
    ("01:30", 89.913),
    ("01:35", 78.852),
    ("01:40", 70.417),
    ("01:45", 63.755),
    ("01:50", 58.347),
    ("01:55", 53.862),
    ("02:00", 50.076),
    ("02:05", 46.835),
    ("02:10", 44.025),
    ("02:15", 41.564),
    ("02:20", 39.389),
    ("02:25", 37.451),
    ("02:30", 35.713),
    ("02:35", 34.144),
    ("02:40", 32.721),
    ("02:45", 31.422),
    ("02:50", 30.233),
    ("02:55", 29.14),
    ("03:00", 28.13),
    ("04:00", 0.0),
    ("05:00", 0.0),
    ("06:00", 0.0),
]

LANDUSE_STYLES = {
    "BUILDING": {
        "runoff_coeff": 0.72,
        "kernel": (0.18, 0.52, 0.22, 0.08),
        "color": "#c07a3d",
    },
    "GREEN": {
        "runoff_coeff": 0.22,
        "kernel": (0.03, 0.08, 0.15, 0.24, 0.26, 0.16, 0.08),
        "color": "#5b9b4c",
    },
    "ROAD": {
        "runoff_coeff": 0.84,
        "kernel": (0.34, 0.46, 0.16, 0.04),
        "color": "#8e8e8e",
    },
    "WATER": {
        "runoff_coeff": 0.92,
        "kernel": (0.58, 0.30, 0.12),
        "color": "#4f87c2",
    },
}

FLOW_ROUTING_MAP = {
    "DW": "DYNWAVE",
    "KW": "KINWAVE",
    "SF": "STEADY",
}


@dataclass(frozen=True)
class NodeInfo:
    row: int
    col: int
    name: str
    x: float
    y: float
    elev: float
    max_depth: float
    sur_depth: float
    aponded: float
    landuse: str


@dataclass(frozen=True)
class InflowSource:
    row: int
    col: int
    node_name: str
    timeseries_name: str
    area_ha: float
    dominant_landuse: str
    landuse_mix: dict[str, float]
    scale_factor: float


def node_name(row: int, col: int) -> str:
    return f"J_{row:03d}_{col:03d}"


def timeseries_name(row: int, col: int) -> str:
    return f"TS_{row:03d}_{col:03d}"


def conduit_name(kind: str, row: int, col: int) -> str:
    return f"C_{kind}_{row:03d}_{col:03d}"


def classify_landuse(row: int, col: int, size: int) -> str:
    road_band = (row % 10 == 0) or (col % 10 == 0)
    water_band = (
        abs(row - size // 2) <= 2 and col > size * 0.58
    ) or (
        abs((row - col) - 12) <= 1 and row > size * 0.60 and col > size * 0.55
    )
    green_core = (
        18 <= row <= 34 and 18 <= col <= 34
    ) or (
        62 <= row <= 82 and 16 <= col <= 38
    ) or (
        row < 16 and col > size - 26
    )

    if water_band:
        return "WATER"
    if road_band:
        return "ROAD"
    if green_core:
        return "GREEN"
    return "BUILDING"


def build_nodes(size: int) -> list[NodeInfo]:
    nodes: list[NodeInfo] = []
    outlet_col = size // 2
    for row in range(size):
        for col in range(size):
            dist = (size - 1 - row) + abs(col - outlet_col)
            elev = 8.0 + dist * 0.14 + (size - 1 - row) * 0.03
            x = col * CELL_SIZE_M
            y = (size - 1 - row) * CELL_SIZE_M
            nodes.append(
                NodeInfo(
                    row=row,
                    col=col,
                    name=node_name(row, col),
                    x=x,
                    y=y,
                    elev=round(elev, 3),
                    max_depth=4.0,
                    sur_depth=0.5,
                    aponded=140.0,
                    landuse=classify_landuse(row, col, size),
                )
            )
    return nodes


def build_inflow_sources(nodes: list[NodeInfo], size: int, stride: int) -> list[InflowSource]:
    node_lookup = {(node.row, node.col): node for node in nodes}
    sources: list[InflowSource] = []
    for row in range(0, size, stride):
        for col in range(0, size, stride):
            row1 = min(row + stride - 1, size - 1)
            col1 = min(col + stride - 1, size - 1)
            center_row = (row + row1) // 2
            center_col = (col + col1) // 2
            center = node_lookup[(center_row, center_col)]
            cells = (row1 - row + 1) * (col1 - col + 1)
            mix_counts = {key: 0 for key in LANDUSE_STYLES}
            for rr in range(row, row1 + 1):
                for cc in range(col, col1 + 1):
                    mix_counts[node_lookup[(rr, cc)].landuse] += 1
            dominant_landuse = max(mix_counts.items(), key=lambda item: item[1])[0]
            dominant_coeff = LANDUSE_STYLES[dominant_landuse]["runoff_coeff"]
            weighted_coeff = sum(
                LANDUSE_STYLES[landuse]["runoff_coeff"] * (count / cells)
                for landuse, count in mix_counts.items()
                if count > 0
            )
            sources.append(
                InflowSource(
                    row=center_row,
                    col=center_col,
                    node_name=center.name,
                    timeseries_name=timeseries_name(center_row, center_col),
                    area_ha=round(cells * AREA_HA, 3),
                    dominant_landuse=dominant_landuse,
                    landuse_mix={k: v / cells for k, v in mix_counts.items() if v > 0},
                    scale_factor=round(cells * AREA_HA * weighted_coeff / dominant_coeff, 4),
                )
            )
    return sources


def convolve_response(intensity_series: list[float], kernel: tuple[float, ...]) -> list[float]:
    result = [0.0] * len(intensity_series)
    for idx in range(len(intensity_series)):
        acc = 0.0
        for lag, weight in enumerate(kernel):
            src = idx - lag
            if src < 0:
                break
            acc += intensity_series[src] * weight
        result[idx] = acc
    return result


def build_unit_hydrograph(landuse: str) -> list[tuple[str, float]]:
    rain = [value for _, value in RAIN_SERIES]
    response = [0.0] * len(rain)
    style = LANDUSE_STYLES[landuse]
    routed_rain = convolve_response(rain, style["kernel"])
    coeff = style["runoff_coeff"]
    for i, intensity in enumerate(routed_rain):
        response[i] += intensity * coeff / 360.0
    return [(RAIN_SERIES[i][0], round(flow, 5)) for i, flow in enumerate(response)]


def downstream_target(row: int, col: int, size: int) -> tuple[str, str]:
    outlet_col = size // 2
    if row == size - 1 and col == outlet_col:
        return ("OUTFALL", "OF_MAIN")
    if row < size - 1 and (row + col) % 2 == 0:
        return ("NODE", node_name(row + 1, col))
    if col < outlet_col:
        return ("NODE", node_name(row, col + 1))
    if col > outlet_col:
        return ("NODE", node_name(row, col - 1))
    return ("NODE", node_name(row + 1, col))


def build_node_lookup(nodes: list[NodeInfo]) -> dict[str, NodeInfo]:
    return {node.name: node for node in nodes}


def iter_link_segments(nodes: list[NodeInfo], size: int) -> Iterable[tuple[float, float, float, float, bool]]:
    node_lookup = build_node_lookup(nodes)
    outlet_col = size // 2
    of_x = outlet_col * CELL_SIZE_M
    of_y = -CELL_SIZE_M
    for node in nodes:
        target_kind, target = downstream_target(node.row, node.col, size)
        x0, y0 = node.col, size - 1 - node.row
        if target_kind == "OUTFALL":
            x1 = of_x / CELL_SIZE_M
            y1 = (size - 1) - (of_y / CELL_SIZE_M)
            yield (x0, y0, x1, y1, True)
            continue
        target_node = node_lookup[target]
        x1, y1 = target_node.col, size - 1 - target_node.row
        is_trunk = (
            node.col == outlet_col
            or target_node.col == outlet_col
            or abs(node.col - outlet_col) <= 1 and node.row > size * 0.70
        )
        yield (x0, y0, x1, y1, is_trunk)


def iter_inp_lines(
    route_key: str,
    nodes: list[NodeInfo],
    inflow_sources: list[InflowSource],
    size: int,
) -> Iterable[str]:
    yield "[TITLE]"
    yield f"Large synthetic urban drainage benchmark ({size}x{size} grid, {len(nodes)} junctions)"
    yield "; Generated automatically for KW / SF / DW benchmark on a city-scale topology"
    yield "; Land-use tiles emulate buildings, road corridors, green parks, and urban water belts"
    yield "; External inflows aggregate tile runoff into source nodes to stress-test routing on a 10k+ network"
    yield ""
    yield "[OPTIONS]"
    yield "FLOW_UNITS CMS"
    yield "INFILTRATION HORTON"
    yield f"FLOW_ROUTING {FLOW_ROUTING_MAP[route_key]}"
    yield "LINK_OFFSETS DEPTH"
    yield "ALLOW_PONDING YES"
    yield "SKIP_STEADY_STATE NO"
    yield f"START_DATE {START_DATE}"
    yield f"START_TIME {START_TIME}"
    yield f"REPORT_START_DATE {START_DATE}"
    yield f"REPORT_START_TIME {START_TIME}"
    yield f"END_DATE {END_DATE}"
    yield f"END_TIME {END_TIME}"
    yield "REPORT_STEP 00:05:00"
    yield "WET_STEP 00:01:00"
    yield "DRY_STEP 00:05:00"
    yield "ROUTING_STEP 00:00:30"
    yield "INERTIAL_DAMPING PARTIAL"
    yield "NORMAL_FLOW_LIMITED BOTH"
    yield "FORCE_MAIN_EQUATION H-W"
    yield "VARIABLE_STEP 0.75"
    yield "LENGTHENING_STEP 0"
    yield "MIN_SURFAREA 1.0"
    yield "MAX_TRIALS 8"
    yield "HEAD_TOLERANCE 0.002"
    yield "SYS_FLOW_TOL 5"
    yield "LAT_FLOW_TOL 5"
    yield "MINIMUM_STEP 0.5"
    yield "THREADS 1"
    yield ""
    yield "[TIMESERIES]"
    yield ";;Name Date Time Value(CMS)"
    for landuse in LANDUSE_STYLES:
        ts_name = f"TS_{landuse}"
        for hhmm, flow in build_unit_hydrograph(landuse):
            yield f"{ts_name} {START_DATE} {hhmm} {flow:.5f}"
    yield ""
    yield "[INFLOWS]"
    yield ";;Node Constituent TimeSeries Type Mfactor Sfactor Baseline Pattern"
    for source in inflow_sources:
        yield (
            f"{source.node_name} FLOW TS_{source.dominant_landuse} FLOW 1.0 "
            f"{source.scale_factor:.4f} 0.0"
        )
    yield ""
    yield "[JUNCTIONS]"
    yield ";;Name Elev MaxDepth InitDepth SurDepth Aponded"
    for node in nodes:
        yield (
            f"{node.name} {node.elev:.3f} {node.max_depth:.2f} 0 "
            f"{node.sur_depth:.2f} {node.aponded:.1f}"
        )
    yield ""
    yield "[OUTFALLS]"
    yield ";;Name Elev Type StageData Gated"
    yield "OF_MAIN 7.60 FREE  NO"
    yield ""
    yield "[CONDUITS]"
    yield ";;Name FromNode ToNode Length Roughness InOffset OutOffset InitFlow MaxFlow"
    for node in nodes:
        target_kind, target = downstream_target(node.row, node.col, size)
        link_name = conduit_name("D", node.row, node.col)
        length = CELL_SIZE_M if target_kind == "NODE" else CELL_SIZE_M * 1.5
        yield f"{link_name} {node.name} {target} {length:.1f} 0.013 0 0 0 0"
    yield ""
    yield "[XSECTIONS]"
    yield ";;Link Shape Geom1 Geom2 Geom3 Geom4 Barrels Culvert"
    outlet_col = size // 2
    for node in nodes:
        dist = (size - 1 - node.row) + abs(node.col - outlet_col)
        diameter = 0.75 + 0.0175 * (size * 1.25 - dist)
        diameter = max(0.8, min(4.2, diameter))
        yield f"{conduit_name('D', node.row, node.col)} CIRCULAR {diameter:.3f} 0 0 0 1"
    yield ""
    yield "[LOSSES]"
    yield ";;Link Inlet Outlet Average FlapGate Seepage"
    for node in nodes:
        yield f"{conduit_name('D', node.row, node.col)} 0 0 0 NO 0"
    yield ""
    yield "[COORDINATES]"
    yield ";;Node X-Coord Y-Coord"
    for node in nodes:
        yield f"{node.name} {node.x:.1f} {node.y:.1f}"
    of_x = (size // 2) * CELL_SIZE_M
    yield f"OF_MAIN {of_x:.1f} {-CELL_SIZE_M:.1f}"
    yield ""
    yield "[REPORT]"
    yield "INPUT NO"
    yield "CONTROLS NO"
    yield "SUBCATCHMENTS NONE"
    yield "NODES NONE"
    yield "LINKS NONE"


def write_inp(
    route_key: str,
    nodes: list[NodeInfo],
    inflow_sources: list[InflowSource],
    size: int,
) -> Path:
    path = BASE_DIR / f"large_city_10k_{route_key}.inp"
    with path.open("w", encoding="ascii", newline="\n") as fp:
        for line in iter_inp_lines(route_key, nodes, inflow_sources, size):
            fp.write(line)
            fp.write("\n")
    return path


def write_visual(nodes: list[NodeInfo], size: int) -> Path:
    png_path = BASE_DIR / "large_city_10k_landuse.png"
    try:
        import matplotlib.pyplot as plt
        from matplotlib.patches import Rectangle

        fig, ax = plt.subplots(figsize=(13, 13), dpi=220)
        for node in nodes:
            color = LANDUSE_STYLES[node.landuse]["color"]
            ax.add_patch(
                Rectangle(
                    (node.col - 0.5, size - 1 - node.row - 0.5),
                    1.0,
                    1.0,
                    facecolor=color,
                    edgecolor="none",
                )
            )
        for x0, y0, x1, y1, is_trunk in iter_link_segments(nodes, size):
            ax.plot(
                [x0, x1],
                [y0, y1],
                color="#1d1d1d" if is_trunk else "#303030",
                linewidth=0.85 if is_trunk else 0.18,
                alpha=0.9 if is_trunk else 0.18,
                zorder=3 if is_trunk else 2,
            )
        xs = [node.col for node in nodes]
        ys = [size - 1 - node.row for node in nodes]
        ax.scatter(xs, ys, s=1.0, c="#f8f8f8", alpha=0.25, linewidths=0, zorder=4)
        source_nodes = [
            node for node in nodes
            if node.row % INFLOW_STRIDE == 2 and node.col % INFLOW_STRIDE == 2
        ]
        ax.scatter(
            [node.col for node in source_nodes],
            [size - 1 - node.row for node in source_nodes],
            s=5,
            c="#ffe36e",
            alpha=0.7,
            linewidths=0,
            zorder=5,
        )
        outlet_col = size // 2
        outlet_x = outlet_col
        outlet_y = size
        ax.scatter([outlet_x], [outlet_y], s=140, c="#ff3b30", marker="*", zorder=6)
        ax.text(
            outlet_x + 1.3,
            outlet_y - 0.2,
            "WWTP / OF_MAIN",
            color="#111111",
            fontsize=9,
            weight="bold",
            zorder=6,
        )
        ax.set_aspect("equal")
        ax.set_xlim(-0.5, size - 0.5)
        ax.set_ylim(-1.5, size + 0.8)
        ax.set_title("Large synthetic city: landuse tiles with sewer links, junctions, and WWTP")
        ax.set_xticks([])
        ax.set_yticks([])
        legend_items = [
            Rectangle((0, 0), 1, 1, facecolor=LANDUSE_STYLES[key]["color"], edgecolor="none")
            for key in ("BUILDING", "GREEN", "ROAD", "WATER")
        ]
        ax.legend(
            legend_items,
            ["Building", "Green", "Road", "Water"],
            loc="lower right",
            framealpha=0.95,
        )
        fig.tight_layout()
        fig.savefig(png_path)
        plt.close(fig)
        return png_path
    except Exception:
        svg_path = BASE_DIR / "large_city_10k_landuse.svg"
        tile = 8
        width = size * tile
        height = size * tile
        with svg_path.open("w", encoding="ascii", newline="\n") as fp:
            fp.write(
                f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
                f'viewBox="0 0 {width} {height}">\n'
            )
            fp.write('<rect width="100%" height="100%" fill="#ffffff"/>\n')
            for node in nodes:
                x = node.col * tile
                y = node.row * tile
                color = LANDUSE_STYLES[node.landuse]["color"]
                fp.write(
                    f'<rect x="{x}" y="{y}" width="{tile}" height="{tile}" '
                    f'fill="{color}" stroke="none"/>\n'
                )
            fp.write("</svg>\n")
        return svg_path


def parse_rpt_metrics(rpt_path: Path) -> dict[str, float | str | None]:
    text = rpt_path.read_text(encoding="utf-8", errors="ignore")
    line_count = text.count("\n") + (1 if text else 0)

    def pick(pattern: str) -> float | None:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if not match:
            return None
        try:
            return float(match.group(1))
        except ValueError:
            return None

    flow_continuity = pick(r"Flow Routing Continuity[\s\S]*?Continuity Error \(%\) \.*\s*([-+]?\d+(?:\.\d+)?)")
    runoff_continuity = pick(r"Runoff Quantity Continuity[\s\S]*?Continuity Error \(%\) \.*\s*([-+]?\d+(?:\.\d+)?)")
    quality_continuity = pick(r"Quality Routing Continuity[\s\S]*?Continuity Error \(%\) \.*\s*([-+]?\d+(?:\.\d+)?)")
    warning_count = len(re.findall(r"\bWARNING\s+\d+:", text, flags=re.IGNORECASE))
    error_count = len(re.findall(r"\bERROR\s+\d+:", text, flags=re.IGNORECASE))

    return {
        "rpt_size_bytes": rpt_path.stat().st_size,
        "rpt_line_count": line_count,
        "report_complete": (
            "Node Depth Summary" in text
            or "Highest Flow Instability Indexes" in text
            or "Link Flow Summary" in text
        ),
        "runoff_continuity_pct": runoff_continuity,
        "flow_routing_continuity_pct": flow_continuity,
        "quality_continuity_pct": quality_continuity,
        "warning_count": warning_count,
        "error_count": error_count,
        "has_warnings": warning_count > 0,
        "has_errors": error_count > 0,
    }


def run_swmm(inp_path: Path, route_key: str) -> dict[str, object]:
    rpt_path = BASE_DIR / f"large_city_10k_{route_key}.rpt"
    out_path = BASE_DIR / f"large_city_10k_{route_key}.out"
    start = time.perf_counter()
    proc = subprocess.run(
        [str(SWMM_EXE), str(inp_path), str(rpt_path), str(out_path)],
        cwd=str(BASE_DIR),
        capture_output=True,
        text=True,
        check=False,
    )
    elapsed = time.perf_counter() - start
    metrics = parse_rpt_metrics(rpt_path) if rpt_path.exists() else {}
    return {
        "route_model": route_key,
        "inp": inp_path.name,
        "rpt": rpt_path.name,
        "out": out_path.name,
        "return_code": proc.returncode,
        "elapsed_seconds": round(elapsed, 3),
        "stdout_tail": proc.stdout.strip().splitlines()[-6:],
        "stderr_tail": proc.stderr.strip().splitlines()[-6:],
        **metrics,
    }


def collect_existing_result(route_key: str) -> dict[str, object]:
    inp_path = BASE_DIR / f"large_city_10k_{route_key}.inp"
    rpt_path = BASE_DIR / f"large_city_10k_{route_key}.rpt"
    out_path = BASE_DIR / f"large_city_10k_{route_key}.out"
    metrics = parse_rpt_metrics(rpt_path) if rpt_path.exists() else {}
    return {
        "route_model": route_key,
        "inp": inp_path.name,
        "rpt": rpt_path.name,
        "out": out_path.name,
        "return_code": 0 if rpt_path.exists() and out_path.exists() else None,
        "elapsed_seconds": None,
        "stdout_tail": [],
        "stderr_tail": [],
        **metrics,
    }


def load_terminal_benchmarks() -> dict[str, dict[str, object]]:
    path = BASE_DIR / "large_city_10k_terminal_benchmark.json"
    if not path.exists():
        return {}
    rows = json.loads(path.read_text(encoding="utf-8-sig"))
    return {row["route_model"]: row for row in rows}


def write_summary(
    nodes: list[NodeInfo],
    inflow_sources: list[InflowSource],
    visual_path: Path,
    benchmark_rows: list[dict[str, object]],
) -> None:
    landuse_counts: dict[str, int] = {key: 0 for key in LANDUSE_STYLES}
    for node in nodes:
        landuse_counts[node.landuse] += 1

    metadata = {
        "grid_size": GRID_SIZE,
        "junction_count": len(nodes),
        "outfall_count": 1,
        "conduit_count": len(nodes),
        "inflow_source_count": len(inflow_sources),
        "cell_size_m": CELL_SIZE_M,
        "tile_area_ha": round(AREA_HA, 4),
        "inflow_stride": INFLOW_STRIDE,
        "visual_file": visual_path.name,
        "landuse_counts": landuse_counts,
        "benchmarks": benchmark_rows,
    }
    elapsed_map = {
        row["route_model"]: row["elapsed_seconds"]
        for row in benchmark_rows
        if row.get("elapsed_seconds") is not None
    }
    if "SF" in elapsed_map and "KW" in elapsed_map and "DW" in elapsed_map:
        metadata["speedup_vs_DW"] = {
            "KW": round(elapsed_map["DW"] / elapsed_map["KW"], 2),
            "SF": round(elapsed_map["DW"] / elapsed_map["SF"], 2),
        }
    (BASE_DIR / "large_city_10k_benchmark.json").write_text(
        json.dumps(metadata, indent=2),
        encoding="utf-8",
    )

    md_lines = [
        "# large_city_10k benchmark",
        "",
        f"- grid_size: `{GRID_SIZE} x {GRID_SIZE}`",
        f"- junction_count: `{len(nodes)}`",
        "- outfall_count: `1`",
        f"- conduit_count: `{len(nodes)}`",
        f"- inflow_source_count: `{len(inflow_sources)}`",
        f"- tile_area_ha: `{AREA_HA:.2f}`",
        f"- inflow_stride: `{INFLOW_STRIDE}`",
        f"- visual_file: `{visual_path.name}`",
        "",
        "## landuse_counts",
        "",
    ]
    for key, value in landuse_counts.items():
        md_lines.append(f"- {key}: `{value}`")
    md_lines.extend(["", "## benchmark", ""])
    md_lines.append("| Model | Return Code | Elapsed (s) | Flow CE (%) | RPT Complete | RPT Size | Warnings | Errors |")
    md_lines.append("| --- | ---: | ---: | ---: | --- | ---: | ---: | ---: |")
    for row in benchmark_rows:
        md_lines.append(
            "| {route_model} | {return_code} | {elapsed_seconds} | {flow_routing_continuity_pct} | "
            "{report_complete} | {rpt_size_bytes} | {warning_count} | {error_count} |".format(
                route_model=row["route_model"],
                return_code=row["return_code"],
                elapsed_seconds=row["elapsed_seconds"],
                flow_routing_continuity_pct=row.get("flow_routing_continuity_pct"),
                report_complete=row.get("report_complete"),
                rpt_size_bytes=row.get("rpt_size_bytes"),
                warning_count=row.get("warning_count"),
                error_count=row.get("error_count"),
            )
        )
    if "SF" in elapsed_map and "KW" in elapsed_map and "DW" in elapsed_map:
        md_lines.extend(
            [
                "",
                "## speedup_vs_DW",
                "",
                f"- KW: `{elapsed_map['DW'] / elapsed_map['KW']:.2f}x`",
                f"- SF: `{elapsed_map['DW'] / elapsed_map['SF']:.2f}x`",
            ]
        )
    (BASE_DIR / "large_city_10k_benchmark.md").write_text(
        "\n".join(md_lines) + "\n",
        encoding="utf-8",
    )


def generate_assets() -> tuple[list[NodeInfo], list[InflowSource], Path]:
    nodes = build_nodes(GRID_SIZE)
    inflow_sources = build_inflow_sources(nodes, GRID_SIZE, INFLOW_STRIDE)
    visual_path = write_visual(nodes, GRID_SIZE)
    for route_key in ("DW", "KW", "SF"):
        write_inp(route_key, nodes, inflow_sources, GRID_SIZE)
    return nodes, inflow_sources, visual_path


def summarize_existing() -> None:
    nodes = build_nodes(GRID_SIZE)
    inflow_sources = build_inflow_sources(nodes, GRID_SIZE, INFLOW_STRIDE)
    visual_path = BASE_DIR / "large_city_10k_landuse.png"
    if not visual_path.exists():
        visual_path = BASE_DIR / "large_city_10k_landuse.svg"
    terminal_rows = load_terminal_benchmarks()
    benchmark_rows = []
    for route_key in ("DW", "KW", "SF"):
        row = collect_existing_result(route_key)
        terminal_row = terminal_rows.get(route_key)
        if terminal_row:
            row["elapsed_seconds"] = terminal_row.get("elapsed_seconds")
            row["return_code"] = terminal_row.get("return_code")
            row["out_size_bytes"] = terminal_row.get("out_size_bytes")
        benchmark_rows.append(row)
    write_summary(nodes, inflow_sources, visual_path, benchmark_rows)


def run_all() -> None:
    nodes, inflow_sources, visual_path = generate_assets()
    benchmark_rows: list[dict[str, object]] = []
    for route_key in ("DW", "KW", "SF"):
        inp_path = BASE_DIR / f"large_city_10k_{route_key}.inp"
        benchmark_rows.append(run_swmm(inp_path, route_key))
    write_summary(nodes, inflow_sources, visual_path, benchmark_rows)
    print(json.dumps({"visual_file": visual_path.name, "benchmarks": benchmark_rows}, indent=2))


def main() -> int:
    global BASE_DIR, SWMM_EXE
    parser = argparse.ArgumentParser(description="Generate and benchmark a synthetic 10,201-node SWMM network.")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--generate-only", action="store_true", help="Generate inputs without running SWMM.")
    mode.add_argument("--summarize-only", action="store_true", help="Summarize existing reports without running SWMM.")
    parser.add_argument("--swmm-exe", type=Path, default=SWMM_EXE, help="Path to the built runswmm executable.")
    parser.add_argument("--output-dir", type=Path, default=BASE_DIR, help="Directory for generated models and results.")
    args = parser.parse_args()
    BASE_DIR = args.output_dir.resolve()
    SWMM_EXE = args.swmm_exe.resolve()
    BASE_DIR.mkdir(parents=True, exist_ok=True)

    if args.generate_only:
        nodes, inflow_sources, visual_path = generate_assets()
        print(
            json.dumps(
                {
                    "visual_file": visual_path.name,
                    "junction_count": len(nodes),
                    "inflow_source_count": len(inflow_sources),
                },
                indent=2,
            )
        )
        return 0

    if args.summarize_only:
        summarize_existing()
        print(json.dumps({"summary_file": "large_city_10k_benchmark.json"}, indent=2))
        return 0

    if not SWMM_EXE.is_file():
        print(f"SWMM executable not found: {SWMM_EXE}. Build the engine or pass --swmm-exe.", file=sys.stderr)
        return 1
    run_all()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
