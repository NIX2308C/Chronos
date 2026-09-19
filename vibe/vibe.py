#!/usr/bin/env python3
"""
vibe - a tiny Git client locked to a single repository.

Speaks Git's smart-HTTP protocol (v1) directly over TLS. No git binary, no
Node, no pip packages: Python 3.8+ standard library only.

Repository: https://lol.tevproject.com/NIX/Chronos
"""

from __future__ import annotations

import argparse
import base64
import binascii
import difflib
import fnmatch
import getpass
import hashlib
import json
import os
import re
import shutil
import ssl
import stat
import struct
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import zlib
from pathlib import Path

# --------------------------------------------------------------------------
# Constants
# --------------------------------------------------------------------------

VERSION = "1.3.0"
AGENT = "vibe/" + VERSION

REPO_WEB_URL = "https://lol.tevproject.com/NIX/Chronos"
REMOTE_URL = REPO_WEB_URL + ".git"
REPO_HOST = "lol.tevproject.com"
DEFAULT_BRANCH = "main"
DEFAULT_DIR = "Chronos"

VIBE_DIR = ".vibe"
ZERO = "0" * 40

CONFIG_HOME = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "vibe"
CRED_FILE = CONFIG_HOME / "credentials.json"
GLOBAL_CONFIG = CONFIG_HOME / "config.json"

OBJ_TYPES = {1: "commit", 2: "tree", 3: "blob", 4: "tag"}
TYPE_NUMS = {"commit": 1, "tree": 2, "blob": 3, "tag": 4}


# --------------------------------------------------------------------------
# Tiny output helpers
# --------------------------------------------------------------------------

def _color(code, text):
    if os.environ.get("NO_COLOR") or not sys.stdout.isatty():
        return text
    return "\033[%sm%s\033[0m" % (code, text)


def bold(t):
    return _color("1", t)


def green(t):
    return _color("32", t)


def red(t):
    return _color("31", t)


def yellow(t):
    return _color("33", t)


def die(msg, code=1):
    print("vibe: " + str(msg), file=sys.stderr)
    sys.exit(code)


def warn(msg):
    print("vibe: " + str(msg), file=sys.stderr)


class VibeError(Exception):
    pass


# --------------------------------------------------------------------------
# Credentials / config
# --------------------------------------------------------------------------

def load_json(path, default=None):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {} if default is None else default


def save_json(path, data, secret=False):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2)
        fh.write("\n")
    if secret:
        os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def load_credentials():
    user = os.environ.get("VIBE_USERNAME")
    token = os.environ.get("VIBE_TOKEN")
    if user and token:
        return {"username": user, "token": token}
    data = load_json(CRED_FILE)
    if data.get("username") and data.get("token"):
        return data
    return None


def auth_header():
    creds = load_credentials()
    if not creds:
        return None
    raw = ("%s:%s" % (creds["username"], creds["token"])).encode("utf-8")
    return "Basic " + base64.b64encode(raw).decode("ascii")


# --------------------------------------------------------------------------
# HTTP transport
# --------------------------------------------------------------------------

def ssl_context():
    if os.environ.get("VIBE_INSECURE") == "1":
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        return ctx
    return ssl.create_default_context()


def http(url, body=None, content_type=None, accept=None, timeout=120):
    """Single HTTP request. Returns response bytes, raises VibeError on failure."""
    req = urllib.request.Request(url, data=body, method="POST" if body is not None else "GET")
    # Some servers gate the smart protocol on a git-looking user agent.
    req.add_header("User-Agent", "git/2.43.0 " + AGENT)
    req.add_header("Accept-Encoding", "identity")
    req.add_header("Pragma", "no-cache")
    if content_type:
        req.add_header("Content-Type", content_type)
    if accept:
        req.add_header("Accept", accept)
    auth = auth_header()
    if auth:
        req.add_header("Authorization", auth)

    handlers = []
    if url.lower().startswith("https"):
        handlers.append(urllib.request.HTTPSHandler(context=ssl_context()))
    opener = urllib.request.build_opener(*handlers)
    try:
        with opener.open(req, timeout=timeout) as resp:
            return resp.read()
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = exc.read().decode("utf-8", "replace").strip()[:400]
        except Exception:
            pass
        if exc.code in (401, 403):
            raise VibeError(
                "authentication failed (HTTP %d).\n"
                "  Run:  vb login\n"
                "  Use a Gitea access token with repo read/write scope\n"
                "  (%s/user/settings/applications)" % (exc.code, "https://" + REPO_HOST)
            )
        if exc.code == 404:
            raise VibeError("repository not found at %s (HTTP 404). %s" % (url, detail))
        raise VibeError("HTTP %d from %s\n%s" % (exc.code, url, detail))
    except urllib.error.URLError as exc:
        reason = getattr(exc, "reason", exc)
        if isinstance(reason, ssl.SSLError):
            raise VibeError(
                "TLS error talking to %s: %s\n"
                "  If this host uses a self-signed certificate, retry with VIBE_INSECURE=1"
                % (REPO_HOST, reason)
            )
        raise VibeError("cannot reach %s: %s" % (REPO_HOST, reason))


# --------------------------------------------------------------------------
# External read-only transport (GitHub imports)
# --------------------------------------------------------------------------

def external_http(url, body=None, content_type=None, accept=None, timeout=120, auth=None):
    """HTTP request that never reuses the saved Gitea credential."""
    req = urllib.request.Request(url, data=body, method="POST" if body is not None else "GET")
    req.add_header("User-Agent", "git/2.43.0 " + AGENT)
    req.add_header("Accept-Encoding", "identity")
    req.add_header("Pragma", "no-cache")
    if content_type:
        req.add_header("Content-Type", content_type)
    if accept:
        req.add_header("Accept", accept)
    if auth:
        req.add_header("Authorization", auth)
    handlers = []
    if url.lower().startswith("https"):
        handlers.append(urllib.request.HTTPSHandler(context=ssl_context()))
    opener = urllib.request.build_opener(*handlers)
    try:
        with opener.open(req, timeout=timeout) as resp:
            return resp.read()
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = exc.read().decode("utf-8", "replace").strip()[:400]
        except Exception:
            pass
        if exc.code in (401, 403):
            raise VibeError(
                "GitHub rejected the read (HTTP %d). If the repo is private, set "
                "VIBE_GITHUB_TOKEN in your shell and retry." % exc.code
            )
        if exc.code == 404:
            raise VibeError("GitHub repository not found at %s" % url)
        raise VibeError("HTTP %d from GitHub\n%s" % (exc.code, detail))
    except urllib.error.URLError as exc:
        raise VibeError("cannot reach GitHub: %s" % getattr(exc, "reason", exc))


def github_auth_header():
    """Optional GitHub token; kept separate so the Gitea token can never leak."""
    token = os.environ.get("VIBE_GITHUB_TOKEN")
    if not token:
        return None
    raw = ("x-access-token:%s" % token).encode("utf-8")
    return "Basic " + base64.b64encode(raw).decode("ascii")


def normalize_github_url(value: str) -> str:
    value = (value or "").strip().rstrip("/")
    if not value:
        raise VibeError("no GitHub repo configured. First run: vb github-pull https://github.com/OWNER/REPO")
    if "://" not in value:
        value = "https://github.com/" + value.lstrip("/")
    parsed = urllib.parse.urlparse(value)
    if parsed.scheme != "https" or parsed.netloc.lower() != "github.com":
        raise VibeError("github-pull only reads from https://github.com/OWNER/REPO")
    path = parsed.path.rstrip("/")
    if path.endswith(".git"):
        path = path[:-4]
    parts = [part for part in path.split("/") if part]
    if len(parts) != 2:
        raise VibeError("GitHub repo must look like https://github.com/OWNER/REPO")
    return "https://github.com/%s/%s.git" % (parts[0], parts[1])


# --------------------------------------------------------------------------
# pkt-line framing
# --------------------------------------------------------------------------

FLUSH = b"0000"


def pkt(payload: bytes) -> bytes:
    return b"%04x" % (len(payload) + 4) + payload


class PktReader:
    """Reads pkt-lines out of an in-memory buffer."""

    def __init__(self, data: bytes):
        self.data = data
        self.pos = 0

    def read(self):
        """Returns bytes payload, b'' for flush-pkt, or None at end of buffer."""
        if self.pos + 4 > len(self.data):
            return None
        head = self.data[self.pos:self.pos + 4]
        try:
            length = int(head, 16)
        except ValueError:
            raise VibeError("malformed pkt-line header %r" % head)
        if length == 0:
            self.pos += 4
            return b""
        if length < 4 or self.pos + length > len(self.data):
            raise VibeError("truncated pkt-line (want %d bytes)" % length)
        payload = self.data[self.pos + 4:self.pos + length]
        self.pos += length
        return payload

    def rest(self):
        return self.data[self.pos:]


def parse_ref_advertisement(data: bytes):
    """Returns (refs dict name->sha, capability set)."""
    reader = PktReader(data)
    refs = {}
    caps = set()
    seen_ref = False
    while True:
        line = reader.read()
        if line is None:
            break
        if line == b"":
            if seen_ref:
                break
            continue
        if line.startswith(b"#"):
            continue
        if line.startswith(b"ERR "):
            raise VibeError("remote error: " + line[4:].decode("utf-8", "replace").strip())
        seen_ref = True
        if b"\x00" in line:
            line, capstr = line.split(b"\x00", 1)
            caps |= set(capstr.decode("utf-8", "replace").split())
        text = line.decode("utf-8", "replace").strip()
        if " " not in text:
            continue
        sha, name = text.split(" ", 1)
        if name.endswith("^{}") or name == "capabilities^{}":
            continue
        refs[name] = sha
    return refs, caps


def demux_sideband(data: bytes, progress=True):
    """Split a side-band-64k stream into (payload bytes, control lines)."""
    reader = PktReader(data)
    payload = bytearray()
    control = []
    while True:
        line = reader.read()
        if line is None:
            break
        if line == b"":
            continue
        band = line[:1]
        if band == b"\x01":
            payload += line[1:]
        elif band == b"\x02":
            if progress:
                text = line[1:].decode("utf-8", "replace")
                sys.stderr.write(text)
                sys.stderr.flush()
        elif band == b"\x03":
            raise VibeError("remote: " + line[1:].decode("utf-8", "replace").strip())
        else:
            text = line.decode("utf-8", "replace").strip()
            if text.startswith("ERR "):
                raise VibeError("remote: " + text[4:])
            control.append(text)
    return bytes(payload), control


