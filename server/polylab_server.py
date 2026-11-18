#!/usr/bin/env python3
"""PolyLab server: serves the lab pages and gives each student their own values.

For labs in server/labs/ (polylinux-basic, netsec-snort), the values a lab is
built on (level seeds, addresses, ports, flags) are derived from a secret key
that only this server has. Each VM of the student's lab gets a small extra
archive with just the values it needs (/etc/polylab/scenario.json, plus
scenario.env for shell installers), which the page appends to that VM's image.
Labs with a single flag are checked here (api/submit); others keep their form.

  polylab_server.py serve --site DIR [--host 127.0.0.1] [--port 8080]
  polylab_server.py new-key
  polylab_server.py archive --lab LAB [--vm VM] --email EMAIL [--date YYYY-MM-DD] --out FILE
  polylab_server.py flag --lab LAB --email EMAIL [--date YYYY-MM-DD]
  polylab_server.py lab-secrets --lab LAB        (seed-v1 labs: for the grader)

Settings (environment):
  POLYLAB_KEY_FILE  file holding the secret key (64+ hex digits); or
  POLYLAB_KEY       the key itself. Keep it secret: it decides every answer.
  POLYLAB_DATA      folder for submissions.jsonl (default: ./polylab-data)
  POLYLAB_TZ        time zone for "today" (default America/New_York)
  POLYLAB_DATE      fixed exercise date (YYYY-MM-DD) for everyone, if set
  POLYLAB_SECRETS   optional JSON file with seed-v1 labs' system passwords
                    (otherwise they are derived from the key)

HTTP, under each lab's folder of the site (e.g. /netsec-snort/):
  POST api/session        {"email": ...}  -> sets the session cookie; returns
                                             email, date, exercise code, VMs
  GET  api/vm/<vm>.cpio.gz                 -> this student's archive for <vm>
  POST api/submit         {"flag": ...}    -> {"correct": true|false}
                                             (labs with a single flag only)
  GET  /api/health                         -> "ok"
Everything else is a file from --site (the folders Build labs assembles).

Students sign in with their email only, for now: the session is signed, so it
cannot be altered, but nothing checks that the email is theirs.
"""
import argparse, base64, datetime, gzip, hashlib, hmac, io, json, mimetypes, os, re, secrets, sys, threading, time
from pathlib import Path
from socketserver import ThreadingMixIn
from wsgiref.simple_server import WSGIRequestHandler, WSGIServer, make_server
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent))
from labs import LABS  # noqa: E402

EMAIL = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
SESSION_HOURS = 12
MAX_BODY = 4096
SUBMITS_PER_10_MIN = 30
mimetypes.add_type("application/wasm", ".wasm")
mimetypes.add_type("application/gzip", ".gz")


# ---- values and archives ---------------------------------------------------

def load_key():
    raw = os.environ.get("POLYLAB_KEY", "")
    if os.environ.get("POLYLAB_KEY_FILE"):
        raw = Path(os.environ["POLYLAB_KEY_FILE"]).read_text()
    raw = raw.strip()
    if not re.fullmatch(r"[0-9a-fA-F]{64,}", raw):
        sys.exit("set POLYLAB_KEY_FILE (or POLYLAB_KEY) to a secret key of 64+ hex digits "
                 "(make one with: polylab_server.py new-key)")
    return bytes.fromhex(raw)


def normalize_email(value):
    email = str(value or "").strip().lower()
    if len(email) > 254 or not EMAIL.match(email):
        raise ValueError("enter a valid email address")
    return email


def exercise_date():
    fixed = os.environ.get("POLYLAB_DATE", "")
    if fixed:
        datetime.date.fromisoformat(fixed)
        return fixed
    return datetime.datetime.now(ZoneInfo(os.environ.get("POLYLAB_TZ", "America/New_York"))).date().isoformat()


def exercise_code(date):
    return format(int(date.replace("-", "")), "X")


