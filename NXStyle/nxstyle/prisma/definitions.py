# SPDX-License-Identifier: LGPL-2.1-or-later
"""Serializable input definitions. Importing this module needs neither Qt nor FreeCAD."""

from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any, Mapping


@dataclass(frozen=True)
class Reference:
    object: str
    subelement: str = ""
    document: str = ""
    point: tuple = ()

    def to_dict(self):
        return {"object": self.object, "subelement": self.subelement,
                "document": self.document, "point": list(self.point)}


@dataclass(frozen=True)
class FieldDef:
    name: str
    label: str
    kind: str = "expression"
    default: Any = "0"


@dataclass
class PreviewResult:
    valid: bool
    shape: Any = None
    placement: Any = None
    error: str = ""
    warnings: tuple = ()
    values: dict = field(default_factory=dict)

    @property
    def diagnostic(self):
        return self.error or "; ".join(self.warnings)


@dataclass(frozen=True)
class CommandDefinition:
    """Typed boundary for a JSON-compatible command recipe."""
    kind: str
    method: str
    fields: Mapping = field(default_factory=dict)
    references: Mapping = field(default_factory=dict)
    options: Mapping = field(default_factory=dict)

    def to_dict(self):
        result = command_spec(self.kind, self.method)
        result["fields"].update(deepcopy(dict(self.fields)))
        result["references"].update(deepcopy(dict(self.references)))
        result.update(deepcopy(dict(self.options)))
        return result


def f(name, label, kind="expression", default="0"):
    return FieldDef(name, label, kind, default)


P = lambda name, label: f(name, label, "point", None)
V = lambda name, label: f(name, label, "vector", None)
R = lambda name, label: f(name, label, "reference", [])
B = lambda name, label: f(name, label, "body", [])

FIELD_SCHEMAS = {
    "point": {
        "coordinates": (f("x", "XC"), f("y", "YC"), f("z", "ZC")),
        "inferred": (R("object", "Select Object"),),
        "cursor": (f("x", "XC"), f("y", "YC"), f("z", "ZC")),
        "point_expression": (f("x", "XC Expression"), f("y", "YC Expression"), f("z", "ZC Expression")),
        "existing": (R("object", "Select Point"),),
        "end_point": (R("object", "Select Edge or Point"),),
        "midpoint": (R("object", "Select Edge"),),
        "center": (R("object", "Select Arc, Ellipse or Sphere"),),
        "quadrant": (R("object", "Select Arc or Circle"), f("solution", "Quadrant (0–3)", default="0")),
        "angle_on_arc": (R("object", "Select Arc or Circle"), f("angle", "Angle")),
        "intersection": (R("objects", "Select Two Objects"),),
        "between_points": (P("point1", "First Point"), P("point2", "Second Point"), f("ratio", "Ratio", default="0.5")),
        "on_curve": (R("object", "Select Curve"), f("station", "Position", default="50"),
                     f("station_mode", "Station Mode", "choice", "ArcLengthPercent")),
        "on_face": (R("object", "Select Face"), f("u", "U", default="0.5"),
                    f("v", "V", default="0.5")),
        "offset": (P("point", "Base Point"), f("dx", "Delta XC"), f("dy", "Delta YC"), f("dz", "Delta ZC")),
    },
    "vector": {
        "components": (f("i", "I"), f("j", "J"), f("k", "K", default="1")),
        "xc": (), "yc": (), "zc": (),
        "two_points": (P("point1", "First Point"), P("point2", "Second Point")),
        "edge": (R("object", "Select Linear Edge or Axis"),),
        "face_normal": (R("object", "Select Face"),),
        "axis": (R("object", "Select Axis or Cylindrical Face"),),
        "inferred": (R("object", "Select Object"),),
        "view_direction": (),
        "at_angle_to_xc": (f("angle", "Angle from XC"),),
        "on_curve": (R("object", "Select Curve"), f("station", "Position", default="50"),
                     f("station_mode", "Station Mode", "choice", "ArcLengthPercent")),
    },
    "axis": {
        "components": (P("point", "Specify Point"), V("direction", "Specify Vector")),
        "xc": (), "yc": (), "zc": (),
        "two_points": (P("point1", "First Point"), P("point2", "Second Point")),
        "point_direction": (P("point", "Specify Point"), V("direction", "Specify Vector")),
        "edge": (R("object", "Select Edge"),),
        "curve_face_axis": (R("object", "Select Curve or Face"),),
        "on_curve": (R("object", "Select Curve"), f("station", "Position", default="50"),
                     f("station_mode", "Station Mode", "choice", "ArcLengthPercent")),
        "face_normal": (R("object", "Select Face"),),
        "intersection": (R("objects", "Select Two Planes"),),
        "bisector": (R("objects", "Select Two Axes"),),
    },
    "plane": {
        "xy": (), "yz": (), "xz": (),
        "coincident": (R("object", "Select Plane or Planar Face"),),
        "at_distance": (R("object", "Select Plane or Face"), f("distance", "Distance")),
        "three_points": (P("point1", "First Point"), P("point2", "Second Point"), P("point3", "Third Point")),
        "point_normal": (P("point", "Specify Point"), V("normal", "Specify Normal")),
        "at_angle": (R("object", "Select Plane or Face"), R("axis", "Select Rotation Axis"), f("angle", "Angle")),
        "bisector": (R("objects", "Select Two Planes"),),
        "tangent": (R("object", "Select Face"), P("point", "Specify Point")),
        "on_curve": (R("object", "Select Curve"), f("station", "Position", default="50"),
                     f("station_mode", "Station Mode", "choice", "ArcLengthPercent")),
        "view_plane": (),
        "coefficients": (f("a", "A"), f("b", "B"), f("c", "C", default="1"), f("d", "D")),
        "two_lines": (R("objects", "Select Two Lines"),),
        "curves_points": (R("objects", "Select Curves and Points"),),
    },
    "block": {
        "origin_lengths": (P("origin", "Specify Point"), f("length", "Length (XC)", default="100"),
                           f("width", "Width (YC)", default="100"), f("height", "Height (ZC)", default="100")),
        "two_points_height": (P("origin", "Origin Corner"), P("point2", "Opposite Corner"),
                              f("height", "Height (ZC)", default="100")),
        "diagonal_points": (P("origin", "Origin Corner"), P("point2", "Opposite Corner")),
    },
    "cylinder": {
        "axis_diameter_height": (P("origin", "Specify Point"), V("direction", "Specify Vector"),
                                 f("diameter", "Diameter", default="50"), f("height", "Height", default="100")),
        "arc_height": (R("arc", "Select Arc or Circle"), f("height", "Height", default="100")),
    },
}
for _kind in ("unite", "subtract", "intersect"):
    FIELD_SCHEMAS[_kind] = {"bodies": (B("target", "Target Body"), B("tools", "Tool Body"))}