# --------------------------------------------------------------------------
# zlib / delta primitives
# --------------------------------------------------------------------------

def inflate_at(buf: bytes, pos: int):
    """Inflate one zlib stream starting at pos. Returns (data, bytes_consumed)."""
    obj = zlib.decompressobj()
    out = bytearray()
    consumed = 0
    chunk_size = 65536
    total = len(buf)
    while True:
        end = min(pos + consumed + chunk_size, total)
        chunk = buf[pos + consumed:end]
        if not chunk:
            if not obj.eof:
                raise VibeError("truncated zlib stream in packfile")
            break
        out += obj.decompress(chunk)
        consumed += len(chunk)
        if obj.eof:
            consumed -= len(obj.unused_data)
            break
    out += obj.flush()
    return bytes(out), consumed


def read_varint(data: bytes, pos: int):
    result = 0
    shift = 0
    while True:
        byte = data[pos]
        pos += 1
        result |= (byte & 0x7F) << shift
        shift += 7
        if not byte & 0x80:
            return result, pos


def apply_delta(base: bytes, delta: bytes) -> bytes:
    pos = 0
    base_size, pos = read_varint(delta, pos)
    if base_size != len(base):
        raise VibeError("delta base size mismatch (%d != %d)" % (base_size, len(base)))
    out_size, pos = read_varint(delta, pos)
    out = bytearray()
    length = len(delta)
    while pos < length:
        op = delta[pos]
        pos += 1
        if op & 0x80:
            offset = 0
            size = 0
            for i in range(4):
                if op & (1 << i):
                    offset |= delta[pos] << (i * 8)
                    pos += 1
            for i in range(3):
                if op & (0x10 << i):
                    size |= delta[pos] << (i * 8)
                    pos += 1
            if size == 0:
                size = 0x10000
            out += base[offset:offset + size]
        elif op:
            out += delta[pos:pos + op]
            pos += op
        else:
            raise VibeError("invalid delta opcode 0x00")
    if len(out) != out_size:
        raise VibeError("delta result size mismatch (%d != %d)" % (len(out), out_size))
    return bytes(out)


def encode_obj_header(type_num: int, size: int) -> bytes:
    out = bytearray()
    byte = (type_num << 4) | (size & 0x0F)
    size >>= 4
    while size:
        out.append(byte | 0x80)
        byte = size & 0x7F
        size >>= 7
    out.append(byte)
    return bytes(out)


# --------------------------------------------------------------------------
# Object store / repository
# --------------------------------------------------------------------------

def hash_object(obj_type: str, data: bytes) -> str:
    header = ("%s %d\x00" % (obj_type, len(data))).encode("ascii")
    return hashlib.sha1(header + data).hexdigest()


class Repo:
    def __init__(self, root: Path):
        self.root = Path(root).resolve()
        self.dir = self.root / VIBE_DIR

    # -- discovery ---------------------------------------------------------
    @classmethod
    def find(cls, start=None):
        current = Path(start or os.getcwd()).resolve()
        for candidate in [current] + list(current.parents):
            if (candidate / VIBE_DIR / "HEAD").exists():
                return cls(candidate)
        die("not inside a vibe repository (run `vb clone` first)")

    @classmethod
    def create(cls, root: Path):
        repo = cls(root)
        (repo.dir / "objects").mkdir(parents=True, exist_ok=True)
        (repo.dir / "refs" / "heads").mkdir(parents=True, exist_ok=True)
        (repo.dir / "refs" / "remotes" / "origin").mkdir(parents=True, exist_ok=True)
        repo.write_head_ref("refs/heads/" + DEFAULT_BRANCH)
        save_json(repo.dir / "config.json", {"remote": REMOTE_URL})
        repo.write_index({})
        return repo

    # -- objects -----------------------------------------------------------
    def object_path(self, sha: str) -> Path:
        return self.dir / "objects" / sha[:2] / sha[2:]

    def has_object(self, sha: str) -> bool:
        return bool(sha) and sha != ZERO and self.object_path(sha).exists()

    def write_object(self, obj_type: str, data: bytes) -> str:
        sha = hash_object(obj_type, data)
        path = self.object_path(sha)
        if path.exists():
            return sha
        path.parent.mkdir(parents=True, exist_ok=True)
        header = ("%s %d\x00" % (obj_type, len(data))).encode("ascii")
        tmp = path.with_name(path.name + ".tmp%d" % os.getpid())
        with open(tmp, "wb") as fh:
            fh.write(zlib.compress(header + data, 1))
        os.replace(tmp, path)
        return sha

    def read_object(self, sha: str):
        path = self.object_path(sha)
        if not path.exists():
            raise VibeError("object %s is missing from the local store" % sha[:10])
        with open(path, "rb") as fh:
            raw = zlib.decompress(fh.read())
        nul = raw.index(b"\x00")
        obj_type, size = raw[:nul].decode("ascii").split(" ")
        data = raw[nul + 1:]
        if len(data) != int(size):
            raise VibeError("object %s is corrupt" % sha[:10])
        return obj_type, data

    def all_object_shas(self):
        base = self.dir / "objects"
        if not base.exists():
            return
        for sub in base.iterdir():
            if not sub.is_dir() or len(sub.name) != 2:
                continue
            for item in sub.iterdir():
                if len(item.name) == 38:
                    yield sub.name + item.name

    # -- refs --------------------------------------------------------------
    def ref_path(self, ref: str) -> Path:
        return self.dir / ref

    def read_ref(self, ref: str):
        path = self.ref_path(ref)
        if not path.exists():
            return None
        value = path.read_text(encoding="utf-8").strip()
        return value or None

    def write_ref(self, ref: str, sha: str):
        path = self.ref_path(ref)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(sha + "\n", encoding="utf-8")

    def delete_ref(self, ref: str):
        path = self.ref_path(ref)
        if path.exists():
            path.unlink()

    def list_refs(self, prefix: str):
        base = self.dir / prefix
        out = {}
        if not base.exists():
            return out
        for path in base.rglob("*"):
            if path.is_file():
                name = prefix + "/" + str(path.relative_to(base)).replace(os.sep, "/")
                out[name] = path.read_text(encoding="utf-8").strip()
        return out

    def write_head_ref(self, ref: str):
        (self.dir / "HEAD").write_text("ref: %s\n" % ref, encoding="utf-8")

    def head_ref(self):
        raw = (self.dir / "HEAD").read_text(encoding="utf-8").strip()
        if raw.startswith("ref: "):
            return raw[5:].strip()
        return None  # detached

    def current_branch(self):
        ref = self.head_ref()
        if ref and ref.startswith("refs/heads/"):
            return ref[len("refs/heads/"):]
        return None

    def head_commit(self):
        ref = self.head_ref()
        if ref:
            return self.read_ref(ref)
        raw = (self.dir / "HEAD").read_text(encoding="utf-8").strip()
        return raw or None

    # -- index (snapshot of last checkout/commit) --------------------------
    def read_index(self):
        data = load_json(self.dir / "index.json", {})
        return {k: (v[0], v[1]) for k, v in data.items()}

    def write_index(self, entries):
        save_json(self.dir / "index.json", {k: [v[0], v[1]] for k, v in sorted(entries.items())})

    # -- config ------------------------------------------------------------
    def config(self):
        merged = load_json(GLOBAL_CONFIG, {})
        merged.update(load_json(self.dir / "config.json", {}))
        return merged

    def identity(self):
        cfg = self.config()
        name = os.environ.get("VIBE_AUTHOR_NAME") or cfg.get("user.name")
        email = os.environ.get("VIBE_AUTHOR_EMAIL") or cfg.get("user.email")
        creds = load_credentials()
        if not name and creds:
            name = creds["username"]
        if not email and creds:
            email = "%s@users.noreply.%s" % (creds["username"], REPO_HOST)
        if not name or not email:
            raise VibeError(
                "author identity unknown. Set it with:\n"
                "  vb config user.name \"Your Name\"\n"
                "  vb config user.email you@example.com"
            )
        return name, email


# --------------------------------------------------------------------------
# Commit / tree parsing
# --------------------------------------------------------------------------

def parse_tree(data: bytes):
    entries = []
    pos = 0
    while pos < len(data):
        space = data.index(b" ", pos)
        mode = data[pos:space].decode("ascii")
        nul = data.index(b"\x00", space)
        name = data[space + 1:nul].decode("utf-8", "surrogateescape")
        sha = binascii.hexlify(data[nul + 1:nul + 21]).decode("ascii")
        entries.append((mode, name, sha))
        pos = nul + 21
    return entries


def build_tree(entries):
    """entries: iterable of (mode, name, sha). Returns serialized tree bytes."""
    def sort_key(entry):
        mode, name, _ = entry
        return (name + "/") if mode == "40000" else name

    out = bytearray()
    for mode, name, sha in sorted(entries, key=sort_key):
        out += mode.encode("ascii") + b" "
        out += name.encode("utf-8", "surrogateescape") + b"\x00"
        out += binascii.unhexlify(sha)
    return bytes(out)


def parse_commit(data: bytes):
    text = data.decode("utf-8", "replace")
    headers = {"parent": []}
    lines = text.split("\n")
    idx = 0
    for idx, line in enumerate(lines):
        if line == "":
            break
        key, _, value = line.partition(" ")
        if key == "parent":
            headers["parent"].append(value)
        else:
            headers.setdefault(key, value)
    headers["message"] = "\n".join(lines[idx + 1:])
    return headers


def commit_time(repo: Repo, sha: str) -> int:
    try:
        obj_type, data = repo.read_object(sha)
        if obj_type != "commit":
            return 0
        committer = parse_commit(data).get("committer", "")
        parts = committer.split()
        if len(parts) >= 2:
            return int(parts[-2])
    except Exception:
        pass
    return 0


