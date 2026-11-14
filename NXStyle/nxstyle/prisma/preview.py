# SPDX-License-Identifier: LGPL-2.1-or-later
"""A disposable, unpickable Coin preview. It never creates document objects."""

import math


def xyz(value, default=(0., 0., 0.)):
    if value is None:
        return default
    try:
        return tuple(float(getattr(value, c)) for c in ("x", "y", "z"))
    except (AttributeError, TypeError):
        try:
            return tuple(float(value[i]) for i in range(3))
        except (TypeError, IndexError):
            return default


def unit(value):
    value = xyz(value, (0., 0., 1.))
    length = math.sqrt(sum(c * c for c in value))
    return tuple(c / length for c in value) if length > 1e-12 else (0., 0., 1.)


def cross(a, b):
    return (a[1]*b[2]-a[2]*b[1], a[2]*b[0]-a[0]*b[2], a[0]*b[1]-a[1]*b[0])


class Preview:
    def __init__(self):
        self.view = self.node = self.scene = None
        self.visible = True

    def clear(self):
        if self.scene is not None and self.node is not None:
            try:
                self.scene.removeChild(self.node)
            except (RuntimeError, ReferenceError):
                pass
        self.view = self.node = self.scene = None

    def show(self, result, spec, view=None):
        self.clear()
        if not self.visible or not result.valid:
            return
        from pivy import coin
        if view is None:
            import FreeCADGui as Gui
            if Gui.ActiveDocument is None:
                return
            view = Gui.activeDocument().activeView()
        root = coin.SoSeparator()
        pick = coin.SoPickStyle()
        pick.style = coin.SoPickStyle.UNPICKABLE
        root.addChild(pick)
        material = coin.SoMaterial()
        material.diffuseColor = (1., .55, .08)
        material.transparency = .55
        root.addChild(material)
        shape = result.shape
        if shape is not None and not shape.isNull():
            vertices, triangles = shape.tessellate(.3)
            coords = coin.SoCoordinate3()
            coords.point.setValues(0, len(vertices), [xyz(v) for v in vertices])
            root.addChild(coords)
            faces = coin.SoIndexedFaceSet()
            indices = [i for tri in triangles for i in (*tri, -1)]
            faces.coordIndex.setValues(0, len(indices), indices)
            hints = coin.SoShapeHints()
            hints.vertexOrdering = coin.SoShapeHints.COUNTERCLOCKWISE
            hints.shapeType = coin.SoShapeHints.SOLID
            root.addChild(hints)
            root.addChild(faces)
            lines = []
            for edge in shape.Edges:
                try:
                    lines.append([xyz(v) for v in edge.discretize(Deflection=.3)])
                except Exception:
                    pass
            self._lines(root, lines, coin)
        else:
            values = result.values
            placement = result.placement
            origin = xyz(values.get("point", getattr(placement, "Base", None)))
            normal = values.get("vector", values.get("normal"))
            if normal is None and placement is not None:
                import FreeCAD as App
                normal = placement.Rotation.multVec(App.Vector(0, 0, 1))
            normal = unit(normal)
            # World lengths make glyphs stable when nested constructors change.
            size = 20.
            kind = spec.get("kind")
            if kind == "point":
                lines = [[tuple(origin[j] + sign * size*.2 * (j == i) for j in range(3))
                          for sign in (-1, 1)] for i in range(3)]
            elif kind in ("axis", "vector"):
                end = tuple(origin[j]+size*normal[j] for j in range(3))
                side = unit(cross(normal, (1, 0, 0) if abs(normal[0]) < .8 else (0, 1, 0)))
                lines = [[tuple(origin[j]-size*normal[j] for j in range(3)), end],
                         [tuple(end[j]-size*.22*normal[j]+size*.1*side[j] for j in range(3)), end,
                          tuple(end[j]-size*.22*normal[j]-size*.1*side[j] for j in range(3))]]
            else:
                a = unit(cross(normal, (1, 0, 0) if abs(normal[0]) < .8 else (0, 1, 0)))
                b = cross(normal, a)
                corners = [tuple(origin[j]+size*(u*a[j]+v*b[j]) for j in range(3))
                           for u, v in ((-1,-1), (1,-1), (1,1), (-1,1), (-1,-1))]
                lines = [corners, [origin, tuple(origin[j]+size*.5*normal[j] for j in range(3))]]
            self._lines(root, lines, coin)
        self.view, self.node, self.scene = view, root, view.getSceneGraph()
        self.scene.addChild(root)
        view.redraw()

    @staticmethod
    def _lines(root, lines, coin):
        line_root = coin.SoSeparator()
        material = coin.SoMaterial()
        material.diffuseColor = (1., .45, .02)
        material.transparency = 0.
        line_root.addChild(material)
        style = coin.SoDrawStyle()
        style.lineWidth = 2.
        line_root.addChild(style)
        coords = coin.SoCoordinate3()
        points = [point for line in lines for point in line]
        coords.point.setValues(0, len(points), points)
        line_root.addChild(coords)
        line_set = coin.SoLineSet()
        line_set.numVertices.setValues(0, len(lines), [len(line) for line in lines])
        line_root.addChild(line_set)
        root.addChild(line_root)
