#!/usr/bin/env python
# Copyright 2014-2020 The PySCF Developers. All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#
# Authors: Paul J. Robinson <pjrobinson@ucla.edu>
#          Qiming Sun <osirpt.sun@gmail.com>
#

'''
Intrinsic Bonding Orbitals
ref. JCTC, 9, 4834

Below here is work done by Paul Robinson.
much of the code below is adapted from code published freely on the website of Gerald Knizia
Ref: JCTC, 2013, 9, 4834-4843
'''

from functools import reduce
import numpy
from pyscf.lib import logger
from pyscf.tools import mo_mapping
from pyscf.lo import iao
from pyscf.lo import orth, pipek
from pyscf import __config__

MINAO = getattr(__config__, 'lo_iao_minao', 'minao')

def ibo(mol, orbocc, locmethod='IBO', iaos=None, s=None,
        exponent=4, grad_tol=1e-8, max_iter=200, minao=MINAO, verbose=logger.NOTE):
    '''Intrinsic Bonding Orbitals

    This function serves as a wrapper to the underlying localization functions
    ibo_loc and PipekMezey to create IBOs.

    Args:
        mol : the molecule or cell object

        orbocc : occupied molecular orbital coefficients

    Kwargs:
        locmethod : string
            the localization method 'PM' for Pipek Mezey localization or 'IBO' for the IBO localization

        iaos : 2D array
            the array of IAOs

        s : 2D array
            the overlap array in the ao basis

    Returns:
        IBOs in the basis defined in mol object.
    '''

    if s is None:
        if getattr(mol, 'pbc_intor', None):  # whether mol object is a cell
            if isinstance(orbocc, numpy.ndarray) and orbocc.ndim == 2:
                s = mol.pbc_intor('int1e_ovlp', hermi=1)
            else:
                raise NotImplementedError('k-points crystal orbitals')
        else:
            s = mol.intor_symmetric('int1e_ovlp')

    if iaos is None:
        iaos = iao.iao(mol, orbocc)

    locmethod = locmethod.strip().upper()
    if locmethod == 'PM':
        EXPONENT = getattr(__config__, 'lo_ibo_PipekMezey_exponent', exponent)
        ibos = PipekMezey(mol, orbocc, iaos, s, exponent=EXPONENT, minao=minao)
        del(EXPONENT)
    else:
        ibos = ibo_loc(mol, orbocc, iaos, s, exponent=exponent,
                       grad_tol=grad_tol, max_iter=max_iter,
                       minao=minao, verbose=verbose)
    return ibos

