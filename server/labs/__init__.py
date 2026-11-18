"""Labs whose values come from the server: lab id -> lab module.

A lab module provides:
  LAB_ID, VMS                    the lab's id and its VM names
  Scenario(key, email, date)     with
    vm_values(vm, identity)      -> what /etc/polylab/scenario.json holds on vm
    vm_files(vm, identity)       -> optional: {path: text} for the VM's archive
                                    (default: just scenario.json)
    flag()                       -> optional: the answer api/submit checks;
                                    labs without it are graded elsewhere
"""
from . import netsec_snort
from .seed_v1 import SeedV1Lab

polylinux_basic = SeedV1Lab("polylinux-basic", levels=10)

LABS = {
    polylinux_basic.LAB_ID: polylinux_basic,
    netsec_snort.LAB_ID: netsec_snort,
}
