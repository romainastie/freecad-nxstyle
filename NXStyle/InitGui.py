# SPDX-License-Identifier: LGPL-2.1-or-later
# NXStyle: NX-like user interface for an unmodified FreeCAD.
# FreeCAD exec()s this file with separate globals and locals, so top-level
# functions cannot see each other; all logic lives in the nxstyle package.
import os

if os.environ.get("PRISMA_PROFILE") == "1":
    import nxstyle.prisma.boot
    nxstyle.prisma.boot.schedule()
elif os.environ.get("NXSTYLE_PROFILE"):
    import nxstyle.boot
    nxstyle.boot.schedule()