def ibo_loc(mol, orbocc, iaos, s, exponent=2, grad_tol=1e-8, max_iter=1000,
            minao=MINAO, verbose=logger.NOTE, iao_mol=None, labels=None):
    '''Intrinsic Bonding Orbitals. [Ref. JCTC, 9, 4834]

    This implementation follows Knizia's implementation execept that the
    resultant IBOs are symmetrically orthogonalized.  Note the IBOs of this
    implementation do not strictly maximize the IAO Mulliken charges.

    IBOs can also be generated by another implementation (see function
    pyscf.lo.ibo.PM). In that function, PySCF builtin Pipek-Mezey localization
    module was used to maximize the IAO Mulliken charges.

    Args:
        mol : the molecule or cell object

        orbocc : 2D array or a list of 2D array
            occupied molecular orbitals or crystal orbitals for each k-point

    Kwargs:
        iaos : 2D array
            the array of IAOs
        exponent : integer
            Localization power in PM scheme
        grad_tol : float
            convergence tolerance for norm of gradients

    Returns:
        IBOs in the big basis (the basis defined in mol object).
    '''
    log = logger.new_logger(mol, verbose)
    assert exponent in (2, 4)

    # Symmetrically orthogonalization of the IAO orbitals as Knizia's
    # implementation.  The IAO returned by iao.iao function is not orthogonal.
    iaos = orth.vec_lowdin(iaos, s)

    #static variables
    StartTime = logger.perf_counter()
    L  = 0 # initialize a value of the localization function for safety
    #max_iter = 20000 #for some reason the convergence of solid is slower
    #fGradConv = 1e-10 #this ought to be pumped up to about 1e-8 but for testing purposes it's fine
    swapGradTolerance = 1e-12

    #dynamic variables
    Converged = False
    
    if iao_mol is None and labels is None: 
        # render Atoms list without ghost atoms
        iao_mol = iao.reference_mol(mol, minao=minao)
        Atoms = [iao_mol.atom_pure_symbol(i) for i in range(iao_mol.natm)]

        #generates the parameters we need about the atomic structure
        nAtoms = len(Atoms)
        AtomOffsets = MakeAtomIbOffsets(Atoms)[0]
        iAtSl = [slice(AtomOffsets[A],AtomOffsets[A+1]) for A in range(nAtoms)]
    else:
        # ZHC NOTE use AO offset if iao_mol is set.
        ## render Atoms list without ghost atoms
        #Atoms = [iao_mol.atom_pure_symbol(i) for i in range(iao_mol.natm)]
        #generates the parameters we need about the atomic structure
        #nAtoms = len(Atoms)
        if labels is None:
            iAtSl = []
            for i, (b0, b1, p0, p1) in enumerate(iao_mol.offset_nr_by_atom()):
                iAtSl.append(slice(p0, p1))
        else:
            iAtSl = get_offset(labels)
        nAtoms = len(iAtSl)

    #converts the occupied MOs to the IAO basis
    CIb = reduce(numpy.dot, (iaos.T, s , orbocc))
    numOccOrbitals = CIb.shape[1]

    log.debug("   {0:^5s} {1:^14s} {2:^11s} {3:^8s}"
              .format("ITER.","LOC(Orbital)","GRADIENT", "TIME"))

    for it in range(max_iter):
        fGrad = 0.00

        #calculate L for convergence checking
        L = 0.0
        for A in range(nAtoms):
            for i in range(numOccOrbitals):
                CAi = CIb[iAtSl[A], i]
                L += numpy.dot(CAi,CAi)**exponent

        # loop over the occupied orbitals pairs i,j
        for i in range(numOccOrbitals):
            for j in range(i):
                # I eperimented with exponentially falling off random noise
                Aij  = 0.0 #numpy.random.random() * numpy.exp(-1*it)
                Bij  = 0.0 #numpy.random.random() * numpy.exp(-1*it)
                for k in range(nAtoms):
                    CIbA = CIb[iAtSl[k],:]
                    Cii  = numpy.dot(CIbA[:,i], CIbA[:,i])
                    Cij  = numpy.dot(CIbA[:,i], CIbA[:,j])
                    Cjj  = numpy.dot(CIbA[:,j], CIbA[:,j])
                    #now I calculate Aij and Bij for the gradient search
                    if exponent == 2:
                        Aij += 4.*Cij**2 - (Cii - Cjj)**2
                        Bij += 4.*Cij*(Cii - Cjj)
                    else:
                        Bij += 4.*Cij*(Cii**3-Cjj**3)
                        Aij += -Cii**4 - Cjj**4 + 6*(Cii**2 + Cjj**2)*Cij**2 + Cii**3 * Cjj + Cii*Cjj**3

                if Aij**2 + Bij**2 < swapGradTolerance:
                    continue
                    #this saves us from replacing already fine orbitals
                else:
                    #THE BELOW IS TAKEN DIRECLTY FROMG KNIZIA's FREE CODE
                    # Calculate 2x2 rotation angle phi.
                    # This correspond to [2] (12)-(15), re-arranged and simplified.
                    phi = 0.25 * numpy.arctan2(Bij, -Aij)
                    fGrad += Bij**2
                    # ^- Bij is the actual gradient. Aij is effectively
                    #    the second derivative at phi=0.

                    # 2x2 rotation form; that's what PM suggest. it works
                    # fine, but I don't like the asymmetry.
                    cs = numpy.cos(phi)
                    ss = numpy.sin(phi)
                    Ci = numpy.array(CIb[:,i], copy=True)
                    Cj = numpy.array(CIb[:,j], copy=True)
                    CIb[:,i] =  cs * Ci + ss * Cj
                    CIb[:,j] = -ss * Ci + cs * Cj
        fGrad = fGrad**0.5

        log.debug(" {0:5d} {1:12.8f} {2:11.2e} {3:8.2f}"
                  .format(it+1, L**(1.0/exponent), fGrad, logger.perf_counter()-StartTime))
        if fGrad < grad_tol:
            Converged = True
            break

    Note = "IB/P%i/2x2, %i iter; Final gradient %.2e" % (exponent, it+1, fGrad)
    if not Converged:
        log.note("\nWARNING: Iterative localization failed to converge!"
                 "\n         %s", Note)
    else:
        log.note(" Iterative localization: %s", Note)
    log.debug(" Localized orbitals deviation from orthogonality: %8.2e",
              numpy.linalg.norm(numpy.dot(CIb.T, CIb) - numpy.eye(numOccOrbitals)))
    # Note CIb is not unitary matrix (although very close to unitary matrix)
    # because the projection <IAO|OccOrb> does not give unitary matrix.
    ibos =  numpy.dot(iaos, (orth.vec_lowdin(CIb)))
    # sort to original order
    u0 = reduce(numpy.dot, (orbocc.conj().T, s , ibos))
    sorted_idx = mo_mapping.mo_1to1map(u0)
    ibos = ibos[:, sorted_idx]
    return ibos

