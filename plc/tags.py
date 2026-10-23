"""Load the PLC tag config (k8s/config/plc-tags.json) into flat Tag records.

Nested JSON objects become OPC UA folders; a leaf is either a full tag spec
({"type": ..., "value": ..., "unit": ..., "sim": ..., "alarm": ..., "enum": ...})
or a bare value ({"PLC_ON": 1}) whose type is inferred.
"""
import json
import random
from dataclasses import dataclass, field

TYPES = {"Boolean", "Int16", "UInt16", "Int32", "UInt32", "Float", "Double", "String"}
SPEC_KEYS = {"type", "value", "unit", "sim", "alarm", "enum"}


@dataclass
class Tag:
    path: str                 # "Drive.Speed_Actual"
    type: str                 # OPC UA built-in type name
    value: object
    unit: str = ""
    sim: object = None        # "counter" | {"walk", "min", "max"} | None
    alarm: dict = field(default_factory=dict)
    enum: dict = field(default_factory=dict)


def _infer_type(value):
    if isinstance(value, bool):
        return "Boolean"
    if isinstance(value, int):
        return "Int32"
    if isinstance(value, float):
        return "Float"
    return "String"


def coerce(type_, value):
    if type_ == "Boolean":
        return bool(value)
    if type_ in ("Float", "Double"):
        return float(value)
    if type_ == "String":
        return str(value)
    return int(value)


def _is_spec(node):
    return isinstance(node, dict) and set(node) <= SPEC_KEYS and bool({"type", "value", "sim"} & set(node))


def _flatten(node, prefix, out):
    for name, child in node.items():
        path = f"{prefix}.{name}" if prefix else name
        if isinstance(child, dict) and not _is_spec(child):
            _flatten(child, path, out)
            continue
        spec = child if _is_spec(child) else {"value": child}
        type_ = spec.get("type") or _infer_type(spec.get("value", 0))
        if type_ not in TYPES:
            raise ValueError(f"{path}: unknown type {type_!r} (expected one of {sorted(TYPES)})")
        default = "" if type_ == "String" else 0
        out.append(Tag(
            path=path,
            type=type_,
            value=coerce(type_, spec.get("value", default)),
            unit=spec.get("unit", ""),
            sim=spec.get("sim"),
            alarm=spec.get("alarm", {}),
            enum=spec.get("enum", {}),
        ))


def load(config_path, plc_id):
    """Return (plc_config, [Tag]) for one PLC."""
    with open(config_path) as f:
        config = json.load(f)
    if plc_id not in config:
        raise KeyError(f"{plc_id} not in {config_path} (has: {', '.join(config)})")
    plc = config[plc_id]
    tags = []
    _flatten(plc.get("tags", {}), "", tags)
    return plc, tags


def apply_generic_sim(tags, values, dt):
    """Drive tags that declare a "sim" behaviour; profiles handle the rest."""
    for tag in tags:
        if tag.sim == "counter":
            values[tag.path] = (values[tag.path] + 1) % 2**32
        elif isinstance(tag.sim, dict) and "walk" in tag.sim:
            lo, hi = tag.sim.get("min", float("-inf")), tag.sim.get("max", float("inf"))
            mid = (lo + hi) / 2 if lo > float("-inf") and hi < float("inf") else tag.value
            v = values[tag.path] + random.gauss(0, tag.sim["walk"]) * dt**0.5 + 0.05 * (mid - values[tag.path]) * dt
            values[tag.path] = coerce(tag.type, min(hi, max(lo, v)))
