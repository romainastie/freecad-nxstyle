# SPDX-License-Identifier: LGPL-2.1-or-later
"""Native PRISMA document adapter. Preview never creates document objects.

Construction recipes are evaluated by the compiled Prisma module. Commits use
native App/Part/Prisma objects and one document transaction, never FeaturePython.
"""

from copy import deepcopy
import json
import math
from .definitions import PreviewResult, Reference, as_spec, command_spec

_TYPES = {"point": "Prisma::ConstructionPoint", "axis": "Prisma::ConstructionAxis",
          "vector": "Prisma::ConstructionAxis", "plane": "Prisma::ConstructionPlane",
          "block": "Prisma::Block", "cylinder": "Prisma::Cylinder"}
_BOOLEANS = {"unite": "Part::Fuse", "subtract": "Part::Cut", "intersect": "Part::Common"}
_CONTAINER_LINKS = {"CurrentOutputs": "App::PropertyLinkListChild",
                    "WorkCoordinateSystem": "App::PropertyLinkChild"}


def _runtime():
    import FreeCAD as App
    import Part
    import Prisma
    return App, Part, Prisma


def _part(doc, create=False):
    part = next((o for o in doc.Objects if getattr(o, "PrismaModel", False)), None)
    if part is None and create:
        part = doc.addObject("App::Part", "PrismaPart")
        part.Label = "Model"
        part.addProperty("App::PropertyBool", "PrismaModel", "PRISMA")
        part.PrismaModel = True
    if part is not None and create:
        _ensure_container_links(part)
    return part


def _ensure_container_links(part):
    """Keep container metadata within its descendants; preserve legacy values.

    Local link scope means the owner's siblings in FreeCAD, so it cannot be
    used for an App::Part's references to its own outputs or WCS. The Child
    property subclasses also persist the correct scope through FCStd restore.
    Call only while creating a document or inside a mutating transaction.
    """
    pending = []
    flag_bits = {"ReadOnly": 1, "Transient": 2, "Hidden": 4, "Output": 8,
                 "NoRecompute": 16, "NoPersist": 32}
    for name, type_id in _CONTAINER_LINKS.items():
        if name not in part.PropertiesList:
            pending.append((name, type_id, None))
        elif part.getTypeIdOfProperty(name) != type_id:
            status = part.getPropertyStatus(name)
            if "LockDynamic" in status:
                raise ValueError(f"Unlock Model.{name} before repairing its link scope")
            pending.append((name, type_id, {
                "value": getattr(part, name), "group": part.getGroupOfProperty(name),
                "documentation": part.getDocumentationOfProperty(name),
                "flags": sum(flag_bits[flag] for flag in part.getTypeOfProperty(name)),
                "status": status,
                "expressions": [(path, expression) for path, expression in part.ExpressionEngine
                                if path == name or path.startswith(name + ".")
                                or path.startswith(name + "[")]}))
    for name, type_id, saved in pending:
        if saved is None:
            part.addProperty(type_id, name, "PRISMA")
            continue
        if not part.removeProperty(name):
            raise ValueError(f"Cannot repair Model.{name}: the property cannot be replaced")
        part.addProperty(type_id, name, saved["group"], saved["documentation"], saved["flags"])
        setattr(part, name, saved["value"])
        for path, expression in saved["expressions"]:
            part.setExpression(path, expression)
        part.setPropertyStatus(name, saved["status"])
    return bool(pending)


def repair_document_metadata(doc):
    """Repair legacy model link scopes in one undoable operation.

    Existing geometry, output ordering and WCS placement are preserved. Normal
    commits perform the same repair inside their own transaction; previews do
    not modify an existing document.
    """
    App, _, _ = _runtime()
    if App.getActiveTransaction():
        raise RuntimeError("Finish the current operation before repairing model metadata")
    part = _part(doc)
    if part is None or all(name in part.PropertiesList and part.getTypeIdOfProperty(name) == type_id
                           for name, type_id in _CONTAINER_LINKS.items()):
        return False
    App.setActiveDocument(doc.Name)
    App.setActiveTransaction("Repair model metadata", True)
    try:
        changed = _ensure_container_links(part)
        doc.recompute()
        App.closeActiveTransaction(False)
        return changed
    except Exception:
        App.closeActiveTransaction(True)
        raise


def new_document(name="Unnamed"):
    App, _, _ = _runtime()
    doc = App.newDocument(name)
    doc.UndoMode = 1
    _part(doc, True)
    doc.recompute()
    return doc


def terminal_outputs(doc):
    part = _part(doc)
    return list(part.CurrentOutputs) if part is not None else []


def get_wcs(doc):
    App, _, _ = _runtime()
    part = _part(doc)
    frame = getattr(part, "WorkCoordinateSystem", None)
    return frame.getGlobalPlacement() if frame is not None else App.Placement()


def set_wcs(doc, frame):
    """Set the coordinate-entry frame; existing captured inputs do not move."""
    App, _, _ = _runtime()
    if App.getActiveTransaction():
        raise RuntimeError("Finish the current operation before changing the WCS")
    App.setActiveDocument(doc.Name)
    App.setActiveTransaction("Set WCS", True)
    try:
        part = _part(doc, True)
        cs = part.WorkCoordinateSystem
        if cs is None:
            cs = doc.addObject("Part::LocalCoordinateSystem", "WCS")
            part.addObject(cs)
            part.WorkCoordinateSystem = cs
        placement = frame.getGlobalPlacement() if hasattr(frame, "getGlobalPlacement") else frame
        cs.Placement = part.getGlobalPlacement().inverse().multiply(placement)
        doc.recompute()
        App.closeActiveTransaction(False)
        return cs
    except Exception:
        App.closeActiveTransaction(True)
        raise