def get_offset(labels):
    offset = []
    last_id = '-1'
    last_i = -1
    for i, lab in enumerate(labels):
        lab_sp = lab.split()
        cur_id = lab_sp[0]
        if int(cur_id) < int(last_id):
            raise ValueError("labels are not ordered... last_id: %s, cur_id: %s"
                             %(last_id, cur_id))
        if cur_id != last_id:
            if last_i >= 0:
                offset.append(slice(last_i, i))
            last_id = cur_id
            last_i = i
    offset.append(slice(last_i, i + 1))
    return offset

def PipekMezey(mol, orbocc, iaos, s, exponent, minao=MINAO):
    '''
    Note this localization is slightly different to Knizia's implementation.
    The localization here reserves orthogonormality during optimization.
    Orbitals are projected to IAO basis first and the Mulliken pop is
    calculated based on IAO basis (in function atomic_pops).  A series of
    unitary matrices are generated and applied on the input orbitals.  The
    intemdiate orbitals in the optimization and the finally localized orbitals
    are all orthogonormal.

    Examples:

    >>> from pyscf import gto, scf
    >>> from pyscf.lo import ibo
    >>> mol = gto.M(atom='H 0 0 0; F 0 0 1', >>> basis='unc-sto3g')
    >>> mf = scf.RHF(mol).run()
    >>> pm = ibo.PM(mol, mf.mo_coeff[:,mf.mo_occ>0])
    >>> loc_orb = pm.kernel()
    '''

    # Note: PM with Lowdin-orth IAOs is implemented in pipek.PM class
    # TODO: Merge the implemenation here to pipek.PM

    cs = numpy.dot(iaos.T.conj(), s)
    s_iao = numpy.dot(cs, iaos)
    iao_inv = numpy.linalg.solve(s_iao, cs)
    iao_mol = iao.reference_mol(mol, minao=minao)

    # Define the mulliken population of each atom based on IAO basis.
    # proj[i].trace is the mulliken population of atom i.
    def atomic_pops(mol, mo_coeff, method=None):
        nmo = mo_coeff.shape[1]
        proj = numpy.empty((mol.natm,nmo,nmo))
        orb_in_iao = reduce(numpy.dot, (iao_inv, mo_coeff))
        for i, (b0, b1, p0, p1) in enumerate(iao_mol.offset_nr_by_atom()):
            csc = reduce(numpy.dot, (orb_in_iao[p0:p1].T, s_iao[p0:p1],
                                     orb_in_iao))
            proj[i] = (csc + csc.T) * .5
        return proj
    pm = pipek.PM(mol, orbocc)
    pm.atomic_pops = atomic_pops
    pm.exponent = exponent
    return pm
PM = Pipek = PipekMezey


