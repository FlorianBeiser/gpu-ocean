# -*- coding: utf-8 -*-

"""
This python module implements the Forward Backward Linear numerical 
scheme for the shallow water equations, described in 
L. P. Røed, "Documentation of simple ocean models for use in ensemble
predictions", Met no report 2012/3 and 2012/5 .

Copyright (C) 2016  SINTEF ICT

This program is free software: you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.

This program is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
GNU General Public License for more details.

You should have received a copy of the GNU General Public License
along with this program.  If not, see <http://www.gnu.org/licenses/>.
"""

#Import packages we need
import numpy as np
import pyopencl as cl #OpenCL in Python
import Common
        
        
        
        
        
        




reload(Common)






"""
Class that solves the SW equations using the Forward-Backward linear scheme
"""
class FBL:

    """
    Initialization routine
    H: Water depth incl ghost cells, (nx+2)*(ny+2) cells
    eta0: Initial deviation from mean sea level incl ghost cells, (nx+2)*(ny+2) cells
    hu0: Initial momentum along x-axis incl ghost cells, (nx+1)*(ny+2) cells
    hv0: Initial momentum along y-axis incl ghost cells, (nx+2)*(ny+1) cells
    nx: Number of cells along x-axis
    ny: Number of cells along y-axis
    dx: Grid cell spacing along x-axis (20 000 m)
    dy: Grid cell spacing along y-axis (20 000 m)
    dt: Size of each timestep (90 s)
    g: Gravitational accelleration (9.81 m/s^2)
    f: Coriolis parameter (1.2e-4 s^1)
    r: Bottom friction coefficient (2.4e-3 m/s)
    wind_stress: Wind stress parameters
    """
    def __init__(self, \
                 cl_ctx, \
                 H, eta0, hu0, hv0, \
                 nx, ny, \
                 dx, dy, dt, \
                 g, f, r, \
                 wind_stress=Common.WindStressParams(), \
                 boundary_conditions=Common.BoundaryConditions(), \
                 block_width=16, block_height=16):
        reload(Common)
        self.cl_ctx = cl_ctx
        self.boundary_conditions = boundary_conditions
        
        print("(ny, nx): " + str((ny, nx)))
        print("H.shape: " + str(H.shape))
        print("eta.shape: " + str(eta0.shape))
        print("U shape: " + str(hu0.shape))
        print("V shape: " + str(hv0.shape))
                
        #Create an OpenCL command queue
        self.cl_queue = cl.CommandQueue(self.cl_ctx)

        #Get kernels
        self.u_kernel = Common.get_kernel(self.cl_ctx, "FBL_U_kernel.opencl", block_width, block_height)
        self.v_kernel = Common.get_kernel(self.cl_ctx, "FBL_V_kernel.opencl", block_width, block_height)
        self.eta_kernel = Common.get_kernel(self.cl_ctx, "FBL_eta_kernel.opencl", block_width, block_height)
                
        #Create data by uploading to device
        ghost_cells_x = 0
        ghost_cells_y = 0
        asym_ghost_cells = None
        if not self.boundary_conditions.isDefault():
            asym_ghost_cells = [0, 0, 0, 0]
            if self.boundary_conditions.north == 2:
                asym_ghost_cells[0] = 1
            if self.boundary_conditions.east == 2:
                asym_ghost_cells[1] = 1
            
        self.H = Common.OpenCLArray2D(self.cl_ctx, nx, ny, ghost_cells_x, ghost_cells_y, H, asym_ghost_cells)
        self.cl_data = Common.SWEDataArakawaC(self.cl_ctx, nx, ny, ghost_cells_x, ghost_cells_y, eta0, hu0, hv0, asym_ghost_cells)
        
        #Save input parameters
        #Notice that we need to specify them in the correct dataformat for the
        #OpenCL kernel
        self.nx = np.int32(nx)
        self.ny = np.int32(ny)
        self.dx = np.float32(dx)
        self.dy = np.float32(dy)
        self.dt = np.float32(dt)
        self.g = np.float32(g)
        self.f = np.float32(f)
        self.r = np.float32(r)
        self.wind_stress = wind_stress
        
        #Initialize time
        self.t = np.float32(0.0)
        
        #Compute kernel launch parameters
        self.local_size = (block_width, block_height) # WARNING::: MUST MATCH defines of block_width/height in kernels!
        self.global_size =  ( \
                       int(np.ceil(self.nx / float(self.local_size[0])) * self.local_size[0]), \
                       int(np.ceil(self.ny / float(self.local_size[1])) * self.local_size[1]) \
                      ) 
        print("FBL.local_size: " + str(self.local_size))
        print("FBL.global_size: " + str(self.global_size))

        self.bc_kernel = FBL_periodic_boundary(self.cl_ctx, \
                                               self.nx, \
                                               self.ny, \
                                               self.boundary_conditions, \
                                               asym_ghost_cells
        )
        
    
    """
    Function which steps n timesteps
    """
    def step(self, t_end=0.0):
        n = int(t_end / self.dt + 1)
        
        for i in range(0, n):        
            local_dt = np.float32(min(self.dt, t_end-i*self.dt))
            
            if (local_dt <= 0.0):
                break

            
            
                
            self.u_kernel.computeUKernel(self.cl_queue, self.global_size, self.local_size, \
                    self.nx, self.ny, \
                    self.dx, self.dy, local_dt, \
                    self.g, self.f, self.r, \
                    self.H.data, self.H.pitch, \
                    self.cl_data.hu0.data, self.cl_data.hu0.pitch, \
                    self.cl_data.hv0.data, self.cl_data.hv0.pitch, \
                    self.cl_data.h0.data, self.cl_data.h0.pitch, \
                    self.wind_stress.type, \
                    self.wind_stress.tau0, self.wind_stress.rho, self.wind_stress.alpha, self.wind_stress.xm, self.wind_stress.Rc, \
                    self.wind_stress.x0, self.wind_stress.y0, \
                    self.wind_stress.u0, self.wind_stress.v0, \
                    self.t)

            if (self.boundary_conditions.north == 2):
                # Fix U boundary
                self.bc_kernel.periodicBoundaryConditionU(self.cl_queue, self.cl_data.hu0)
            
            self.v_kernel.computeVKernel(self.cl_queue, self.global_size, self.local_size, \
                    self.nx, self.ny, \
                    self.dx, self.dy, local_dt, \
                    self.g, self.f, self.r, \
                    self.H.data, self.H.pitch, \
                    self.cl_data.hu0.data, self.cl_data.hu0.pitch, \
                    self.cl_data.hv0.data, self.cl_data.hv0.pitch, \
                    self.cl_data.h0.data, self.cl_data.h0.pitch, \
                    self.wind_stress.type, \
                    self.wind_stress.tau0, self.wind_stress.rho, self.wind_stress.alpha, self.wind_stress.xm, self.wind_stress.Rc, \
                    self.wind_stress.x0, self.wind_stress.y0, \
                    self.wind_stress.u0, self.wind_stress.v0, \
                    self.t)

            # Fix V boundary
            if (self.boundary_conditions.east == 2) :
                self.bc_kernel.periodicBoundaryConditionV(self.cl_queue, self.cl_data.hv0)
            
            self.eta_kernel.computeEtaKernel(self.cl_queue, self.global_size, self.local_size, \
                    self.nx, self.ny, \
                    self.dx, self.dy, local_dt, \
                    self.g, self.f, self.r, \
                    self.H.data, self.H.pitch, \
                    self.cl_data.hu0.data, self.cl_data.hu0.pitch, \
                    self.cl_data.hv0.data, self.cl_data.hv0.pitch, \
                    self.cl_data.h0.data, self.cl_data.h0.pitch)

            
            self.t += local_dt
        
        return self.t
    
    
    
    
    def download(self):
        return self.cl_data.download(self.cl_queue)


        
        