METHOD_LABELS = {
    "coordinates": "Coordinates", "existing": "Existing Point", "end_point": "End Point",
    "midpoint": "Midpoint", "center": "Arc/Ellipse/Sphere Center", "intersection": "Intersection",
    "between_points": "Between Two Points", "on_curve": "Point on Curve", "on_face": "Point on Face",
    "offset": "Offset", "components": "Components", "xc": "XC Axis", "yc": "YC Axis", "zc": "ZC Axis",
    "two_points": "Two Points", "edge": "Edge", "face_normal": "Face Normal", "axis": "Axis",
    "point_direction": "Point and Direction", "curve_face_axis": "Curve or Face Axis",
    "xy": "XC-YC Plane", "yz": "YC-ZC Plane", "xz": "XC-ZC Plane", "coincident": "Through Object",
    "at_distance": "At Distance", "three_points": "Three Points", "point_normal": "Point and Direction",
    "at_angle": "At Angle", "bisector": "Bisector", "tangent": "Tangent",
    "origin_lengths": "Origin and Edge Lengths", "two_points_height": "Two Points and Height",
    "diagonal_points": "Two Diagonal Points", "axis_diameter_height": "Axis, Diameter, and Height",
    "arc_height": "Arc and Height", "bodies": "Bodies",
    "inferred": "Inferred", "cursor": "Cursor Location", "point_expression": "Point Expression",
    "quadrant": "Quadrant Point", "angle_on_arc": "Angle on Arc", "view_direction": "View Direction",
    "at_angle_to_xc": "At Angle to XC", "view_plane": "View Plane", "coefficients": "Coefficients",
    "two_lines": "Two Lines", "curves_points": "Curves and Points",
}
METHODS = {kind: tuple(methods) for kind, methods in FIELD_SCHEMAS.items()}


def normalize_method(kind, method=None):
    kind = str(kind).lower()
    if kind not in METHODS:
        raise ValueError(f"Unknown command {kind!r}")
    if method is None:
        return METHODS[kind][0]
    if method in METHODS[kind]:
        return method
    key = str(method).casefold().replace("_", " ")
    for candidate in METHODS[kind]:
        if METHOD_LABELS.get(candidate, candidate).casefold() == key:
            return candidate
    raise ValueError(f"Unsupported {kind} method {method!r}")


def command_spec(kind, method=None):
    kind = str(kind).lower()
    method = normalize_method(kind, method)
    spec = {"version": 1, "kind": kind, "method": method, "fields": {}, "references": {},
            "frame": "absolute", "associative": True, "reverse": False,
            "offset": {"method": "none", "values": []}}
    for item in FIELD_SCHEMAS[kind][method]:
        if item.kind in ("reference", "body"):
            spec["references"][item.name] = []
        elif item.kind in ("point", "vector"):
            spec[item.name] = command_spec(item.kind)
        else:
            spec["fields"][item.name] = deepcopy(item.default)
    if kind in ("block", "cylinder"):
        spec.update(boolean="none", target=[], keep_target=False, keep_tool=False)
    if kind in ("unite", "subtract", "intersect"):
        spec.update(keep_target=False, keep_tool=False)
    return spec


def as_spec(value):
    if isinstance(value, CommandDefinition):
        return value.to_dict()
    if not isinstance(value, Mapping):
        raise TypeError("A command definition must be a mapping")
    out = deepcopy(dict(value))
    out["kind"] = str(out["kind"]).lower()
    out["method"] = normalize_method(out["kind"], out.get("method"))
    out.setdefault("fields", {})
    out.setdefault("references", {})
    return out