def shell_str(l, n_cor, n_val):
    '''
    Help function to define core and valence shells for shell with different l
    '''
    cor_shell = [
        "[{n}s]", "[{n}px] [{n}py] [{n}pz]",
        "[{n}d0] [{n}d2-] [{n}d1+] [{n}d2+] [{n}d1-]",
        "[{n}f1+] [{n}f1-] [{n}f0] [{n}f3+] [{n}f2-] [{n}f3-] [{n}f2+]"]
    val_shell = [
        l_str.replace('[', '').replace(']', '') for l_str in cor_shell]
    l_str = ' '.join(
        [cor_shell[l].format(n=i) for i in range(l + 1, l + 1 + n_cor)] +
        [val_shell[l].format(n=i) for i in range(l + 1 + n_cor,
                                                 l + 1 + n_cor + n_val)])
    return l_str


'''
These are parameters for selecting the valence space correctly.
The parameters are taken from in G. Knizia's free code
https://sites.psu.edu/knizia/software/
'''
def MakeAtomInfos():
    nCoreX = {"H": 0, "He": 0}
    for At in "Li Be B C O N F Ne".split(): nCoreX[At] = 1
    for At in "Na Mg Al Si P S Cl Ar".split(): nCoreX[At] = 5
    for At in "Na Mg Al Si P S Cl Ar".split(): nCoreX[At] = 5
    for At in "K Ca".split(): nCoreX[At] = 18/2
    for At in "Sc Ti V Cr Mn Fe Co Ni Cu Zn".split(): nCoreX[At] = 18/2
    for At in "Ga Ge As Se Br Kr".split(): nCoreX[At] = 18/2+5 # [Ar] and the 5 d orbitals.
    nAoX = {"H": 1, "He": 1}
    for At in "Li Be".split(): nAoX[At] = 2
    for At in "B C O N F Ne".split(): nAoX[At] = 5
    for At in "Na Mg".split(): nAoX[At] = 3*1 + 1*3
    for At in "Al Si P S Cl Ar".split(): nAoX[At] = 3*1 + 2*3
    for At in "K Ca".split(): nAoX[At] = 18/2+1
    for At in "Sc Ti V Cr Mn Fe Co Ni Cu Zn".split(): nAoX[At] = 18/2+1+5   # 4s, 3d
    for At in "Ga Ge As Se Br Kr".split(): nAoX[At] = 18/2+1+5+3

    AoLabels = {}

    def SetAo(At, AoDecl):
        Labels = AoDecl.split()
        AoLabels[At] = Labels
        assert(len(Labels) == nAoX[At])
        nCore = len([o for o in Labels if o.startswith('[')])
        assert(nCore == nCoreX[At])

    # atomic orbitals in the MINAO basis: [xx] denotes core orbitals.
    for At in "H He".split(): SetAo(At, "1s")
    for At in "Li Be".split(): SetAo(At, "[1s] 2s")
    for At in "B C O N F Ne".split(): SetAo(At, "[1s] 2s 2px 2py 2pz")
    for At in "Na Mg".split(): SetAo(At, "[1s] [2s] 3s [2px] [2py] [2pz]")
    for At in "Al Si P S Cl Ar".split(): SetAo(At, "[1s] [2s] 3s [2px] [2py] [2pz] 3px 3py 3pz")
    for At in "K Ca".split(): SetAo(At, "[1s] [2s] [3s] 4s [2px] [2py] [2pz] [3px] [3py] [3pz]")
    for At in "Sc Ti V Cr Mn Fe Co Ni Cu Zn".split(): SetAo(At, "[1s] [2s] [3s] 4s [2px] [2py] [2pz] [3px] [3py] [3pz] 3d0 3d2- 3d1+ 3d2+ 3d1-")
    for At in "Ga Ge As Se Br Kr".split(): SetAo(At, "[1s] [2s] [3s] 4s [2px] [2py] [2pz] [3px] [3py] [3pz] 4px 4py 4pz [3d0] [3d2-] [3d1+] [3d2+] [3d1-]")
    for At in "Rb Sr".split():
        nCoreX[At] = 36/2
        nAoX[At] = nCoreX[At] + 1
        SetAo(At, ' '.join ([shell_str(0,4,1),
                             shell_str(1,3,0),
                             shell_str(2,1,0)]))
    for At in "Y Zr Nb Mo Tc Ru Rh Pd Ag Cd".split():
        nCoreX[At] = 36/2
        nAoX[At] = nCoreX[At] + 1 + 5
        SetAo(At, ' '.join ([shell_str(0,4,1),
                             shell_str(1,3,0),
                             shell_str(2,1,1)]))
    for At in "In Sn Sb Te I Xe".split():
        nCoreX[At] = 36/2 + 5
        nAoX[At] = nCoreX[At] + 1 + 3
        SetAo(At, ' '.join ([shell_str(0,4,1),
                             shell_str(1,3,1),
                             shell_str(2,2,0)]))
    for At in "Cs Ba".split():
        nCoreX[At] = 54/2
        nAoX[At] = nCoreX[At] + 1
        SetAo(At, ' '.join ([shell_str(0,5,1),
                             shell_str(1,4,0),
                             shell_str(2,2,0)]))
    for At in "Ce Pr Nd Pm Sm Eu Gd Tb Dy Ho Er Tm Yb Lu".split():
        nCoreX[At] = 54/2
        nAoX[At] = nCoreX[At] + 1 + 5 + 7
        SetAo(At, ' '.join ([shell_str(0,5,1),
                             shell_str(1,4,0),
                             shell_str(2,2,1),
                             shell_str(3,0,1)]))
    for At in "La Hf Ta W Re Os Ir Pt Au Hg".split():
        nCoreX[At] = 54/2 + 7
        nAoX[At] = nCoreX[At] + 1 + 5
        SetAo(At, ' '.join ([shell_str(0,5,1),
                             shell_str(1,4,0),
                             shell_str(2,2,1),
                             shell_str(3,1,0)]))
    for At in "Tl Pb Bi Po At Rn".split():
        nCoreX[At] = 54/2 + 7 + 5
        nAoX[At] = nCoreX[At] + 1 + 3
        SetAo(At, ' '.join ([shell_str(0,5,1),
                             shell_str(1,4,1),
                             shell_str(2,3,0),
                             shell_str(3,1,0)]))
    for At in "Fr Ra".split():
        nCoreX[At] = 86/2
        nAoX[At] = nCoreX[At] + 1
        SetAo(At, ' '.join ([shell_str(0,6,1),
                             shell_str(1,5,0),
                             shell_str(2,3,0),
                             shell_str(3,1,0)]))
    for At in "Th Pa U Np Pu Am Cm Bk Cf Es Fm Md No".split():
        nCoreX[At] = 86/2
        nAoX[At] = nCoreX[At] + 1 + 5 + 7
        SetAo(At, ' '.join ([shell_str(0,6,1),
                             shell_str(1,5,0),
                             shell_str(2,3,1),
                             shell_str(3,1,1)]))
    for At in "Ac Lr Rf Db Sg Bh Hs Mt Ds Rg Cn".split():
        nCoreX[At] = 86/2 + 7
        nAoX[At] = nCoreX[At] + 1 + 5
        SetAo(At, ' '.join ([shell_str(0,6,1),
                             shell_str(1,5,0),
                             shell_str(2,3,1),
                             shell_str(3,2,0)]))
    for At in "Nh Fl Mc Lv Ts Og".split():
        nCoreX[At] = 86/2 + 7 + 5
        nAoX[At] = nCoreX[At] + 1 + 3
        SetAo(At, ' '.join ([shell_str(0,6,1),
                             shell_str(1,5,1),
                             shell_str(2,4,0),
                             shell_str(3,2,0)]))
    # note: f order is '4f1+','4f1-','4f0','4f3+','4f2-','4f3-','4f2+',

    return nCoreX, nAoX, AoLabels


def MakeAtomIbOffsets(Atoms):
    """calcualte offset of first orbital of individual atoms
    in the valence minimal basis (IB)"""
    nCoreX, nAoX, AoLabels = MakeAtomInfos()
    iBfAt = [0]
    for Atom in Atoms:
        Atom = ''.join(char for char in Atom if char.isalpha())
        iBfAt.append(iBfAt[-1] + nAoX[Atom])
    return iBfAt, nCoreX, nAoX, AoLabels

del(MINAO)