def _placement_record(p):
    return {"base": [p.Base.x, p.Base.y, p.Base.z], "rotation": list(p.Rotation.Q)}


def _recipe_signature(spec):
    ignored = {"captured_frame", "snapshot_placement", "snapshot_signature", "snapshot_dimensions",
               "boolean", "target", "keep_target", "keep_tool", "_specified"}
    def normalized(value):
        if isinstance(value, dict):
            return {key: normalized(item) for key, item in value.items() if key not in ignored}
        if isinstance(value, (list, tuple)):
            return [normalized(item) for item in value]
        return value
    return json.dumps(normalized(spec), sort_keys=True)


def _show(obj, visible):
    view = getattr(obj, "ViewObject", None)
    if view is not None:
        view.Visibility = visible


class ModelAdapter:
    def __init__(self, doc=None):
        self.App, self.Part, self.native = _runtime()
        self.doc = doc if doc is not None else self.App.ActiveDocument
        if self.doc is None:
            raise ValueError("Create a model document first")
        self._captured_wcs = get_wcs(self.doc)

    def begin_context(self, spec=None, editing=None):
        self._captured_wcs = get_wcs(self.doc)

    def _context(self):
        part = _part(self.doc)
        return part.getGlobalPlacement() if part is not None else self.App.Placement()

    def _frame(self, spec):
        saved = spec.get("captured_frame")
        if saved:
            return self.App.Placement(self.App.Vector(*saved["base"]), self.App.Rotation(*saved["rotation"]))
        if spec.get("frame", "absolute") == "wcs":
            world = get_wcs(self.doc) if spec.get("associative_frame", False) else self._captured_wcs
        else:
            world = self.App.Placement()
        return self._context().inverse().multiply(world)

    def _ref(self, value):
        value = value.to_dict() if isinstance(value, Reference) else value
        if isinstance(value, str):
            value = {"object": value}
        if not isinstance(value, dict) or not value.get("object"):
            raise ValueError("Select a reference object")
        if value.get("document") not in (None, "", self.doc.Name):
            raise ValueError("Use a linked component for references from another document")
        obj = self.doc.getObject(value["object"])
        if obj is None:
            raise ValueError(f"Reference {value['object']!r} no longer exists")
        sub = value.get("subelement", "")
        return obj, [sub] if sub else [""]

    @staticmethod
    def _list(value):
        if value is None:
            return []
        return value if isinstance(value, (list, tuple)) else [value]

    def _refs(self, spec, key):
        value = spec.get("references", {}).get(key, spec.get(key, []))
        return [self._ref(v) for v in self._list(value)]

    def _number(self, value, anchor=None, dimension="scalar"):
        if isinstance(value, bool):
            raise ValueError("Expected a numeric expression")
        if isinstance(value, (int, float)):
            result = float(value)
        else:
            text = str(value).strip()
            if not text or len(text) > 4096:
                raise ValueError("Enter a numeric expression")
            anchor = anchor or _part(self.doc)
            if anchor is not None:
                parsed = anchor.evalExpression(text)
            else:
                parsed = self.App.Units.Quantity(text)
            unit = getattr(parsed, "Unit", None)
            scalar = self.App.Units.Quantity("1").Unit
            expected = self.App.Units.Quantity({"scalar": "1", "length": "1 mm", "angle": "1 deg"}[dimension]).Unit
            if unit is not None and unit != scalar and unit != expected:
                raise ValueError(f"Expected a {dimension} expression")
            result = float(getattr(parsed, "Value", parsed))
        if not math.isfinite(result):
            raise ValueError("An expression must have a finite value")
        return result

    def _point(self, spec):
        if spec.get("_specified") is False:
            raise ValueError("Specify the required point")
        result = self._evaluate(as_spec(spec))
        if not result.valid:
            raise ValueError(result.error)
        return result.placement.Base

    def _vector(self, spec):
        result = self._evaluate(as_spec(spec))
        if not result.valid:
            raise ValueError(result.error)
        return result.placement.Rotation.multVec(self.App.Vector(0, 0, 1))

    def _construction(self, spec, helpers=False):
        kind, method = spec["kind"], spec["method"]
        fields = spec.get("fields", {})
        frame = self._frame(spec)
        args = {"Frame": frame, "Context": self._context(), "Associative": True,
                "Reverse": bool(spec.get("reverse", False)), "Support": [], "Points": [], "FrameSupport": None,
                "OffsetMode": "None", "OffsetValues": self.App.Vector()}
        if spec.get("frame") == "wcs" and spec.get("associative_frame"):
            part = _part(self.doc)
            args["FrameSupport"] = getattr(part, "WorkCoordinateSystem", None)
        for key in spec.get("references", {}):
            args["Support"].extend(self._refs(spec, key))
        for refs in spec.get("references", {}).values():
            for ref in self._list(refs):
                if isinstance(ref, dict) and len(ref.get("point", [])) == 3:
                    args["PickPoint"] = self._context().inverse().multVec(self.App.Vector(*ref["point"]))
                    args["HasPickPoint"] = True
        def nested_point(key):
            value = spec.get(key)
            if value is None:
                raise ValueError(f"Specify {key}")
            if helpers:
                obj = self._create(as_spec(value), internal=True)
                args["Support"].append((obj, [""]))
                args.setdefault("_inputs", []).append((key, obj))
            else:
                args["Points"].append(frame.inverse().multVec(self._point(value)))
        def direction(key):
            value = spec.get(key, command_spec("vector"))
            args["Direction"] = frame.Rotation.inverted().multVec(self._vector(value))
            if helpers:
                obj = self._create(as_spec(value), internal=True)
                args["Support"].append((obj, [""]))
                args.setdefault("_inputs", []).append((key, obj))
        for key, prop in (("x", "X"), ("y", "Y"), ("z", "Z"), ("distance", "Distance"),
                          ("angle", "Angle"), ("station", "Station"), ("u", "U"), ("v", "V"),
                          ("ratio", "Ratio"), ("solution", "Solution")):
            if key in fields:
                dimension = "length" if prop in ("X", "Y", "Z", "Distance") else "angle" if prop == "Angle" else "scalar"
                mode = spec.get("coordinate_mode", "cartesian").lower()
                if kind == "point" and mode in ("cylindrical", "spherical") and key == "y":
                    dimension = "angle"
                if kind == "point" and mode == "spherical" and key == "z":
                    dimension = "angle"
                if prop == "Station" and fields.get("station_mode") == "ArcLength":
                    dimension = "length"
                value = self._number(fields[key], dimension=dimension)
                args[prop] = int(value) if prop == "Solution" else value
        args["StationMode"] = fields.get("station_mode", "ArcLengthPercent")
        if kind == "point":
            names = {"coordinates": "Coordinates", "cursor": "Coordinates", "point_expression": "Coordinates",
                     "existing": "Vertex", "end_point": "EndPoint", "midpoint": "Midpoint", "center": "Center",
                     "quadrant": "Quadrant", "angle_on_arc": "AngleOnArc", "intersection": "Intersection",
                     "on_curve": "OnCurve", "on_face": "OnFace", "offset": "Offset", "between_points": "BetweenPoints"}
            if method == "inferred":
                method = self._infer_point(spec)
            args["Method"] = names[method]
            if method in ("coordinates", "cursor", "point_expression"):
                coordinate_mode = spec.get("coordinate_mode", "cartesian").title()
                args["CoordinateMode"] = coordinate_mode
                if coordinate_mode in ("Cylindrical", "Spherical"):
                    args["Radius"] = self._number(fields.get("x", "0"), dimension="length")
                    args["Azimuth"] = self._number(fields.get("y", "0"), dimension="angle")
                    if coordinate_mode == "Spherical":
                        args["Elevation"] = self._number(fields.get("z", "0"), dimension="angle")
            if method == "between_points":
                nested_point("point1")
                nested_point("point2")
            elif method == "offset":
                nested_point("point")
                for key, prop in (("dx", "X"), ("dy", "Y"), ("dz", "Z")):
                    args[prop] = self._number(fields.get(key, "0"), dimension="length")
        elif kind in ("axis", "vector"):
            names = {"components": "Components", "point_direction": "Components", "two_points": "TwoPoints",
                     "edge": "Edge", "axis": "AxisOfCurveFace", "curve_face_axis": "AxisOfCurveFace", "face_normal": "FaceNormal",
                     "intersection": "Intersection", "bisector": "Bisector", "on_curve": "Tangent",
                     "at_angle_to_xc": "AtAngle", "view_direction": "Components"}
            if method == "inferred":
                method = "face_normal" if self._first_shape(spec).ShapeType == "Face" else "edge"
            if method in ("xc", "yc", "zc"):
                args.update(Method="Principal", Solution=("xc", "yc", "zc").index(method))
            else:
                args["Method"] = names[method]
            if method == "two_points":
                nested_point("point1")
                nested_point("point2")
            elif kind == "axis" and method in ("components", "point_direction"):
                nested_point("point")
                direction("direction")
            elif method == "components":
                args["Direction"] = self.App.Vector(*(self._number(fields.get(k, "0")) for k in ("i", "j", "k")))
            elif method == "view_direction":
                world_frame = self._context().multiply(frame)
                args["Direction"] = world_frame.Rotation.inverted().multVec(self.App.Vector(*spec.get("view_direction", (0, 0, 1))))
        else:
            names = {"coincident": "Coincident", "at_distance": "Offset", "three_points": "ThreePoints",
                     "point_normal": "PointNormal", "at_angle": "Angle", "bisector": "Bisector", "tangent": "Tangent",
                     "on_curve": "OnCurve", "coefficients": "Coefficients", "two_lines": "TwoLines",
                     "curves_points": "CurvesPoints", "view_plane": "PointNormal"}
            if method in ("xy", "yz", "xz"):
                args.update(Method="Principal", Solution=("xy", "yz", "xz").index(method))
            else:
                args["Method"] = names[method]
            if method == "three_points":
                for key in ("point1", "point2", "point3"):
                    nested_point(key)
            elif method == "point_normal":
                nested_point("point")
                direction("normal")
            elif method == "tangent" and "point" in spec:
                nested_point("point")
            elif method == "coefficients":
                args["Direction"] = self.App.Vector(*(self._number(fields.get(k, "0")) for k in ("a", "b", "c")))
                args["Distance"] = self._number(fields.get("d", "0"), dimension="length")
            elif method == "view_plane":
                world_frame = self._context().multiply(frame)
                args["Direction"] = world_frame.Rotation.inverted().multVec(self.App.Vector(*spec.get("view_direction", (0, 0, 1))))
                args["Points"] = [world_frame.inverse().multVec(self.App.Vector(*spec.get("view_point", (0, 0, 0))))]
        offset = spec.get("offset", {})
        if offset.get("method", "none") != "none":
            args["OffsetMode"] = offset["method"].title()
            dimensions = {"rectangular": ("length", "length", "length"),
                          "cylindrical": ("length", "angle", "length"),
                          "spherical": ("length", "angle", "angle")}[offset["method"]]
            values = [self._number(v, dimension=d) for v, d in zip(offset.get("values", []), dimensions)]
            values += [0.0] * (3 - len(values))
            args["OffsetValues"] = self.App.Vector(*values[:3])
        if not spec.get("associative", True):
            placement = self.native.evaluate("Axis" if kind == "vector" else kind.title(),
                                             {k: v for k, v in args.items() if not k.startswith("_")})
            args.update(Associative=False, SnapshotPlacement=placement)
            args["Support"] = []
            args.pop("FrameSupport", None)
        return args

    @staticmethod
    def _has_references(spec):
        return any(spec.get("references", {}).values()) or any(
            ModelAdapter._has_references(v) for v in spec.values() if isinstance(v, dict) and "kind" in v)

    def _first_shape(self, spec):
        refs = [ref for value in spec.get("references", {}).values() for ref in self._list(value)]
        if not refs:
            raise ValueError("Select an object")
        obj, subs = self._ref(refs[0])
        # Whole datums have computed geometry rather than a Shape property, and
        # getSubObject can return None for a qualified whole-datum path. Resolve
        # them through the same native Part shape service used by construction.
        shape = self.Part.getShape(obj, subs[0], needSubElement=True, transform=True)
        if shape.isNull():
            raise ValueError("The selected reference no longer resolves to geometry")
        return shape

    def _infer_point(self, spec):
        shape = self._first_shape(spec)
        if shape.ShapeType == "Vertex":
            return "existing"
        if shape.ShapeType == "Edge":
            curve = getattr(shape, "Curve", None)
            return "center" if type(curve).__name__ in ("Circle", "Ellipse") else "end_point"
        if shape.ShapeType == "Face":
            return "on_face"
        raise ValueError("Cannot infer a point from this object")

    def _solid_parameters(self, spec, helpers=False):
        kind, method = spec["kind"], spec["method"]
        fields = spec.get("fields", {})
        frame = self._frame(spec)
        args = {"Context": self._context(), "Associative": bool(spec.get("associative", True)),
                "Frame": frame, "FrameSupport": None, "OriginSupport": [], "Points": [],
                "PlacementOffset": self.App.Placement()}
        if kind == "cylinder" and spec.get("reverse", False):
            args["PlacementOffset"] = self.App.Placement(self.App.Vector(), self.App.Rotation(self.App.Vector(1, 0, 0), 180))
        if kind == "block" and args["Associative"] and spec.get("frame") == "wcs" and spec.get("associative_frame"):
            args["FrameSupport"] = getattr(_part(self.doc), "WorkCoordinateSystem", None)
        inputs = []
        def add_point(key):
            point_spec = as_spec(spec.get(key, command_spec("point")))
            point = self._point(point_spec)
            if helpers and args["Associative"]:
                obj = self._create(point_spec, internal=True)
                args["OriginSupport"].append((obj, [""]))
                inputs.append((key, obj))
            else:
                args["Points"].append(frame.inverse().multVec(point))
            return point
        if kind == "block":
            args["BlockMethod"] = {"origin_lengths": "OriginLengths", "two_points_height": "TwoPointsHeight",
                                   "diagonal_points": "DiagonalPoints"}[method]
            add_point("origin")
            if method != "origin_lengths":
                add_point("point2")
            for key in ("length", "width", "height"):
                if key in fields:
                    args[key.title()] = self._number(fields[key], dimension="length")
            size = self.native.dimensions("Block", dict(args, Associative=True))
            dimensions = {"Length": size.x, "Width": size.y, "Height": size.z}
        else:
            args["ArcSupport"] = []
            args["CylinderMethod"] = "ArcHeight" if method == "arc_height" else "AxisDiameterHeight"
            if method == "arc_height":
                args["ArcSupport"] = self._refs(spec, "arc")
            else:
                origin = add_point("origin")
                direction = self._vector(spec.get("direction", command_spec("vector")))
                args["Frame"] = self.App.Placement(origin, self.App.Rotation(self.App.Vector(0, 0, 1), direction))
                args["Points"] = []
                args["Radius"] = self._number(fields["diameter"], dimension="length") / 2
                if helpers and args["Associative"]:
                    axis = self._create(as_spec(spec.get("direction", command_spec("vector"))), internal=True)
                    args["FrameSupport"] = axis
                    inputs.append(("direction", axis))
            args["Height"] = self._number(fields["height"], dimension="length")
            size = self.native.dimensions("Cylinder", dict(args, Associative=True))
            dimensions = {"Radius": size.x, "Height": size.y}
        if any(v <= 1e-9 or not math.isfinite(v) for v in dimensions.values()):
            raise ValueError("All solid dimensions must be greater than zero")
        args.update(dimensions)
        if not args["Associative"]:
            live = dict(args, Associative=True)
            args["SnapshotPlacement"] = self.native.evaluate(kind.title(), live)
        args["_inputs"] = inputs
        return args, dimensions

    def _evaluate(self, spec):
        kind = spec["kind"]
        if (not spec.get("associative", True) and spec.get("snapshot_placement")
                and spec.get("snapshot_signature") == _recipe_signature(spec)):
            saved = spec["snapshot_placement"]
            placement = self.App.Placement(self.App.Vector(*saved["base"]), self.App.Rotation(*saved["rotation"]))
            if kind in ("block", "cylinder"):
                dims = spec["snapshot_dimensions"]
                shape = self.Part.makeBox(dims["Length"], dims["Width"], dims["Height"]) if kind == "block" \
                    else self.Part.makeCylinder(dims["Radius"], dims["Height"])
                shape.Placement = placement
                boolean = spec.get("boolean", "none")
                if boolean != "none":
                    targets = self._refs(spec, "target")
                    if len(targets) != 1:
                        raise ValueError("Select one target body")
                    self._validate_body(targets[0][0])
                    shape = self._combine(boolean, self._body_shape(targets[0][0]), shape)
                shape.Placement = self._context().multiply(shape.Placement)
                return PreviewResult(True, shape=shape, placement=placement, values=dims)
            world = self._context().multiply(placement)
            direction = world.Rotation.multVec(self.App.Vector(0, 0, 1))
            return PreviewResult(True, placement=placement,
                                 values={"point": tuple(world.Base), "vector": tuple(direction), "normal": tuple(direction)})
        if kind in _BOOLEANS:
            return self._preview_boolean(spec)
        if kind in ("block", "cylinder"):
            args, dims = self._solid_parameters(spec)
            placement = self.native.evaluate(kind.title(), {k: v for k, v in args.items() if not k.startswith("_")})
            shape = self.Part.makeBox(dims["Length"], dims["Width"], dims["Height"]) if kind == "block" \
                else self.Part.makeCylinder(dims["Radius"], dims["Height"])
            shape.Placement = placement
            boolean = spec.get("boolean", "none")
            if boolean != "none":
                targets = self._refs(spec, "target")
                if len(targets) != 1:
                    raise ValueError("Select one target body")
                self._validate_body(targets[0][0])
                shape = self._combine(boolean, self._body_shape(targets[0][0]), shape)
            self._validate_shape(shape)
            shape.Placement = self._context().multiply(shape.Placement)
            return PreviewResult(True, shape, placement, values=dims)
        args = self._construction(spec)
        placement = self.native.evaluate("Axis" if kind == "vector" else kind.title(), args)
        world = self._context().multiply(placement)
        z = world.Rotation.multVec(self.App.Vector(0, 0, 1))
        return PreviewResult(True, placement=placement,
                             values={"point": tuple(world.Base), "vector": tuple(z), "normal": tuple(z)})

    def preview(self, spec):
        try:
            return self._evaluate(as_spec(spec))
        except Exception as exc:
            return PreviewResult(False, error=str(exc))

    @staticmethod
    def _validate_shape(shape):
        if shape.isNull() or not shape.isValid() or not shape.Solids:
            raise ValueError("The operation does not produce a valid solid")

    def _combine(self, operation, target, tool):
        if operation not in _BOOLEANS:
            raise ValueError("Unknown Boolean operation")
        result = {"unite": target.fuse, "subtract": target.cut, "intersect": target.common}[operation](tool)
        self._validate_shape(result)
        if operation == "unite" and len(result.Solids) != 1:
            raise ValueError("The tool does not intersect the target body")
        if operation == "subtract" and abs(result.Volume - target.Volume) <= max(1e-9, abs(target.Volume) * 1e-10):
            raise ValueError("The operation does not change the target body")
        return result

    def _validate_body(self, obj):
        part = _part(self.doc)
        owner = obj.getParentGeoFeatureGroup()
        if part is None or owner not in (None, part):
            raise ValueError("Boolean inputs must belong to this model or be unowned imported bodies")
        if not hasattr(obj, "Shape") or len(obj.Shape.Solids) != 1:
            raise ValueError("Boolean inputs must be single solid bodies")

    def _body_shape(self, obj):
        """Return operand geometry in the model's local coordinate system."""
        shape = obj.Shape.copy()
        if obj.getParentGeoFeatureGroup() is None:
            shape.Placement = self._context().inverse().multiply(shape.Placement)
        return shape

    def _adopt_body(self, obj):
        """Move an imported root body into the model without changing world geometry."""
        self._validate_body(obj)
        if obj.getParentGeoFeatureGroup() is None:
            placement = obj.getGlobalPlacement()
            _part(self.doc, True).addObject(obj)
            obj.Placement = self._context().inverse().multiply(placement)

    def _boolean_inputs(self, spec):
        target = self._refs(spec, "target")
        tools = self._refs(spec, "tools")
        if len(target) != 1 or len(tools) != 1:
            raise ValueError("Select exactly one target and one tool body")
        objects = [target[0][0]] + [obj for obj, _ in tools]
        if len({obj.Name for obj in objects}) != len(objects):
            raise ValueError("Target and tool bodies must be distinct")
        for obj in objects:
            self._validate_body(obj)
        return objects[0], objects[1:]

    def _preview_boolean(self, spec):
        target, tools = self._boolean_inputs(spec)
        shape = self._body_shape(target)
        for tool in tools:
            shape = self._combine(spec["kind"], shape, self._body_shape(tool))
        shape.Placement = self._context().multiply(shape.Placement)
        return PreviewResult(True, shape=shape)

    def _metadata(self, obj, spec, internal=False):
        if "PrismaDefinition" not in obj.PropertiesList:
            obj.addProperty("App::PropertyString", "PrismaDefinition", "PRISMA")
            obj.addProperty("App::PropertyBool", "PrismaInternal", "PRISMA")
        saved = deepcopy(spec)
        saved["captured_frame"] = _placement_record(self._frame(spec))
        if not spec.get("associative", True) and hasattr(obj, "SnapshotPlacement"):
            saved["snapshot_placement"] = _placement_record(obj.SnapshotPlacement)
            saved["snapshot_dimensions"] = {key: getattr(obj, key).Value for key in
                                             ("Length", "Width", "Height", "Radius") if key in obj.PropertiesList}
            saved["snapshot_signature"] = _recipe_signature(saved)
        obj.PrismaDefinition = json.dumps(saved, sort_keys=True)
        obj.PrismaInternal = internal
        _show(obj, not internal)

    def _bindings(self, obj, spec, args):
        if "PrismaReferenceBindings" not in obj.PropertiesList:
            obj.addProperty("App::PropertyString", "PrismaReferenceBindings", "PRISMA")
            obj.addProperty("App::PropertyLinkList", "PrismaConstructorInputs", "PRISMA")
            obj.addProperty("App::PropertyString", "PrismaInputPaths", "PRISMA")
        bindings = []
        position = 0
        for group, references in (spec.get("references", {}).items() if spec.get("associative", True) else []):
            for index, _ref in enumerate(self._list(references)):
                prop = "ArcSupport" if spec["kind"] == "cylinder" and group == "arc" else "Support"
                bindings.append({"path": [group, index], "property": prop, "index": position})
                position += 1
        obj.PrismaReferenceBindings = json.dumps(bindings)
        inputs = args.get("_inputs", [])
        obj.PrismaConstructorInputs = [child for _key, child in inputs]
        obj.PrismaInputPaths = json.dumps([key for key, _child in inputs])

    def _assign(self, obj, args):
        for key, value in args.items():
            if key.startswith("_") or key == "Context":
                continue
            if key not in obj.PropertiesList:
                raise RuntimeError(f"The native Prisma module lacks property {key}; rebuild it")
            setattr(obj, key, value)

    def _expressions(self, obj, spec):
        fields = spec.get("fields", {})
        mapping = {"x": "X", "y": "Y", "z": "Z", "dx": "X", "dy": "Y", "dz": "Z",
                   "d": "Distance", "distance": "Distance", "angle": "Angle",
                   "station": "Station", "u": "U", "v": "V", "ratio": "Ratio",
                   "length": "Length", "width": "Width", "height": "Height"}
        coordinate_mode = spec.get("coordinate_mode", "cartesian").lower()
        if spec["kind"] == "point" and coordinate_mode in ("cylindrical", "spherical"):
            mapping.update(x="Radius", y="Azimuth")
            if coordinate_mode == "spherical":
                mapping["z"] = "Elevation"
        for key, prop in mapping.items():
            if key in fields and prop in obj.PropertiesList and not (spec["kind"] == "cylinder" and key == "angle"):
                obj.setExpression(prop, str(fields[key]))
        if "diameter" in fields and "Radius" in obj.PropertiesList:
            obj.setExpression("Radius", "(" + str(fields["diameter"]) + ") / 2")
        if spec["kind"] == "vector" and spec["method"] == "components":
            for key, axis in zip(("i", "j", "k"), ("x", "y", "z")):
                obj.setExpression("Direction." + axis, str(fields.get(key, "0")))
        if spec["kind"] == "plane" and spec["method"] == "coefficients":
            for key, axis in zip(("a", "b", "c"), ("x", "y", "z")):
                obj.setExpression("Direction." + axis, str(fields.get(key, "0")))
        offset = spec.get("offset", {})
        if offset.get("method", "none") != "none":
            for axis, value in zip(("x", "y", "z"), offset.get("values", [])):
                obj.setExpression("OffsetValues." + axis, str(value))

    def _create(self, spec, internal=False, editing=None):
        kind = spec["kind"]
        if kind in _BOOLEANS:
            return self._create_boolean(spec, editing)
        obj = editing
        if obj is None:
            obj = self.doc.addObject(_TYPES[kind], kind.title())
            _part(self.doc, True).addObject(obj)
        elif obj.TypeId != _TYPES[kind]:
            raise ValueError("The selected feature has a different native type")
        if editing is not None:
            for path, _expression in list(getattr(obj, "ExpressionEngine", [])):
                obj.setExpression(path, None)
        restored_snapshot = (editing is not None and not spec.get("associative", True)
                             and spec.get("snapshot_placement")
                             and spec.get("snapshot_signature") == _recipe_signature(spec))
        if restored_snapshot:
            # Existing snapshot geometry needs no live source to confirm an edit.
            args = {}
        elif kind in ("block", "cylinder"):
            args, dims = self._solid_parameters(spec, helpers=True)
            self._assign(obj, args)
            for prop, value in dims.items():
                setattr(obj, prop, value)
        else:
            args = self._construction(spec, helpers=True)
            self._assign(obj, args)
        self._expressions(obj, spec)
        self._metadata(obj, spec, internal)
        self._bindings(obj, spec, args)
        self.doc.recompute()
        if kind in ("block", "cylinder") and spec.get("boolean", "none") != "none":
            targets = self._refs(spec, "target")
            if len(targets) != 1:
                raise ValueError("Select one target body")
            boolean = command_spec(spec["boolean"])
            boolean["references"] = {"target": [{"object": targets[0][0].Name}], "tools": [{"object": obj.Name}]}
            boolean.update(keep_target=spec.get("keep_target", False), keep_tool=spec.get("keep_tool", False))
            result = self._create_boolean(boolean)
            result.addProperty("App::PropertyLink", "PrismaPrimitive", "PRISMA")
            result.PrismaPrimitive = obj
            _show(obj, bool(spec.get("keep_tool", False)))
            self._metadata(result, spec, internal)
            return result
        return obj

    def _create_boolean(self, spec, editing=None):
        target, tools = self._boolean_inputs(spec)
        for operand in [target] + tools:
            self._adopt_body(operand)
        chain = list(getattr(editing, "PrismaOperationNodes", [])) + [editing] if editing is not None else []
        if editing is not None and (editing.TypeId != _BOOLEANS[spec["kind"]] or len(chain) != len(tools)):
            raise ValueError("Changing a Boolean's operation or tool count requires a new operation")
        result = target
        nodes = []
        for i, tool in enumerate(tools):
            node = chain[i] if chain else self.doc.addObject(_BOOLEANS[spec["kind"]], spec["kind"].title())
            if not chain:
                _part(self.doc, True).addObject(node)
            node.Base, node.Tool = result, tool
            result = node
            nodes.append(node)
        if "PrismaOperationNodes" not in result.PropertiesList:
            result.addProperty("App::PropertyLinkList", "PrismaOperationNodes", "PRISMA")
        # Exclude self to avoid an expression/document graph cycle.
        result.PrismaOperationNodes = nodes[:-1]
        self._metadata(result, spec)
        for node in nodes[:-1]:
            _show(node, False)
        return result

    def commit(self, spec, editing=None):
        spec = as_spec(spec)
        if editing is not None:
            self._validate_edit_dependencies(spec, editing)
        checked = self.preview(spec)
        if not checked.valid:
            raise ValueError(checked.error)
        if self.App.getActiveTransaction():
            raise RuntimeError("A document transaction is already active")
        doc = self.doc
        self.App.setActiveDocument(doc.Name)
        self.App.setActiveTransaction(("Edit " if editing is not None else "") + spec["kind"].title(), True)
        try:
            part = _part(doc, True)
            previous = list(part.CurrentOutputs)
            old_helpers = self._internal_dependencies(editing) if editing is not None else []
            underlying = getattr(editing, "PrismaPrimitive", None) if editing is not None else None
            if underlying is not None:
                old = self.load(editing)
                if old.get("boolean") != spec.get("boolean"):
                    raise ValueError("Changing a primitive's Boolean mode requires a new operation")
                pure = deepcopy(spec)
                pure["boolean"] = "none"
                self._create(pure, editing=underlying)
                targets = self._refs(spec, "target")
                if len(targets) != 1 or targets[0][0] in (editing, underlying):
                    raise ValueError("Select a distinct target body")
                self._adopt_body(targets[0][0])
                editing.Base, editing.Tool = targets[0][0], underlying
                result = editing
                self._metadata(result, spec)
            else:
                result = self._create(spec, editing=editing)
            doc.recompute()
            diagnostic = getattr(result, "Diagnostic", "")
            if not result.isValid() or diagnostic:
                raise ValueError(diagnostic or result.getStatusString())
            if spec["kind"] in ("block", "cylinder") or spec["kind"] in _BOOLEANS:
                self._validate_shape(result.Shape)
            if spec["kind"] in ("block", "cylinder") or spec["kind"] in _BOOLEANS:
                self._rebuild_outputs(part, previous)
            self._cleanup_helpers(old_helpers)
            self.App.closeActiveTransaction(False)
            return result
        except Exception:
            self.App.closeActiveTransaction(True)
            doc.recompute()
            raise

    def _validate_edit_dependencies(self, spec, editing):
        """FreeCAD permits some reference cycles; reject them before mutation."""
        forbidden = {editing.Name}
        primitive = getattr(editing, "PrismaPrimitive", None)
        if primitive is not None:
            forbidden.add(primitive.Name)
        references = []
        def collect(value):
            if not isinstance(value, dict) or "kind" not in value:
                return
            associative = value.get("associative", True)
            if associative:
                for key in value.get("references", {}):
                    references.extend(obj for obj, _subs in self._refs(value, key))
            if "target" in value:
                references.extend(obj for obj, _subs in self._refs(value, "target"))
            if associative and value.get("frame") == "wcs" and value.get("associative_frame"):
                cs = getattr(_part(self.doc), "WorkCoordinateSystem", None)
                if cs is not None:
                    references.append(cs)
            if associative:
                for child in value.values():
                    if isinstance(child, dict) and "kind" in child:
                        collect(child)
        collect(spec)
        pending, seen = references[:], set()
        while pending:
            obj = pending.pop()
            if obj.Name in forbidden:
                raise ValueError("This reference would create a circular dependency")
            if obj.Name not in seen:
                seen.add(obj.Name)
                pending.extend(obj.OutList)

    def load(self, feature):
        raw = getattr(feature, "PrismaDefinition", "")
        if not raw:
            raise ValueError("This feature has no PRISMA construction definition")
        spec = as_spec(json.loads(raw))
        primitive = getattr(feature, "PrismaPrimitive", None)
        source = primitive if primitive is not None else feature
        if primitive is not None:
            saved_primitive = json.loads(primitive.PrismaDefinition)
            for key in ("captured_frame", "snapshot_placement", "snapshot_signature", "snapshot_dimensions"):
                if key in saved_primitive:
                    spec[key] = saved_primitive[key]
        if feature.TypeId in _BOOLEANS.values():
            nodes = list(getattr(feature, "PrismaOperationNodes", [])) + [feature]
            target = {"object": nodes[0].Base.Name, "document": self.doc.Name}
            if primitive is not None:
                spec["target"] = [target]
            else:
                spec["references"]["target"] = [target]
                spec["references"]["tools"] = [{"object": node.Tool.Name, "document": self.doc.Name} for node in nodes]
        for binding in json.loads(getattr(source, "PrismaReferenceBindings", "[]")):
            support = getattr(source, binding["property"], [])
            if binding["index"] >= len(support):
                raise ValueError("A saved construction reference is missing")
            obj, subs = support[binding["index"]]
            if obj is None:
                raise ValueError("A saved construction reference is broken")
            group, index = binding["path"]
            spec["references"][group][index] = {"object": obj.Name, "document": self.doc.Name,
                                                 "subelement": subs[0] if subs else ""}
        paths = json.loads(getattr(source, "PrismaInputPaths", "[]"))
        for path, child in zip(paths, getattr(source, "PrismaConstructorInputs", [])):
            spec[path] = self.load(child)
        mapping = {"x": "X", "y": "Y", "z": "Z", "dx": "X", "dy": "Y", "dz": "Z",
                   "d": "Distance", "distance": "Distance", "angle": "Angle",
                   "station": "Station", "u": "U", "v": "V", "ratio": "Ratio",
                   "length": "Length", "width": "Width", "height": "Height"}
        mode = spec.get("coordinate_mode", "cartesian").lower()
        if spec["kind"] == "point" and mode in ("cylindrical", "spherical"):
            mapping.update(x="Radius", y="Azimuth")
            if mode == "spherical":
                mapping["z"] = "Elevation"
        expressions = dict(getattr(source, "ExpressionEngine", []))
        for key, prop in mapping.items():
            if key in spec["fields"] and prop in source.PropertiesList:
                spec["fields"][key] = expressions.get(prop, str(getattr(getattr(source, prop), "Value", getattr(source, prop))))
        return spec

    def _rebuild_outputs(self, part, previous):
        """Reconcile terminal bodies from native links, including retention edits.

        Consumed inputs remain native history objects. Recomputing the endpoint
        set restores an old target when a Boolean is retargeted and supports
        toggling retention after the original operation hid its operands.
        """
        candidates = {}
        for obj in list(part.Group) + list(previous):
            if hasattr(obj, "Shape") and obj.Shape.Solids and not getattr(obj, "PrismaInternal", False):
                candidates[obj.Name] = obj
        consumed, intermediate = set(), set()
        for obj in list(part.Group):
            if obj.TypeId not in _BOOLEANS.values() or not getattr(obj, "PrismaDefinition", ""):
                continue
            recipe = json.loads(obj.PrismaDefinition)
            nodes = list(getattr(obj, "PrismaOperationNodes", [])) + [obj]
            target, tools = nodes[0].Base, [node.Tool for node in nodes]
            for operand in [target] + tools:
                candidates[operand.Name] = operand
            if not recipe.get("keep_target", False):
                consumed.add(target.Name)
            if not recipe.get("keep_tool", False):
                consumed.update(tool.Name for tool in tools)
            intermediate.update(node.Name for node in nodes[:-1])
        outputs = [obj for name, obj in candidates.items() if name not in consumed and name not in intermediate]
        part.CurrentOutputs = outputs
        for name, obj in candidates.items():
            _show(obj, name not in consumed and name not in intermediate)

    @staticmethod
    def _internal_dependencies(feature):
        pending = list(getattr(feature, "OutList", []))
        seen, result = set(), []
        while pending:
            obj = pending.pop()
            if obj.Name in seen:
                continue
            seen.add(obj.Name)
            if getattr(obj, "PrismaInternal", False):
                result.append(obj)
                pending.extend(obj.OutList)
            elif getattr(feature, "PrismaPrimitive", None) is obj:
                pending.extend(obj.OutList)
        return result

    def _cleanup_helpers(self, candidates):
        candidates = {o.Name: o for o in candidates if self.doc.getObject(o.Name) is not None}
        part = _part(self.doc)
        kept = {name for name, obj in candidates.items()
                if any(parent is not part and parent.Name not in candidates for parent in obj.InList)}
        pending = [candidates[name] for name in kept]
        while pending:
            for child in pending.pop().OutList:
                if child.Name in candidates and child.Name not in kept:
                    kept.add(child.Name)
                    pending.append(child)
        for name in set(candidates) - kept:
            self.doc.removeObject(name)