def scenario(key, lab, email, date):
    return LABS[lab].Scenario(key, email, date)


def identity(email, date):
    return {"email": email, "date": date, "code": exercise_code(date)}


def newc(name, mode, data, ino):
    namez = name.encode() + b"\0"
    fields = [ino, mode, 0, 0, 1 if not mode & 0o040000 else 2, 0, len(data), 0, 0, 0, 0, len(namez), 0]
    head = b"070701" + b"".join(b"%08X" % f for f in fields)
    pad = lambda n: b"\0" * ((4 - n % 4) % 4)
    return head + namez + pad(110 + len(namez)) + data + pad(len(data))


def vm_files(lab, s, vm, ident):
    """{path: text} for one VM's archive."""
    if hasattr(s, "vm_files"):
        return s.vm_files(vm, ident)
    return {"etc/polylab/scenario.json": json.dumps(s.vm_values(vm, ident), indent=2, sort_keys=True) + "\n"}


def vm_archive(files):
    """gzip'd newc cpio of these files (and their folders), byte-for-byte deterministic."""
    dirs = sorted({str(Path(p).parents[i]) for p in files for i in range(len(Path(p).parents) - 1)})
    blob, ino = b"", 0
    for d in dirs:
        ino += 1
        blob += newc(d, 0o040755, b"", ino)
    for path in sorted(files):
        ino += 1
        blob += newc(path, 0o100644, files[path].encode(), ino)
    blob += newc("TRAILER!!!", 0, b"", 0)
    out = io.BytesIO()
    with gzip.GzipFile(filename="", mode="wb", fileobj=out, mtime=0) as gz:
        gz.write(blob)
    return out.getvalue()


# ---- the web app -------------------------------------------------------------