def format_ident(name, email, when=None):
    when = when or time.time()
    seconds = int(when)
    offset = -time.timezone if (time.localtime(seconds).tm_isdst == 0) else -time.altzone
    sign = "+" if offset >= 0 else "-"
    offset = abs(offset)
    return "%s <%s> %d %s%02d%02d" % (name, email, seconds, sign, offset // 3600, (offset % 3600) // 60)


def build_commit(tree_sha, parents, name, email, message):
    lines = ["tree " + tree_sha]
    for parent in parents:
        lines.append("parent " + parent)
    ident = format_ident(name, email)
    lines.append("author " + ident)
    lines.append("committer " + ident)
    lines.append("")
    lines.append(message.rstrip("\n") + "\n")
    return "\n".join(lines).encode("utf-8")


# --------------------------------------------------------------------------
# Graph walking
# --------------------------------------------------------------------------

def commit_parents(repo: Repo, sha: str):
    obj_type, data = repo.read_object(sha)
    if obj_type == "tag":
        for line in data.decode("utf-8", "replace").split("\n"):
            if line.startswith("object "):
                return commit_parents(repo, line.split()[1])
    if obj_type != "commit":
        return []
    return parse_commit(data)["parent"]


def ancestors(repo: Repo, sha: str, limit=None):
    seen = set()
    queue = [sha]
    while queue:
        current = queue.pop()
        if not current or current in seen or not repo.has_object(current):
            continue
        seen.add(current)
        if limit and len(seen) >= limit:
            break
        queue.extend(commit_parents(repo, current))
    return seen


def is_ancestor(repo: Repo, maybe_ancestor: str, descendant: str) -> bool:
    if maybe_ancestor == descendant:
        return True
    return maybe_ancestor in ancestors(repo, descendant)


def merge_base(repo: Repo, a: str, b: str):
    a_set = ancestors(repo, a)
    best = None
    best_time = -1
    for candidate in ancestors(repo, b):
        if candidate in a_set:
            when = commit_time(repo, candidate)
            if when > best_time:
                best, best_time = candidate, when
    return best


def reachable_objects(repo: Repo, commits, stop=frozenset()):
    """All object shas reachable from the given commits, minus `stop` objects."""
    objects = set()
    seen_commits = set()
    queue = [c for c in commits if c and c != ZERO]

    def walk_tree(tree_sha):
        stack = [tree_sha]
        while stack:
            current = stack.pop()
            if current in objects or current in stop or not repo.has_object(current):
                continue
            objects.add(current)
            obj_type, data = repo.read_object(current)
            if obj_type != "tree":
                continue
            for mode, _name, sha in parse_tree(data):
                if sha in objects or sha in stop:
                    continue
                if mode == "40000":
                    stack.append(sha)
                elif repo.has_object(sha):
                    objects.add(sha)

    while queue:
        sha = queue.pop()
        if not sha or sha in seen_commits or sha in stop or not repo.has_object(sha):
            continue
        seen_commits.add(sha)
        objects.add(sha)
        obj_type, data = repo.read_object(sha)
        if obj_type != "commit":
            continue
        info = parse_commit(data)
        walk_tree(info["tree"])
        queue.extend(info["parent"])
    return objects


def check_closure(repo: Repo, tips):
    """Walk everything `tips` reach. Returns (missing shas, commit shas seen).

    Commits and trees get parsed - they are small, and any checkout has to read
    them anyway - while blobs are only checked for existence. The commit set is
    only trustworthy as "complete" when nothing came back missing.
    """
    missing = set()
    commits = set()
    trees = []
    queue = [t for t in tips if t and t != ZERO]
    while queue:
        sha = queue.pop()
        if sha in commits or sha in missing:
            continue
        if not repo.has_object(sha):
            missing.add(sha)
            continue
        obj_type, data = repo.read_object(sha)
        if obj_type != "commit":
            continue
        commits.add(sha)
        info = parse_commit(data)
        trees.append(info["tree"])
        queue.extend(info["parent"])

    seen = set()
    while trees:
        sha = trees.pop()
        if sha in seen:
            continue
        seen.add(sha)
        if not repo.has_object(sha):
            missing.add(sha)
            continue
        obj_type, data = repo.read_object(sha)
        if obj_type != "tree":
            continue
        for mode, _name, child in parse_tree(data):
            if child in seen:
                continue
            if mode == "40000":
                trees.append(child)
            else:
                seen.add(child)
                if not repo.has_object(child):
                    missing.add(child)
    return missing, commits


def recent_commits(repo: Repo, limit=200):
    """Commits we already have, newest first-ish - used as `have` lines."""
    tips = set()
    for group in ("refs/heads", "refs/remotes"):
        tips.update(repo.list_refs(group).values())
    head = repo.head_commit()
    if head:
        tips.add(head)
    out = []
    seen = set()
    queue = [t for t in tips if t and repo.has_object(t)]
    while queue and len(out) < limit:
        sha = queue.pop(0)
        if sha in seen or not repo.has_object(sha):
            continue
        seen.add(sha)
        out.append(sha)
        queue.extend(commit_parents(repo, sha))
    return out


# --------------------------------------------------------------------------
# Packfile read / write
# --------------------------------------------------------------------------

def unpack(repo: Repo, data: bytes, quiet=False):
    """Parse a packfile, resolve deltas, write every object into the store."""
    if len(data) < 32 or data[:4] != b"PACK":
        raise VibeError("server did not return a packfile")
    version, count = struct.unpack(">II", data[4:12])
    if version not in (2, 3):
        raise VibeError("unsupported pack version %d" % version)

    body_end = len(data) - 20
    checksum = data[body_end:]
    if hashlib.sha1(data[:body_end]).digest() != checksum:
        raise VibeError("packfile checksum mismatch (corrupt download)")

    pos = 12
    by_offset = {}          # offset -> (type, content)
    written = set()
    deferred = []           # (offset, base_sha_or_None, base_offset_or_None, delta)

    def store(obj_type, content):
        sha = repo.write_object(obj_type, content)
        written.add(sha)
        return sha

    for index in range(count):
        obj_offset = pos
        byte = data[pos]
        pos += 1
        type_num = (byte >> 4) & 7
        size = byte & 0x0F
        shift = 4
        while byte & 0x80:
            byte = data[pos]
            pos += 1
            size |= (byte & 0x7F) << shift
            shift += 7

        base_offset = None
        base_sha = None
        if type_num == 6:                     # OBJ_OFS_DELTA
            byte = data[pos]
            pos += 1
            offset = byte & 0x7F
            while byte & 0x80:
                byte = data[pos]
                pos += 1
                offset = ((offset + 1) << 7) | (byte & 0x7F)
            base_offset = obj_offset - offset
        elif type_num == 7:                   # OBJ_REF_DELTA
            base_sha = binascii.hexlify(data[pos:pos + 20]).decode("ascii")
            pos += 20

        content, consumed = inflate_at(data, pos)
        pos += consumed
        if len(content) != size:
            raise VibeError("object %d has bad size in pack" % index)

        if type_num in OBJ_TYPES:
            obj_type = OBJ_TYPES[type_num]
            by_offset[obj_offset] = (obj_type, content)
            store(obj_type, content)
        elif type_num == 6:
            if base_offset in by_offset:
                base_type, base_data = by_offset[base_offset]
                resolved = apply_delta(base_data, content)
                by_offset[obj_offset] = (base_type, resolved)
                store(base_type, resolved)
            else:
                # Base is itself an unresolved delta further along the pack.
                deferred.append((obj_offset, None, base_offset, content))
        elif type_num == 7:
            deferred.append((obj_offset, base_sha, None, content))
        else:
            raise VibeError("unknown pack object type %d" % type_num)

        if not quiet and count > 200 and index % 200 == 0:
            sys.stderr.write("\rvibe: unpacking %d/%d" % (index + 1, count))
            sys.stderr.flush()

    # REF_DELTAs may point at objects later in the pack, or at ones we already
    # own (thin packs). Loop until nothing new resolves.
    while deferred:
        progress = False
        remaining = []
        for obj_offset, base_sha, base_offset, delta in deferred:
            if base_offset is not None:
                if base_offset not in by_offset:
                    remaining.append((obj_offset, base_sha, base_offset, delta))
                    continue
                base_type, base_data = by_offset[base_offset]
            elif repo.has_object(base_sha):
                base_type, base_data = repo.read_object(base_sha)
            else:
                remaining.append((obj_offset, base_sha, base_offset, delta))
                continue
            resolved = apply_delta(base_data, delta)
            by_offset[obj_offset] = (base_type, resolved)
            store(base_type, resolved)
            progress = True
        deferred = remaining
        if deferred and not progress:
            raise VibeError("pack has %d deltas with missing bases" % len(deferred))

    if not quiet and count > 200:
        sys.stderr.write("\r")
        sys.stderr.flush()
    return written


def build_pack(repo: Repo, shas):
    body = bytearray(b"PACK")
    body += struct.pack(">II", 2, len(shas))
    for sha in shas:
        obj_type, data = repo.read_object(sha)
        body += encode_obj_header(TYPE_NUMS[obj_type], len(data))
        body += zlib.compress(data, 6)
    body += hashlib.sha1(bytes(body)).digest()
    return bytes(body)


# --------------------------------------------------------------------------
# Remote operations
# --------------------------------------------------------------------------

def check_url(url):
    if not url:
        return
    normalized = url.rstrip("/")
    for suffix in ("/src/branch/" + DEFAULT_BRANCH, ".git"):
        if normalized.endswith(suffix):
            normalized = normalized[: -len(suffix)]
    normalized = normalized.rstrip("/")
    if normalized.split("/src/")[0] != REPO_WEB_URL:
        raise VibeError(
            "vibe only talks to %s\n  (refusing %s)" % (REPO_WEB_URL, url)
        )


def remote_refs(service):
    url = "%s/info/refs?service=%s" % (REMOTE_URL, service)
    data = http(url, accept="application/x-%s-advertisement" % service)
    if len(data) < 4 or not all(c in b"0123456789abcdefABCDEF" for c in data[:4]):
        raise VibeError("server did not answer with the smart HTTP protocol")
    return parse_ref_advertisement(data)


def fetch_into(repo: Repo, heads, caps, post, source, remote, quiet=False):
    """Make the store hold everything `heads` reach, then move refs/remotes/<remote>/*.

    `post` sends one upload-pack request body and returns the response bytes.
    """
    present = [sha for sha in heads.values() if repo.has_object(sha)]
    missing, verified = check_closure(repo, present)

    if missing:
        # Claiming `have` for a commit whose objects are only half here is what
        # makes this state stick: the server trims exactly the objects we are
        # short of. So claim nothing and ask for the heads outright.
        if not quiet:
            warn("%d object(s) that should already be here are missing - "
                 "asking %s for a complete copy" % (len(missing), source))
        wants = sorted(set(heads.values()))
        haves = []
    else:
        wants = sorted({sha for sha in heads.values() if not repo.has_object(sha)})
        haves = [c for c in recent_commits(repo) if c in verified]

    if wants:
        wanted_caps = []
        for cap in ("side-band-64k", "ofs-delta", "thin-pack", "no-progress" if quiet else None):
            if cap and cap in caps:
                wanted_caps.append(cap)
        wanted_caps.append("agent=" + AGENT)
        sideband = "side-band-64k" in caps

        request = bytearray()
        for index, sha in enumerate(wants):
            line = "want %s" % sha
            if index == 0:
                line += " " + " ".join(wanted_caps)
            request += pkt((line + "\n").encode("ascii"))
        request += FLUSH
        for sha in haves:
            request += pkt(("have %s\n" % sha).encode("ascii"))
        request += pkt(b"done\n")

        response = post(bytes(request))
        if sideband:
            packdata, _control = demux_sideband(response, progress=not quiet)
        else:
            marker = response.find(b"PACK")
            if marker < 0:
                raise VibeError("no packfile in the response from %s" % source)
            packdata = response[marker:]
        unpack(repo, packdata, quiet=quiet)

        # Refuse to record refs that point into a store with holes, so the
        # failure surfaces here instead of during some later checkout.
        still, _ = check_closure(repo, list(heads.values()))
        if still:
            raise VibeError(
                "%s did not send %d object(s) this repository needs (first: %s).\n"
                "  The local copy has holes that the remote cannot fill.\n"
                "  Clone a fresh one beside it and move your work across:\n"
                "    vb clone %s %s-fresh"
                % (source, len(still), short(sorted(still)[0]), REPO_WEB_URL, DEFAULT_DIR))

    prefix = "refs/remotes/" + remote + "/"
    for name, sha in heads.items():
        repo.write_ref(prefix + name[len("refs/heads/"):], sha)
    # Drop remote-tracking refs for branches that no longer exist.
    for name in list(repo.list_refs("refs/remotes/" + remote)):
        if ("refs/heads/" + name[len(prefix):]) not in heads:
            repo.delete_ref(name)


def fetch(repo: Repo, quiet=False):
    """Fetch all remote heads into refs/remotes/origin/*. Returns remote heads."""
    refs, caps = remote_refs("git-upload-pack")
    heads = {n: s for n, s in refs.items() if n.startswith("refs/heads/")}
    if not heads:
        return {}

    def post(body):
        return http(
            REMOTE_URL + "/git-upload-pack",
            body=body,
            content_type="application/x-git-upload-pack-request",
            accept="application/x-git-upload-pack-result",
        )

    fetch_into(repo, heads, caps, post, REPO_HOST, "origin", quiet=quiet)
    return heads


def external_remote_refs(remote_url: str):
    service = "git-upload-pack"
    url = "%s/info/refs?service=%s" % (remote_url, service)
    data = external_http(
        url,
        accept="application/x-%s-advertisement" % service,
        auth=github_auth_header(),
    )
    if len(data) < 4 or not all(c in b"0123456789abcdefABCDEF" for c in data[:4]):
        raise VibeError("GitHub did not answer with the smart HTTP protocol")
    return parse_ref_advertisement(data)


def fetch_github(repo: Repo, remote_url: str, quiet=False):
    """Read all GitHub branch heads into refs/remotes/github/* using stdlib only."""
    refs, caps = external_remote_refs(remote_url)
    heads = {n: s for n, s in refs.items() if n.startswith("refs/heads/")}
    if not heads:
        return {}

    def post(body):
        return external_http(
            remote_url + "/git-upload-pack",
            body=body,
            content_type="application/x-git-upload-pack-request",
            accept="application/x-git-upload-pack-result",
            auth=github_auth_header(),
        )

    fetch_into(repo, heads, caps, post, "GitHub", "github", quiet=quiet)
    return heads


def push_many(repo: Repo, updates, quiet=False):
    """Push several branches in one request.

    updates: list of (branch, old_sha, new_sha). One packfile carries the
    objects for every ref being moved, so a mirror is a single round trip.
    """
    if not updates:
        return 0
    refs, caps = remote_refs("git-receive-pack")

    for branch, old, _new in updates:
        server_old = refs.get("refs/heads/" + branch, ZERO)
        if server_old != old:
            raise VibeError(
                "remote %s moved to %s while pushing - run `vb pull` first"
                % (branch, short(server_old) if server_old != ZERO else "deleted")
            )

    # Everything the remote can already reach is off the table.
    stop = set()
    for _branch, old, _new in updates:
        if repo.has_object(old):
            stop |= reachable_objects(repo, [old])
    for name, sha in refs.items():
        if name.startswith("refs/heads/") and repo.has_object(sha):
            stop |= reachable_objects(repo, [sha])

    objects = set()
    for _branch, _old, new in updates:
        objects |= reachable_objects(repo, [new], stop=stop)
    objects = sorted(objects - stop)

    wanted_caps = [c for c in ("report-status", "side-band-64k", "ofs-delta") if c in caps]
    wanted_caps.append("agent=" + AGENT)

    request = bytearray()
    for index, (branch, old, new) in enumerate(updates):
        command = "%s %s refs/heads/%s" % (old, new, branch)
        if index == 0:
            command += "\x00" + " ".join(wanted_caps)
        request += pkt((command + "\n").encode("ascii"))
    request += FLUSH
    request += build_pack(repo, objects)

    if not quiet:
        print("Sending %d object%s (%s) for %d branch%s"
              % (len(objects), "" if len(objects) == 1 else "s",
                 human_size(len(request)), len(updates),
                 "" if len(updates) == 1 else "es"))

    response = http(
        REMOTE_URL + "/git-receive-pack",
        body=bytes(request),
        content_type="application/x-git-receive-pack-request",
        accept="application/x-git-receive-pack-result",
    )

    if "side-band-64k" in wanted_caps:
        payload, control = demux_sideband(response, progress=not quiet)
    else:
        payload, control = response, []
    lines = []
    reader = PktReader(payload)
    while True:
        line = reader.read()
        if line is None:
            break
        if line:
            lines.append(line.decode("utf-8", "replace").strip())
    lines.extend(control)

    unpack_ok = any(line == "unpack ok" for line in lines)
    ref_ok = any(line.startswith("ok ") for line in lines)
    rejected = [line for line in lines if line.startswith("ng ")]
    if rejected or (lines and not (unpack_ok or ref_ok)):
        raise VibeError("remote rejected the push:\n  " + "\n  ".join(lines or ["(no report)"]))

    for branch, _old, new in updates:
        repo.write_ref("refs/remotes/origin/" + branch, new)
    return len(objects)


def push_refs(repo: Repo, branch: str, old: str, new: str, quiet=False):
    return push_many(repo, [(branch, old, new)], quiet=quiet)


def human_size(count):
    for unit in ("B", "KiB", "MiB", "GiB"):
        if count < 1024 or unit == "GiB":
            return "%.1f %s" % (count, unit) if unit != "B" else "%d B" % count
        count /= 1024.0


# --------------------------------------------------------------------------
# Ignore rules
# --------------------------------------------------------------------------

class IgnoreRules:
    """A practical subset of .gitignore semantics."""

    def __init__(self):
        self.rules = []  # (base_dir, pattern, negated, dir_only, anchored)

    def add_file(self, path: Path, base: str):
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return
        for raw in text.splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            negated = line.startswith("!")
            if negated:
                line = line[1:]
            dir_only = line.endswith("/")
            line = line.rstrip("/")
            anchored = line.startswith("/") or ("/" in line)
            line = line.lstrip("/")
            if line:
                self.rules.append((base, line, negated, dir_only, anchored))

    def match(self, rel_path: str, is_dir: bool) -> bool:
        ignored = False
        for base, pattern, negated, dir_only, anchored in self.rules:
            if base and not (rel_path == base or rel_path.startswith(base + "/")):
                continue
            local = rel_path[len(base) + 1:] if base else rel_path
            if dir_only and not is_dir and "/" not in local:
                continue
            candidates = [local]
            if not anchored:
                candidates.append(local.rsplit("/", 1)[-1])
            hit = False
            for candidate in candidates:
                if fnmatch.fnmatch(candidate, pattern):
                    hit = True
                    break
                # a matched directory ignores everything under it
                if candidate.startswith(pattern + "/"):
                    hit = True
                    break
            if hit:
                ignored = not negated
        return ignored


def load_ignores(root: Path) -> IgnoreRules:
    rules = IgnoreRules()
    for name in (".gitignore", ".vibeignore"):
        path = root / name
        if path.exists():
            rules.add_file(path, "")
    for path in root.rglob(".gitignore"):
        rel_dir = str(path.parent.relative_to(root)).replace(os.sep, "/")
        if rel_dir in (".", ""):
            continue
        if rel_dir.split("/")[0] == VIBE_DIR:
            continue
        rules.add_file(path, rel_dir)
    return rules


# --------------------------------------------------------------------------
# Working tree
# --------------------------------------------------------------------------

def file_mode(path: Path) -> str:
    if path.is_symlink():
        return "120000"
    return "100755" if os.access(path, os.X_OK) else "100644"


def snapshot_worktree(repo: Repo, write=True):
    """Hash every tracked-eligible file. Returns dict path -> (mode, sha)."""
    ignores = load_ignores(repo.root)
    entries = {}
    for dirpath, dirnames, filenames in os.walk(repo.root):
        rel_dir = os.path.relpath(dirpath, repo.root).replace(os.sep, "/")
        if rel_dir == ".":
            rel_dir = ""
        dirnames[:] = sorted(d for d in dirnames if d != VIBE_DIR and d != ".git")
        keep = []
        for name in dirnames:
            rel = (rel_dir + "/" + name) if rel_dir else name
            if not ignores.match(rel, True):
                keep.append(name)
        dirnames[:] = keep
        for name in sorted(filenames):
            rel = (rel_dir + "/" + name) if rel_dir else name
            if ignores.match(rel, False):
                continue
            path = Path(dirpath) / name
            try:
                if path.is_symlink():
                    data = os.readlink(path).encode("utf-8", "surrogateescape")
                else:
                    data = path.read_bytes()
            except OSError:
                continue
            sha = repo.write_object("blob", data) if write else hash_object("blob", data)
            entries[rel] = (file_mode(path), sha)
    return entries


def tree_to_entries(repo: Repo, tree_sha: str, prefix=""):
    """Flatten a tree into dict path -> (mode, sha)."""
    entries = {}
    if not tree_sha:
        return entries
    obj_type, data = repo.read_object(tree_sha)
    if obj_type != "tree":
        raise VibeError("%s is not a tree" % tree_sha[:10])
    for mode, name, sha in parse_tree(data):
        rel = prefix + name
        if mode == "40000":
            entries.update(tree_to_entries(repo, sha, rel + "/"))
        else:
            entries[rel] = (mode, sha)
    return entries


def commit_entries(repo: Repo, commit_sha: str):
    if not commit_sha:
        return {}
    obj_type, data = repo.read_object(commit_sha)
    if obj_type != "commit":
        raise VibeError("%s is not a commit" % commit_sha[:10])
    return tree_to_entries(repo, parse_commit(data)["tree"])


def write_tree_from_entries(repo: Repo, entries):
    root = {}
    for path, (mode, sha) in entries.items():
        parts = path.split("/")
        node = root
        for part in parts[:-1]:
            node = node.setdefault(part, {})
            if not isinstance(node, dict):
                raise VibeError("path conflict at " + path)
        node[parts[-1]] = (mode, sha)

    def write(node):
        items = []
        for name, value in node.items():
            if isinstance(value, dict):
                items.append(("40000", name, write(value)))
            else:
                items.append((value[0], name, value[1]))
        return repo.write_object("tree", build_tree(items))

    return write(root)


def write_worktree_file(repo: Repo, rel: str, mode: str, sha: str):
    path = repo.root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    obj_type, data = repo.read_object(sha)
    if obj_type != "blob":
        raise VibeError("expected a blob for %s" % rel)
    if path.is_symlink() or path.exists():
        if path.is_dir() and not path.is_symlink():
            shutil.rmtree(path)
        else:
            path.unlink()
    if mode == "120000":
        os.symlink(data.decode("utf-8", "surrogateescape"), path)
        return
    path.write_bytes(data)
    if mode == "100755":
        os.chmod(path, os.stat(path).st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def remove_worktree_file(repo: Repo, rel: str):
    path = repo.root / rel
    if path.is_symlink() or path.exists():
        try:
            path.unlink()
        except IsADirectoryError:
            shutil.rmtree(path)
    parent = path.parent
    while parent != repo.root:
        try:
            next(parent.iterdir())
            break
        except StopIteration:
            parent.rmdir()
            parent = parent.parent
        except OSError:
            break


def checkout_entries(repo: Repo, entries):
    """Make the working tree match `entries`, then rewrite the index."""
    current = repo.read_index()
    for rel in sorted(set(current) - set(entries)):
        remove_worktree_file(repo, rel)
    for rel, (mode, sha) in sorted(entries.items()):
        old = current.get(rel)
        path = repo.root / rel
        if old == (mode, sha) and (path.exists() or path.is_symlink()):
            continue
        write_worktree_file(repo, rel, mode, sha)
    repo.write_index(entries)


def worktree_changes(repo: Repo):
    """Returns (added, modified, deleted) relative to the index."""
    index = repo.read_index()
    current = snapshot_worktree(repo, write=False)
    added = sorted(set(current) - set(index))
    deleted = sorted(set(index) - set(current))
    modified = sorted(p for p in set(current) & set(index) if current[p] != index[p])
    return added, modified, deleted


def require_no_merge(repo: Repo, action="continue"):
    if (repo.dir / "MERGE_HEAD").exists():
        raise VibeError(
            "a merge is still in progress, so you can't %s yet.\n"
            "  Fix the conflicted files, then:  vb commit -m \"merge ...\"\n"
            "  Or throw the merge away:         vb merge --abort" % action
        )


def require_clean(repo: Repo, action="continue"):
    added, modified, deleted = worktree_changes(repo)
    if added or modified or deleted:
        raise VibeError(
            "you have uncommitted changes - commit them first (vb commit -m \"...\") "
            "before you %s" % action
        )


# --------------------------------------------------------------------------
# Three-way merge
# --------------------------------------------------------------------------

def find_sync_regions(base, left, right):
    """Classic diff3 sync-point search (as used by bzr's merge3)."""
    left_matches = difflib.SequenceMatcher(None, base, left).get_matching_blocks()
    right_matches = difflib.SequenceMatcher(None, base, right).get_matching_blocks()
    regions = []
    i = j = 0
    while i < len(left_matches) and j < len(right_matches):
        l_base, l_pos, l_len = left_matches[i]
        r_base, r_pos, r_len = right_matches[j]
        start = max(l_base, r_base)
        end = min(l_base + l_len, r_base + r_len)
        if start < end:
            regions.append((
                start, end,
                l_pos + (start - l_base), l_pos + (end - l_base),
                r_pos + (start - r_base), r_pos + (end - r_base),
            ))
        if l_base + l_len < r_base + r_len:
            i += 1
        else:
            j += 1
    regions.append((len(base), len(base), len(left), len(left), len(right), len(right)))
    return regions


def merge_lines(base, ours, theirs, ours_label, theirs_label):
    """Line-level three-way merge. Returns (lines, conflict_count)."""
    output = []
    conflicts = 0
    base_pos = ours_pos = theirs_pos = 0
    for region in find_sync_regions(base, ours, theirs):
        b_start, b_end, o_start, o_end, t_start, t_end = region
        base_chunk = base[base_pos:b_start]
        ours_chunk = ours[ours_pos:o_start]
        theirs_chunk = theirs[theirs_pos:t_start]

        if ours_chunk == theirs_chunk:
            output.extend(ours_chunk)
        elif ours_chunk == base_chunk:
            output.extend(theirs_chunk)
        elif theirs_chunk == base_chunk:
            output.extend(ours_chunk)
        else:
            conflicts += 1
            output.append("<<<<<<< %s\n" % ours_label)
            output.extend(ours_chunk)
            output.append("||||||| base\n")
            output.extend(base_chunk)
            output.append("=======\n")
            output.extend(theirs_chunk)
            output.append(">>>>>>> %s\n" % theirs_label)

        # the synced region itself is identical on all three sides
        output.extend(ours[o_start:o_end])
        base_pos, ours_pos, theirs_pos = b_end, o_end, t_end
    return output, conflicts


def is_binary(data: bytes) -> bool:
    return b"\x00" in data[:8000]


def three_way_merge(repo: Repo, base_entries, ours_entries, theirs_entries,
                    ours_label, theirs_label):
    """Merge file sets. Returns (entries, conflicted_paths)."""
    result = {}
    conflicted = []
    paths = set(base_entries) | set(ours_entries) | set(theirs_entries)
    for path in sorted(paths):
        base = base_entries.get(path)
        ours = ours_entries.get(path)
        theirs = theirs_entries.get(path)

        if ours == theirs:
            if ours:
                result[path] = ours
            continue
        if ours == base:
            if theirs:
                result[path] = theirs
            continue
        if theirs == base:
            if ours:
                result[path] = ours
            continue
        if ours is None or theirs is None:
            # one side deleted, the other edited
            conflicted.append(path)
            result[path] = ours or theirs
            continue

        base_blob = repo.read_object(base[1])[1] if base else b""
        ours_blob = repo.read_object(ours[1])[1]
        theirs_blob = repo.read_object(theirs[1])[1]

        if is_binary(base_blob) or is_binary(ours_blob) or is_binary(theirs_blob):
            conflicted.append(path)
            result[path] = ours
            continue

        merged, conflicts = merge_lines(
            base_blob.decode("utf-8", "surrogateescape").splitlines(keepends=True),
            ours_blob.decode("utf-8", "surrogateescape").splitlines(keepends=True),
            theirs_blob.decode("utf-8", "surrogateescape").splitlines(keepends=True),
            ours_label, theirs_label,
        )
        blob = "".join(merged).encode("utf-8", "surrogateescape")
        sha = repo.write_object("blob", blob)
        mode = ours[0] if ours[0] == theirs[0] else "100644"
        result[path] = (mode, sha)
        if conflicts:
            conflicted.append(path)
    return result, conflicted


# --------------------------------------------------------------------------
# Revision resolution
# --------------------------------------------------------------------------

REV_SUFFIX = re.compile(r"(?:\^|~\d*)$")


def split_rev_suffix(name: str):
    """Split "HEAD~2^" into ("HEAD", 3). Unsuffixed names come back unchanged."""
    steps = 0
    while True:
        match = REV_SUFFIX.search(name)
        if not match or match.start() == 0:
            break
        token = match.group(0)
        steps += 1 if token in ("^", "~") else int(token[1:])
        name = name[: match.start()]
    return (name or "HEAD"), steps


def resolve_rev(repo: Repo, name: str):
    if not name:
        return None

    # Trailing ~N / ^ walk back through first parents: HEAD~2, a1b2c3d4^^
    base, suffix_steps = split_rev_suffix(name)
    sha = _resolve_rev_exact(repo, base)
    for _ in range(suffix_steps):
        parents = commit_parents(repo, sha)
        if not parents:
            raise VibeError("%s has no parent that far back" % short(sha))
        sha = parents[0]
    return sha


def _resolve_rev_exact(repo: Repo, name: str):
    if name in ("HEAD", "@"):
        head = repo.head_commit()
        if not head:
            raise VibeError("no commits yet")
        return head
    candidates = [
        name,
        "refs/heads/" + name,
        "refs/remotes/" + name,
        "refs/remotes/origin/" + name,
    ]
    for candidate in candidates:
        if candidate.startswith("refs/"):
            sha = repo.read_ref(candidate)
            if sha:
                return sha
    if len(name) >= 4 and all(c in "0123456789abcdef" for c in name.lower()):
        matches = [s for s in repo.all_object_shas() if s.startswith(name.lower())]
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            raise VibeError("ambiguous revision %s" % name)
    raise VibeError("unknown revision: %s" % name)


def short(sha):
    return sha[:8] if sha else "-"


def move_head(repo: Repo, sha: str):
    """Point the current branch at sha - or HEAD itself when it is detached.

    Reattaching a detached HEAD to a branch here would silently rewrite that
    branch's tip and orphan every commit it had, so a detached HEAD stays
    detached.
    """
    ref = repo.head_ref()
    if ref:
        repo.write_ref(ref, sha)
        repo.write_head_ref(ref)
    else:
        (repo.dir / "HEAD").write_text(sha + "\n", encoding="utf-8")


# --------------------------------------------------------------------------
# Commands
# --------------------------------------------------------------------------

def cmd_login(args):
    print("Gitea sign-in for %s" % REPO_HOST)
    print("Create a token at https://%s/user/settings/applications" % REPO_HOST)
    username = args.username or input("Username: ").strip()
    token = args.token or getpass.getpass("Token (or password): ").strip()
    if not username or not token:
        raise VibeError("username and token are both required")
    save_json(CRED_FILE, {"username": username, "token": token}, secret=True)
    try:
        remote_refs("git-upload-pack")
    except VibeError:
        os.remove(CRED_FILE)
        raise
    print(green("Logged in as %s" % username) + " (saved to %s)" % CRED_FILE)


def cmd_logout(args):
    if CRED_FILE.exists():
        os.remove(CRED_FILE)
        print("Credentials removed.")
    else:
        print("Not logged in.")


def cmd_clone(args):
    check_url(args.url)
    if not load_credentials():
        raise VibeError("this repository is private - run `vb login` first")
    dest = Path(args.directory or DEFAULT_DIR)
    if dest.exists() and any(dest.iterdir()):
        raise VibeError("destination '%s' already exists and is not empty" % dest)
    dest.mkdir(parents=True, exist_ok=True)

    print("Cloning %s into %s" % (REPO_WEB_URL, dest))
    repo = Repo.create(dest)
    heads = fetch(repo, quiet=args.quiet)
    if not heads:
        print("Remote is empty - starting a fresh %s branch." % DEFAULT_BRANCH)
        return

    branch = DEFAULT_BRANCH if ("refs/heads/" + DEFAULT_BRANCH) in heads else \
        sorted(heads)[0][len("refs/heads/"):]
    sha = heads["refs/heads/" + branch]
    repo.write_ref("refs/heads/" + branch, sha)
    repo.write_head_ref("refs/heads/" + branch)
    checkout_entries(repo, commit_entries(repo, sha))
    print(green("Cloned") + " %s at %s (%d files)" % (branch, short(sha), len(repo.read_index())))


def cmd_pull(args):
    repo = Repo.find()
    require_no_merge(repo, "pull")
    branch = repo.current_branch() or DEFAULT_BRANCH
    local = repo.head_commit()

    print("Fetching %s" % REPO_WEB_URL)
    heads = fetch(repo, quiet=args.quiet)
    remote = heads.get("refs/heads/" + branch)
    if remote is None:
        raise VibeError("remote has no branch named '%s'" % branch)

    if local is None:
        repo.write_ref("refs/heads/" + branch, remote)
        checkout_entries(repo, commit_entries(repo, remote))
        print(green("Checked out") + " %s at %s" % (branch, short(remote)))
        return
    if local == remote:
        print("Already up to date.")
        return
    if is_ancestor(repo, remote, local):
        print("Local %s is ahead of origin - nothing to pull." % branch)
        return

    if is_ancestor(repo, local, remote):
        require_clean(repo, "pull")
        repo.write_ref("refs/heads/" + branch, remote)
        checkout_entries(repo, commit_entries(repo, remote))
        print(green("Fast-forward") + " %s..%s" % (short(local), short(remote)))
        return

    print(yellow("Histories diverged") + " - merging origin/%s into %s" % (branch, branch))
    do_merge(repo, remote, "origin/" + branch, quiet=args.quiet)


def cmd_push(args):
    repo = Repo.find()
    require_no_merge(repo, "push")
    branch = repo.current_branch()
    if not branch:
        raise VibeError("HEAD is detached - check out a branch first")

    added, modified, deleted = worktree_changes(repo)
    dirty = bool(added or modified or deleted)
    if dirty and not args.message:
        raise VibeError(
            "you have uncommitted changes.\n"
            "  Commit them:  vb commit -m \"what changed\"\n"
            "  Or push in one step:  vb push -m \"what changed\""
        )

    # Check the remote before creating a commit, so a rejected push doesn't
    # leave a surprise commit behind.
    refs, _caps = remote_refs("git-receive-pack")
    remote = refs.get("refs/heads/" + branch, ZERO)
    head = repo.head_commit()
    if remote != ZERO and not args.force:
        if not head:
            raise VibeError("origin/%s already exists - run `vb pull` first" % branch)
        if not repo.has_object(remote) or not is_ancestor(repo, remote, head):
            raise VibeError(
                "origin/%s has commits you don't - run `vb pull`, then push again" % branch
            )

    if dirty:
        do_commit(repo, args.message)

    local = repo.head_commit()
    if not local:
        raise VibeError("nothing committed yet")
    if remote == local:
        print("Everything up to date.")
        return

    count = push_refs(repo, branch, remote, local, quiet=args.quiet)
    repo.write_ref("refs/remotes/origin/" + branch, local)
    print(green("Pushed") + " %s -> origin/%s (%s..%s, %d objects)"
          % (branch, branch, short(remote) if remote != ZERO else "new", short(local), count))


def cmd_mirror(args):
    """Bring every branch into line in both directions, in one shot."""
    repo = Repo.find()
    require_no_merge(repo, "mirror")
    current = repo.current_branch()

    if not args.up:
        print("Fetching every branch from %s" % REPO_WEB_URL)
        heads = fetch(repo, quiet=args.quiet)
    else:
        heads, _caps = remote_refs("git-receive-pack")
        heads = {n: s for n, s in heads.items() if n.startswith("refs/heads/")}

    created, advanced, diverged, skipped, gone = [], [], [], [], []

    # --- downstream: remote -> local ------------------------------------
    if not args.up:
        for name, remote_sha in sorted(heads.items()):
            branch = name[len("refs/heads/"):]
            local_sha = repo.read_ref("refs/heads/" + branch)

            if local_sha is None:
                if not args.dry_run:
                    repo.write_ref("refs/heads/" + branch, remote_sha)
                created.append(branch)
                continue
            if local_sha == remote_sha:
                continue
            if not is_ancestor(repo, local_sha, remote_sha):
                if not is_ancestor(repo, remote_sha, local_sha):
                    diverged.append(branch)
                continue

            # Local is strictly behind, so the ref can move without a merge.
            if branch == current:
                try:
                    require_clean(repo, "update %s" % branch)
                except VibeError:
                    skipped.append(branch + " (uncommitted changes)")
                    continue
                if not args.dry_run:
                    repo.write_ref("refs/heads/" + branch, remote_sha)
                    checkout_entries(repo, commit_entries(repo, remote_sha))
            elif not args.dry_run:
                repo.write_ref("refs/heads/" + branch, remote_sha)
            advanced.append(branch)

    # --- upstream: local -> remote --------------------------------------
    updates = []
    if not args.down:
        for name, local_sha in sorted(repo.list_refs("refs/heads").items()):
            branch = name[len("refs/heads/"):]
            remote_sha = heads.get("refs/heads/" + branch, ZERO)
            if remote_sha == local_sha:
                continue
            if remote_sha == ZERO:
                updates.append((branch, ZERO, local_sha))
                continue
            if branch in diverged:
                continue
            if repo.has_object(remote_sha) and is_ancestor(repo, remote_sha, local_sha):
                updates.append((branch, remote_sha, local_sha))

    for name in sorted(repo.list_refs("refs/heads")):
        branch = name[len("refs/heads/"):]
        if ("refs/heads/" + branch) not in heads and not any(u[0] == branch for u in updates):
            gone.append(branch)

    pushed = 0
    if updates and not args.dry_run:
        pushed = push_many(repo, updates, quiet=args.quiet)

    # --- report ----------------------------------------------------------
    prefix = "would " if args.dry_run else ""
    for branch in created:
        print(green("  %screate " % prefix) + "%s (new from origin)" % branch)
    for branch in advanced:
        print(green("  %supdate " % prefix) + "%s (fast-forward from origin)" % branch)
    for branch, old, new in updates:
        label = "new branch" if old == ZERO else "%s..%s" % (short(old), short(new))
        print(green("  %spush   " % prefix) + "%s (%s)" % (branch, label))
    for branch in skipped:
        print(yellow("  skip    ") + branch)
    for branch in gone:
        print(yellow("  local   ") + "%s is not on origin and has no new commits" % branch)
    for branch in diverged:
        print(red("  stuck   ") + "%s has diverged - vb checkout %s && vb pull"
              % (branch, branch))

    total = len(created) + len(advanced) + len(updates)
    if not total and not diverged and not skipped:
        print("Every branch is already in sync.")
    elif args.dry_run:
        print("\nDry run - nothing was changed. Drop --dry-run to apply.")
    else:
        print("\n%d branch%s synced%s"
              % (total, "" if total == 1 else "es",
                 (", %d objects pushed" % pushed) if pushed else ""))
    if diverged:
        sys.exit(1)


def do_commit(repo: Repo, message: str):
    name, email = repo.identity()
    entries = snapshot_worktree(repo, write=True)
    tree = write_tree_from_entries(repo, entries)

    parents = []
    head = repo.head_commit()
    if head:
        parents.append(head)
    merge_head_path = repo.dir / "MERGE_HEAD"
    if merge_head_path.exists():
        second = merge_head_path.read_text(encoding="utf-8").strip()
        if second and second not in parents:
            parents.append(second)

    if head and not merge_head_path.exists():
        head_tree = parse_commit(repo.read_object(head)[1])["tree"]
        if head_tree == tree:
            raise VibeError("nothing to commit - working tree matches HEAD")

    sha = repo.write_object("commit", build_commit(tree, parents, name, email, message))
    detached = repo.head_ref() is None
    move_head(repo, sha)
    repo.write_index(entries)
    if merge_head_path.exists():
        merge_head_path.unlink()
    conflicts_path = repo.dir / "MERGE_CONFLICTS"
    if conflicts_path.exists():
        conflicts_path.unlink()
    print(green("Committed") + " %s  %s" % (short(sha), message.splitlines()[0]))
    if detached:
        warn("HEAD is detached, so this commit is on no branch yet.\n"
             "  Keep it:  vb branch <name> && vb checkout <name>")
    return sha


def cmd_commit(args):
    repo = Repo.find()
    if (repo.dir / "MERGE_HEAD").exists():
        conflicts = []
        for rel in snapshot_worktree(repo, write=False):
            try:
                if b"<<<<<<< " in (repo.root / rel).read_bytes()[:1 << 20]:
                    conflicts.append(rel)
            except OSError:
                continue
        if conflicts and not args.force:
            raise VibeError("conflict markers still present in:\n  " + "\n  ".join(conflicts))
    do_commit(repo, args.message)


def do_merge(repo: Repo, other: str, label: str, quiet=False):
    head = repo.head_commit()
    if not head:
        raise VibeError("nothing to merge into - no commits yet")
    if is_ancestor(repo, other, head):
        print("Already up to date.")
        return
    if is_ancestor(repo, head, other):
        require_clean(repo, "merge")
        move_head(repo, other)
        checkout_entries(repo, commit_entries(repo, other))
        print(green("Fast-forward") + " to %s" % short(other))
        return

    require_clean(repo, "merge")
    base = merge_base(repo, head, other)
    if not base:
        raise VibeError("no common ancestor between %s and %s" % (short(head), short(other)))

    branch = repo.current_branch() or "HEAD"
    entries, conflicts = three_way_merge(
        repo,
        commit_entries(repo, base),
        commit_entries(repo, head),
        commit_entries(repo, other),
        branch, label,
    )
    checkout_entries(repo, entries)

    if conflicts:
        (repo.dir / "MERGE_HEAD").write_text(other + "\n", encoding="utf-8")
        (repo.dir / "MERGE_CONFLICTS").write_text("\n".join(conflicts) + "\n", encoding="utf-8")
        print(red("Conflicts in %d file(s):" % len(conflicts)))
        for path in conflicts:
            print("  " + path)
        print("\nFix them, then run:  vb commit -m \"merge %s\"" % label)
        sys.exit(1)

    name, email = repo.identity()
    tree = write_tree_from_entries(repo, entries)
    message = "Merge %s into %s" % (label, branch)
    sha = repo.write_object("commit", build_commit(tree, [head, other], name, email, message))
    move_head(repo, sha)
    print(green("Merged") + " %s into %s -> %s" % (label, branch, short(sha)))



def ensure_synced_with_gitea(repo: Repo, branch: str, quiet=False):
    """Fetch Gitea and require local branch to equal origin before imports/recovery."""
    heads = fetch(repo, quiet=quiet)
    remote = heads.get("refs/heads/" + branch)
    local = repo.head_commit()
    if remote is None:
        return ZERO
    if local == remote:
        return remote
    if local and is_ancestor(repo, local, remote):
        require_clean(repo, "sync with Gitea")
        repo.write_ref("refs/heads/" + branch, remote)
        checkout_entries(repo, commit_entries(repo, remote))
        return remote
    if local and is_ancestor(repo, remote, local):
        raise VibeError(
            "your local %s has commits that are not on Gitea yet. Run `vb push` first." % branch
        )
    raise VibeError("local %s and Gitea have diverged. Run `vb pull` first." % branch)


def github_pull_plan(repo: Repo, branch, github_sha, gitea_sha, local_sha):
    """Decide what one branch needs. Returns (action, base_sha, reason).

    action is one of: create, forward, import, same, older, skip.
    """
    if gitea_sha is None and local_sha:
        return "skip", None, "exists only in your copy - vb push first"

    if gitea_sha and local_sha and local_sha != gitea_sha:
        if is_ancestor(repo, gitea_sha, local_sha):
            return "skip", gitea_sha, "you have commits Gitea does not - vb push first"
        if not is_ancestor(repo, local_sha, gitea_sha):
            return "skip", gitea_sha, "your copy and Gitea diverged - vb pull first"

    if gitea_sha is None:
        return "create", None, ""
    if github_sha == gitea_sha:
        return "same", gitea_sha, ""
    if is_ancestor(repo, github_sha, gitea_sha):
        return "older", gitea_sha, "GitHub is behind Gitea"
    if is_ancestor(repo, gitea_sha, github_sha):
        return "forward", gitea_sha, ""
    return "import", gitea_sha, ""


def cmd_github_pull(args):
    """One-shot import of every GitHub branch into the fixed Gitea repository."""
    repo = Repo.find()
    require_no_merge(repo, "import from GitHub")
    require_clean(repo, "import from GitHub")
    current = repo.current_branch()

    cfg_path = repo.dir / "config.json"
    cfg = load_json(cfg_path, {})
    if args.url:
        github_url = normalize_github_url(args.url)
        cfg["github.url"] = github_url[:-4] if github_url.endswith(".git") else github_url
        if not args.dry_run:
            save_json(cfg_path, cfg)
    else:
        github_url = normalize_github_url(cfg.get("github.url", ""))

    print("Checking Gitea before import")
    gitea_heads = fetch(repo, quiet=args.quiet)

    print("Reading GitHub %s" % github_url[:-4])
    github_heads = fetch_github(repo, github_url, quiet=args.quiet)
    if not github_heads:
        raise VibeError("GitHub has no branches to import")

    if args.branch:
        if ("refs/heads/" + args.branch) not in github_heads:
            available = sorted(n[len("refs/heads/"):] for n in github_heads)
            raise VibeError("GitHub has no branch '%s'. It has: %s"
                            % (args.branch, ", ".join(available)))
        wanted = [args.branch]
    else:
        wanted = sorted(n[len("refs/heads/"):] for n in github_heads)

    updates = []            # (branch, gitea_old, new_sha) - one packfile for all
    done = []               # (action, branch, new_sha) for the report
    skipped = []
    checkout_to = None

    for branch in wanted:
        github_sha = github_heads["refs/heads/" + branch]
        gitea_sha = gitea_heads.get("refs/heads/" + branch)
        local_sha = repo.read_ref("refs/heads/" + branch)

        action, base, reason = github_pull_plan(
            repo, branch, github_sha, gitea_sha, local_sha)
        if action == "skip":
            skipped.append((branch, reason))
            continue

        if action in ("same", "older"):
            new_sha = base
        elif action in ("create", "forward"):
            new_sha = github_sha
        else:
            if args.dry_run:
                done.append((action, branch, None))
                continue
            # Both histories stay reachable, but the files come out as GitHub's.
            obj_type, data = repo.read_object(github_sha)
            if obj_type != "commit":
                raise VibeError("GitHub %s does not point at a commit" % branch)
            name, email = repo.identity()
            message = args.message or "Import latest GitHub %s into Gitea" % branch
            new_sha = repo.write_object("commit", build_commit(
                parse_commit(data)["tree"], [base, github_sha], name, email, message))

        done.append((action, branch, new_sha))
        if args.dry_run:
            continue
        if new_sha != base:
            updates.append((branch, base or ZERO, new_sha))
        if local_sha != new_sha:
            repo.write_ref("refs/heads/" + branch, new_sha)
            if branch == current:
                checkout_to = new_sha

    labels = {
        "create": ("create ", "new branch from GitHub"),
        "forward": ("update ", "fast-forward to GitHub"),
        "import": ("import ", "both histories kept, files from GitHub"),
        "same": ("same   ", "already matches GitHub"),
        "older": ("same   ", "Gitea is ahead of GitHub - left alone"),
    }
    prefix = "would " if args.dry_run else ""
    for action, branch, _sha in done:
        label, note = labels[action]
        line = "  %s%s%s (%s)" % (prefix, label, branch, note)
        print(green(line) if action in ("create", "forward", "import") else line)
    for branch, reason in skipped:
        print(yellow("  skip    ") + "%s (%s)" % (branch, reason))
    if not args.branch:
        for name in sorted(gitea_heads):
            branch = name[len("refs/heads/"):]
            if ("refs/heads/" + branch) not in github_heads:
                print("  gitea   %s is not on GitHub - left alone" % branch)

    if args.dry_run:
        print("\nDry run - nothing was changed. Drop --dry-run to apply.")
        if skipped:
            sys.exit(1)
        return

    if checkout_to:
        checkout_entries(repo, commit_entries(repo, checkout_to))

    if not updates:
        print("\nGitea already matches GitHub.")
    else:
        count = push_many(repo, updates, quiet=args.quiet)
        print("\n" + green("Imported") + " %d branch%s into Gitea (%d object%s)"
              % (len(updates), "" if len(updates) == 1 else "es",
                 count, "" if count == 1 else "s"))
    if skipped:
        sys.exit(1)


def cmd_versions(args):
    repo = Repo.find()
    sha = repo.head_commit()
    if not sha:
        print("No versions yet.")
        return
    print("Past versions (use: vb recover <id>)")
    shown = 0
    queue = [sha]
    seen = set()
    while queue and shown < args.number:
        current = queue.pop(0)
        if current in seen or not repo.has_object(current):
            continue
        seen.add(current)
        obj_type, data = repo.read_object(current)
        if obj_type != "commit":
            continue
        info = parse_commit(data)
        when = time.strftime("%Y-%m-%d %H:%M", time.localtime(commit_time(repo, current)))
        subject = info["message"].strip().splitlines()[0] if info["message"].strip() else "(no message)"
        print("%3d  %s  %s  %s" % (shown + 1, yellow(short(current)), when, subject))
        shown += 1
        queue.extend(info["parent"])


def cmd_recover(args):
    repo = Repo.find()
    require_no_merge(repo, "recover a version")
    require_clean(repo, "recover a version")
    branch = repo.current_branch()
    if not branch:
        raise VibeError("HEAD is detached - check out a branch first")

    if args.no_push:
        # Local-only recovery: never touch the network.
        remote_old = repo.read_ref("refs/remotes/origin/" + branch) or ZERO
    else:
        print("Checking Gitea before recovery")
        remote_old = ensure_synced_with_gitea(repo, branch, quiet=args.quiet)
    head = repo.head_commit()
    target = resolve_rev(repo, args.rev)
    if target == head:
        print("That version is already current.")
        return
    obj_type, target_data = repo.read_object(target)
    if obj_type != "commit":
        raise VibeError("%s is not a commit/version" % args.rev)

    info = parse_commit(target_data)
    tree = info["tree"]
    subject = info["message"].strip().splitlines()[0] if info["message"].strip() else short(target)
    message = args.message or "Recover version %s: %s" % (short(target), subject)
    name, email = repo.identity()
    new_sha = repo.write_object("commit", build_commit(tree, [head], name, email, message))
    repo.write_ref("refs/heads/" + branch, new_sha)
    checkout_entries(repo, commit_entries(repo, target))

    if args.no_push:
        print(green("Recovered locally") + " %s as new commit %s" % (short(target), short(new_sha)))
        print("Run `vb push` when you want to save this recovery to Gitea.")
        return

    count = push_refs(repo, branch, remote_old, new_sha, quiet=args.quiet)
    repo.write_ref("refs/remotes/origin/" + branch, new_sha)
    print(green("Recovered") + " %s as %s and pushed to Gitea (%d objects)"
          % (short(target), short(new_sha), count))


def cmd_merge(args):
    repo = Repo.find()
    if args.abort:
        merge_head = repo.dir / "MERGE_HEAD"
        if not merge_head.exists():
            raise VibeError("no merge in progress")
        merge_head.unlink()
        conflicts_path = repo.dir / "MERGE_CONFLICTS"
        if conflicts_path.exists():
            conflicts_path.unlink()
        checkout_entries(repo, commit_entries(repo, repo.head_commit()))
        print("Merge aborted.")
        return
    require_no_merge(repo, "start another merge")
    target = resolve_rev(repo, args.branch)
    do_merge(repo, target, args.branch, quiet=args.quiet)


def cmd_status(args):
    repo = Repo.find()
    branch = repo.current_branch() or "(detached)"
    head = repo.head_commit()
    remote = repo.read_ref("refs/remotes/origin/" + branch) if branch else None
    print("On branch %s%s" % (bold(branch), "" if not head else "  at " + short(head)))
    if remote and head:
        if remote == head:
            print("Up to date with origin/%s" % branch)
        elif is_ancestor(repo, remote, head):
            ahead = len(ancestors(repo, head) - ancestors(repo, remote))
            print("Ahead of origin/%s by %d commit(s)" % (branch, ahead))
        elif is_ancestor(repo, head, remote):
            print("Behind origin/%s - run `vb pull`" % branch)
        else:
            print("Diverged from origin/%s - run `vb pull`" % branch)
    conflicts_path = repo.dir / "MERGE_CONFLICTS"
    if (repo.dir / "MERGE_HEAD").exists():
        print(yellow("Merge in progress") + " - resolve conflicts, then `vb commit -m ...`")
        if conflicts_path.exists():
            for rel in conflicts_path.read_text(encoding="utf-8").splitlines():
                print(red("  unmerged: ") + rel)

    added, modified, deleted = worktree_changes(repo)
    if not (added or modified or deleted):
        print("\nWorking tree clean.")
        return
    print("")
    for path in added:
        print(green("  new:      ") + path)
    for path in modified:
        print(yellow("  modified: ") + path)
    for path in deleted:
        print(red("  deleted:  ") + path)


def cmd_log(args):
    repo = Repo.find()
    sha = resolve_rev(repo, args.rev) if args.rev else repo.head_commit()
    if not sha:
        print("No commits yet.")
        return
    shown = 0
    queue = [sha]
    seen = set()
    while queue and shown < args.number:
        current = queue.pop(0)
        if current in seen or not repo.has_object(current):
            continue
        seen.add(current)
        info = parse_commit(repo.read_object(current)[1])
        author = info.get("author", "")
        who = author.split(" <")[0]
        when = time.strftime("%Y-%m-%d %H:%M", time.localtime(commit_time(repo, current)))
        subject = info["message"].strip().splitlines()[0] if info["message"].strip() else ""
        print("%s  %s  %s  %s" % (yellow(short(current)), when, bold(who), subject))
        shown += 1
        queue.extend(info["parent"])


def cmd_branch(args):
    repo = Repo.find()
    if args.name:
        head = repo.head_commit()
        if not head:
            raise VibeError("no commits yet")
        repo.write_ref("refs/heads/" + args.name, head)
        print("Created branch %s at %s" % (args.name, short(head)))
        return
    current = repo.current_branch()
    for name, sha in sorted(repo.list_refs("refs/heads").items()):
        short_name = name[len("refs/heads/"):]
        marker = "* " if short_name == current else "  "
        print("%s%s  %s" % (marker, short_name.ljust(20), short(sha)))
    remotes = repo.list_refs("refs/remotes")
    if remotes:
        print("\nremote:")
        for name, sha in sorted(remotes.items()):
            print("  %s  %s" % (name[len("refs/remotes/"):].ljust(20), short(sha)))


def cmd_checkout(args):
    repo = Repo.find()
    require_no_merge(repo, "switch branches")
    name = args.branch
    sha = None
    if args.create:
        head = repo.head_commit()
        if not head:
            raise VibeError("no commits yet")
        # Check the tree before creating the ref, or a refused checkout leaves
        # a stray branch behind.
        require_clean(repo, "switch branches")
        repo.write_ref("refs/heads/" + name, head)
        sha = head
    else:
        sha = resolve_rev(repo, name)
        if not repo.read_ref("refs/heads/" + name):
            remote = repo.read_ref("refs/remotes/origin/" + name)
            if remote:
                repo.write_ref("refs/heads/" + name, remote)
                sha = remote
    require_clean(repo, "switch branches")
    if repo.read_ref("refs/heads/" + name):
        repo.write_head_ref("refs/heads/" + name)
    else:
        (repo.dir / "HEAD").write_text(sha + "\n", encoding="utf-8")
    checkout_entries(repo, commit_entries(repo, sha))
    print("Switched to %s at %s" % (name, short(sha)))
    if repo.head_ref() is None:
        warn("HEAD is detached - you are on no branch.\n"
             "  Back to your work:  vb checkout %s\n"
             "  Or keep this spot:  vb branch <name> && vb checkout <name>"
             % (repo.current_branch() or DEFAULT_BRANCH))


def cmd_config(args):
    if args.key is None:
        repo = Repo.find()
        for key, value in sorted(repo.config().items()):
            print("%s = %s" % (key, value))
        return
    target = GLOBAL_CONFIG if args.glob else None
    if target is None:
        repo = Repo.find()
        target = repo.dir / "config.json"
    data = load_json(target, {})
    if args.value is None:
        print(data.get(args.key, ""))
        return
    data[args.key] = args.value
    save_json(target, data)
    print("%s = %s" % (args.key, args.value))


def cmd_remote(args):
    print("origin  %s" % REMOTE_URL)
    print("web     %s" % REPO_WEB_URL)
    creds = load_credentials()
    print("auth    %s" % (creds["username"] if creds else "(not logged in - run `vb login`)"))


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

EPILOG = """\
examples:
  vb login                       save your Gitea token
  vb clone                       clone Chronos into ./Chronos
  vb status                      see what changed
  vb push -m "fix the thing"     commit everything and push
  vb pull                        fetch + fast-forward or merge
  vb merge origin/main           three-way merge another branch
  vb mirror                      sync every branch, both directions
  vb github-pull OWNER/REPO      import every GitHub branch into Gitea once
  vb versions                    list recoverable past versions
  vb recover <id>                restore an old version as a new Gitea commit
  vb mirror --dry-run            ...but show what it would do first

vibe only ever talks to %s
""" % REPO_WEB_URL


def build_parser():
    parser = argparse.ArgumentParser(
        prog="vibe",
        description="A tiny Git client locked to the Chronos repository.",
        epilog=EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--version", action="version", version="vibe " + VERSION)
    parser.add_argument("-q", "--quiet", action="store_true", help="less output")
    subs = parser.add_subparsers(dest="command")

    p = subs.add_parser("login", help="store your Gitea username + access token")
    p.add_argument("username", nargs="?")
    p.add_argument("token", nargs="?")
    p.set_defaults(func=cmd_login)

    p = subs.add_parser("logout", help="forget stored credentials")
    p.set_defaults(func=cmd_logout)

    p = subs.add_parser("clone", help="copy the repo into a new directory")
    p.add_argument("url", nargs="?", help="ignored unless it points at Chronos")
    p.add_argument("directory", nargs="?")
    p.set_defaults(func=cmd_clone)

    p = subs.add_parser("pull", help="fetch and integrate remote changes")
    p.set_defaults(func=cmd_pull)

    p = subs.add_parser("push", help="send local commits to the remote")
    p.add_argument("-m", "--message", help="commit pending changes with this message first")
    p.add_argument("-f", "--force", action="store_true", help="skip the fast-forward check")
    p.set_defaults(func=cmd_push)

    p = subs.add_parser("mirror", help="sync every branch, both directions")
    p.add_argument("--down", action="store_true", help="only bring changes down from origin")
    p.add_argument("--up", action="store_true", help="only send local branches up")
    p.add_argument("-n", "--dry-run", action="store_true", help="show what would happen")
    p.set_defaults(func=cmd_mirror)

    p = subs.add_parser("github-pull", help="one-shot import of every GitHub branch into Gitea")
    p.add_argument("url", nargs="?", help="https://github.com/OWNER/REPO (remembered after first use)")
    p.add_argument("--branch", help="import only this branch (default: every GitHub branch)")
    p.add_argument("-m", "--message", help="message for any preserving import commit")
    p.add_argument("-n", "--dry-run", action="store_true", help="show what would happen")
    p.set_defaults(func=cmd_github_pull)

    p = subs.add_parser("versions", help="list recoverable past versions")
    p.add_argument("-n", "--number", type=int, default=25)
    p.set_defaults(func=cmd_versions)

    p = subs.add_parser("recover", help="restore an old version without deleting history")
    p.add_argument("rev", help="version/commit id from `vb versions`")
    p.add_argument("-m", "--message", help="recovery commit message")
    p.add_argument("--no-push", action="store_true", help="recover locally but do not push to Gitea yet")
    p.set_defaults(func=cmd_recover)

    p = subs.add_parser("merge", help="three-way merge another branch into this one")
    p.add_argument("branch", nargs="?", default="origin/" + DEFAULT_BRANCH)
    p.add_argument("--abort", action="store_true", help="throw away an in-progress merge")
    p.set_defaults(func=cmd_merge)

    p = subs.add_parser("commit", help="snapshot the working tree")
    p.add_argument("-m", "--message", required=True)
    p.add_argument("-f", "--force", action="store_true", help="commit even with conflict markers")
    p.set_defaults(func=cmd_commit)

    p = subs.add_parser("status", help="show branch state and local changes")
    p.set_defaults(func=cmd_status)

    p = subs.add_parser("log", help="show commit history")
    p.add_argument("rev", nargs="?")
    p.add_argument("-n", "--number", type=int, default=15)
    p.set_defaults(func=cmd_log)

    p = subs.add_parser("branch", help="list or create branches")
    p.add_argument("name", nargs="?")
    p.set_defaults(func=cmd_branch)

    p = subs.add_parser("checkout", help="switch branches")
    p.add_argument("branch")
    p.add_argument("-b", "--create", action="store_true")
    p.set_defaults(func=cmd_checkout)

    p = subs.add_parser("config", help="get or set config values")
    p.add_argument("key", nargs="?")
    p.add_argument("value", nargs="?")
    p.add_argument("--global", dest="glob", action="store_true")
    p.set_defaults(func=cmd_config)

    p = subs.add_parser("remote", help="show the (fixed) remote")
    p.set_defaults(func=cmd_remote)

    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        parser.print_help()
        return 0
    try:
        args.func(args)
    except VibeError as exc:
        die(exc)
    except KeyboardInterrupt:
        die("interrupted", 130)
    except BrokenPipeError:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
