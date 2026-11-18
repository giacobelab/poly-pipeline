"""Tests for lab.toml reading and the layer checks: python3 -m unittest discover -s tests

Standard library only; run by the plan job on every push and pull request.
"""
import gzip, io, subprocess, sys, tempfile, unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import manifest  # noqa: E402

EXAMPLES = ROOT / "docs" / "examples"
SHA_A, SHA_B, SHA_C = "a" * 40, "b" * 40, "c" * 40
BASIC = """schema = 1
id = "demo"
title = "Demo"
baseline = "basic"
[test]
boot = "generic"
"""


def newc(entries):
    """A gzip'd newc archive of (name, mode, data) entries."""
    blob = b""
    for ino, (name, mode, data) in enumerate(entries + [("TRAILER!!!", 0, b"")], 1):
        namez = name.encode() + b"\0"
        fields = [ino, mode, 0, 0, 1, 0, len(data), 0, 0, 0, 0, len(namez), 0]
        pad = lambda n: b"\0" * ((4 - n % 4) % 4)
        blob += b"070701" + b"".join(b"%08X" % f for f in fields) + namez + pad(110 + len(namez)) + data + pad(len(data))
    out = io.BytesIO()
    with gzip.GzipFile(fileobj=out, mode="wb", mtime=0) as gz:
        gz.write(blob)
    return out.getvalue()


class Examples(unittest.TestCase):
    def test_every_example_parses(self):
        files = sorted(EXAMPLES.glob("*.toml"))
        self.assertGreaterEqual(len(files), 4)
        for f in files:
            entry = manifest.parse(f.read_text(), f.stem)
            self.assertTrue(entry["title"])

    def test_snort_example_resolves_its_vms(self):
        text = (EXAMPLES / "netsec-snort.toml").read_text()
        pin = {"repo": "o/defender", "ref": SHA_A,
               "vms": {"attacker": {"repo": "o/attacker", "ref": SHA_B}, "noise": {"repo": "o/noise", "ref": SHA_C}}}
        self.assertTrue(manifest.is_pin_only(pin))
        full = manifest.resolve("netsec-snort", pin, text)
        self.assertEqual({n: (v["repo"], v["ref"]) for n, v in full["vms"].items()},
                         {"defender": ("o/defender", SHA_A), "attacker": ("o/attacker", SHA_B),
                          "noise": ("o/noise", SHA_C)})
        self.assertEqual(full["overlay"]["dest"], "/")
        self.assertIn("/etc/inittab", full["overlay"]["replaces"])


class Errors(unittest.TestCase):
    def bad(self, text, lab_id="demo", match=""):
        with self.assertRaises(manifest.ManifestError) as cm:
            manifest.parse(text, lab_id)
        self.assertIn(match, str(cm.exception))

    def test_rejections(self):
        self.bad(BASIC, "other", "polyci.json lists it as 'other'")
        self.bad(BASIC + 'colour = "red"\n', match="unknown key")
        self.bad(BASIC.replace('baseline = "basic"\n', ""), match="needs baseline")
        self.bad(BASIC.replace('"generic"', '"mystery"'), match="test.boot")
        self.bad(BASIC.replace("schema = 1", "schema = 2"), match="schema")
        self.bad(BASIC + '[overlay]\nreplaces = ["etc/inittab"]\n', match="absolute file path")
        self.bad(BASIC + '[overlay]\ndest = "root"\n', match="absolute path")
        self.bad(BASIC + '[levels]\nusers = "level"\n', match="{n}")
        self.bad(BASIC + '[server]\nvalues = "true"\n', match="true or false")
        self.bad(BASIC + "this is not toml", match="not valid TOML")
        two = BASIC.replace('baseline = "basic"\n', "") + '[[vm]]\nname = "a"\nbaseline = "x"\n' * 2
        self.bad(two, match="must be unique")

    def test_pins_must_match_the_vms(self):
        text = (EXAMPLES / "netsec-snort.toml").read_text()
        pin = {"repo": "o/d", "ref": SHA_A, "vms": {"ghost": {"repo": "o/g", "ref": SHA_B}}}
        with self.assertRaises(manifest.ManifestError):
            manifest.resolve("netsec-snort", pin, text)
        with self.assertRaises(manifest.ManifestError):
            manifest.resolve("demo", {"repo": "o/r", "ref": SHA_A}, "")

    def test_full_entries_are_not_pin_only(self):
        self.assertFalse(manifest.is_pin_only({"repo": "o/r", "ref": SHA_A, "baseline": "basic"}))
        self.assertFalse(manifest.is_pin_only({"vms": {"a": {"repo": "o/r", "ref": SHA_A, "baseline": "x"}}}))


class Layers(unittest.TestCase):
    def verify(self, base, lab, *replaces):
        with tempfile.TemporaryDirectory() as tmp:
            t = Path(tmp)
            (t / "base").write_bytes(base)
            (t / "lab").write_bytes(lab)
            (t / "packaged").write_bytes(base + lab)
            (t / "manifest").write_text("")
            return subprocess.run([sys.executable, str(ROOT / "scripts" / "verify-image.py"), "--packaged",
                                   str(t / "packaged"), "--base", str(t / "base"), "--lab", str(t / "lab"),
                                   "--manifest", str(t / "manifest"), "--replaces", *replaces],
                                  capture_output=True, text=True)

    def test_replacing_needs_declaring(self):
        base = newc([("etc", 0o040755, b""), ("etc/issue", 0o100644, b"base"), ("init", 0o100755, b"x")])
        lab = newc([("etc", 0o040755, b""), ("etc/issue", 0o100644, b"lab"), ("etc/new", 0o100644, b"n")])
        out = self.verify(base, lab)
        self.assertNotEqual(out.returncode, 0)
        self.assertIn("without declaring it", out.stdout)
        self.assertEqual(self.verify(base, lab, "/etc/issue").returncode, 0, self.verify(base, lab, "/etc/issue").stdout)

    def test_protected_and_reserved_paths(self):
        base = newc([("init", 0o100755, b"x")])
        out = self.verify(base, newc([("init", 0o100755, b"mine")]), "/init")
        self.assertIn("no lab may replace", out.stdout)
        out = self.verify(base, newc([("etc", 0o040755, b""), ("etc/polylab", 0o040755, b""),
                                      ("etc/polylab/scenario.env", 0o100644, b"x")]))
        self.assertIn("reserved for the PolyLab server", out.stdout)


class Scenario(unittest.TestCase):
    def check(self, blob):
        with tempfile.NamedTemporaryFile(suffix=".cpio.gz", delete=False) as fh:
            fh.write(blob)
        try:
            return subprocess.run([sys.executable, str(ROOT / "scripts" / "check-scenario.py"), fh.name],
                                  capture_output=True, text=True)
        finally:
            Path(fh.name).unlink()

    def test_only_etc_polylab(self):
        good = newc([("etc", 0o040755, b""), ("etc/polylab", 0o040755, b""),
                     ("etc/polylab/scenario.json", 0o100644, b"{}")])
        self.assertEqual(self.check(good).returncode, 0)
        for bad in ([("etc", 0o040755, b""), ("etc/profile", 0o100644, b"x")],
                    [("etc/polylab/x", 0o104755, b"x")],
                    [("etc/polylab/link", 0o120777, b"/etc/shadow")],
                    [("etc/polylab/../../root/x", 0o100644, b"x")]):
            self.assertNotEqual(self.check(newc(bad)).returncode, 0, bad)


if __name__ == "__main__":
    unittest.main()