class App:
    def __init__(self, key, site, data):
        self.key, self.site, self.data = key, Path(site).resolve(), Path(data)
        self.data.mkdir(parents=True, exist_ok=True)
        self.lock = threading.Lock()
        self.submits = {}

    # sessions: base64(json) "." hmac, in an HttpOnly cookie scoped to the lab
    def sign(self, payload):
        body = base64.urlsafe_b64encode(json.dumps(payload, sort_keys=True).encode()).decode().rstrip("=")
        mac = hmac.new(self.key, b"session\0" + body.encode(), hashlib.sha256).hexdigest()[:40]
        return f"{body}.{mac}"

    def session(self, environ, lab):
        cookies = dict(c.strip().split("=", 1) for c in environ.get("HTTP_COOKIE", "").split(";") if "=" in c)
        token = cookies.get("polylab_session", "")
        body, _, mac = token.partition(".")
        good = hmac.new(self.key, b"session\0" + body.encode(), hashlib.sha256).hexdigest()[:40]
        if not body or not hmac.compare_digest(mac, good):
            return None
        payload = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
        if payload.get("lab") != lab or payload.get("exp", 0) < time.time():
            return None
        return payload

    def __call__(self, environ, start_response):
        try:
            return self.route(environ, start_response)
        except Exception as e:  # never leak a traceback to students
            print(f"error: {e!r}", file=sys.stderr, flush=True)
            return self.json(start_response, 500, {"error": "server error"})

    def route(self, environ, start_response):
        method, path = environ["REQUEST_METHOD"], environ.get("PATH_INFO", "/")
        if path == "/api/health":
            return self.reply(start_response, 200, b"ok\n", "text/plain")
        m = re.fullmatch(r"/([a-z0-9][a-z0-9.-]*)/api/(session|submit|vm/([a-z][a-z0-9-]*)\.cpio\.gz)", path)
        if m:
            lab, action, vm = m.group(1), m.group(2), m.group(3)
            if lab not in LABS:
                return self.json(start_response, 404, {"error": f"{lab} has no server values"})
            if action == "session" and method == "POST":
                return self.new_session(environ, start_response, lab)
            sess = self.session(environ, lab)
            if not sess:
                return self.json(start_response, 401, {"error": "start the lab again (no session)"})
            if action == "submit" and method == "POST":
                return self.submit(environ, start_response, lab, sess)
            if vm and method == "GET":
                if vm not in LABS[lab].VMS:
                    return self.json(start_response, 404, {"error": f"no VM {vm}"})
                s = scenario(self.key, lab, sess["email"], sess["date"])
                body = vm_archive(vm_files(lab, s, vm, identity(sess["email"], sess["date"])))
                return self.reply(start_response, 200, body, "application/gzip", [("Cache-Control", "no-store")])
            return self.json(start_response, 405, {"error": "method not allowed"})
        if method not in ("GET", "HEAD"):
            return self.json(start_response, 405, {"error": "method not allowed"})
        return self.static(start_response, path, method == "HEAD")

    def body(self, environ):
        size = int(environ.get("CONTENT_LENGTH") or 0)
        if size > MAX_BODY:
            raise ValueError("request too large")
        return json.loads(environ["wsgi.input"].read(size) or b"{}")

    def new_session(self, environ, start_response, lab):
        try:
            email = normalize_email(self.body(environ).get("email"))
        except ValueError as e:
            return self.json(start_response, 400, {"error": str(e)})
        date = exercise_date()
        token = self.sign({"lab": lab, "email": email, "date": date, "exp": int(time.time()) + SESSION_HOURS * 3600})
        secure = "; Secure" if (environ.get("HTTP_X_FORWARDED_PROTO") or environ.get("wsgi.url_scheme")) == "https" else ""
        cookie = f"polylab_session={token}; Path=/{lab}/; HttpOnly; SameSite=Strict; Max-Age={SESSION_HOURS * 3600}{secure}"
        self.log(lab, email, date, "session", None, environ)
        graded_here = hasattr(scenario(self.key, lab, email, date), "flag")
        return self.json(start_response, 200, {**identity(email, date), "vms": list(LABS[lab].VMS),
                                               "submit": graded_here}, [("Set-Cookie", cookie)])

    def submit(self, environ, start_response, lab, sess):
        email, date = sess["email"], sess["date"]
        s = scenario(self.key, lab, email, date)
        if not hasattr(s, "flag"):
            return self.json(start_response, 404, {"error": "this lab is graded through its answer form"})
        now = time.time()
        with self.lock:
            recent = [t for t in self.submits.get(email, []) if now - t < 600]
            if len(recent) >= SUBMITS_PER_10_MIN:
                return self.json(start_response, 429, {"error": "too many attempts; wait a few minutes"})
            self.submits[email] = recent + [now]
        try:
            flag = str(self.body(environ).get("flag", "")).strip()[:200]
        except ValueError as e:
            return self.json(start_response, 400, {"error": str(e)})
        correct = hmac.compare_digest(flag, s.flag())
        self.log(lab, email, date, "submit", {"flag": flag, "correct": correct}, environ)
        return self.json(start_response, 200, {"correct": correct})

    def log(self, lab, email, date, event, extra, environ):
        entry = {"time": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
                 "lab": lab, "email": email, "date": date, "event": event,
                 "client": environ.get("HTTP_X_FORWARDED_FOR") or environ.get("REMOTE_ADDR", "")}
        if extra:
            entry.update(extra)
        with self.lock, open(self.data / "submissions.jsonl", "a") as fh:
            fh.write(json.dumps(entry) + "\n")

    def static(self, start_response, path, head):
        target = (self.site / path.lstrip("/")).resolve()
        if target != self.site and self.site not in target.parents:
            return self.reply(start_response, 404, b"not found\n", "text/plain")
        if target.is_dir():
            if not path.endswith("/"):
                return self.reply(start_response, 301, b"", "text/plain", [("Location", path + "/")])
            target = target / "index.html"
        if not target.is_file():
            return self.reply(start_response, 404, b"not found\n", "text/plain")
        kind = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        size = target.stat().st_size
        start_response("200 OK", [("Content-Type", kind), ("Content-Length", str(size)),
                                  ("X-Content-Type-Options", "nosniff")])
        if head:
            return [b""]
        fh = open(target, "rb")
        return iter(lambda: fh.read(1 << 16), b"")

    def json(self, start_response, status, obj, headers=()):
        return self.reply(start_response, status, json.dumps(obj).encode(), "application/json",
                          [("Cache-Control", "no-store"), *headers])

    def reply(self, start_response, status, body, kind, headers=()):
        reason = {200: "OK", 301: "Moved Permanently", 400: "Bad Request", 401: "Unauthorized", 404: "Not Found",
                  405: "Method Not Allowed", 429: "Too Many Requests", 500: "Internal Server Error"}[status]
        start_response(f"{status} {reason}", [("Content-Type", kind), ("Content-Length", str(len(body))),
                                              ("X-Content-Type-Options", "nosniff"), *headers])
        return [body]


