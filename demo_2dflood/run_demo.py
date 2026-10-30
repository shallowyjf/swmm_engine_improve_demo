"""
SWMM 2D Flood Demo Runner
Loads the SWMM shared library via ctypes and collects simulation time series.
"""
import argparse
import ctypes
import csv
import json
import os
import sys
from pathlib import Path

# --------------- Constants from swmm5.h ---------------
GAGE, SUBCATCH, NODE, LINK = 0, 1, 2, 3

PROPS = {
    "SUBCATCH_RAINFALL": 202, "SUBCATCH_INFIL": 204, "SUBCATCH_RUNOFF": 205,
    "NODE_DEPTH": 303, "NODE_OVERFLOW": 308, "NODE_VOLUME": 305, "NODE_LATFLOW": 306,
    "LINK_FLOW": 410, "LINK_CAPACITY": 414,
    "SYS_ELAPSEDTIME": 2,
}


class SwmmRunner:
    def __init__(self, dll_path, inp_path, rpt_path, out_path):
        loader = ctypes.WinDLL if os.name == "nt" else ctypes.CDLL
        self.dll = loader(str(dll_path))
        self._setup_api()
        self.inp = str(inp_path)
        self.rpt = str(rpt_path)
        self.out = str(out_path)
        self.objects = {}

    def _setup_api(self):
        lib = self.dll
        lib.swmm_open.argtypes = [ctypes.c_char_p] * 3
        lib.swmm_open.restype = ctypes.c_int
        lib.swmm_start.argtypes = [ctypes.c_int]
        lib.swmm_start.restype = ctypes.c_int
        lib.swmm_step.argtypes = [ctypes.POINTER(ctypes.c_double)]
        lib.swmm_step.restype = ctypes.c_int
        lib.swmm_end.restype = ctypes.c_int
        lib.swmm_report.restype = ctypes.c_int
        lib.swmm_close.restype = ctypes.c_int
        lib.swmm_getMassBalErr.argtypes = [ctypes.POINTER(ctypes.c_float)] * 3
        lib.swmm_getMassBalErr.restype = ctypes.c_int
        lib.swmm_getValue.argtypes = [ctypes.c_int, ctypes.c_int]
        lib.swmm_getValue.restype = ctypes.c_double
        lib.swmm_getCount.argtypes = [ctypes.c_int]
        lib.swmm_getCount.restype = ctypes.c_int
        lib.swmm_getName.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_char_p, ctypes.c_int]

    def open(self):
        err = self.dll.swmm_open(
            self.inp.encode("utf-8"), self.rpt.encode("utf-8"), self.out.encode("utf-8"))
        if err:
            raise RuntimeError(f"swmm_open error {err}")
        self._load_names()

    def _load_names(self):
        buf = ctypes.create_string_buffer(256)
        for obj_type, obj_name in [(SUBCATCH, "subcatchments"), (NODE, "nodes"), (LINK, "links")]:
            count = self.dll.swmm_getCount(obj_type)
            names = []
            for i in range(count):
                self.dll.swmm_getName(obj_type, i, buf, 256)
                names.append(buf.value.decode("utf-8"))
            self.objects[obj_name] = {"count": count, "names": names}

    def start(self, save_out=True):
        self.dll.swmm_start(1 if save_out else 0)

    def run_and_collect(self, collect_interval_sec=60.0):
        """Step through simulation, collecting data at each interval."""
        n_subcatch = self.objects["subcatchments"]["count"]
        n_nodes = self.objects["nodes"]["count"]
        n_links = self.objects["links"]["count"]

        data = {
            "time_min": [],
            "subcatch_runoff": {name: [] for name in self.objects["subcatchments"]["names"]},
            "subcatch_infil": {name: [] for name in self.objects["subcatchments"]["names"]},
            "node_depth": {name: [] for name in self.objects["nodes"]["names"]},
            "node_overflow": {name: [] for name in self.objects["nodes"]["names"]},
            "node_volume": {name: [] for name in self.objects["nodes"]["names"]},
            "link_flow": {name: [] for name in self.objects["links"]["names"]},
            "link_capacity": {name: [] for name in self.objects["links"]["names"]},
        }

        elapsed = ctypes.c_double(0.0)
        step_count = 0
        last_collect_min = -1

        while True:
            err = self.dll.swmm_step(ctypes.byref(elapsed))
            if err:
                raise RuntimeError(f"swmm_step error {err}")
            if elapsed.value == 0.0:
                break

            step_count += 1

            # elapsed is in decimal days; convert to minutes of simulation time
            sim_min = elapsed.value * 1440.0  # 1440 min/day
            if int(sim_min) > last_collect_min:
                last_collect_min = int(sim_min)
                data["time_min"].append(sim_min)

                for idx, name in enumerate(self.objects["subcatchments"]["names"]):
                    data["subcatch_runoff"][name].append(
                        self.dll.swmm_getValue(PROPS["SUBCATCH_RUNOFF"], idx))
                    data["subcatch_infil"][name].append(
                        self.dll.swmm_getValue(PROPS["SUBCATCH_INFIL"], idx))

                for idx, name in enumerate(self.objects["nodes"]["names"]):
                    data["node_depth"][name].append(
                        self.dll.swmm_getValue(PROPS["NODE_DEPTH"], idx))
                    data["node_overflow"][name].append(
                        self.dll.swmm_getValue(PROPS["NODE_OVERFLOW"], idx))
                    data["node_volume"][name].append(
                        self.dll.swmm_getValue(PROPS["NODE_VOLUME"], idx))

                for idx, name in enumerate(self.objects["links"]["names"]):
                    data["link_flow"][name].append(
                        self.dll.swmm_getValue(PROPS["LINK_FLOW"], idx))
                    data["link_capacity"][name].append(
                        self.dll.swmm_getValue(PROPS["LINK_CAPACITY"], idx))

        # Final elapsed time may be slightly past end
        return data, step_count

    def end_and_report(self):
        self.dll.swmm_end()
        err = self.dll.swmm_report()
        if err:
            print(f"  swmm_report warning: {err}")

    def get_mass_balance(self):
        runoff_err = ctypes.c_float(0.0)
        flow_err = ctypes.c_float(0.0)
        qual_err = ctypes.c_float(0.0)
        self.dll.swmm_getMassBalErr(
            ctypes.byref(runoff_err), ctypes.byref(flow_err), ctypes.byref(qual_err))
        return {
            "runoff_error_pct": round(runoff_err.value, 4),
            "flow_error_pct": round(flow_err.value, 4),
            "quality_error_pct": round(qual_err.value, 4),
        }

    def close(self):
        self.dll.swmm_close()


