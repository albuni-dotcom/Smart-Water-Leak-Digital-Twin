"""mechanical/fem_analysis.py

FreeCAD FEM static analysis of the Smart Water Leak Detection frame (V2).

Target: FreeCAD 1.1.4 (also compatible with 0.20 / 0.21 / 1.0)

Run inside FreeCAD
------------------
From the GUI (Report View shows all messages):

    FreeCAD mechanical/fem_analysis.py

From the command line (headless, requires freecadcmd on PATH):

    freecadcmd mechanical/fem_analysis.py

Requirements
------------
* FreeCAD 1.1.4 or newer.
* FEM workbench with Gmsh and CalculiX (ccx) installed.
* No external Python packages are required.

Output
------
* Maximum Von Mises stress (MPa)
* Maximum displacement (mm)
* Safety factor against yield (Aluminum 6063-T5, 145 MPa)
* PASS / MARGINAL / FAIL verdict

Notes on units (FreeCAD 1.1.4)
------------------------------
* CalculiX FRD results are imported in SI units: stress in Pa,
  displacement in m. The extraction helpers below convert them
  explicitly to MPa and mm.
* The legacy SolverCcxTools result object still exposes
  ``StressValues`` (scalar per node) and ``DisplacementLengths``.
  Recent builds may expose ``vonMises`` instead of ``StressValues``;
  both are checked.
"""

import os
import sys
import traceback


# ------------------------------------------------------------------
# FreeCAD imports (must be inside FreeCAD)
# ------------------------------------------------------------------
try:
    import FreeCAD as App
    import Part
    import ObjectsFem
    import Fem  # noqa: F401  — ensures the FEM workbench is loaded
    from FreeCAD import Base
except ImportError as exc:
    print("This script must be run inside FreeCAD (freecad or freecadcmd).")
    print(f"Import error: {exc}")
    sys.exit(1)


# ==================================================================
# Configuration
# ==================================================================
PROFILE_MM = 20.0
COLUMN_X_MM = 175.0
COLUMN_Y_MM = 145.0
FRAME_HEIGHT_MM = 1500.0

MATERIAL_NAME = "Aluminum 6063-T5"
YOUNGS_MODULUS = "69000 MPa"
POISSON_RATIO = "0.33"
DENSITY = "2700 kg/m^3"

YIELD_STRENGTH_MPA = 145.0
LOAD_N = 170.0
MESH_MAX_MM = 15.0
MESH_MIN_MM = 3.0

DOC_NAME = "SmartWaterFrameFEM"


# ==================================================================
# Logging helpers
# ==================================================================
def log(msg):
    App.Console.PrintMessage(str(msg) + "\n")


def log_err(msg):
    App.Console.PrintError(str(msg) + "\n")


def log_sep():
    log("=" * 66)


# ==================================================================
# Geometry
# ==================================================================
def build_frame_shape():
    """Build the idealized frame as a single fused solid.

    All coordinates are in millimetres.
    """
    parts = []

    # Vertical columns
    for x in (-COLUMN_X_MM, COLUMN_X_MM):
        for y in (-COLUMN_Y_MM, COLUMN_Y_MM):
            parts.append(Part.makeBox(
                PROFILE_MM, PROFILE_MM, FRAME_HEIGHT_MM,
                Base.Vector(
                    x - PROFILE_MM / 2.0,
                    y - PROFILE_MM / 2.0,
                    0.0,
                )
            ))

    # Top frame: X-direction members
    top_frame_z = FRAME_HEIGHT_MM - PROFILE_MM
    for y in (-COLUMN_Y_MM, COLUMN_Y_MM):
        parts.append(Part.makeBox(
            370.0, PROFILE_MM, PROFILE_MM,
            Base.Vector(
                -185.0,
                y - PROFILE_MM / 2.0,
                top_frame_z,
            )
        ))

    # Top frame: Y-direction members
    for x in (-COLUMN_X_MM, COLUMN_X_MM):
        parts.append(Part.makeBox(
            PROFILE_MM, 310.0, PROFILE_MM,
            Base.Vector(
                x - PROFILE_MM / 2.0,
                -155.0,
                top_frame_z,
            )
        ))

    # Horizontal crossbars (front face, y = -145)
    for z in (550.0, 850.0):
        parts.append(Part.makeBox(
            370.0, PROFILE_MM, PROFILE_MM,
            Base.Vector(
                -185.0,
                -155.0,
                z - PROFILE_MM / 2.0,
            )
        ))

    # Fuse into one continuous solid
    log(f"      Fusing {len(parts)} solids...")
    fused = parts[0]
    for p in parts[1:]:
        fused = fused.fuse(p)

    try:
        fused = fused.removeSplitter()
    except Exception as exc:
        log(f"      removeSplitter skipped: {exc}")

    return fused


