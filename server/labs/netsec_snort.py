"""Per-student values for the Snort incident-response lab (netsec-snort).

The same ranges and choices as the lab's original scenario.py, but every value
is derived from a secret key that only the server has, so the values (and the
flag) cannot be computed from the lab's code. Each VM gets only the values it
needs, in /etc/polylab/scenario.json:

  defender   identity, and the stage-3 threat-intel bulletin (port and marker)
  attacker   identity, and the traffic for stages 1-3
  noise      identity, its five client addresses and the stage-3 decoy

The flag (stage 1): <keyword>_<attacker's last octet>_<destination port>.
"""
import hashlib
import hmac

LAB_ID = "netsec-snort"
VERSION = "polylab-snort-v2"
KEYWORDS = ("amber", "cedar", "falcon", "lantern", "marble", "orbit", "quartz", "willow")
VMS = ("defender", "attacker", "noise")


class Scenario:
    def __init__(self, key, email, date):
        self.key, self.email, self.date = key, email, date  # date: "YYYY-MM-DD"

    def seed(self, stage):
        fields = (VERSION, LAB_ID, self.email, self.date, str(stage))
        message = b"".join(f.encode() + b"\0" for f in fields)
        return hmac.new(self.key, message, hashlib.sha256).digest()

    def derive(self, stage, label, length=32):
        return hashlib.sha256(self.seed(stage) + label.encode() + b"\0").digest()[:length]

    def number(self, stage, label, minimum, maximum):
        return minimum + int.from_bytes(self.derive(stage, label, 8), "big") % (maximum - minimum + 1)

    def stage(self, n):
        if n == 1:
            return {"stage": 1, "source_ip": "10.0.0.%d" % self.number(1, "source-ip", 100, 139),
                    "destination_port": (2200, 3306, 5353, 8080, 8443)[self.number(1, "port-index", 0, 4)],
                    "keyword": KEYWORDS[self.number(1, "keyword-index", 0, len(KEYWORDS) - 1)]}
        if n == 2:
            return {"stage": 2, "source_ip": "10.0.0.%d" % self.number(2, "source-ip", 140, 179),
                    "destination_port": 84, "community_sid": 24265,
                    "community_message": "MALWARE-OTHER Malicious UA detected on non-standard port"}
        if n == 3:
            return {"stage": 3, "source_ip": "10.0.0.%d" % self.number(3, "source-ip", 180, 219),
                    "destination_port": (4444, 5555, 6666, 7777, 8888)[self.number(3, "port-index", 0, 4)],
                    "marker_hex": self.derive(3, "hex-marker", 8).hex()}
        raise ValueError("stage must be 1, 2 or 3")

    def noise_addresses(self):
        start = self.number(1, "noise-address-start", 30, 69)
        return ["10.0.0.%d" % (30 + ((start - 30 + offset * 7) % 50)) for offset in range(5)]

    def flag(self):
        s1 = self.stage(1)
        return "%s_%s_%d" % (s1["keyword"], s1["source_ip"].rsplit(".", 1)[1], s1["destination_port"])

    def vm_values(self, vm, identity):
        """What /etc/polylab/scenario.json holds on one VM."""
        base = {"lab": LAB_ID, "version": VERSION, "identity": identity}
        if vm == "defender":
            s3 = self.stage(3)
            return {**base, "stages": {"3": {"stage": 3, "destination_port": s3["destination_port"],
                                             "marker_hex": s3["marker_hex"]}}}
        if vm == "attacker":
            return {**base, "stages": {str(n): self.stage(n) for n in (1, 2, 3)}}
        if vm == "noise":
            s3 = self.stage(3)
            return {**base, "noise_addresses": self.noise_addresses(),
                    "stage3_decoy": {"destination_port": s3["destination_port"],
                                     "partial_marker_hex": s3["marker_hex"][:8]}}
        raise KeyError(vm)
