"""Simulated PLC: serves its tags over OPC UA.

Address space (namespace urn:edge-scada-sim):
    Objects/<plc-id>/<Folder>/<Tag>     NodeId ns=2;s=<plc-id>.<Folder>.<Tag>
Every tag variable carries properties the gateway reads on browse:
    Unit (String), Enum (JSON String), AlarmLimits (JSON String)

Tags are read-only to clients. Every scan_ms the profile logic and generic
sims update the values; OPC UA subscriptions only fire for values that change.
"""
import asyncio
import json
import logging
import os
import signal
from datetime import datetime, timezone

from asyncua import Server, ua
from profiles import PROFILES
from tags import apply_generic_sim, load

PLC_ID = os.getenv("PLC_ID", "plc-1")
CONFIG = os.getenv("TAGS_CONFIG", "/config/plc-tags.json")
PORT = int(os.getenv("OPCUA_PORT", "4840"))
NAMESPACE = "urn:edge-scada-sim"


async def build_address_space(server, idx, tags):
    """Create folders + variables; return {path: (node, VariantType)}."""
    root = await server.nodes.objects.add_folder(ua.NodeId(PLC_ID, idx), ua.QualifiedName(PLC_ID, idx))
    folders = {"": root}
    nodes = {}
    for tag in tags:
        *parents, name = tag.path.split(".")
        parent_path = ""
        for part in parents:
            path = f"{parent_path}.{part}" if parent_path else part
            if path not in folders:
                folders[path] = await folders[parent_path].add_folder(
                    ua.NodeId(f"{PLC_ID}.{path}", idx), ua.QualifiedName(part, idx))
            parent_path = path
        vtype = getattr(ua.VariantType, tag.type)
        var = await folders[parent_path].add_variable(
            ua.NodeId(f"{PLC_ID}.{tag.path}", idx), ua.QualifiedName(name, idx), ua.Variant(tag.value, vtype))
        await var.add_property(idx, "Unit", tag.unit)
        await var.add_property(idx, "Enum", json.dumps(tag.enum))
        await var.add_property(idx, "AlarmLimits", json.dumps(tag.alarm))
        nodes[tag.path] = (var, vtype)
    return nodes


async def main():
    logging.basicConfig(level=logging.WARNING)
    plc, tags = load(CONFIG, PLC_ID)
    scan = plc.get("scan_ms", 100) / 1000
    profile_name = plc.get("profile")
    if profile_name and profile_name not in PROFILES:
        raise SystemExit(f"{PLC_ID}: unknown profile {profile_name!r} (have: {', '.join(PROFILES)})")

    server = Server()
    await server.init()
    server.set_endpoint(f"opc.tcp://0.0.0.0:{PORT}/")
    server.set_server_name(f"edge-scada-sim {PLC_ID}")
    server.set_security_policy([ua.SecurityPolicyType.NoSecurity])  # ponytail: no security, add certs later
    idx = await server.register_namespace(NAMESPACE)
    nodes = await build_address_space(server, idx, tags)

    values = {t.path: t.value for t in tags}
    profile = PROFILES[profile_name](values) if profile_name else None
    print(f"[{PLC_ID}] {plc.get('description', '')}: {len(tags)} tags, profile={profile_name}, "
          f"scan={scan * 1000:.0f} ms, opc.tcp://0.0.0.0:{PORT}", flush=True)

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)

    async with server:
        last = dict(values)
        while not stop.is_set():
            if profile:
                profile.step(scan)
            apply_generic_sim(tags, values, scan)
            now = datetime.now(timezone.utc)
            for path, value in values.items():
                if value != last[path]:
                    node, vtype = nodes[path]
                    dv = ua.DataValue(ua.Variant(value, vtype), SourceTimestamp=now, ServerTimestamp=now)
                    await server.write_attribute_value(node.nodeid, dv)
            last = dict(values)
            try:
                await asyncio.wait_for(stop.wait(), scan)
            except TimeoutError:
                pass
    print(f"[{PLC_ID}] stopped", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
