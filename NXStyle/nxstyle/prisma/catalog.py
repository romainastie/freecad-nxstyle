# SPDX-License-Identifier: LGPL-2.1-or-later
"""The deliberately small, independently authored Basic Modeling command set."""

from pathlib import Path

_ICONS = Path(__file__).resolve().parents[1] / "resources" / "icons" / "nx"


def icon(name):
    return str(_ICONS / (name + ".svg"))

COMMANDS = {
    "Prisma_Point": ("point", "Point", icon("point")),
    "Prisma_DatumAxis": ("axis", "Datum Axis", icon("datum-axis")),
    "Prisma_DatumPlane": ("plane", "Datum Plane", icon("datum-plane")),
    "Prisma_Block": ("block", "Block", icon("block")),
    "Prisma_Cylinder": ("cylinder", "Cylinder", icon("cylinder")),
    "Prisma_Unite": ("unite", "Unite", icon("boolean")),
    "Prisma_Subtract": ("subtract", "Subtract", icon("boolean")),
    "Prisma_Intersect": ("intersect", "Intersect", icon("boolean")),
    "Prisma_WCS": ("wcs", "WCS", icon("csys")),
    "Prisma_ImportRole": ("role", "Import NX Role…", ":/icons/Std_Import.svg"),
    "Prisma_EditParameters": ("edit", "Edit Parameters", ":/icons/Std_DlgParameter.svg"),
}

KIND_COMMANDS = {kind: command for command, (kind, _label, _icon) in COMMANDS.items()}
SOLID_COMMANDS = frozenset("Prisma_" + name for name in ("Block", "Cylinder", "Unite", "Subtract", "Intersect"))
TITLES = {kind: label for kind, label, _icon in COMMANDS.values()}
TITLES["vector"] = "Vector"

# Constructor labels are contextual: an On Curve vector and point are different
# operations. These are our supported controls, not a claim about hidden NX UI.
METHOD_TEXT = {
    "vector": {"inferred": "Inferred Vector", "on_curve": "On Curve Vector", "components": "Vector Components"},
    "axis": {"on_curve": "On Curve Vector"},
    "plane": {"curves_points": "Curves and Points", "three_points": "Curves and Points", "on_curve": "On Curve"},
}
METHOD_ORDER = {
    "point": ("inferred", "cursor", "existing", "end_point", "midpoint", "center", "quadrant", "intersection",
              "between_points", "on_curve", "on_face", "angle_on_arc", "coordinates", "point_expression", "offset"),
    "vector": ("inferred", "two_points", "edge", "axis", "face_normal", "on_curve", "xc", "yc", "zc",
               "view_direction", "at_angle_to_xc", "components"),
    "plane": ("coincident", "at_distance", "at_angle", "bisector", "curves_points", "tangent", "point_normal",
              "on_curve", "xy", "yz", "xz", "view_plane", "coefficients", "two_lines"),
}


def basic_layout():
    from .roles import default_layout
    result = default_layout()
    for tab in result["tabs"]:
        for group in tab["groups"]:
            for item in group["items"]:
                item.setdefault("size", "large")
    result["tabs"][0]["groups"].append({"name": "Utilities", "items": [
        {"cmd": "Prisma_WCS", "label": "WCS", "size": "small"},
        {"cmd": "Prisma_EditParameters", "label": "Edit Parameters", "size": "small"},
        {"cmd": "Prisma_ImportRole", "label": "Import NX Role…", "size": "small"}]})
    result["tabs"].append({"id": "view", "name": "View", "groups": [
        {"name": "Orientation", "items": [
            {"cmd": "Std_ViewIsometric", "label": "Isometric", "size": "large"},
            {"cmd": "Std_ViewTop", "label": "Top"}, {"cmd": "Std_ViewFront", "label": "Front"},
            {"cmd": "Std_ViewRight", "label": "Right"}]},
        {"name": "Display", "items": [{"cmd": "Std_ViewFitAll", "label": "Fit", "size": "large"},
            {"cmd": "Std_ToggleVisibility", "label": "Show / Hide"}]}]})
    return result
