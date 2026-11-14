# SPDX-License-Identifier: LGPL-2.1-or-later
# Ship the audited UI without its optional third-party workbenches.
file(GLOB_RECURSE PrismaUI_Scripts CONFIGURE_DEPENDS
    RELATIVE "${CMAKE_CURRENT_SOURCE_DIR}/NXStyle"
    "NXStyle/*.py" "NXStyle/*.json" "NXStyle/*.svg"
    "NXStyle/*.qss" "NXStyle/*.png" "NXStyle/*.ts" "NXStyle/*.toml"
)
list(FILTER PrismaUI_Scripts EXCLUDE REGEX "(^|/)__pycache__/")
list(TRANSFORM PrismaUI_Scripts PREPEND "${CMAKE_CURRENT_SOURCE_DIR}/NXStyle/"
    OUTPUT_VARIABLE PrismaUI_SourceFiles)
add_custom_target(PrismaUIScripts ALL SOURCES ${PrismaUI_SourceFiles})
fc_target_copy_resource(PrismaUIScripts
    "${CMAKE_CURRENT_SOURCE_DIR}/NXStyle"
    "${CMAKE_BINARY_DIR}/Mod/NXStyle"
    ${PrismaUI_Scripts}
)
install(DIRECTORY NXStyle/ DESTINATION Mod/NXStyle
    PATTERN "__pycache__" EXCLUDE PATTERN "*.pyc" EXCLUDE)
