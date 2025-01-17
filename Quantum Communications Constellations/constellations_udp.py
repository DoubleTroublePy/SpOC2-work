# Basic imports
import pykep as pk
import numpy as np
import scipy
import os
from matplotlib import pyplot as plt
import seaborn as sns

# SGP4 - we use SPG4 to propagate orbits around New Mars as a proxy
# for a plausible orbital positions propagator around a habitable planet
from sgp4.api import Satrec, SatrecArray
from sgp4.api import WGS72

# Networkx
import networkx as nx

# Static data
def get_mothership_satellites():
    """Construct array of mothership orbital elements
    (the TLEs of actual Earth-orbiting satellites below are used as a proxy for
    plausible orbital dynamics around a habitable planet)
    """
    mothership_tles = [
        [
            "1 39634U 14016A   22349.82483685  .00000056  00000-0  21508-4 0  9992",
            "2 39634  98.1813 354.7934 0001199  83.3324 276.7993 14.59201191463475"
        ],
        [
            "1 26400U 00037A   00208.84261022 +.00077745 +00000-0 +00000-0 0  9995",
            "2 26400 051.5790 297.6001 0012791 171.3037 188.7763 15.69818870002328"
        ],
        [
            "1 36508U 10013A   22349.92638064  .00000262  00000-0  64328-4 0  9992",
            "2 36508  92.0240 328.0627 0004726  21.3451 338.7953 14.51905975672463"
        ],
        [
            "1 40128U 14050A   22349.31276420 -.00000077  00000-0  00000-0 0  9995",
            "2 40128  50.1564 325.0733 1614819 130.5958 244.6527  1.85519534 54574"
        ],
        [
            "1 49810U 21116B   23065.71091236 -.00000083  00000+0  00000+0 0  9998",
            "2 49810  57.2480  13.9949 0001242 301.4399 239.8890  1.70475839  7777"
        ],
        [
            "1 44878U 19092F   22349.75758852  .00015493  00000-0  00000-0 0  9998",
            "2 44878  97.4767 172.6133 0012815  68.6990 291.5614 15.23910904165768"
        ],
        [
            "1 04382U 70034A   22349.88472104  .00001138  00000-0  18306-3 0  9999",
            "2 04382  68.4200 140.9159 1043234  48.2283 320.3286 13.08911192477908"
        ]
    ]

    # Assembling the list of Satrec motherships
    motherships = []
    for tle in mothership_tles:
        motherships.append(Satrec.twoline2rv(tle[0], tle[1]))
    return motherships