def save_data_csv(data, path):
    """Save collected time series to CSV."""
    with open(path, "w", newline="") as f:
        writer = csv.writer(f)
        # Build header
        header = ["time_min"]
        for cat in ["subcatch_runoff", "subcatch_infil"]:
            for name in data[cat]:
                header.append(f"{cat}|{name}")
        for cat in ["node_depth", "node_overflow", "node_volume"]:
            for name in data[cat]:
                header.append(f"{cat}|{name}")
        for cat in ["link_flow", "link_capacity"]:
            for name in data[cat]:
                header.append(f"{cat}|{name}")
        writer.writerow(header)

        n = len(data["time_min"])
        for i in range(n):
            row = [data["time_min"][i]]
            for cat in ["subcatch_runoff", "subcatch_infil",
                        "node_depth", "node_overflow", "node_volume",
                        "link_flow", "link_capacity"]:
                for name in data[cat]:
                    row.append(data[cat][name][i])
            writer.writerow(row)


def save_summary_json(data, mass_bal, step_count, path):
    """Save summary statistics to JSON."""
    summary = {
        "model": "demo_valley.inp",
        "total_steps": step_count,
        "duration_hr": round(max(data["time_min"]) / 60.0, 2) if data["time_min"] else 0,  # time_min already in minutes
        "mass_balance": mass_bal,
        "peak_values": {},
    }

    # Peak subcatchment runoff
    for name in data["subcatch_runoff"]:
        vals = data["subcatch_runoff"][name]
        if vals:
            max_val = max(vals)
            max_idx = vals.index(max_val)
            summary["peak_values"][f"runoff_{name}_CMS"] = round(max_val, 4)
            summary["peak_values"][f"runoff_{name}_time_min"] = round(data["time_min"][max_idx], 1)

    # Peak node depth and overflow
    for name in data["node_depth"]:
        vals = data["node_depth"][name]
        if vals:
            max_val = max(vals)
            max_idx = vals.index(max_val)
            summary["peak_values"][f"depth_{name}_m"] = round(max_val, 3)

    for name in data["node_overflow"]:
        vals = data["node_overflow"][name]
        if vals:
            max_val = max(vals)
            summary["peak_values"][f"overflow_{name}_CMS"] = round(max_val, 4)

    # Peak link flow and capacity
    for name in data["link_flow"]:
        vals = data["link_flow"][name]
        if vals:
            max_val = max(vals)
            max_idx = vals.index(max_val)
            summary["peak_values"][f"flow_{name}_CMS"] = round(max_val, 4)
            summary["peak_values"][f"flow_{name}_time_min"] = round(data["time_min"][max_idx], 1)

    for name in data["link_capacity"]:
        vals = data["link_capacity"][name]
        if vals:
            max_val = max(vals)
            summary["peak_values"][f"capacity_{name}_ratio"] = round(max_val, 3)

    with open(path, "w") as f:
        json.dump(summary, f, indent=2, ensure_ascii=False)