class QuietHandler(WSGIRequestHandler):
    def log_message(self, fmt, *args):
        if os.environ.get("POLYLAB_ACCESS_LOG"):
            super().log_message(fmt, *args)


class ThreadingServer(ThreadingMixIn, WSGIServer):
    daemon_threads = True


def application(environ, start_response):
    """For a WSGI server (gunicorn, mod_wsgi): settings from the environment,
    POLYLAB_SITE for the site folder."""
    global _app
    if "_app" not in globals():
        _app = App(load_key(), os.environ["POLYLAB_SITE"], os.environ.get("POLYLAB_DATA", "polylab-data"))
    return _app(environ, start_response)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="command", required=True)
    s = sub.add_parser("serve")
    s.add_argument("--site", required=True)
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8080)
    sub.add_parser("new-key")
    ls = sub.add_parser("lab-secrets")
    ls.add_argument("--lab", required=True, choices=sorted(LABS))
    for name in ("archive", "flag"):
        p = sub.add_parser(name)
        p.add_argument("--lab", required=True, choices=sorted(LABS))
        p.add_argument("--email", required=True)
        p.add_argument("--date", help="YYYY-MM-DD (default: today, as the server would use)")
        if name == "archive":
            p.add_argument("--vm", help="VM name (default: the lab's only VM)")
            p.add_argument("--out", required=True)
    a = ap.parse_args()

    if a.command == "new-key":
        print(secrets.token_hex(32))
        return
    if a.command == "serve":
        app = App(load_key(), a.site, os.environ.get("POLYLAB_DATA", "polylab-data"))
        server = make_server(a.host, a.port, app, server_class=ThreadingServer, handler_class=QuietHandler)
        print(f"PolyLab server on http://{a.host}:{a.port}/ (site {app.site}); labs with server values: "
              f"{', '.join(sorted(LABS))}", flush=True)
        server.serve_forever()
        return
    if a.command == "lab-secrets":
        s = scenario(load_key(), a.lab, "nobody@example.edu", "2000-01-01")
        if not hasattr(s, "system_password"):
            sys.exit(f"{a.lab} is not a seed-v1 lab")
        print(f"SYSTEM_PASSWORD={s.system_password}\nLEVEL_PASSWORD_ROOT={s.level_password_root}")
        return
    key, email = load_key(), normalize_email(a.email)
    date = a.date or exercise_date()
    datetime.date.fromisoformat(date)
    s = scenario(key, a.lab, email, date)
    if a.command == "flag":
        if not hasattr(s, "flag"):
            sys.exit(f"{a.lab} has no single flag; it is graded through its answer form")
        print(s.flag())
    else:
        vms = LABS[a.lab].VMS
        vm = a.vm or (vms[0] if len(vms) == 1 else None)
        if vm not in vms:
            sys.exit(f"--vm must be one of {', '.join(vms)}")
        Path(a.out).write_bytes(vm_archive(vm_files(a.lab, s, vm, identity(email, date))))


if __name__ == "__main__":
    main()