def select_faces(shape, predicate):
    """Return 1-based FreeCAD face names (e.g. "Face3")."""
    names = []
    for idx, face in enumerate(shape.Faces):
        try:
            if predicate(face):
                names.append(f"Face{idx + 1}")
        except Exception:
            pass
    return names


# ==================================================================
# FEM helpers
# ==================================================================
def run_gmsh_mesh(mesh_obj):
    """Generate the Gmsh mesh programmatically. Returns True on success."""
    try:
        import femmesh.gmshtools as gmshtools
    except ImportError as exc:
        log_err(f"      Gmsh Python bindings unavailable: {exc}")
        return False

    try:
        gmsh_tools = gmshtools.GmshTools(mesh_obj)
        err = gmsh_tools.create_mesh()
    except Exception as exc:
        log_err(f"      Gmsh execution error: {exc}")
        return False

    if err:
        log_err(f"      Gmsh reported an error: {err}")
        return False

    fem_mesh = mesh_obj.FemMesh
    if fem_mesh is None or fem_mesh.NodeCount == 0:
        log_err("      Gmsh produced an empty mesh.")
        return False

    log(f"      Nodes:    {fem_mesh.NodeCount}")
    log(f"      Volume elements: {fem_mesh.VolumeCount}")
    return True


def find_result_object(analysis):
    """Locate the first Fem::FemResultObject inside the analysis group."""
    for obj in analysis.Group:
        try:
            if obj.isDerivedFrom("Fem::FemResultObject"):
                return obj
        except Exception:
            continue
    return None


def extract_max_von_mises(result_obj):
    """Return the maximum Von Mises stress in MPa.

    FreeCAD 1.1.4 imports CalculiX FRD results in SI units: stress in
    Pa. Some builds expose ``vonMises``, older builds expose
    ``StressValues``. Both are checked, and the result is converted to
    MPa explicitly.
    """
    values = []

    if hasattr(result_obj, "vonMises") and result_obj.vonMises:
        try:
            values = list(result_obj.vonMises)
        except Exception:
            values = []

    if not values and hasattr(result_obj, "StressValues") \
            and result_obj.StressValues:
        try:
            values = list(result_obj.StressValues)
        except Exception:
            values = []

    if not values:
        return 0.0

    try:
        max_vm = float(max(values))
    except (TypeError, ValueError):
        return 0.0

    # Explicit Pa -> MPa conversion for FreeCAD 1.1.4.
    if max_vm > 10000.0:
        max_vm /= 1.0e6
    # Defensive fallback: some builds return MPa directly.
    elif 0.0 < max_vm < 1.0:
        max_vm *= 1.0e6

    return max_vm


def extract_max_displacement(result_obj):
    """Return the maximum displacement magnitude in mm.

    FreeCAD 1.1.4 imports displacement in metres. Convert to mm
    explicitly.
    """
    values = []

    if hasattr(result_obj, "DisplacementLengths") \
            and result_obj.DisplacementLengths:
        try:
            values = list(result_obj.DisplacementLengths)
        except Exception:
            values = []

    if not values and hasattr(result_obj, "DisplacementVectors") \
            and result_obj.DisplacementVectors:
        try:
            values = [v.Length for v in result_obj.DisplacementVectors]
        except Exception:
            values = []

    if not values:
        return 0.0

    try:
        max_disp = float(max(values))
    except (TypeError, ValueError):
        return 0.0

    # Explicit m -> mm conversion for FreeCAD 1.1.4.
    if 0.0 < max_disp < 1.0e-3:
        max_disp *= 1000.0

    return max_disp