class FBL_periodic_boundary:
    def __init__(self, cl_ctx, nx, ny, \
                 boundary_conditions, asym_ghost_cells, \
                 block_width=16, block_height=16 ):

        self.cl_ctx = cl_ctx
        self.boundary_conditions = boundary_conditions
        self.asym_ghost_cells = asym_ghost_cells
        
        self.nx = np.int32(nx)
        self.ny = np.int32(ny)

        # Load kernel for periodic boundary.
        self.periodicBoundaryKernel \
            = Common.get_kernel(self.cl_ctx,\
            "FBL_periodic_boundary.opencl", block_width, block_height)

         #Compute kernel launch parameters
        self.local_size = (block_width, block_height) # WARNING::: MUST MATCH defines of block_width/height in kernels!
        self.global_size = ( \
                int(np.ceil(self.nx+1 / float(self.local_size[0])) * self.local_size[0]), \
                int(np.ceil(self.ny+1 / float(self.local_size[1])) * self.local_size[1]) )

    

    """
    Updates hu according periodic boundary conditions
    """
    def periodicBoundaryConditionU(self, cl_queue, hu0):
        ## Currently, this only works with 0 ghost cells:
        assert(hu0.nx == hu0.nx_halo), \
            "The current data does not have zero ghost cells"

        ## Call kernel that swaps the boundaries.
        #print("Periodic boundary conditions")
        self.periodicBoundaryKernel.periodicBoundaryUKernel(cl_queue, self.global_size, self.local_size, \
            self.nx, self.ny, \
            hu0.data, hu0.pitch)

    """
    Updates hv according to periodic boundary conditions
    """
    def periodicBoundaryConditionV(self, cl_queue, hv0):
        ## Currently, this only works with 0 ghost cells:
        assert(hv0.ny == hv0.ny_halo), \
            "The current data does not have zero ghost cells"
        
        ## Call kernel that swaps the boundaries.
        #print("Periodic boundary conditions")
        self.periodicBoundaryKernel.periodicBoundaryVKernel(cl_queue, self.global_size, self.local_size, \
            self.nx, self.ny, \
            hv0.data, hv0.pitch)

        

