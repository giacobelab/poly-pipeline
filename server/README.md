# PolyLab server

Serves the lab pages and gives each student their own values, from a secret
key that never leaves the server. That way nobody can work out another
student's answers from the labs' public code.

- **Single file:** `polylab_server.py`, Python 3.9+ standard library only.
  There is nothing to install.
- **Per-lab values:** `labs/` has one module per lab with server values.
  - `polylinux-basic` uses the seed-v1 formula with a secret system password
    (`labs/seed_v1.py`).
  - `labs/netsec_snort.py` has the Snort lab's values ready. That lab's page
    and VM changes are not done yet.

## How it works

1. **Email:** the lab page asks for the student's email and sends it to
   `api/session`. The server answers with the email, the exercise date and
   code, and the lab's VMs, and sets a signed session cookie (12 hours,
   HttpOnly, only for that lab).
2. **Per-student archive:** for each VM the page downloads
   `api/vm/<vm>.cpio.gz`. It's a few hundred bytes holding
   `/etc/polylab/scenario.json` (and `scenario.env` for shell installers).
   The page appends it to the VM's image, and the kernel unpacks both. The
   archive holds only the values that VM needs, such as polylinux-basic's ten
   level seeds.
3. **Inside the VM:** the lab's installer uses those values instead of asking
   for the email or using built-in passwords.
4. **Flags:** labs with a single flag (`flag()` in their module) are checked
   at `api/submit`. Others keep their answer form; see Grading.
5. **Log:** sessions and submissions go to `submissions.jsonl` in the data
   folder.

**Limits:** students sign in with their email only, for now. The session
can't be altered, but nothing checks that the email is theirs. A sign-in (PSU
SSO, Canvas) can replace `api/session` later without changing the labs.

What runs in the student's browser can always be inspected. A determined
student can find *their own* values that way, but not anyone else's.

## Set up on a server

Run these on the server (Linux, Python 3.9+), as a dedicated user.

**1. Make the key**, readable only by that user. Back it up: every answer
depends on it, and a new key changes all of them.
```sh
python3 polylab_server.py new-key > /etc/polylab/key
chmod 600 /etc/polylab/key
```

**2. Put each lab's page in the site folder.** Server labs are not on GitHub
Pages. Their release has a `site.tar.gz` instead (the page, v86 and the
images); unpack it into a folder named after the lab:
```sh
mkdir -p /srv/polylab/site/polylinux-basic
tar -xzf site.tar.gz -C /srv/polylab/site/polylinux-basic
```
The page is then at `https://<server>/polylinux-basic/`. Other labs' Pages
folders can sit next to it and are served as plain files.

**3. Run it** behind the web server that handles HTTPS (nginx, Apache),
proxying to `127.0.0.1:8080`:
```sh
POLYLAB_KEY_FILE=/etc/polylab/key POLYLAB_DATA=/var/lib/polylab \
  python3 polylab_server.py serve --site /srv/polylab/site --port 8080
```
The built-in server handles a class easily. For more, run the same app under
a WSGI server, e.g. `POLYLAB_SITE=/srv/polylab/site POLYLAB_KEY_FILE=...
gunicorn -w 4 polylab_server:application`.

The proxy must pass `X-Forwarded-Proto` (so the session cookie gets
`Secure`) and `X-Forwarded-For` (for the log).

**Settings**

| Variable | Meaning |
|---|---|
| `POLYLAB_KEY_FILE` or `POLYLAB_KEY` | the secret key (64+ hex digits) |
| `POLYLAB_DATA` | folder for `submissions.jsonl` (default `./polylab-data`) |
| `POLYLAB_TZ` | time zone for the exercise date (default `America/New_York`) |
| `POLYLAB_DATE` | one fixed exercise date (`YYYY-MM-DD`) for everyone |
| `POLYLAB_SECRETS` | optional JSON file with seed-v1 labs' passwords (below) |

## Grading seed-v1 labs (polylinux-basic)

Answers still go through the lab's answer form. A grader that regenerates
answers uses the same seed-v1 formula, so it just needs the lab's secret
`SYSTEM_PASSWORD` (and `LEVEL_PASSWORD_ROOT`):
```sh
POLYLAB_KEY_FILE=/etc/polylab/key python3 polylab_server.py lab-secrets --lab polylinux-basic
```

By default these are derived from the key. To choose them yourself (for
example, to keep a grader's existing password), put them in a JSON file and
point `POLYLAB_SECRETS` at it:
```json
{"polylinux-basic": {"system_password": "...", "level_password_root": "levelPassword"}}
```

## Command line

```sh
polylab_server.py new-key                       # a new secret key
polylab_server.py lab-secrets --lab LAB         # seed-v1 labs: for the grader
polylab_server.py archive --lab LAB [--vm VM] --email E [--date D] --out F
                                                # one student's archive (CI uses this)
polylab_server.py flag --lab LAB --email E [--date D]   # single-flag labs
```

## Tests

CI (`labs.yml`) tests a lab with `"server": true` in `polyci.json` using a
throwaway key:
- the QEMU boot test appends a student archive (`boot-test.py --scenario`)
  and checks the installer uses it;
- the Chrome page test runs against this server and checks the page's email
  form, the appended archive, and that the first level names the student.
