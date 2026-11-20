#!/usr/bin/env python3
"""kdata: raw data capture from Klipper/Kalico over the Klippy API socket.

Runs on the printer's host (the CM4). Standard library only.

  kdata.py endpoints
      List every API endpoint and printer object the running config exposes.

  kdata.py record OUTDIR [--stream SPEC ...] [--status OBJ=f1,f2 ...]
                         [--gcode-file FILE | --duration SECONDS]
      Subscribe to bulk streams, optionally run a G-code file, and write
      everything to OUTDIR. Each stream gets its own JSONL file of raw API
      messages; nothing is processed or thrown away.

Stream SPEC is an endpoint path plus optional params, e.g.
    beacon/dump
    beacon/dump_accel
    motion_report/dump_trapq:name=extruder
    motion_report/dump_stepper:name=extruder
    tmc/stallguard_dump:name=extruder

Use --preset squish for the streams experiments/squish needs
(--preset squish-full adds accelerometer, extruder steps and StallGuard).

A G-code file may contain the line
    ; --- record from here ---
Everything before it (heating, cooling) runs first, unrecorded.
"""
import argparse
import json
import os
import shutil
import socket
import sys
import threading
import time

DEFAULT_SOCKET = os.path.expanduser("~/printer_data/comms/klippy.sock")

PRESETS = {
    # what the squish analyses use (~6 MB per minute of recording)
    "squish": {
        "streams": [
            "beacon/dump",
            "motion_report/dump_trapq:name=toolhead",
            "motion_report/dump_trapq:name=extruder",
        ],
        "status": [
            "extruder=temperature,target,power,pressure_advance,"
            "loss_filament,loss_ambient,temp_block",
            "toolhead=estimated_print_time,position",
        ],
    },
}
# everything, including the heavy streams (accelerometer, every extruder step)
PRESETS["squish-full"] = {
    "streams": PRESETS["squish"]["streams"] + [
        "beacon/dump_accel",
        "motion_report/dump_stepper:name=extruder",
        "tmc/stallguard_dump:name=extruder",
    ],
    "status": PRESETS["squish"]["status"],
}

RECORD_MARKER = "; --- record from here ---"
MIN_FREE_MB = 500


def free_mb(path):
    st = os.statvfs(path)
    return st.f_bavail * st.f_frsize / 1e6


class KlippyClient:
    """Minimal JSON-over-unix-socket client (messages end with 0x03)."""

    def __init__(self, path):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.connect(path)
        self.lock = threading.Lock()
        self.next_id = 1
        self.pending = {}  # id -> (event, holder)
        self.handlers = {}  # template key -> callable(msg)
        self.closed = False
        self.reader = threading.Thread(target=self._read_loop, daemon=True)
        self.reader.start()

    def _read_loop(self):
        buf = b""
        while True:
            try:
                data = self.sock.recv(65536)
            except OSError:
                data = b""
            if not data:
                self.closed = True
                for ev, _ in list(self.pending.values()):
                    ev.set()
                return
            buf += data
            parts = buf.split(b"\x03")
            buf = parts.pop()
            for raw in parts:
                try:
                    msg = json.loads(raw)
                except ValueError:
                    continue
                self._dispatch(msg)

    def _dispatch(self, msg):
        if "id" in msg and msg["id"] in self.pending:
            ev, holder = self.pending.pop(msg["id"])
            holder.append(msg)
            ev.set()
            return
        key = msg.get("key")
        if key in self.handlers:
            self.handlers[key](msg)

    def send(self, method, params=None):
        with self.lock:
            mid = self.next_id
            self.next_id += 1
        ev, holder = threading.Event(), []
        self.pending[mid] = (ev, holder)
        out = json.dumps({"id": mid, "method": method, "params": params or {}})
        self.sock.sendall(out.encode() + b"\x03")
        return ev, holder

    def call(self, method, params=None, timeout=None):
        ev, holder = self.send(method, params)
        if not ev.wait(timeout):
            raise TimeoutError("%s timed out" % method)
        if not holder:
            raise ConnectionError("klippy socket closed")
        msg = holder[0]
        if "error" in msg:
            raise RuntimeError("%s: %s" % (method, msg["error"]))
        return msg.get("result")

    def subscribe(self, method, params, key, handler):
        self.handlers[key] = handler
        params = dict(params)
        params["response_template"] = {"key": key}
        return self.call(method, params, timeout=10.0)

    def close(self):
        try:
            self.sock.close()
        except OSError:
            pass


def parse_stream(spec):
    path, _, rest = spec.partition(":")
    params = {}
    if rest:
        for kv in rest.split(","):
            k, _, v = kv.partition("=")
            params[k] = v
    return path, params


def parse_status(spec):
    obj, _, fields = spec.partition("=")
    return obj, [f for f in fields.split(",") if f] or None


def cmd_endpoints(args):
    kc = KlippyClient(args.socket)
    info = kc.call("info", {}, timeout=5)
    eps = kc.call("list_endpoints", {}, timeout=5)
    objs = kc.call("objects/list", {}, timeout=5)
    print("software: %s (%s)" % (info.get("software_version"), info.get("state")))
    print("\nendpoints:")
    for e in sorted(eps.get("endpoints", [])):
        print("  " + e)
    print("\nprinter objects:")
    for o in sorted(objs.get("objects", [])):
        print("  " + o)
    print(
        "\nNote: mux endpoints (dump_trapq, dump_stepper, stallguard_dump, ...)"
        "\nare listed once; valid 'name' values follow the object names above."
    )
    kc.close()


