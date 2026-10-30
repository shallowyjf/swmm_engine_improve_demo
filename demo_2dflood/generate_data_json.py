"""
Convert simulation outputs to data.json and data_compact.json for the HTML viewer.
Subsamples to 5-min intervals for compact browser loading.
"""
import csv
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

BASE = Path(__file__).resolve().parent


def parse_inp(inp_path):
    """Parse topology sections from INP file."""
    text = inp_path.read_text(encoding="utf-8")
    polygons = defaultdict(list)
    coordinates = {}
    conduits = []
    junctions = []
    subcatch_info = {}
    section = None

    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith(";;"):
            continue
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1].upper().strip()
            continue

        if section == "POLYGONS":
            parts = line.split()
            if len(parts) >= 3:
                try:
                    polygons[parts[0]].append([float(parts[1]), float(parts[2])])
                except ValueError:
                    pass
        elif section == "COORDINATES":
            parts = line.split()
            if len(parts) >= 3:
                try:
                    coordinates[parts[0]] = [float(parts[1]), float(parts[2])]
                except ValueError:
                    pass
        elif section == "CONDUITS":
            parts = line.split()
            if len(parts) >= 4:
                conduits.append({
                    "name": parts[0], "from": parts[1], "to": parts[2],
                    "length": float(parts[3]), "roughness": float(parts[4]),
                })
        elif section == "JUNCTIONS":
            parts = line.split()
            if len(parts) >= 3:
                junctions.append({
                    "name": parts[0], "elev": float(parts[1]),
                    "maxDepth": float(parts[2]),
                })
        elif section == "SUBCATCHMENTS":
            parts = line.split()
            if len(parts) >= 7:
                subcatch_info[parts[0]] = {
                    "outlet": parts[2],
                    "area_ha": float(parts[3]),
                    "imp_pct": float(parts[4]),
                    "slope_pct": float(parts[6]),
                }
        elif section == "XSECTIONS":
            parts = line.split()
            if len(parts) >= 3:
                for c in conduits:
                    if c["name"] == parts[0]:
                        c["shape"] = parts[1]
                        c["diameter"] = float(parts[2])

    return {
        "polygons": dict(polygons),
        "coordinates": coordinates,
        "conduits": conduits,
        "junctions": junctions,
        "subcatch_info": subcatch_info,
    }


def load_csv_subsampled(csv_path, step=5):
    """Load CSV, subsample every `step` rows."""
    with open(csv_path) as f:
        reader = csv.reader(f)
        header = next(reader)
        rows = [row for row in reader]

    cols = {h: i for i, h in enumerate(header)}
    sampled = rows[::step]

    time = [float(r[cols["time_min"]]) for r in sampled]
    n = len(sampled)

    series = {}
    for prefix in ["subcatch_runoff", "subcatch_infil", "node_depth",
                    "node_overflow", "node_volume", "link_flow", "link_capacity"]:
        series[prefix] = {}
        for col_name, idx in cols.items():
            if col_name.startswith(prefix + "|"):
                obj_name = col_name.split("|", 1)[1]
                series[prefix][obj_name] = [float(r[idx]) for r in sampled]

    return time, series


def main():
    inp_path = BASE / "demo_valley.inp"
    csv_path = BASE / "demo_results.csv"
    json_path = BASE / "demo_summary.json"
    out_path = BASE / "data.json"

    print("Loading inputs...")
    topo = parse_inp(inp_path)
    time, series = load_csv_subsampled(csv_path, step=5)
    with open(json_path) as f:
        summary = json.load(f)

    # Land use mapping
    sc_landuse = {
        "S_WA1": "BUILDING", "S_WA2": "MIXED", "S_WA3": "ROAD",
        "S_WB1": "BUILDING", "S_WB2": "GREEN", "S_WB3": "ROAD",
        "S_WC1": "BUILDING", "S_WC2": "GREEN", "S_WC3": "ROAD",
        "S_ED1": "BUILDING", "S_ED2": "MIXED", "S_ED3": "ROAD",
        "S_EE1": "BUILDING", "S_EE2": "GREEN", "S_EE3": "MIXED",
    }

    # Build subcatchment array
    subcatchments = []
    for name in series["subcatch_runoff"]:
        info = topo["subcatch_info"].get(name, {})
        subcatchments.append({
            "name": name,
            "landuse": sc_landuse.get(name, "MIXED"),
            "outlet": info.get("outlet", ""),
            "area_ha": info.get("area_ha", 0),
            "imp_pct": info.get("imp_pct", 0),
            "slope_pct": info.get("slope_pct", 0),
            "polygon": topo["polygons"].get(name, []),
        })

    # Build node array
    nodes = []
    junc_map = {j["name"]: j for j in topo["junctions"]}
    for name, coord in topo["coordinates"].items():
        j = junc_map.get(name, {})
        nodes.append({
            "name": name,
            "x": coord[0], "y": coord[1],
            "elev": j.get("elev", 0),
            "maxDepth": j.get("maxDepth", 3.0),
            "type": "outfall" if name == "OUTFALL" else "junction",
        })

    # Build link array (conduits from topology)
    links = []
    for c in topo["conduits"]:
        links.append({
            "name": c["name"],
            "from": c["from"],
            "to": c["to"],
            "length": c["length"],
            "diameter": c.get("diameter", 0.5),
            "roughness": c.get("roughness", 0.013),
        })

    # Build cascade chain info
    cascades = [
        {"name": "West Chain A", "chain": ["S_WA1", "S_WA2", "S_WA3"],
         "landuses": ["BUILDING", "MIXED", "ROAD"], "outlet_node": "J_W"},
        {"name": "West Chain B", "chain": ["S_WB1", "S_WB2", "S_WB3"],
         "landuses": ["BUILDING", "GREEN", "ROAD"], "outlet_node": "J_W"},
        {"name": "West Chain C", "chain": ["S_WC1", "S_WC2", "S_WC3"],
         "landuses": ["BUILDING", "GREEN", "ROAD"], "outlet_node": "J_W"},
        {"name": "East Chain D", "chain": ["S_ED1", "S_ED2", "S_ED3"],
         "landuses": ["BUILDING", "MIXED", "ROAD"], "outlet_node": "J_E"},
        {"name": "East Chain E", "chain": ["S_EE1", "S_EE2", "S_EE3"],
         "landuses": ["BUILDING", "GREEN", "MIXED"], "outlet_node": "J_E"},
    ]

    data = {
        "metadata": {
            "title": "SWMM 2D Flood Demo - Valley Cascading Prototype",
            "duration_hr": summary["duration_hr"],
            "time_step_min": 5,
            "mass_balance": summary["mass_balance"],
            "peak_values": summary["peak_values"],
        },
        "time": time,
        "topology": {
            "subcatchments": subcatchments,
            "nodes": nodes,
            "links": links,
            "cascades": cascades,
        },
        "series": series,
    }

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=1, ensure_ascii=False)

    compact_path = BASE / "data_compact.json"
    with open(compact_path, "w", encoding="utf-8") as f:
        json.dump(data, f, separators=(",", ":"), ensure_ascii=False)

    print(f"Generated: {out_path}")
    print(f"Generated: {compact_path}")
    print(f"  {len(time)} time points (every 5 min)")
    print(f"  {len(subcatchments)} subcatchments, {len(nodes)} nodes, {len(links)} links")


if __name__ == "__main__":
    main()