def make_solver(doc):
    """Create a CalculiX solver object, compatible with FreeCAD 1.1.4."""
    factory = getattr(ObjectsFem, "makeSolverCalculiXCcxTools", None)
    if factory is None:
        factory = getattr(ObjectsFem, "makeSolverCalculixCcxTools", None)
    if factory is None:
        raise RuntimeError(
            "Neither makeSolverCalculiXCcxTools nor "
            "makeSolverCalculixCcxTools is available in this FreeCAD build."
        )
    return factory(doc, "SolverCcxTools")


# ==================================================================
# Main
# ==================================================================
def main():
    log_sep()
    log("Smart Water Leak Detection - Frame FEM Analysis")
    log_sep()

    # --------------------------------------------------------
    # 0. Fresh document
    # --------------------------------------------------------
    if DOC_NAME in App.listDocuments():
        App.closeDocument(DOC_NAME)
    doc = App.newDocument(DOC_NAME)

    # --------------------------------------------------------
    # 1. Geometry
    # --------------------------------------------------------
    log("[1/7] Building idealized frame geometry...")
    try:
        shape = build_frame_shape()
    except Exception as exc:
        log_err(f"      Geometry build failed: {exc}")
        traceback.print_exc()
        return

    frame_obj = doc.addObject("Part::Feature", "Frame")
    frame_obj.Shape = shape
    frame_obj.Label = "Aluminum Frame (fused)"
    doc.recompute()

    bb = shape.BoundBox
    log(f"      Volume: {shape.Volume / 1000.0:.2f} cm^3")
    log(f"      Bounding box: {bb.XLength:.1f} x {bb.YLength:.1f} x "
        f"{bb.ZLength:.1f} mm")
    log(f"      Faces: {len(shape.Faces)}")

    # --------------------------------------------------------
    # 2. Analysis container
    # --------------------------------------------------------
    log("[2/7] Creating FEM analysis container...")
    analysis = ObjectsFem.makeAnalysis(doc, "Analysis")
    doc.recompute()

    # --------------------------------------------------------
    # 3. Material
    # --------------------------------------------------------
    log(f"[3/7] Assigning material: {MATERIAL_NAME}")
    material = ObjectsFem.makeMaterialSolid(doc, "Material_Aluminum")
    mat_dict = material.Material
    mat_dict["Name"] = MATERIAL_NAME
    mat_dict["YoungsModulus"] = YOUNGS_MODULUS
    mat_dict["PoissonRatio"] = POISSON_RATIO
    mat_dict["Density"] = DENSITY
    material.Material = mat_dict
    analysis.addObject(material)
    doc.recompute()

    # --------------------------------------------------------
    # 4. Mesh
    # --------------------------------------------------------
    log(f"[4/7] Generating Gmsh mesh "
        f"(min={MESH_MIN_MM} mm, max={MESH_MAX_MM} mm)...")
    mesh = ObjectsFem.makeMeshGmsh(doc, "FEMMeshGmsh")
    mesh.Shape = frame_obj
    mesh.CharacteristicLengthMax = MESH_MAX_MM
    mesh.CharacteristicLengthMin = MESH_MIN_MM
    mesh.ElementOrder = "1st"
    mesh.ElementDimension = "3D"
    analysis.addObject(mesh)
    doc.recompute()

    if not run_gmsh_mesh(mesh):
        log_err("      Mesh generation failed - aborting.")
        return

    # --------------------------------------------------------
    # 5. Constraints
    # --------------------------------------------------------
    log("[5/7] Adding boundary conditions and loads...")

    fixed_faces = select_faces(
        frame_obj.Shape,
        lambda f: f.CenterOfMass.z < 1.0,
    )
    if not fixed_faces:
        log_err("      No bottom faces (z < 1 mm) found - aborting.")
        return
    log(f"      Fixed faces (column bottoms): {len(fixed_faces)}")

    fixed = ObjectsFem.makeConstraintFixed(doc, "FixedConstraint")
    fixed.References = [(frame_obj, name) for name in fixed_faces]
    analysis.addObject(fixed)

    def is_top(f):
        c = f.CenterOfMass
        if c.z < FRAME_HEIGHT_MM - 10.0:
            return False
        try:
            return f.normalAt(0, 0).z > 0.9
        except Exception:
            return False

    top_faces = select_faces(frame_obj.Shape, is_top)
    if not top_faces:
        log_err("      No top faces (z ~ 1500 mm) found - aborting.")
        return
    log(f"      Top faces (load targets): {len(top_faces)}")

    # FreeCAD 1.1+ requires a directional reference, not a vector.
    vertical_edge_name = None
    for idx, edge in enumerate(frame_obj.Shape.Edges):
        try:
            z0 = edge.Vertexes[0].Point.z
            z1 = edge.Vertexes[1].Point.z
            if abs(z0 - z1) > 100.0:
                vertical_edge_name = f"Edge{idx + 1}"
                break
        except Exception:
            continue

    if vertical_edge_name is None:
        log_err("      No vertical edge found for force direction - aborting.")
        return
    log(f"      Force direction edge: {vertical_edge_name}")

    force = ObjectsFem.makeConstraintForce(doc, "ForceConstraint")
    force.References = [(frame_obj, name) for name in top_faces]
    try:
        force.Force = f"{LOAD_N} N"
    except Exception:
        force.Force = LOAD_N

    force.Direction = (frame_obj, [vertical_edge_name])
    force.Reversed = True
    analysis.addObject(force)

    # --------------------------------------------------------
    # 6. Solver
    # --------------------------------------------------------
    log("[6/7] Configuring CalculiX solver...")
    try:
        solver = make_solver(doc)
    except Exception as exc:
        log_err(f"      Cannot create solver: {exc}")
        return

    for attr, value in (
        ("AnalysisType", "static"),
        ("GeometricalNonlinearity", "linear"),
        ("ThermoMechSteadyState", False),
        ("MatrixSolverType", "default"),
        ("IterationsControlParameterTimeUse", False),
        ("SplitInputWriter", False),
    ):
        try:
            setattr(solver, attr, value)
        except AttributeError:
            pass

    analysis.addObject(solver)
    doc.recompute()

    # --------------------------------------------------------
    # 7. Run CalculiX
    # --------------------------------------------------------
    log("[7/7] Running CalculiX...")
    try:
        from femtools import ccxtools
        fea = ccxtools.FemToolsCcx(analysis, solver)
        fea.update_objects()
        fea.setup_working_dir()
        fea.setup_ccx()
        err = fea.check_prerequisites()
        if err:
            log_err(f"      Prerequisites failed: {err}")
            return
        fea.purge_results()
        fea.write_inp_file()
        fea.ccx_run()
        fea.load_results()
    except Exception as exc:
        log_err(f"      Solver execution failed: {exc}")
        traceback.print_exc()
        return

    # --------------------------------------------------------
    # Results
    # --------------------------------------------------------
    result_obj = find_result_object(analysis)
    if result_obj is None:
        log_err("      No result object found. Check the Report View "
                "for CalculiX errors.")
        return

    max_vm = extract_max_von_mises(result_obj)
    max_disp = extract_max_displacement(result_obj)

    safety = (
        YIELD_STRENGTH_MPA / max_vm
        if max_vm > 1.0e-9 else float("inf")
    )

    log("")
    log_sep()
    log("FEM ANALYSIS RESULTS")
    log_sep()
    log(f"  Max Von Mises stress : {max_vm:10.3f} MPa")
    log(f"  Max displacement     : {max_disp:10.4f} mm")
    log(f"  Yield strength       : {YIELD_STRENGTH_MPA:10.1f} MPa")
    log(f"  Safety factor        : {safety:10.2f}")
    log_sep()
    if safety >= 2.0:
        log("  Status: PASS (safety factor >= 2.0)")
    elif safety >= 1.0:
        log("  Status: MARGINAL (safety factor between 1.0 and 2.0)")
    else:
        log("  Status: FAIL (safety factor < 1.0)")
    log_sep()

    doc.recompute()


if __name__ == "__main__":
    main()