def cmd_record(args):
    streams = list(args.stream or [])
    status = list(args.status or [])
    if args.preset:
        streams = PRESETS[args.preset]["streams"] + streams
        status = PRESETS[args.preset]["status"] + status
    if not streams and not status:
        sys.exit("nothing to record: give --stream/--status or --preset")
    if not args.gcode_file and args.duration is None:
        sys.exit("give --gcode-file or --duration")

    parent = os.path.dirname(os.path.abspath(args.outdir)) or "."
    if os.path.isdir(parent) and free_mb(parent) < MIN_FREE_MB:
        sys.exit("only %.0f MB free on the disk: free some space first "
                 "(need %d MB)" % (free_mb(parent), MIN_FREE_MB))
    prep = script = None
    if args.gcode_file:
        with open(args.gcode_file) as f:
            script = f.read()
        if RECORD_MARKER in script:
            prep, script = script.split(RECORD_MARKER, 1)
    os.makedirs(args.outdir, exist_ok=False)
    if args.gcode_file:
        shutil.copy(args.gcode_file, os.path.join(args.outdir, "experiment.gcode"))
    kc = KlippyClient(args.socket)
    if prep and prep.strip():
        print("preparing (heating/cooling, not recorded) ...")
        try:
            kc.call("gcode/script", {"script": prep}, timeout=None)
        except KeyboardInterrupt:
            kc.close()
            sys.exit("interrupted during preparation; nothing recorded")
    meta = {
        "started_host_time": time.time(),
        "argv": sys.argv,
        "info": kc.call("info", {}, timeout=5),
        "streams": {},
        "status": status,
        "errors": {},
    }
    files, counts = {}, {}
    disk_full = threading.Event()

    def make_handler(key):
        f = files[key]
        lock = threading.Lock()

        def handler(msg):
            if disk_full.is_set():
                return
            line = json.dumps({"recv": time.time(), "params": msg.get("params")})
            with lock:
                try:
                    f.write(line + "\n")
                    counts[key] += 1
                except OSError as e:
                    disk_full.set()
                    print("\nWRITE FAILED (%s): recording stopped; the printer "
                          "keeps running the G-code" % e)

        return handler

    for i, spec in enumerate(streams):
        path, params = parse_stream(spec)
        key = "s%d" % i
        fname = (path.replace("/", "_") + ("_" + "_".join(params.values())
                 if params else "")) + ".jsonl"
        files[key] = open(os.path.join(args.outdir, fname), "w")
        counts[key] = 0
        try:
            header = kc.subscribe(path, params, key, make_handler(key))
            meta["streams"][spec] = {"file": fname, "header": header}
            print("subscribed %-45s %s" % (spec, json.dumps(header)))
        except Exception as e:  # keep going: missing streams are data too
            meta["errors"][spec] = str(e)
            print("FAILED     %-45s %s" % (spec, e))

    if status:
        key = "status"
        files[key] = open(os.path.join(args.outdir, "status.jsonl"), "w")
        counts[key] = 0
        objs = dict(parse_status(s) for s in status)
        try:
            first = kc.subscribe("objects/subscribe", {"objects": objs}, key,
                                 make_handler(key))
            files[key].write(json.dumps({"recv": time.time(), "params": first})
                             + "\n")
            print("subscribed status: %s" % ", ".join(objs))
        except Exception as e:
            meta["errors"]["objects/subscribe"] = str(e)
            print("FAILED     status: %s" % e)

    time.sleep(args.pre)
    t0 = time.time()
    try:
        if args.gcode_file:
            print("running %s ..." % args.gcode_file)
            kc.call("gcode/script", {"script": script}, timeout=None)
            print("gcode finished")
        else:
            time.sleep(args.duration)
    except KeyboardInterrupt:
        print("interrupted (the printer may still be running the G-code; "
              "check Mainsail)")
    except Exception as e:
        meta["errors"]["gcode"] = str(e)
        print("gcode error: %s" % e)
    time.sleep(args.post)
    kc.close()

    meta["capture_seconds"] = time.time() - t0
    meta["message_counts"] = {
        spec: counts["s%d" % i] for i, spec in enumerate(streams)
    }
    meta["disk_full"] = disk_full.is_set()
    for f in files.values():
        try:
            f.close()
        except OSError:
            pass
    with open(os.path.join(args.outdir, "meta.json"), "w") as f:
        json.dump(meta, f, indent=2)
    print("wrote %s" % args.outdir)
    for spec, n in meta["message_counts"].items():
        print("  %-45s %d batches" % (spec, n))


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--socket", default=DEFAULT_SOCKET)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("endpoints").set_defaults(func=cmd_endpoints)
    rp = sub.add_parser("record")
    rp.add_argument("outdir")
    rp.add_argument("--preset", choices=sorted(PRESETS))
    rp.add_argument("--stream", action="append")
    rp.add_argument("--status", action="append")
    rp.add_argument("--gcode-file")
    rp.add_argument("--duration", type=float)
    rp.add_argument("--pre", type=float, default=1.0,
                    help="seconds of data before the gcode starts")
    rp.add_argument("--post", type=float, default=1.5,
                    help="seconds of data after the gcode ends")
    rp.set_defaults(func=cmd_record)
    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
