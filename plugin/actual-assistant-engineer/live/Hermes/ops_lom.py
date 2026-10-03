"""Generic Live Object Model access: get, set, and call anything Live exposes to scripts.

Paths start at `song` or `app` and use attribute names with optional index or
exact-name selectors: song.tracks["BASS"].devices[0].parameters["Threshold"].
Private names (leading underscore) are refused. Curated ops stay the fast path;
these cover everything else.
"""

import re

from .ops import OpError, op

SEGMENT = re.compile(r'([A-Za-z]\w*)((?:\[(?:-?\d+|"[^"]*")\])*)')
SELECTOR = re.compile(r'\[(-?\d+|"[^"]*")\]')
MAX_LIST = 256


def parse_path(path):
    """Split a path into (attribute, [selectors]) segments; selectors are ints or names."""
    segments = []
    for part in _split_dots(path):
        m = SEGMENT.fullmatch(part)
        if not m:
            raise OpError("bad path segment %r" % part)
        sels = [int(s) if not s.startswith('"') else s[1:-1] for s in SELECTOR.findall(m.group(2))]
        segments.append((m.group(1), sels))
    if not segments:
        raise OpError("empty path")
    return segments


def _split_dots(path):
    """Split on dots outside of quoted selectors."""
    parts, buf, quoted = [], "", False
    for ch in path:
        if ch == '"':
            quoted = not quoted
        if ch == "." and not quoted:
            parts.append(buf)
            buf = ""
        else:
            buf += ch
    parts.append(buf)
    return parts


def select(seq, sel):
    items = list(seq)
    if isinstance(sel, int):
        if not -len(items) <= sel < len(items):
            raise OpError("index %d out of range (have %d)" % (sel, len(items)))
        return items[sel]
    matches = [x for x in items if getattr(x, "name", None) == sel]
    if len(matches) != 1:
        raise OpError("name %r matched %d items" % (sel, len(matches)))
    return matches[0]


def resolve(ctx, path):
    segments = parse_path(path)
    roots = {"song": lambda: ctx.song, "app": lambda: ctx.app}
    head, sels = segments[0]
    if head not in roots:
        raise OpError("path must start with song or app")
    obj = roots[head]()
    for s in sels:
        obj = select(obj, s)
    for name, sels in segments[1:]:
        if name.startswith("_"):
            raise OpError("private attribute %r" % name)
        if not hasattr(obj, name):
            raise OpError("%s has no attribute %r" % (type(obj).__name__, name))
        obj = getattr(obj, name)
        for s in sels:
            obj = select(obj, s)
    return obj


def to_json(value, depth=0):
    """Plain values pass through; Live objects become a small descriptor."""
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, (list, tuple)) or _is_vector(value):
        items = list(value)
        out = [to_json(v, depth + 1) for v in items[:MAX_LIST]]
        if len(items) > MAX_LIST:
            out.append({"truncated": len(items) - MAX_LIST})
        return out
    desc = {"type": type(value).__name__}
    for attr in ("name", "display_name", "class_name", "value"):
        if hasattr(value, attr):
            try:
                v = getattr(value, attr)
                if isinstance(v, (bool, int, float, str)):
                    desc[attr] = v
            except Exception:
                pass
    return desc


def _is_vector(value):
    return type(value).__name__.endswith("Vector") or hasattr(value, "__len__") and hasattr(value, "__getitem__") and not isinstance(value, (str, bytes, dict))


def from_json(ctx, value):
    """Arguments may reference Live objects as {"path": "..."}."""
    if isinstance(value, dict) and set(value) == {"path"}:
        return resolve(ctx, value["path"])
    if isinstance(value, list):
        return [from_json(ctx, v) for v in value]
    return value


@op("get")
def _get(ctx, path, props=None):
    """Read an object (descriptor) or specific properties of it."""
    obj = resolve(ctx, path)
    if not props:
        return {"value": to_json(obj)}
    out = {}
    for p in props:
        if p.startswith("_"):
            raise OpError("private attribute %r" % p)
        if not hasattr(obj, p):
            raise OpError("%s has no attribute %r" % (type(obj).__name__, p))
        out[p] = to_json(getattr(obj, p))
    return {"values": out}


@op("set")
def _set(ctx, path, prop, value):
    obj = resolve(ctx, path)
    if prop.startswith("_"):
        raise OpError("private attribute %r" % prop)
    if not hasattr(obj, prop):
        raise OpError("%s has no attribute %r" % (type(obj).__name__, prop))
    setattr(obj, prop, from_json(ctx, value))
    return {"value": to_json(getattr(obj, prop))}


@op("call")
def _call(ctx, path, method, args=(), kwargs=None):
    obj = resolve(ctx, path)
    if method.startswith("_") or "listener" in method:
        raise OpError("method %r is not callable through the bridge" % method)
    fn = getattr(obj, method, None)
    if not callable(fn):
        raise OpError("%s has no method %r" % (type(obj).__name__, method))
    result = fn(*[from_json(ctx, a) for a in args], **{k: from_json(ctx, v) for k, v in (kwargs or {}).items()})
    return {"result": to_json(result)}


@op("describe")
def _describe(ctx, path):
    """List an object's public properties and methods (for discovery)."""
    obj = resolve(ctx, path)
    props, methods = [], []
    for name in dir(obj):
        if name.startswith("_") or "listener" in name:
            continue
        try:
            attr = getattr(obj, name)
        except Exception:
            continue
        (methods if callable(attr) else props).append(name)
    return {"type": type(obj).__name__, "props": props, "methods": methods}
