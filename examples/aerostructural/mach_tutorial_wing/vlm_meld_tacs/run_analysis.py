# rst Imports
from __future__ import division, print_function

import numpy as np
import openmdao.api as om
import tacs_setup
from funtofem.mphys import MeldBuilder
from mpi4py import MPI
from tacs.mphys import TacsBuilder
from vlm_solver.mphys_vlm import VlmBuilder

from mphys import MPhysVariables, Multipoint
from mphys.scenarios import ScenarioAeroStructural


class Top(Multipoint):
    def setup(self):
        # VLM
        mesh_file = "wing_VLM.dat"
        mach = 0.85
        aoa0 = 2.0
        aoa1 = 5.0
        q_inf = 3000.0
        vel = 178.0
        nu = 3.5e-5

        aero_builder = VlmBuilder(mesh_file)
        aero_builder.initialize(self.comm)

        dvs = self.add_subsystem("dvs", om.IndepVarComp(), promotes=["*"])
        dvs.add_output("aoa", val=[aoa0, aoa1], units="deg")
        dvs.add_output("mach", mach)
        dvs.add_output("q_inf", q_inf)
        dvs.add_output("vel", vel)
        dvs.add_output("nu", nu)

        self.add_subsystem("mesh_aero", aero_builder.get_mesh_coordinate_subsystem())

        # TACS setup
        struct_builder = TacsBuilder(
            mesh_file="wingbox_Y_Z_flip.bdf",
            element_callback=tacs_setup.element_callback,
            problem_setup=tacs_setup.problem_setup,
            coupling_loads=[MPhysVariables.Structures.Loads.AERODYNAMIC],
        )
        struct_builder.initialize(self.comm)
        ndv_struct = struct_builder.get_ndv()

        self.add_subsystem(
            "mesh_struct", struct_builder.get_mesh_coordinate_subsystem()
        )

        dvs.add_output("dv_struct", np.array(ndv_struct * [0.002]))

        # MELD setup
        isym = 1
        ldxfer_builder = MeldBuilder(aero_builder, struct_builder, isym=isym)
        ldxfer_builder.initialize(self.comm)

        for iscen, scenario in enumerate(["cruise", "maneuver"]):
            nonlinear_solver = om.NonlinearBlockGS(
                maxiter=25, iprint=2, use_aitken=True, rtol=1e-14, atol=1e-14
            )
            linear_solver = om.LinearBlockGS(
                maxiter=25, iprint=2, use_aitken=True, rtol=1e-14, atol=1e-14
            )
            self.mphys_add_scenario(
                scenario,
                ScenarioAeroStructural(
                    aero_builder=aero_builder,
                    struct_builder=struct_builder,
                    ldxfer_builder=ldxfer_builder,
                ),
                nonlinear_solver,
                linear_solver,
            )

            self.connect(
                f"mesh_aero.{MPhysVariables.Aerodynamics.Surface.Mesh.COORDINATES}",
                f"{scenario}.{MPhysVariables.Aerodynamics.Surface.COORDINATES_INITIAL}",
            )
            self.connect(
                f"mesh_struct.{MPhysVariables.Structures.Mesh.COORDINATES}",
                f"{scenario}.{MPhysVariables.Structures.COORDINATES}",
            )

            for dv in ["q_inf", "vel", "nu", "mach", "dv_struct"]:
                self.connect(dv, f"{scenario}.{dv}")
            self.connect("aoa", f"{scenario}.aoa", src_indices=[iscen])


################################################################################
# OpenMDAO setup
################################################################################
prob = om.Problem()
prob.model = Top()

prob.setup()
om.n2(prob, show_browser=False, outfile="mphys_as_vlm.html")

prob.run_model()

if MPI.COMM_WORLD.rank == 0:
    print("C_L =", prob["cruise.C_L"])
    print("KS =", prob["maneuver.ks_vmfailure"])