class constellation_udp:
    """A Pygmo compatible UDP (User Defined Problem) representing the constellation design problem for SpOC 2023.

    Two Walker constellations are defined in a mixint chromosome:
        x = [a1,ei,i1,w1,eta1] + [a2,e2,i2,w2,eta2] + [S1,P1,F1] + [S2,P2,F2] + [r1,r2,r3,r4]

    The constellations must relay information between 7 motherships in orbit and 4 rovers on the surface of New Mars
    """
    def __init__(self):
        """Constructor"""

        # Define the time grid over which to optimize the communications network
        self._t0 = 10000 # starting epoch in mjd2000
        self.n_epochs = 11 # number of epochs in time grid to consider
        self._duration = 10 # difference between the first and last epoch considered in years
        jd0, fr = pk.epoch(self._t0, 'mjd2000').jd, 0.0 # reference Julian date
        self.jds = np.linspace(jd0, jd0 + self._duration * 365.25, self.n_epochs) # time grid in JD
        self.frs = self.jds * fr # date fractions (defaults to 0)

        # Reference epoch for SGP4 is 1949 December 31 00:00 UT
        self.ep_ref = pk.epoch_from_iso_string("19491231T000000")

        # SGP4-ready mothership satellites
        mothership_satellites = get_mothership_satellites()
        self.pos_m = self.construct_mothership_pos(SatrecArray(mothership_satellites))
        self.n_motherships = len(mothership_satellites)

        # Latitudes and longitudes of rovers
        rovers_db_path = os.path.join(".", "data", "spoc2", "constellations", "rovers.txt")
        self.rovers_db = np.loadtxt(rovers_db_path)
        self.lambdas = self.rovers_db[:, 0] # latitudes
        self.phis = self.rovers_db[:, 1] # longitudes
        self._min_rover_dist = 3000 # minimum inter-rover distance (km)
        self.n_rovers = 4

        # Minimum line-of-sight parameter (km)
        # Radius amplification factor: 1.05
        self.LOS = 1.05 * pk.EARTH_RADIUS / 1000.0
        # Radius of the New-Mars planet (km)
        self.R_p = pk.EARTH_RADIUS / 1000.0
        # Angular velocity of New Mars (rad/s)
        self.w_p = 7.29e-5 # 2 * pi / (23 hours 56 minutes 4 seconds)
        # Threshold zenith angle constraint for rover-sat link (rad)
        self._zenith_angle = np.pi / 3
        self.eps_z = np.cos(self._zenith_angle)
        # Minimum inter-satellite distance (km)
        self._min_sat_dist = 50

    def get_bounds(self):
        """Get bounds for the decision variables.

        Returns:
            Tuple of lists: bounds for the decision variables.
        """
        lb = [1.06, 0., 0., 0., 1.0] + [2.0, 0., 0., 0., 1.0] + [4, 2, 0] + [4, 2, 0] + [0, 0, 0, 0]
        ub = [1.8, 0.02, np.pi, 2*np.pi, 1000.0] + [3.5, 0.1, np.pi, 2*np.pi, 1000.0] + [10, 10, 9] + [10, 10, 9] + [99, 99, 99, 99]
        return (lb, ub)

    def get_nix(self):
        """Get number of integer variables.

        Returns:
            int: number of integer variables.
        """
        return 6 + 4

    def get_nobj(self):
        """Get number of objectives.

        Returns:
            int: the number of objectives
        """
        return 2
    
    def get_nic(self):
        """Get number of inequality constraints.

        Returns:
            int: the number of constraints
        """
        return 2
    
    def get_rover_constraint(self, lambda0, phi0):
        """Evaluate the rover constraint (minimum distance between any two rovers)

        Args:
            lambda0 (float, N_r x 1): latitudes of the rovers
            phi0 (float, N_r x 1): longitudes of the rovers

        Returns
            float: the difference between the actual and allowable minimum distance between rovers
        """
        # Compute rover positions on the planet
        pos = np.zeros((self.n_rovers, 3))
        pos[:, 0] = np.sin(lambda0) * np.cos(phi0)
        pos[:, 1] = np.cos(lambda0) * np.cos(phi0)
        pos[:, 2] = np.sin(phi0)
        def safe_arccos(u, v):
            inner_product = np.dot(u, v)
            if inner_product > 1:
                return 0
            if inner_product < -1:
                return np.pi
            return np.arccos(inner_product)
        d = scipy.spatial.distance.cdist(pos, pos, lambda u, v: pk.EARTH_RADIUS/1000 * safe_arccos(u, v))
        d = d + np.diag([np.inf]*4)
        min_d = np.min(d)
        # Will be negative if min(d) is larger than the min allowable inter-rover distance
        return self._min_rover_dist - min_d, min_d
    
    def get_sat_constraint(self, d_min):
        """Evaluate the satellite constraint (minimum distance between any two satellites)

        Args:
            d_min (float): the minimum distance between any two satellites at any epoch

        Returns:
            float: the difference between the actual and allowable minimum distance between satellites
        """
        # Will be negative if d_min is larger than the min allowable inter-satellite distance
        return self._min_sat_dist - d_min

    def line_of_sight(self, r1,r2):
        """Given two position vectors returns the distance of the line of sight to the origin

        Args:
            r1 (numpy array): first point
            r2 (numpy array): second point
        """
        denom = np.linalg.norm(r2-r1)
        if denom < 1e-6:
            # if r1 ~= r2, it will return the norm of r1
            return np.linalg.norm(r1)
        else:
            r21 = (r2-r1) / denom
            h1 = np.dot(r1,r21)
            arg = np.linalg.norm(r1)**2 - h1**2
            # We check for a positive arg in case r1 and r2 are near collinearity
            return np.sqrt(arg) if arg > 1e-6 else 0.0

    def zenith_angle(self, src, dst):
        """Computes the cosine of the zenith angle (theta_z) of the LOS between source and destination node
        
        Args:
            src (numpy array, N_r x 3): rover x, y, z positions
            dst (numpy array, N_s x 3): mothership x, y, z positions
        
        Returns:
            float: cosine of the zenith angle
        """
        dpos = dst - src
        if np.linalg.norm(dpos) < 1e-6:
            cos_theta_z = 0
        else:
            cos_theta_z = np.dot(dpos, src) / (np.linalg.norm(dpos) * np.linalg.norm(src))
        return cos_theta_z

    def qkd_metric(self, idx, src, dst, cos_theta_z, eta):
        """Computes the edge weight according to QKD probabilities
        
            Args:
                idx (int): index of node in the graph
                src (numpy array, 1x3): position of source node
                dst (numpy array, 1x3): position of destination node
                cos_theta_z (float): cosine of the zenith angle of qkd link
                eta (int): satellite quality indicator for corresponding constellation

            Returns:
                float: edge weight
                float: communications link distance between src and dst
        """

        edge_weight = -np.log(eta) # constellation quality score
        d_link = np.linalg.norm(src - dst) # distance of communications link
        edge_weight += 2 * np.log(d_link) # final edge weight
        if edge_weight < 0:
            # Safeguard: whenever this happens, the collision-avoidance constraint is
            # also not satisfied. Nevertheless, we must return a value for the edge weight
            # to ensure that the fitness does not throw (a negative valued edge would also
            # imply the non-existence of a shortest path)
            edge_weight = 1e3

        if idx <= self.n_rovers:
            if cos_theta_z >= self.eps_z: # Apply max zenith angle constraint to mothership-rover link
                edge_weight += 1.0 / np.sin(np.pi / 2 - np.arccos(cos_theta_z))
            else:
                edge_weight = 0
        return edge_weight, d_link

    def average_shortest_path(self, G, src, dst, epoch, verbose=False):
        """Computes the average shortest path length between the source and destination *partitions* of nodes in the graph *G*
        (the source is assumed to be the motherships and the destination the rovers)

        Args:
            G (networkx graph): The graph
            src (int): the number of motherships (to be used as a negative index in G)
            dst (int): the number of rovers (to be used as a negative index in G)
            epoch (int): the current epoch (for error handling purposes only)
            verbose (bool): turn on flag for additional logs

        Returns:
            float: average shortest path
        """
        retval = 0.
        n_nodes = len(G.nodes())
        for i in range(dst):
            for j in range(src):
                # Find the shortest path if one exists
                try:
                    retval += nx.shortest_path_length(G, n_nodes - src - dst + j, n_nodes - dst + i, weight='weight', method='dijkstra')
                except nx.exception.NetworkXNoPath as err:
                    if verbose:
                        print("Mothership {} (node {}) cannot reach rover {} (node {}) at epoch {}".format(\
                            j + 1, n_nodes - src - dst + j, i + 1,  n_nodes - dst + i, epoch))
                    retval += 1e4
        return retval / src / dst

    def generate_walker(self, S,P,F,a,e,incl,w,t0):
        """Generates a Walker constallation as a SatrecArray

        Args:
            S (int): number of satellites per plane
            P (int): number of planes        
            F (int): spacing parameter (i.e. if 2 phasing repeats each 2 planes)
            a (float): semi-major axis
            e (float): eccentricity
            incl (float): inclination
            w (float): argument of perigee
            t0 (float): epoch

        Returns:
            SatrecArray: satellites ready to be SGP4 propagated
        """
        walker_l = []
        mean_motion = np.sqrt(pk.MU_EARTH/a**3/pk.EARTH_RADIUS**3)
        # planes
        for i in range(P):
            #satellites
            for j in range(S):
                satellite = Satrec()
                satellite.sgp4init(
                    WGS72,                            # gravity model
                    'i',                              # 'a' = old AFSPC mode, 'i' = improved mode
                    j + i*S,                          # satnum: Satellite number
                    t0-self.ep_ref.mjd2000,           # epoch: days since 1949 December 31 00:00 UT
                    0.0,                              # bstar: drag coefficient (1/earth radii) - 3.8792e-05
                    0.0,                              # ndot: ballistic coefficient (revs/day)
                    0.0,                               # nddot: mean motion 2nd derivative (revs/day^3)
                    e,                                # ecco: eccentricity
                    w,                                # argpo: argument of perigee (radians)
                    incl,                             # inclo: inclination (radians)
                    2*np.pi/P/S*F*i+2.*np.pi/S*j,     # mo: mean anomaly (radians)
                    mean_motion*60,                   # no_kozai: mean motion (radians/minute)
                    2.*np.pi/P*i                      # nodeo: R.A. of ascending node (radians)
                )
                walker_l.append(satellite)
        # Creating the vectorized list
        return SatrecArray(walker_l)
    
    def build_graph(self, ep_idx, pos, num_w1_sats, eta):
        """Builds a networkx graph from the satellite positions. Links are weighted via a "QKD-inspired metric
        and only exist when motherships/constellation satellites/rovers have line-of-sight

        Args:
            ep_idx (int): idx of the epoch in the time grid 
            pos (numpy array 3xN): position vector of the satellites
            num_w1_sats (int): number of satellites in the first Walker constellation
            eta (tuple): satellite quality indicator for each Walker constellation

        Returns:
            networkx graph: nodes are motherships/Walker satellites/rovers; links are distances when there is LOS
        """
        N = pos[:, ep_idx, :].shape[0] # number of vertices
        adjmatrix = np.zeros((N, N))
        d_min = np.inf
        for i in range(N):
            for j in range(i):
                # Ensure there is LOS
                los = self.line_of_sight(pos[i, ep_idx, :], pos[j, ep_idx, :])
                cos_theta_z = self.zenith_angle(pos[i, ep_idx, :], pos[j, ep_idx, :])

                if los >= self.LOS or cos_theta_z > 0:
                    # Eta based on j because it is the destination satellite in the link
                    eta_j = eta[0] if j < num_w1_sats else eta[1]
                    adjmatrix[i,j], d_link = self.qkd_metric(N-i, pos[i, ep_idx, :], pos[j, ep_idx, :], cos_theta_z, eta_j)
                    if d_link < d_min:
                        d_min = d_link
                    adjmatrix[j,i] = adjmatrix[i,j]
        return nx.from_numpy_array(adjmatrix), adjmatrix, d_min

    def construct_walkers(self, x):
        """Generates two Walker constellations according to specifications
        
        Args:
            x (list): chromosome describing the New Mars communications infrastructure

        Returns:
            SatrecArray: Walker1 constellation satellites ready to be SGP4 propagated
            SatrecArray: Walker2 constellation satellites ready to be SGP4 propagated
        """
        # Parse the chromosome
        a1,e1,i1,w1,_,a2,e2,i2,w2,_,S1,P1,F1,S2,P2,F2,_,_,_,_ = x
        # Construct the 1st walker constellation as a SatrecArray
        walker1 = self.generate_walker(int(S1),int(P1),int(F1),a1,e1,i1,w1,self._t0)
        # Construct the 2nd walker constellation as a SatrecArray
        walker2 = self.generate_walker(int(S2),int(P2),int(F2),a2,e2,i2,w2,self._t0)
        return walker1, walker2

    def construct_mothership_pos(self, motherships):
        """Computes the position of the motherships over a predefined time grid
        
        Args:
            motherships (SatrecArray): mothership satellites ready to be SGP4 propagated

        Returns:
            float, n_motherships x n_epochs x 3: mothership x, y, z positions
        """

        err, pos, _ = motherships.sgp4(self.jds, self.frs)
        # Check propagation went well
        if not np.all(err == 0):
            raise ValueError("The motherships cannot be propagated succesfully on the defined time grid")
        return pos

    def construct_rover_pos(self, lambda0, phi0):
        """Computes the position of the rovers at time t based on the initial latitude and longitude
        
        Args:
            lambda0 (float, N_r x 1): initial latitudes of the rovers
            phi0 (float, N_r x 1): initial longitudes of the rovers

        Returns:
            float, n_rovers x n_epochs x 3: rover x, y, z positions
        """
        pos_r = np.zeros((self.n_rovers, self.jds.shape[0], 3))
        time_range = (self.jds - self.jds[0]) * 24 * 3600 # in seconds
        for i, t in enumerate(time_range):
            pos_r[:, i, 0] = self.R_p * np.cos(lambda0) * np.cos(phi0 + self.w_p * t) # x
            pos_r[:, i, 1] = self.R_p * np.cos(lambda0) * np.sin(phi0 + self.w_p * t) # y
            pos_r[:, i, 2] = self.R_p * np.sin(lambda0) # z

        return pos_r

    def construct_pos(self, walker1, walker2, pos_r):
        """Construct cumulative position of Walker satellites, motherships and rovers

        Args:
            walker1 (SatrecArray): Walker1 constellation satellites ready to be SGP4 propagated
            walker2 (SatrecArray): Walker2 constellation satellites ready to be SGP4 propagated
            pos_r (float, n_rovers x n_epochs x 3): rover x, y, z positions

        Returns:
            float, (S1xP1 + S2xP2 + n_motherships + n_rovers) x n_epochs x 3: overall position vector
        """
        # Compute ephemerides for Walker1 satellites at all epochs)
        err_w1, pos_w1, _ = walker1.sgp4(self.jds, self.frs)
        # Compute ephemerides for Walker2 satellites at all epochs)
        err_w2, pos_w2, _ = walker2.sgp4(self.jds, self.frs)
        # Check propagation went well
        if not (np.all(err_w1 == 0) and np.all(err_w2 == 0)):
            raise ValueError("The walker constellations cannot be propagated successfully on the defined time grid")
        # Position vector for Walker constellation satellites, motherships and rovers)
        cum_pos = np.concatenate((pos_w1,pos_w2, self.pos_m, pos_r))
        return cum_pos

    def fitness(self, x, verbose=False):
        """Evaluate the fitness of the decision variables.

        Args:
            x (list): chromosome describing the New Mars communications infrastructure
            verbose (bool): If True, print some info.

        Returns:
            float: fitness for average shortest path
            float: fitness for total number of satellites
            float: constraint for rover positioning
        """
        # Construct the Walker constellations based on input chromosome 
        walker1, walker2 = self.construct_walkers(x)
        # Extract the quality factors and the number of satellites in the Walkers
        _,_,_,_,eta1,_,_,_,_,eta2,S1,P1,_,S2,P2,_,_,_,_,_ = x
        N1 = S1 * P1
        N2 = S2 * P2
        # Extract the rover indices from the input chromosome
        rovers_idx = np.array(x[-4:]).astype(int)
        # Look up latitude and longitudes corresponding to rover indices
        lambda0 = self.lambdas[rovers_idx]
        phi0 = self.phis[rovers_idx]
        # Construct the rover positions
        rovers = self.construct_rover_pos(lambda0, phi0)
        # Concatenate the position of the Walkers, motherships and rover
        cum_pos = self.construct_pos(walker1, walker2, rovers)

        # Evaluating the fitness function
        if verbose:
            print("FITNESS EVALUATION:")

        # First objective (minimize):
        # Compute the average shortest path between any mothership-rover pair
        # Iterate over epochs
        f1 = 0
        nf1 = 34 # f1 normalization factor
        d_sat_min_ep = np.inf
        for ep_idx in range(1, self.n_epochs):
            # Constructs the graph:
            # Nodes: Walker sats + motherships + rovers
            # Edges: LOS communication
            G, _, d_sat_min = self.build_graph(ep_idx, cum_pos, N1, (eta1, eta2))
            if d_sat_min < d_sat_min_ep:
                d_sat_min_ep = d_sat_min
            f1 += self.average_shortest_path(G, self.n_motherships, self.n_rovers, ep_idx, verbose)

        # Average over the number of epochs
        f1 /= (self.n_epochs - 1)

        # Second objective (minimize):
        # Compute the total number of satellites (weighted by the quality factors)
        f2 = eta1 * N1 + eta2 * N2
        nf2 = 100000 # f2 normalization factor

        # Constraints:
        # The minimum distance between any two rovers needs to be at least 3000km
        # to ensure good coverage of the surface of New Mars
        min_rover_d, d_rover_min = self.get_rover_constraint(lambda0, phi0)
        # The minimum distance between any two nodes of the graph across all epochs 
        # needs to be at least 50km to ensure a collision-free communications network
        min_sat_d = self.get_sat_constraint(d_sat_min_ep) 

        # Additional information on the fitness of the input chromosome
        if verbose:
            print(100 * "-")
            print("RESULTS:")
            print("Total number of satellites (W1: {}, W2: {}): {}".format(N1, N2, N1+N2))
            print("OBJECTIVE #1 - Average communications cost: {}".format(f1/nf1))
            print("OBJECTIVE #2 - Cost of infrastructure: {}".format(f2/nf2))
            print("CONSTRAINT - Minimum distance between rovers ({}): {} km".format("NOK" if min_rover_d > 0 else "OK", d_rover_min))
            print("CONSTRAINT - Minimum distance between sats ({}): {} km".format("NOK" if min_sat_d > 0 else "OK", d_sat_min_ep))
            print(100 * "-")
        return [f1/nf1, f2/nf2, min_rover_d, min_sat_d]
    
    def pretty(self, x):
        """A verbose evaluation of the fitness functions

        Args:
            x (list): chromosome describing the New Mars communications infrastructure

        Returns:
            float: fitness for average shortest path
            float: fitness for total number of satellites
            float: constraint for rover positioning
            float: constraint for satellite positioning
        """
        f1, f2, c1, c2 = self.fitness(x, verbose=True)
        return f1, f2, c1, c2

    def example(self, verbose=False):
        """A random chromosome example for the constellation optimization

        Returns:
            list: a valid chromosome representing a possible constellation design
        """
        if verbose:
            print("CHROMOSOME:")
            print("x = [a1, e1, i1, w1, eta1] + [a2, e2, i2, w2, eta2] + [S1, P1, F1] + [S2, P2, F2] + [r1, r2, r3, r4]")
            print(100 * "-")
            print("a1: float representing the normalized semi-major axis of Walker1 satellite orbits (in km) [1.05,1.8]")
            print("e1: float representing the eccentricity [0, 0.1]")
            print("i1: float representing the inclination [0, pi]")
            print("w1: float representing the argument of the perigee [0, 2pi]")
            print("eta1: float defined as the quality indicator of satellites in the first walker constellation [0, 100]")
            print(100 * "-")
            print("a2: float representing the normalized semi-major axis of Walker2 satellite orbits (in km) [2.0,3.5]")
            print("e2: float representing the eccentricity [0, 0.1]")
            print("i2: float representing the inclination [0, pi]")
            print("w2: float representing the argument of the perigee [0, 2pi]")
            print("eta2: float defined as the quality indicator of satellites in the first walker constellation [0, 100]")
            print(100 * "-")
            print("S1: integer corresponding to the number of satellites per plane [4, 10]")
            print("P1: integer corresponding to the number of planes [2, 10]")
            print("F1: integer defining the phasing of the constellation [0, 9]")
            print(100 * "-")
            print("S2: integer corresponding to the number of satellites per plane [4, 10]")
            print("P2: integer corresponding to the number of planes [2, 10]")
            print("F2: integer defining the phasing of the constellation [0, 9]")
            print(100 * "-")
            print("r1: index of rover 1 [0, 99]")
            print("r2: index of rover 2 [0, 99]")
            print("r3: index of rover 3 [0, 99]")
            print("r4: index of rover 4 [0, 99]")
            print(100 * "-")

        return [1.8, 0.0, 1.2, 0.0, 55.0] + [2.3, 0.0, 1.2, 0.0, 15.0] + [10, 2, 1] + [10, 2, 1] + [13, 21, 34, 55]
    
    def compute_orbit_walker(self, walker, ep0, sma):
        """Compute one full-orbit of the Walker constellation planes (for plots)

        Args:
            walker (sgp4.SatrecArray): the array of Walker satellites to plot
            ep0 (float): Julian date denoting starting epoch
            sma (float): semi-major axis of orbit

        Returns:
            pos (numpy array, P x N x 3): N orbital x, y, z positions for P planes
        """
        
        # Extract mean motion
        mean_motion = np.sqrt(pk.MU_EARTH / sma**3 / pk.EARTH_RADIUS**3) * 24 * 60 * 60 / (2 * np.pi)
        # Compute time range for one full orbit
        jds = np.linspace(ep0, ep0 + 1 / mean_motion, 100)
        frs = jds * 0.0
        # Propagate using SGP4
        err, pos, _ = walker.sgp4(jds, frs)
        if not np.all(err == 0):
            raise ValueError("The satellite cannot be propagated successfully on the defined time grid")
        
        return pos
    
    def compute_orbit_motherships(self, ep0):
        """Compute one full-orbit of the motherships from epoch ep0 (for plots)

        Args:
            ep0 (float): Julian date denoting starting epoch

        Returns:
            orbits (numpy array, S x N x 3): N orbital x, y, z positions for S satellites
        """
        
        # Pre-allocate return array
        N = 100 # number of samples along orbit
        # Get SGP4-ready motherships
        motherships = get_mothership_satellites()
        orbits = np.zeros((len(motherships), N, 3))
        for i, usr in enumerate(motherships):
            # Extract mean motion
            mean_motion = usr.no_kozai * 24 * 60 / (2 * np.pi) # revolutions per day
            # Compute time range for one full orbit
            jds = np.linspace(ep0, ep0 + 1 / mean_motion, N)
            frs = jds * 0.0
            # Propagate using SGP4
            err, pos, _ = usr.sgp4_array(jds, frs)
            if not np.all(err == 0):
                raise ValueError("The satellite cannot be propagated successfully on the defined time grid")
            orbits[i] = pos
        
        return orbits
    
    def plot(self, x, src, dst, ep=1, lims=10000, ax=None, dark_mode=True):
        """Plot the full constellations with solution path and optional orbits

        Args:
            x (list): chromosome describing the communications network
            src (int): mothership index denoting path source
            dst (int): rover index denoting path destination
            ep (int): index of the epoch in the predefined time grid
            lims (float, optional): plot limits. Defaults to 10000.
            ax (matplotlib 3D axis, optional): plot axis.
            dark_mode (bool, optional): dark background for plot (recommended)

        Returns:
            matplotlib.axis: the 3D plot axes
            list: indices of the graph nodes on the communications path (if one is found, otherwise [])
        """
        
        # Create the plotting axis if needed
        if ax is None:
            fig = plt.figure()
            ax = fig.add_subplot(projection='3d')

        # Apply a dark background for better visualization
        if dark_mode:
            sns.set(style="darkgrid")
            plt.style.use("dark_background")
            
        # Construct the two Walker constellations from the specifications 
        walker1, walker2 = self.construct_walkers(x)
        # Construct the rover positions
        rovers_idx = np.array(x[-4:]).astype(int)
        lambda0 = self.lambdas[rovers_idx]
        phi0 = self.phis[rovers_idx]
        rovers = self.construct_rover_pos(lambda0, phi0)
        # Construct the Walker satellite positions
        pos = self.construct_pos(walker1, walker2, rovers)
        # Compute and plot the orbits of the Walker and mothership satellites at the epoch ep
        # Walker 1
        N1 = x[10] * x[11]
        w1_orb = self.compute_orbit_walker(walker1, self.jds[ep], x[0])
        for i in range(N1):
            ax.plot(w1_orb[i, :, 0], w1_orb[i, :, 1], w1_orb[i, :, 2], 'r-', linewidth=0.5)
        # Walker 2
        N2 = x[13] * x[14]
        w2_orb = self.compute_orbit_walker(walker2, self.jds[ep], x[5])
        for i in range(N2):
            ax.plot(w2_orb[i, :, 0], w2_orb[i, :, 1], w2_orb[i, :, 2], 'b-', linewidth=0.5)
        # Motherships
        mothership_orb = self.compute_orbit_motherships(self.jds[ep])
        for i in range(len(mothership_orb)):
            ax.plot(mothership_orb[i, :, 0], mothership_orb[i, :, 1], mothership_orb[i, :, 2], 'w-', linewidth=0.5)

        # Overlay the Walker satellite and mothership positions at epoch ep 
        # Walker1: red, Walker2: blue, motherships: white, rovers: yellow
        ax.scatter(pos[:len(walker1),ep,0], pos[:len(walker1),ep,1], pos[:len(walker1),ep,2], c='r', marker="1", s=200)
        ax.scatter(pos[len(walker1):-self.n_motherships-self.n_rovers,ep,0], pos[len(walker1):-self.n_motherships-self.n_rovers,ep,1], pos[len(walker1):-self.n_motherships-self.n_rovers,ep,2], c='b', marker="1", s=200)
        ax.scatter(pos[-self.n_motherships-self.n_rovers:-self.n_rovers,ep,0], pos[-self.n_motherships-self.n_rovers:-self.n_rovers,ep,1], pos[-self.n_motherships-self.n_rovers:-self.n_rovers,ep,2], c='w', marker="1", s=300)
        # Annotate source nodes (motherships)
        for i in range(self.n_motherships):
            ax.text(pos[-self.n_motherships-self.n_rovers+i,ep,0], pos[-self.n_motherships-self.n_rovers+i,ep,1], pos[-self.n_motherships-self.n_rovers+i,ep,2],  '%s' % (str(i+1)), size=20, zorder=1,  color='w')         
        
        # Annotate destination nodes (rovers)
        ax.scatter(pos[-self.n_rovers:,ep,0], pos[-self.n_rovers:,ep,1], pos[-self.n_rovers:,ep,2], c='y', marker="^", s=200)
        for i in range(self.n_rovers):
            ax.text(pos[-self.n_rovers+i,ep,0], pos[-self.n_rovers+i,ep,1], pos[-self.n_rovers+i,ep,2],  '%s' % (str(i+1)), size=20, zorder=1,  color='y') 

        # Build the communications network
        path = []
        eta1, eta2 = x[4], x[9]
        G, _, _ = self.build_graph(ep, pos, N1, (eta1, eta2))
        N = len(G)
        src_node = N1 + N2 + src - 1
        dst_node = N1 + N2 + self.n_motherships + dst - 1
        # Find the shortest path (if one exists)
        try:
            path = nx.shortest_path(G, src_node, dst_node, weight="weight", method="dijkstra")
            for i,j in zip(path[:-1], path[1:]):
                ax.plot([pos[i,ep,0], pos[j,ep,0]], [pos[i,ep,1], pos[j,ep,1]], [pos[i,ep,2], pos[j,ep,2]], 'g-.', linewidth=3)
            print("Mothership {} (node {}) communicates with rover {} (node {}) at epoch {} via: {}".format(\
                src, src_node, dst,  dst_node, ep, path))
        except nx.exception.NetworkXNoPath as e:
            print("Mothership {} (node {}) cannot reach rover {} (node {}) at epoch {}".format(\
                src, src_node, dst,  dst_node, ep))

        # Plot the New Mars planet
        r = pk.EARTH_RADIUS/1000
        u, v = np.mgrid[0:2 * np.pi:30j, 0:np.pi:20j]
        x = r * np.cos(u) * np.sin(v)
        y = r * np.sin(u) * np.sin(v)
        z = r * np.cos(v)
        ax.plot_surface(x, y, z, alpha=0.3, color="purple", linewidth=0)
        ax.set_axis_off()
        ax.set_xlim(-lims,lims)
        ax.set_ylim(-lims,lims)
        ax.set_zlim(-lims,lims)
        ax.set_box_aspect([ub - lb for lb, ub in (getattr(ax, f'get_{a}lim')() for a in 'xyz')])
        return ax, path

def combine_scores(points):
    """ Function for aggregating single solutions into one score using hypervolume indicator """
    import pygmo as pg
    ref_point = np.array([1.2, 1.4])
    
    # solutions that not dominate the reference point are excluded
    filtered_points = [s[:2] for s in points if pg.pareto_dominance(s[:2], ref_point)]
    
    if len(filtered_points) == 0:
        return 0.0
    else:
        hv = pg.hypervolume(filtered_points)
        return -hv.compute(ref_point)

# Optimize udp        
udp = constellation_udp()