def find_swmm_library(base, explicit_path=None):
    """Resolve an explicit library or a standard in-repository CMake output."""
    if explicit_path:
        candidate = Path(explicit_path).expanduser().resolve()
        if candidate.is_file():
            return candidate
        raise FileNotFoundError(f"SWMM library not found: {candidate}")

    engine = base.parent / "swmm524_engine"
    names = ("swmm5.dll",) if os.name == "nt" else ("libswmm5.so", "libswmm5.dylib")
    build_dirs = ("build_vs/Release", "build/Release", "build", "build_vs")
    candidates = [engine / directory / name
                  for directory in build_dirs for name in names]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    searched = "\n  ".join(str(path) for path in candidates)
    raise FileNotFoundError(
        "SWMM shared library not found. Build the engine first or pass --dll PATH.\n"
        f"Searched:\n  {searched}"
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dll", help="Path to swmm5.dll, libswmm5.so or libswmm5.dylib")
    args = parser.parse_args()
    base = Path(__file__).resolve().parent
    try:
        dll = find_swmm_library(base, args.dll)
    except FileNotFoundError as exc:
        parser.error(str(exc))
    inp = base / "demo_valley.inp"
    rpt = base / "demo_valley.rpt"
    out = base / "demo_valley.out"
    csv_out = base / "demo_results.csv"
    json_out = base / "demo_summary.json"

    if not inp.exists():
        print(f"ERROR: Input file not found: {inp}")
        sys.exit(1)

    print("=" * 60)
    print("  SWMM 2D Flood Demo - Valley Cascading Prototype")
    print("=" * 60)
    print(f"  DLL:   {dll}")
    print(f"  Input: {inp.name}")
    print(f"  Output: {rpt.name}, {out.name}")
    print()

    # Initialize
    runner = SwmmRunner(dll, inp, rpt, out)
    runner.open()

    n_sub = runner.objects["subcatchments"]["count"]
    n_node = runner.objects["nodes"]["count"]
    n_link = runner.objects["links"]["count"]
    print(f"  Objects loaded: {n_sub} subcatchments, {n_node} nodes, {n_link} links")
    print(f"  Subcatchments: {', '.join(runner.objects['subcatchments']['names'])}")
    print(f"  Nodes:         {', '.join(runner.objects['nodes']['names'])}")
    print(f"  Links:         {', '.join(runner.objects['links']['names'])}")
    print()

    # Simulate
    print("  Running simulation step-by-step...")
    runner.start(save_out=True)
    data, step_count = runner.run_and_collect(collect_interval_sec=60.0)
    print(f"  Completed {step_count} routing steps, collected {len(data['time_min'])} data points")

    # Finish
    runner.end_and_report()
    mass_bal = runner.get_mass_balance()
    runner.close()

    print()
    print(f"  Mass Balance:")
    print(f"    Runoff continuity error: {mass_bal['runoff_error_pct']:.4f}%")
    print(f"    Flow continuity error:   {mass_bal['flow_error_pct']:.4f}%")

    # Validate
    if abs(mass_bal["runoff_error_pct"]) > 1.0:
        print(f"  WARNING: Runoff continuity error exceeds 1%!")
    if abs(mass_bal["flow_error_pct"]) > 1.0:
        print(f"  WARNING: Flow continuity error exceeds 1%!")

    # Save
    save_data_csv(data, csv_out)
    print(f"  Time series saved: {csv_out}")

    save_summary_json(data, mass_bal, step_count, json_out)
    print(f"  Summary saved:     {json_out}")

    print()
    print("=" * 60)
    print("  Demo run complete.")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
