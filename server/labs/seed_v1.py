"""PolyLinux "seed-v1" labs (polylinux-basic, PolyBandit, ...), with a secret
system password.

These labs build their levels inside the VM from one seed per level:

  level_seed = SHA256 of the NUL-terminated fields
      "polylinux-seed-v1", LAB_ID, email, YYYY-MM-DD,
      SYSTEM_PASSWORD, LEVEL_PASSWORD_ROOT + N, N

The formula is unchanged. What changes is that SYSTEM_PASSWORD is secret: the
server computes each student's seeds and gives the VM only those, the email
and the date, so nobody can compute another student's seeds (and so answers)
from the lab's public code. A grader that regenerates answers needs the same
SYSTEM_PASSWORD and LEVEL_PASSWORD_ROOT: `polylab_server.py lab-secrets`.

The VM gets /etc/polylab/scenario.json and, for the shell installers,
/etc/polylab/scenario.env (POSIX shell, single-quoted values):
  POLYLAB_LAB, USER_ID, currentDate, EXERCISE_CODE, LEVEL_HASH_1..N
"""
import hashlib
import hmac
import json
import os
from pathlib import Path


def lab_secrets(key, lab_id):
    """SYSTEM_PASSWORD and LEVEL_PASSWORD_ROOT for a lab.

    From POLYLAB_SECRETS (a JSON file: {"<lab>": {"system_password": ...,
    "level_password_root": ...}}) when it names the lab, otherwise derived
    from the server key (so they never have to be stored separately).
    """
    path = os.environ.get("POLYLAB_SECRETS")
    if path:
        entry = json.loads(Path(path).read_text()).get(lab_id)
        if entry:
            return entry["system_password"], entry.get("level_password_root", "levelPassword")
    mac = hmac.new(key, f"seed-v1-system-password\0{lab_id}".encode(), hashlib.sha256).hexdigest()
    return "sp-" + mac[:40], "levelPassword"


def shell_quote(value):
    return "'" + str(value).replace("'", "'\\''") + "'"


class SeedV1Lab:
    """A lab module: LAB_ID, VMS and a Scenario class, as server/labs expects."""

    def __init__(self, lab_id, levels):
        self.LAB_ID, self.LEVELS, self.VMS = lab_id, levels, ("main",)
        lab = self

        class Scenario:
            def __init__(self, key, email, date):
                self.key, self.email, self.date = key, email, date
                self.system_password, self.level_password_root = lab_secrets(key, lab.LAB_ID)

            def level_hash(self, n):
                fields = ("polylinux-seed-v1", lab.LAB_ID, self.email, self.date,
                          self.system_password, f"{self.level_password_root}{n}", str(n))
                return hashlib.sha256(b"".join(f.encode() + b"\0" for f in fields)).hexdigest()

            def vm_values(self, vm, identity):
                if vm != "main":
                    raise KeyError(vm)
                return {"lab": lab.LAB_ID, "seed_contract": "seed-v1", "identity": identity,
                        "level_hashes": {str(n): self.level_hash(n) for n in range(1, lab.LEVELS + 1)}}

            def vm_files(self, vm, identity):
                values = self.vm_values(vm, identity)
                env = [f"POLYLAB_LAB={shell_quote(lab.LAB_ID)}",
                       f"USER_ID={shell_quote(identity['email'])}",
                       f"currentDate={shell_quote(identity['date'])}",
                       f"EXERCISE_CODE={shell_quote(identity['code'])}"]
                env += [f"LEVEL_HASH_{n}={shell_quote(h)}" for n, h in values["level_hashes"].items()]
                return {"etc/polylab/scenario.json": json.dumps(values, indent=2, sort_keys=True) + "\n",
                        "etc/polylab/scenario.env": "\n".join(env) + "\n"}

        self.Scenario = Scenario
