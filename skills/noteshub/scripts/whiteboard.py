#!/usr/bin/env python3
"""Read, create and edit NotesHub whiteboards (Excalidraw) without a browser.

NotesHub stores whiteboards as `*.excalidraw.svg` (an SVG picture with the full
Excalidraw scene embedded in it) or `*.excalidraw` (plain Excalidraw JSON).
NotesHub reads ONLY the embedded scene; the SVG drawing is just a preview for
GitHub, file browsers, etc. Python 3.8+, standard library only.

Commands (run `whiteboard.py <command> -h` for details):

  info    FILE                         human-readable outline: shapes, labels, arrows, free space
  decode  FILE [-o scene.json]         dump the full Excalidraw scene JSON
  new     FILE --from SPEC.json        create a whiteboard from simple element specs
  add     FILE --from SPEC.json        append elements (and apply {"type":"delete"} entries)
  set-label FILE --id ID --text TEXT   change a text element or a shape/arrow label (re-wraps, re-centers)
  apply   FILE --scene scene.json      replace the scene with an edited `decode` output
  preview FILE                         rebuild the simplified SVG preview from the scene
  check   FILE                         validate the file the way NotesHub will read it
  render-exact FILE                    redraw the preview with the real Excalidraw renderer
                                       (needs Node.js + Playwright; see references/whiteboards.md)

Element specs follow the compact format used by Excalidraw's own MCP server:
shapes with `label`, arrows with `from`/`to` (or explicit points), text, lines.
See references/whiteboards.md for the full spec and examples.
"""

import argparse
import base64
import json
import math
import os
import random
import re
import string
import subprocess
import sys
import time
import zlib
from xml.sax.saxutils import escape as _xml_escape

PAYLOAD_RE = re.compile(r"<!-- payload-start -->\s*(.+?)\s*<!-- payload-end -->")
MIME_EXCALIDRAW = "application/vnd.excalidraw+json"
SOURCE = "https://github.com/NotesHubApp/noteshub-skills"
PREVIEW_MARKER = 'data-noteshub-preview="simplified"'
EXPORT_PADDING = 10

# Excalidraw font ids -> CSS family stacks for the simplified preview.
FONT_FAMILIES = {
    1: "Virgil, Excalifont, 'Comic Sans MS', 'Segoe Print', cursive",
    2: "Helvetica, Arial, sans-serif",
    3: "Cascadia, 'Cascadia Code', Menlo, Consolas, monospace",
    5: "Excalifont, Virgil, 'Comic Sans MS', 'Segoe Print', cursive",
    6: "Nunito, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif",
    7: "'Lilita One', Impact, 'Arial Black', sans-serif",
    8: "'Comic Shanns', 'Comic Sans MS', monospace",
    9: "'Liberation Sans', Arial, Helvetica, sans-serif",
}
LINE_HEIGHTS = {1: 1.25, 2: 1.15, 3: 1.2, 5: 1.25, 6: 1.35, 7: 1.15, 8: 1.25, 9: 1.15}
DEFAULT_FONT = 5          # Excalifont, NotesHub's current default
BOUND_TEXT_PADDING = 5
FONT_NAMES = {"excalifont": 5, "virgil": 1, "hand": 5, "handwritten": 5, "helvetica": 2,
              "normal": 6, "nunito": 6, "sans": 6, "code": 3, "cascadia": 3, "mono": 3,
              "lilita": 7, "comic": 8, "liberation": 9}

SHAPES = ("rectangle", "ellipse", "diamond")
LINEAR = ("arrow", "line")


class WhiteboardError(Exception):
    pass


# --------------------------------------------------------------------------------------
# File format: read / write
# --------------------------------------------------------------------------------------

def _decode_payload(b64):
    raw = base64.b64decode(b64)
    for enc in ("latin-1", "utf-8"):
        try:
            outer = json.loads(raw.decode(enc))
            break
        except (UnicodeDecodeError, ValueError):
            outer = None
    if not isinstance(outer, dict) or "encoded" not in outer:
        raise WhiteboardError("Embedded scene payload is not in a format this script understands")
    encoded = outer["encoded"]
    if outer.get("encoding") == "bstring":
        data = encoded.encode("latin-1")
        text = zlib.decompress(data).decode("utf-8") if outer.get("compressed") else data.decode("utf-8")
    else:
        text = encoded
    return json.loads(text)


def _encode_payload(scene):
    text = json.dumps(scene, ensure_ascii=False, separators=(",", ":"))
    encoded = zlib.compress(text.encode("utf-8"), 9).decode("latin-1")
    outer = json.dumps({"version": "1", "encoding": "bstring", "compressed": True, "encoded": encoded},
                       ensure_ascii=False, separators=(",", ":"))
    return base64.b64encode(outer.encode("latin-1")).decode("ascii")


def file_format(path):
    low = path.lower()
    if low.endswith(".excalidraw.svg"):
        return "svg"
    if low.endswith(".excalidraw"):
        return "json"
    raise WhiteboardError("Whiteboard files must end with .excalidraw.svg or .excalidraw: %s" % path)


def read_scene(path):
    with open(path, "r", encoding="utf-8") as f:
        raw = f.read()
    if raw.lstrip().startswith("{"):
        scene = json.loads(raw)
    else:
        if raw.count("<svg") > 1:
            raise WhiteboardError(
                "%s contains %d <svg> documents - leftovers of a sync conflict. Open it in NotesHub "
                "to choose a version (or keep one <svg>...</svg> block) before editing." % (path, raw.count("<svg")))
        if "<<<<<<<" in raw or ">>>>>>>" in raw:
            raise WhiteboardError("%s contains git conflict markers; resolve them first" % path)
        if "payload-type:%s" % MIME_EXCALIDRAW not in raw:
            raise WhiteboardError("%s is an SVG without an embedded Excalidraw scene" % path)
        m = PAYLOAD_RE.search(raw)
        if not m:
            raise WhiteboardError("Could not find the embedded scene in %s" % path)
        scene = _decode_payload(m.group(1))
    scene.setdefault("type", "excalidraw")
    scene.setdefault("version", 2)
    scene.setdefault("elements", [])
    scene.setdefault("appState", {"gridSize": None, "viewBackgroundColor": "#ffffff"})
    scene.setdefault("files", {})
    return scene, raw


def write_scene(path, scene):
    fmt = file_format(path)
    if fmt == "json":
        content = json.dumps(scene, ensure_ascii=False, indent=2)
    else:
        content = render_svg(scene)
    tmp = path + ".tmp-noteshub"
    with open(tmp, "w", encoding="utf-8", newline="") as f:
        f.write(content)
    # Self-check before replacing the real file.
    check, _ = read_scene(tmp)
    if len(check["elements"]) != len(scene["elements"]):
        os.remove(tmp)
        raise WhiteboardError("Internal error: re-reading the written file gave a different scene")
    os.replace(tmp, path)


# --------------------------------------------------------------------------------------
# Element construction (spec -> full Excalidraw elements)
# --------------------------------------------------------------------------------------

_ID_CHARS = string.ascii_letters + string.digits + "_-"


def new_id():
    return "".join(random.choice(_ID_CHARS) for _ in range(21))


def _rand_int():
    return random.randint(1, 2 ** 31 - 1)


def _now_ms():
    return int(time.time() * 1000)


def _base(etype, spec):
    return {
        "id": spec.get("id") or new_id(),
        "type": etype,
        "x": float(spec.get("x", 0)),
        "y": float(spec.get("y", 0)),
        "width": float(spec.get("width", 0)),
        "height": float(spec.get("height", 0)),
        "angle": float(spec.get("angle", 0)),
        "strokeColor": spec.get("strokeColor", "#1e1e1e"),
        "backgroundColor": spec.get("backgroundColor", "transparent"),
        "fillStyle": spec.get("fillStyle", "solid"),
        "strokeWidth": spec.get("strokeWidth", 2),
        "strokeStyle": spec.get("strokeStyle", "solid"),
        "roughness": spec.get("roughness", 1),
        "opacity": spec.get("opacity", 100),
        "groupIds": list(spec.get("groupIds", [])),
        "frameId": spec.get("frameId"),
        "index": None,
        "roundness": None,
        "seed": _rand_int(),
        "version": 1,
        "versionNonce": _rand_int(),
        "isDeleted": False,
        "boundElements": [],
        "updated": _now_ms(),
        "link": spec.get("link"),
        "locked": bool(spec.get("locked", False)),
    }


def _font_id(value):
    if value is None:
        return DEFAULT_FONT
    if isinstance(value, int):
        return value
    return FONT_NAMES.get(str(value).lower(), DEFAULT_FONT)


def _char_width(ch):
    if ch in "il.,:;'|!`ijlI1()[]{} ":
        return 0.32
    if ch in "mwMW@%":
        return 0.85
    if ch.isupper():
        return 0.66
    if ord(ch) >= 0x2E80:          # CJK and other wide scripts
        return 1.0
    return 0.55


def measure_text(text, font_size, font_id=DEFAULT_FONT):
    """Approximate Excalidraw's measurement (good enough; Excalidraw re-measures on open)."""
    lines = text.split("\n")
    width = max(sum(_char_width(c) for c in line) for line in lines) * font_size
    height = len(lines) * font_size * LINE_HEIGHTS.get(font_id, 1.25)
    return width, height


def wrap_text(text, max_width, font_size):
    """Word-wrap text to max_width (approximate), keeping explicit newlines."""
    out = []
    for para in text.split("\n"):
        words, line = para.split(" "), ""
        for w in words:
            cand = w if not line else line + " " + w
            if line and measure_text(cand, font_size)[0] > max_width:
                out.append(line)
                line = w
            else:
                line = cand
        out.append(line)
    return "\n".join(out)


def make_text(spec, container=None):
    font_id = _font_id(spec.get("fontFamily"))
    font_size = float(spec.get("fontSize", 20))
    original = str(spec.get("text", ""))
    el = _base("text", spec)
    el.update({"strokeColor": spec.get("strokeColor", "#1e1e1e"), "backgroundColor": "transparent",
               "fontSize": font_size, "fontFamily": font_id, "text": original,
               "textAlign": spec.get("textAlign", "left"), "verticalAlign": spec.get("verticalAlign", "top"),
               "containerId": None, "originalText": original, "autoResize": True,
               "lineHeight": LINE_HEIGHTS.get(font_id, 1.25)})
    w, h = measure_text(original, font_size, font_id)
    el["width"], el["height"] = w, h
    if container is not None:
        el["containerId"] = container["id"]
        el["textAlign"] = spec.get("textAlign", "center")
        el["verticalAlign"] = spec.get("verticalAlign", "middle")
        el["groupIds"] = list(container.get("groupIds", []))
        el["frameId"] = container.get("frameId")
    return el


def _fit_label(container, label_spec):
    """Create a bound label for a shape, wrapping and growing the shape if needed."""
    if isinstance(label_spec, str):
        label_spec = {"text": label_spec}
    spec = dict(label_spec)
    spec.setdefault("strokeColor", spec.get("strokeColor") or "#1e1e1e")
    t = make_text(spec, container)
    font_size, font_id = t["fontSize"], t["fontFamily"]
    # Inner box available for text (ellipses/diamonds have less room).
    shrink = {"rectangle": 1.0, "ellipse": 0.7071, "diamond": 0.5}[container["type"]]
    max_w = max(container["width"] * shrink - 2 * BOUND_TEXT_PADDING, font_size)
    wrapped = wrap_text(t["originalText"], max_w, font_size)
    w, h = measure_text(wrapped, font_size, font_id)
    if w > max_w:
        container["width"] = (w + 2 * BOUND_TEXT_PADDING) / shrink
    need_h = (h + 2 * BOUND_TEXT_PADDING) / shrink
    if need_h > container["height"]:
        container["height"] = need_h
    t["text"], t["width"], t["height"] = wrapped, w, h
    cx = container["x"] + container["width"] / 2
    cy = container["y"] + container["height"] / 2
    t["x"], t["y"] = cx - w / 2, cy - h / 2
    container["boundElements"].append({"type": "text", "id": t["id"]})
    return t


def _geom(el):
    return {"type": el["type"], "x": el["x"], "y": el["y"], "width": el["width"], "height": el["height"]}


def reroute_arrow(arrow, by_id, old_geoms=None):
    """Keep a straight (2-point) bound arrow attached after its shapes moved or resized.
    Ends that pointed at the shape's center are re-aimed edge-to-edge; ends anchored
    elsewhere (e.g. a fixed side) keep the same relative spot on the shape."""
    if arrow.get("type") != "arrow" or len(arrow.get("points") or []) != 2:
        return False
    old_geoms = old_geoms or {}
    s = by_id.get((arrow.get("startBinding") or {}).get("elementId"))
    e = by_id.get((arrow.get("endBinding") or {}).get("elementId"))
    if s is None and e is None:
        return False
    old_a = (arrow["x"], arrow["y"])
    old_b = (arrow["x"] + arrow["points"][1][0], arrow["y"] + arrow["points"][1][1])
    gs = old_geoms.get(s["id"], _geom(s)) if s is not None else None
    ge = old_geoms.get(e["id"], _geom(e)) if e is not None else None
    old_ref_a = _center(gs) if gs else old_a
    old_ref_b = _center(ge) if ge else old_b

    def mode(shape_old, pt, other_ref):
        if shape_old is None:
            return "free"
        auto = _edge_point(shape_old, other_ref)
        return "auto" if math.hypot(pt[0] - auto[0], pt[1] - auto[1]) < 8 else "anchored"

    ma, mb = mode(gs, old_a, old_ref_b), mode(ge, old_b, old_ref_a)

    def anchored(shape, shape_old, pt):
        fx = (pt[0] - shape_old["x"]) / shape_old["width"] if shape_old["width"] else 0.5
        fy = (pt[1] - shape_old["y"]) / shape_old["height"] if shape_old["height"] else 0.5
        return shape["x"] + fx * shape["width"], shape["y"] + fy * shape["height"]

    a = anchored(s, gs, old_a) if ma == "anchored" else old_a
    b = anchored(e, ge, old_b) if mb == "anchored" else old_b
    ref_a = _center(s) if ma == "auto" else a
    ref_b = _center(e) if mb == "auto" else b
    if ma == "auto":
        a = _edge_point(s, ref_b)
    if mb == "auto":
        b = _edge_point(e, ref_a)
    if (a, b) == (old_a, old_b):
        return False
    arrow["x"], arrow["y"] = a
    arrow["points"] = [[0.0, 0.0], [b[0] - a[0], b[1] - a[1]]]
    arrow["width"], arrow["height"] = abs(b[0] - a[0]), abs(b[1] - a[1])
    for bnd in arrow.get("boundElements") or []:
        t = by_id.get(bnd.get("id"))
        if t is not None and t.get("type") == "text":
            mx, my = _polyline_midpoint(arrow)
            t["x"], t["y"] = mx - t["width"] / 2, my - t["height"] / 2
            _touch(t)
    _touch(arrow)
    return True


def reroute_bound_arrows(shape, by_id, old_geoms=None, skip=()):
    for bnd in shape.get("boundElements") or []:
        if bnd.get("type") == "arrow" and bnd.get("id") not in skip:
            arrow = by_id.get(bnd.get("id"))
            if arrow is not None and not arrow.get("isDeleted"):
                reroute_arrow(arrow, by_id, old_geoms)


def relayout_text(t, container=None, by_id=None):
    """Re-measure a text element after its text changed; re-wrap and re-center bound labels.
    A container that must grow keeps its center, and arrows attached to it are re-aimed."""
    font_size, font_id = t.get("fontSize", 20), t.get("fontFamily", DEFAULT_FONT)
    original = t.get("originalText") or t.get("text", "")
    if container is not None and container["type"] in SHAPES:
        shrink = {"rectangle": 1.0, "ellipse": 0.7071, "diamond": 0.5}[container["type"]]
        max_w = max(container["width"] * shrink - 2 * BOUND_TEXT_PADDING, font_size)
        wrapped = wrap_text(original, max_w, font_size)
        w, h = measure_text(wrapped, font_size, font_id)
        cx0, cy0 = _center(container)
        old_size = (container["width"], container["height"])
        old_geom = _geom(container)
        if w > max_w:
            container["width"] = (w + 2 * BOUND_TEXT_PADDING) / shrink
        if (h + 2 * BOUND_TEXT_PADDING) / shrink > container["height"]:
            container["height"] = (h + 2 * BOUND_TEXT_PADDING) / shrink
        if (container["width"], container["height"]) != old_size:
            container["x"] = cx0 - container["width"] / 2
            container["y"] = cy0 - container["height"] / 2
            _touch(container)
            if by_id is not None:
                reroute_bound_arrows(container, by_id, {container["id"]: old_geom})
        t["text"], t["width"], t["height"] = wrapped, w, h
        cx, cy = _center(container)
        t["x"], t["y"] = cx - w / 2, cy - h / 2
    elif container is not None and container["type"] in LINEAR:
        w, h = measure_text(original, font_size, font_id)
        t["text"], t["width"], t["height"] = original, w, h
        mx, my = _polyline_midpoint(container)
        t["x"], t["y"] = mx - w / 2, my - h / 2
    else:
        w, h = measure_text(original, font_size, font_id)
        t["text"], t["width"], t["height"] = original, w, h


def _touch(e):
    e["version"] = e.get("version", 1) + 1
    e["versionNonce"] = _rand_int()
    e["updated"] = _now_ms()


def make_shape(spec):
    etype = spec["type"]
    el = _base(etype, spec)
    if "roundness" in spec:
        el["roundness"] = spec["roundness"]
    else:
        el["roundness"] = {"type": 3} if etype == "rectangle" else {"type": 2}
    if el["width"] <= 0 or el["height"] <= 0:
        el["width"] = el["width"] or 160.0
        el["height"] = el["height"] or 80.0
    out = [el]
    if spec.get("label"):
        out.append(_fit_label(el, spec["label"]))
    return out


def _center(el):
    return el["x"] + el["width"] / 2, el["y"] + el["height"] / 2


def _edge_point(el, toward, gap=4.0):
    """Point on el's outline (plus gap) along the line from its center toward `toward`."""
    cx, cy = _center(el)
    dx, dy = toward[0] - cx, toward[1] - cy
    if dx == 0 and dy == 0:
        return cx, cy
    hw, hh = el["width"] / 2, el["height"] / 2
    t = None
    if el["type"] == "ellipse":
        t = 1.0 / math.sqrt((dx / hw) ** 2 + (dy / hh) ** 2)
    elif el["type"] == "diamond":
        t = 1.0 / (abs(dx) / hw + abs(dy) / hh)
    else:
        tx = hw / abs(dx) if dx else float("inf")
        ty = hh / abs(dy) if dy else float("inf")
        t = min(tx, ty)
    length = math.hypot(dx, dy)
    t += gap / length
    return cx + dx * t, cy + dy * t


def make_linear(spec, by_id):
    etype = spec["type"]
    el = _base(etype, spec)
    el["roundness"] = spec.get("roundness", {"type": 2})
    start_id = spec.get("from") or (spec.get("startBinding") or {}).get("elementId")
    end_id = spec.get("to") or (spec.get("endBinding") or {}).get("elementId")
    start_el = by_id.get(start_id) if start_id else None
    end_el = by_id.get(end_id) if end_id else None
    if start_id and not start_el:
        raise WhiteboardError("Arrow %s starts at unknown element id %r" % (el["id"], start_id))
    if end_id and not end_el:
        raise WhiteboardError("Arrow %s ends at unknown element id %r" % (el["id"], end_id))

    if spec.get("points"):
        pts = [[float(p[0]), float(p[1])] for p in spec["points"]]
        if pts[0] != [0.0, 0.0]:
            ox, oy = pts[0]
            el["x"] += ox
            el["y"] += oy
            pts = [[p[0] - ox, p[1] - oy] for p in pts]
    elif start_el is not None or end_el is not None:
        def anchor(target, fixed):
            if fixed:
                return target["x"] + fixed[0] * target["width"], target["y"] + fixed[1] * target["height"]
            return None
        sfix = (spec.get("startBinding") or {}).get("fixedPoint")
        efix = (spec.get("endBinding") or {}).get("fixedPoint")
        a = anchor(start_el, sfix) if start_el is not None else None
        b = anchor(end_el, efix) if end_el is not None else None
        a_ref = a or (_center(start_el) if start_el is not None else (el["x"], el["y"]))
        b_ref = b or (_center(end_el) if end_el is not None else (el["x"] + 100, el["y"]))
        if a is None and start_el is not None:
            a = _edge_point(start_el, b_ref)
        if b is None and end_el is not None:
            b = _edge_point(end_el, a_ref)
        a = a or a_ref
        b = b or b_ref
        el["x"], el["y"] = a
        pts = [[0.0, 0.0], [b[0] - a[0], b[1] - a[1]]]
    else:
        w = float(spec.get("width", 100) or 100)
        h = float(spec.get("height", 0) or 0)
        pts = [[0.0, 0.0], [w, h]]
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    el["points"] = pts
    el["width"], el["height"] = max(xs) - min(xs), max(ys) - min(ys)
    el["lastCommittedPoint"] = None
    el["startArrowhead"] = spec.get("startArrowhead")
    el["endArrowhead"] = spec.get("endArrowhead", "arrow" if etype == "arrow" else None)
    el["startBinding"] = el["endBinding"] = None
    if etype == "arrow":
        el["elbowed"] = False
        if start_el is not None:
            el["startBinding"] = {"elementId": start_el["id"], "focus": 0, "gap": 4, "fixedPoint": None}
            start_el.setdefault("boundElements", []).append({"id": el["id"], "type": "arrow"})
        if end_el is not None:
            el["endBinding"] = {"elementId": end_el["id"], "focus": 0, "gap": 4, "fixedPoint": None}
            if end_el is not start_el:
                end_el.setdefault("boundElements", []).append({"id": el["id"], "type": "arrow"})
    out = [el]
    if spec.get("label") and etype == "arrow":
        lab = spec["label"] if isinstance(spec["label"], dict) else {"text": spec["label"]}
        lab = dict(lab)
        lab.setdefault("fontSize", 16)
        t = make_text(lab, el)
        mid = _polyline_midpoint(el)
        t["x"], t["y"] = mid[0] - t["width"] / 2, mid[1] - t["height"] / 2
        el["boundElements"].append({"type": "text", "id": t["id"]})
        out.append(t)
    return out


def _polyline_midpoint(el):
    pts = [(el["x"] + p[0], el["y"] + p[1]) for p in el["points"]]
    if len(pts) == 2:
        return (pts[0][0] + pts[1][0]) / 2, (pts[0][1] + pts[1][1]) / 2
    seg = len(pts) // 2
    a, b = pts[seg - 1], pts[seg]
    return (a[0] + b[0]) / 2, (a[1] + b[1]) / 2


def build_elements(specs, existing):
    """Turn spec dicts into full elements. Returns (new_elements, ids_to_delete)."""
    if isinstance(specs, dict) and "elements" in specs:
        specs = specs["elements"]
    if not isinstance(specs, list):
        raise WhiteboardError("The spec file must contain a JSON array of elements")
    by_id = {e["id"]: e for e in existing if not e.get("isDeleted")}
    new, deletes = [], []
    used = set(by_id)
    for spec in specs:
        if not isinstance(spec, dict) or "type" not in spec:
            raise WhiteboardError("Every spec needs a 'type': %r" % (spec,))
        etype = spec["type"]
        if etype in ("cameraUpdate", "restoreCheckpoint"):
            continue                      # MCP-only pseudo elements: nothing to store
        if etype == "delete":
            ids = spec.get("ids", [])
            deletes += [i.strip() for i in ids.split(",")] if isinstance(ids, str) else list(ids)
            continue
        if spec.get("id") in used:
            raise WhiteboardError("Element id %r already exists; ids must be unique" % spec["id"])
        if etype in SHAPES:
            made = make_shape(spec)
        elif etype in LINEAR:
            made = make_linear(spec, by_id)
        elif etype == "text":
            made = [make_text(spec)]
        elif etype == "frame":
            el = _base("frame", spec)
            el["name"] = spec.get("name")
            made = [el]
        else:
            raise WhiteboardError("Unsupported element type %r (use rectangle, ellipse, diamond, "
                                  "arrow, line, text, frame)" % etype)
        for e in made:
            used.add(e["id"])
            by_id[e["id"]] = e
        new += made
    return new, deletes


# --------------------------------------------------------------------------------------
# Scene editing helpers
# --------------------------------------------------------------------------------------

_DIGITS = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"


def _int_len(head):
    if "a" <= head <= "z":
        return ord(head) - ord("a") + 2
    if "A" <= head <= "Z":
        return ord("Z") - ord(head) + 2
    raise ValueError(head)


def _valid_key(k):
    if not isinstance(k, str) or not k:
        return False
    try:
        n = _int_len(k[0])
    except ValueError:
        return False
    if len(k) < n or any(c not in _DIGITS for c in k[1:]):
        return False
    return not (len(k) > n and k.endswith("0"))


def _increment_integer(x):
    head, digs = x[0], list(x[1:])
    for i in range(len(digs) - 1, -1, -1):
        d = _DIGITS.index(digs[i]) + 1
        if d == len(_DIGITS):
            digs[i] = "0"
        else:
            digs[i] = _DIGITS[d]
            return head + "".join(digs)
    if head == "Z":
        return "a0"
    if head == "z":
        return None
    h = chr(ord(head) + 1)
    if h > "a":
        digs.append("0")
    else:
        digs.pop()
    return h + "".join(digs)


def _key_after(k):
    if k is None:
        return "a0"
    nxt = _increment_integer(k[:_int_len(k[0])])
    if nxt is None:
        raise WhiteboardError("Ran out of fractional index keys")
    return nxt


def assign_indices(elements):
    """Keep valid increasing `index` keys; give appended/invalid elements keys after their predecessor.
    If existing keys are out of order, renumber everything (Excalidraw does the same on load)."""
    prev = None
    ok = True
    for e in elements:
        k = e.get("index")
        if _valid_key(k) and (prev is None or k > prev):
            prev = k
            continue
        if k is not None and _valid_key(k):
            ok = False
            break
        e["index"] = _key_after(prev)
        prev = e["index"]
    if not ok:
        prev = None
        for e in elements:
            e["index"] = _key_after(prev)
            prev = e["index"]


def apply_deletes(elements, ids):
    """Mark elements (and their bound labels) deleted and clean up bindings."""
    ids = set(i for i in ids if i)
    known = {e["id"] for e in elements}
    missing = ids - known
    if missing:
        raise WhiteboardError("Cannot delete unknown element id(s): %s" % ", ".join(sorted(missing)))
    for e in elements:
        if e.get("containerId") in ids:
            ids.add(e["id"])
    for e in elements:
        if e["id"] in ids and not e.get("isDeleted"):
            e["isDeleted"] = True
            e["version"] = e.get("version", 1) + 1
            e["versionNonce"] = _rand_int()
            e["updated"] = _now_ms()
    for e in elements:
        if e.get("isDeleted"):
            continue
        if e.get("boundElements"):
            e["boundElements"] = [b for b in e["boundElements"] if b.get("id") not in ids]
        for key in ("startBinding", "endBinding"):
            if e.get(key) and e[key].get("elementId") in ids:
                e[key] = None
        if e.get("containerId") in ids:
            e["containerId"] = None


def validate_scene(scene):
    """Return a list of problems that would confuse NotesHub/Excalidraw."""
    problems = []
    els = scene.get("elements", [])
    seen = set()
    for e in els:
        if "id" not in e or "type" not in e:
            problems.append("element without id/type: %s" % json.dumps(e)[:80])
            continue
        if e["id"] in seen:
            problems.append("duplicate id %s" % e["id"])
        seen.add(e["id"])
    live = {e["id"]: e for e in els if not e.get("isDeleted") and "id" in e}
    for e in live.values():
        for b in e.get("boundElements") or []:
            if b.get("id") not in live:
                problems.append("%s %s lists missing bound element %s" % (e["type"], e["id"], b.get("id")))
        if e.get("containerId") and e["containerId"] not in live:
            problems.append("text %s points at missing container %s" % (e["id"], e["containerId"]))
        for key in ("startBinding", "endBinding"):
            if e.get(key) and e[key].get("elementId") not in live:
                problems.append("arrow %s %s points at missing element" % (e["id"], key))
        if e["type"] in LINEAR and not e.get("points"):
            problems.append("%s %s has no points" % (e["type"], e["id"]))
    return problems


# --------------------------------------------------------------------------------------
# Simplified SVG preview (what GitHub & file browsers show; NotesHub ignores it)
# --------------------------------------------------------------------------------------

def _fmt(v):
    return ("%.2f" % v).rstrip("0").rstrip(".") if isinstance(v, float) else str(v)


def _corner_radius(el):
    r = el.get("roundness")
    if not r:
        return 0
    m = min(el["width"], el["height"])
    if r.get("type") == 3:
        fixed = r.get("value") or 32
        return m * 0.25 if m <= fixed / 0.25 else fixed
    return m * 0.25


def _element_bounds(el):
    if el["type"] in LINEAR or el["type"] == "freedraw":
        pts = el.get("points") or [[0, 0]]
        xs = [el["x"] + p[0] for p in pts]
        ys = [el["y"] + p[1] for p in pts]
        return min(xs), min(ys), max(xs), max(ys)
    x1, y1 = el["x"], el["y"]
    x2, y2 = x1 + el["width"], y1 + el["height"]
    if el.get("angle"):
        cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
        c, s = math.cos(el["angle"]), math.sin(el["angle"])
        corners = [(x1, y1), (x2, y1), (x2, y2), (x1, y2)]
        rot = [(cx + (x - cx) * c - (y - cy) * s, cy + (x - cx) * s + (y - cy) * c) for x, y in corners]
        return (min(p[0] for p in rot), min(p[1] for p in rot), max(p[0] for p in rot), max(p[1] for p in rot))
    return x1, y1, x2, y2


def scene_bounds(elements):
    live = [e for e in elements if not e.get("isDeleted")]
    if not live:
        return 0, 0, 0, 0
    bs = [_element_bounds(e) for e in live]
    return min(b[0] for b in bs), min(b[1] for b in bs), max(b[2] for b in bs), max(b[3] for b in bs)


def _dash(el):
    sw = el.get("strokeWidth", 2)
    style = el.get("strokeStyle", "solid")
    if style == "dashed":
        return ' stroke-dasharray="8 %s"' % _fmt(8 + sw)
    if style == "dotted":
        return ' stroke-dasharray="1.5 %s" stroke-linecap="round"' % _fmt(6 + sw)
    return ""


class _Defs(object):
    def __init__(self):
        self.items = {}

    def fill(self, el):
        bg = el.get("backgroundColor", "transparent")
        if not bg or bg == "transparent":
            return "none"
        style = el.get("fillStyle", "solid")
        if style == "solid":
            return bg
        key = "%s-%s" % (style, re.sub(r"[^0-9a-zA-Z]", "", bg))
        if key not in self.items:
            lines = '<path d="M0 0L0 8" stroke="%s" stroke-width="1.5"/>' % bg
            if style == "cross-hatch":
                lines += '<path d="M0 0L8 0" stroke="%s" stroke-width="1.5"/>' % bg
            self.items[key] = ('<pattern id="%s" width="8" height="8" patternUnits="userSpaceOnUse" '
                               'patternTransform="rotate(45)">%s</pattern>' % (key, lines))
        return "url(#%s)" % key

    def render(self):
        return "<defs>%s</defs>" % "".join(self.items.values()) if self.items else ""


def _arrowhead(kind, tip, prev, el):
    if not kind:
        return ""
    sw = el.get("strokeWidth", 2)
    col = el.get("strokeColor", "#1e1e1e")
    ang = math.atan2(tip[1] - prev[1], tip[0] - prev[0])
    size = 10 + sw * 2
    def at(a, d):
        return tip[0] - d * math.cos(ang + a), tip[1] - d * math.sin(ang + a)
    if kind in ("arrow",):
        p1, p2 = at(0.45, size), at(-0.45, size)
        return ('<path d="M%s %s L%s %s L%s %s" fill="none" stroke="%s" stroke-width="%s" stroke-linecap="round" '
                'stroke-linejoin="round"/>' % (_fmt(p1[0]), _fmt(p1[1]), _fmt(tip[0]), _fmt(tip[1]),
                                               _fmt(p2[0]), _fmt(p2[1]), col, sw))
    if kind in ("triangle", "triangle_outline"):
        p1, p2 = at(0.4, size), at(-0.4, size)
        fill = col if kind == "triangle" else "none"
        return '<path d="M%s %s L%s %s L%s %s Z" fill="%s" stroke="%s" stroke-width="%s"/>' % (
            _fmt(tip[0]), _fmt(tip[1]), _fmt(p1[0]), _fmt(p1[1]), _fmt(p2[0]), _fmt(p2[1]), fill, col, sw)
    if kind == "bar":
        p1, p2 = at(math.pi / 2, size / 2), at(-math.pi / 2, size / 2)
        return '<path d="M%s %s L%s %s" stroke="%s" stroke-width="%s"/>' % (
            _fmt(p1[0]), _fmt(p1[1]), _fmt(p2[0]), _fmt(p2[1]), col, sw)
    if kind in ("dot", "circle", "circle_outline"):
        fill = "none" if kind == "circle_outline" else col
        return '<circle cx="%s" cy="%s" r="%s" fill="%s" stroke="%s" stroke-width="%s"/>' % (
            _fmt(tip[0]), _fmt(tip[1]), _fmt(size / 3), fill, col, sw)
    if kind in ("diamond", "diamond_outline"):
        p1, p3 = at(0.5, size / 1.6), at(-0.5, size / 1.6)
        back = at(0, size * 1.1)
        fill = "none" if kind == "diamond_outline" else col
        return '<path d="M%s %s L%s %s L%s %s L%s %s Z" fill="%s" stroke="%s" stroke-width="%s"/>' % (
            _fmt(tip[0]), _fmt(tip[1]), _fmt(p1[0]), _fmt(p1[1]), _fmt(back[0]), _fmt(back[1]),
            _fmt(p3[0]), _fmt(p3[1]), fill, col, sw)
    return _arrowhead("arrow", tip, prev, el)


def _render_text(el, ox, oy):
    font_id = el.get("fontFamily", DEFAULT_FONT)
    fs = el.get("fontSize", 20)
    lh = fs * el.get("lineHeight", LINE_HEIGHTS.get(font_id, 1.25))
    align = el.get("textAlign", "left")
    anchor = {"left": "start", "center": "middle", "right": "end"}.get(align, "start")
    x = el["x"] - ox + {"left": 0, "center": el["width"] / 2, "right": el["width"]}.get(align, 0)
    lines = str(el.get("text", "")).split("\n")
    out = ['<text font-family="%s" font-size="%spx" fill="%s" text-anchor="%s" style="white-space: pre;">' % (
        _xml_escape(FONT_FAMILIES.get(font_id, FONT_FAMILIES[DEFAULT_FONT]), {'"': "&quot;"}), _fmt(float(fs)),
        el.get("strokeColor", "#1e1e1e"), anchor)]
    for i, line in enumerate(lines):
        y = el["y"] - oy + i * lh + lh / 2 + fs * 0.35
        out.append('<tspan x="%s" y="%s">%s</tspan>' % (_fmt(x), _fmt(y), _xml_escape(line)))
    out.append("</text>")
    return "".join(out)


def _label_mask(el, ox, oy, by_id, defs):
    """Excalidraw cuts the arrow line where its label sits; do the same with an SVG mask."""
    for b in el.get("boundElements") or []:
        t = by_id.get(b.get("id"))
        if t is None or t.get("type") != "text" or t.get("isDeleted"):
            continue
        x1, y1, x2, y2 = scene_bounds([el])
        pad = 40
        mid = "m-" + re.sub(r"[^0-9a-zA-Z_-]", "", el["id"])
        defs.items[mid] = ('<mask id="%s" maskUnits="userSpaceOnUse"><rect x="%s" y="%s" width="%s" height="%s" fill="#fff"/>'
                           '<rect x="%s" y="%s" width="%s" height="%s" fill="#000"/></mask>') % (
            mid, _fmt(x1 - ox - pad), _fmt(y1 - oy - pad), _fmt(x2 - x1 + 2 * pad), _fmt(y2 - y1 + 2 * pad),
            _fmt(t["x"] - ox - 4), _fmt(t["y"] - oy - 2), _fmt(t["width"] + 8), _fmt(t["height"] + 4))
        return ' mask="url(#%s)"' % mid
    return ""


def _render_element(el, ox, oy, defs, files, by_id=None):
    t = el["type"]
    x, y = el["x"] - ox, el["y"] - oy
    w, h = el.get("width", 0), el.get("height", 0)
    sw = el.get("strokeWidth", 2)
    stroke = el.get("strokeColor", "#1e1e1e")
    common = 'stroke="%s" stroke-width="%s"%s' % (stroke, sw, _dash(el))
    body = ""
    if t == "rectangle":
        r = _corner_radius(el)
        body = '<rect x="%s" y="%s" width="%s" height="%s"%s fill="%s" %s/>' % (
            _fmt(x), _fmt(y), _fmt(w), _fmt(h), (' rx="%s"' % _fmt(r)) if r else "", defs.fill(el), common)
    elif t == "ellipse":
        body = '<ellipse cx="%s" cy="%s" rx="%s" ry="%s" fill="%s" %s/>' % (
            _fmt(x + w / 2), _fmt(y + h / 2), _fmt(w / 2), _fmt(h / 2), defs.fill(el), common)
    elif t == "diamond":
        body = '<path d="M%s %s L%s %s L%s %s L%s %s Z" fill="%s" stroke-linejoin="round" %s/>' % (
            _fmt(x + w / 2), _fmt(y), _fmt(x + w), _fmt(y + h / 2), _fmt(x + w / 2), _fmt(y + h),
            _fmt(x), _fmt(y + h / 2), defs.fill(el), common)
    elif t in LINEAR or t == "freedraw":
        pts = [(x + p[0], y + p[1]) for p in el.get("points") or []]
        if len(pts) >= 2 or (t == "freedraw" and pts):
            d = "M" + " L".join("%s %s" % (_fmt(px), _fmt(py)) for px, py in pts)
            closed = t == "line" and len(pts) > 2 and pts[0] == pts[-1]
            body = '<path d="%s%s" fill="%s" stroke-linecap="round" stroke-linejoin="round" %s/>' % (
                d, " Z" if closed else "", defs.fill(el) if closed else "none", common)
            if t == "arrow" and len(pts) >= 2:
                body += _arrowhead(el.get("endArrowhead"), pts[-1], pts[-2], el)
                body += _arrowhead(el.get("startArrowhead"), pts[0], pts[1], el)
    elif t == "text":
        body = _render_text(el, ox, oy)
    elif t == "image":
        f = files.get(el.get("fileId") or "", {})
        if f.get("dataURL"):
            body = '<image href="%s" x="%s" y="%s" width="%s" height="%s" preserveAspectRatio="none"/>' % (
                _xml_escape(f["dataURL"], {'"': "&quot;"}), _fmt(x), _fmt(y), _fmt(w), _fmt(h))
        else:
            body = '<rect x="%s" y="%s" width="%s" height="%s" fill="#f1f3f5" stroke="#adb5bd"/>' % (
                _fmt(x), _fmt(y), _fmt(w), _fmt(h))
    elif t in ("frame", "magicframe"):
        body = '<rect x="%s" y="%s" width="%s" height="%s" rx="8" fill="none" stroke="#bbb" stroke-width="1"/>' % (
            _fmt(x), _fmt(y), _fmt(w), _fmt(h))
        if el.get("name"):
            body += '<text x="%s" y="%s" font-family="sans-serif" font-size="14px" fill="#999">%s</text>' % (
                _fmt(x), _fmt(y - 6), _xml_escape(el["name"]))
    elif t in ("embeddable", "iframe"):
        body = '<rect x="%s" y="%s" width="%s" height="%s" fill="#f8f9fa" %s/>' % (_fmt(x), _fmt(y), _fmt(w), _fmt(h), common)
    if not body:
        return ""
    attrs = []
    if el.get("opacity", 100) != 100:
        attrs.append('opacity="%s"' % _fmt(el["opacity"] / 100.0))
    if t == "arrow" and by_id is not None:
        m = _label_mask(el, ox, oy, by_id, defs)
        if m:
            attrs.append(m.strip())
    if el.get("angle") and t not in LINEAR and t != "freedraw":
        attrs.append('transform="rotate(%s %s %s)"' % (_fmt(math.degrees(el["angle"])), _fmt(x + w / 2), _fmt(y + h / 2)))
    return "<g%s>%s</g>" % ((" " + " ".join(attrs)) if attrs else "", body)


def render_svg(scene):
    elements = [e for e in scene.get("elements", []) if not e.get("isDeleted")]
    x1, y1, x2, y2 = scene_bounds(elements)
    ox, oy = x1 - EXPORT_PADDING, y1 - EXPORT_PADDING
    W = max(x2 - x1 + 2 * EXPORT_PADDING, 1)
    H = max(y2 - y1 + 2 * EXPORT_PADDING, 1)
    defs = _Defs()
    files = scene.get("files") or {}
    by_id = {e["id"]: e for e in elements}
    body = "".join(_render_element(e, ox, oy, defs, files, by_id) for e in elements)
    return ('<svg version="1.1" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 %s %s" width="%s" height="%s" %s>'
            '<!-- svg-source:excalidraw -->'
            '<metadata><!-- payload-type:%s --><!-- payload-version:2 --><!-- payload-start -->%s'
            '<!-- payload-end --></metadata>'
            '<!-- Simplified preview written by noteshub-skills. NotesHub reads only the embedded scene above; '
            'opening and saving this whiteboard in NotesHub (or running whiteboard.py render-exact) '
            'redraws the full hand-drawn preview. -->'
            '%s%s</svg>') % (_fmt(float(W)), _fmt(float(H)), _fmt(float(W)), _fmt(float(H)), PREVIEW_MARKER,
                             MIME_EXCALIDRAW, _encode_payload(scene), defs.render(), body)


def new_scene():
    return {"type": "excalidraw", "version": 2, "source": SOURCE, "elements": [],
            "appState": {"gridSize": None, "viewBackgroundColor": "#ffffff"}, "files": {}}


# --------------------------------------------------------------------------------------
# Commands
# --------------------------------------------------------------------------------------

def _label_of(el, by_id):
    for b in el.get("boundElements") or []:
        t = by_id.get(b.get("id"))
        if t and t["type"] == "text" and not t.get("isDeleted"):
            return t.get("originalText") or t.get("text")
    return None


def cmd_info(a):
    scene, raw = read_scene(a.file)
    els = scene["elements"]
    live = [e for e in els if not e.get("isDeleted")]
    by_id = {e["id"]: e for e in live}
    x1, y1, x2, y2 = scene_bounds(live)
    preview = "simplified (stale until re-rendered)" if PREVIEW_MARKER in raw else ("excalidraw" if raw.lstrip().startswith("<svg") else "none (JSON file)")
    print("%s: %d elements (%d deleted kept for sync), bounds x %s..%s, y %s..%s, preview: %s" % (
        os.path.basename(a.file), len(live), len(els) - len(live), _fmt(float(x1)), _fmt(float(x2)),
        _fmt(float(y1)), _fmt(float(y2)), preview))
    print("Free space: right of x=%s or below y=%s" % (_fmt(float(x2 + 60)), _fmt(float(y2 + 60))))

    def label(e):
        lab = _label_of(e, by_id)
        return (' "%s"' % lab.replace("\n", " / ")) if lab else ""
    shapes = [e for e in live if e["type"] in SHAPES + ("frame", "image", "embeddable", "iframe")]
    if shapes:
        print("\nShapes:")
        for e in shapes:
            print("  %-9s %s%s at (%s, %s) %sx%s%s" % (
                e["type"], e["id"], label(e), _fmt(float(e["x"])), _fmt(float(e["y"])), _fmt(float(e["width"])),
                _fmt(float(e["height"])), (" bg " + e["backgroundColor"]) if e.get("backgroundColor") not in (None, "transparent") else ""))
    links = [e for e in live if e["type"] in LINEAR]
    if links:
        print("\nArrows/lines:")
        for e in links:
            def end(b):
                if not b:
                    return "(free)"
                t = by_id.get(b.get("elementId"))
                return "%s%s" % (b.get("elementId"), label(t) if t else " (missing)")
            print("  %-5s %s: %s -> %s%s" % (e["type"], e["id"], end(e.get("startBinding")), end(e.get("endBinding")), label(e)))
    texts = [e for e in live if e["type"] == "text" and not e.get("containerId")]
    if texts:
        print("\nFree text:")
        for e in texts:
            txt = e.get("originalText") or e.get("text", "")
            print('  %s at (%s, %s) size %s: "%s"' % (e["id"], _fmt(float(e["x"])), _fmt(float(e["y"])),
                                                     _fmt(float(e.get("fontSize", 20))), txt.replace("\n", " / ")[:120]))
    other = [e for e in live if e["type"] == "freedraw"]
    if other:
        print("\n%d freehand stroke(s)" % len(other))
    problems = validate_scene(scene)
    if problems:
        print("\nProblems:\n  " + "\n  ".join(problems))


def cmd_decode(a):
    scene, _ = read_scene(a.file)
    text = json.dumps(scene, ensure_ascii=False, indent=2)
    if a.output:
        with open(a.output, "w", encoding="utf-8") as f:
            f.write(text)
        print("Wrote %s (%d elements)" % (a.output, len(scene["elements"])))
    else:
        print(text)


def _load_specs(path):
    with open(path, "r", encoding="utf-8") if path != "-" else sys.stdin as f:
        return json.load(f)


def cmd_new(a):
    file_format(a.file)
    if os.path.exists(a.file) and not a.force:
        raise WhiteboardError("%s already exists (use `add`, or --force to overwrite)" % a.file)
    scene = new_scene()
    if a.background:
        scene["appState"]["viewBackgroundColor"] = a.background
    new, deletes = build_elements(_load_specs(a.spec), [])
    scene["elements"] = new
    if deletes:
        apply_deletes(scene["elements"], deletes)
    assign_indices(scene["elements"])
    _finish(a.file, scene)
    print("Created %s with %d elements" % (a.file, len([e for e in new if not e.get("isDeleted")])))


def cmd_add(a):
    scene, _ = read_scene(a.file)
    new, deletes = build_elements(_load_specs(a.spec), scene["elements"])
    scene["elements"].extend(new)
    if deletes:
        apply_deletes(scene["elements"], deletes)
    # Existing shapes that gained arrow bindings were modified: bump their versions.
    new_ids = {n["id"] for n in new}
    for e in scene["elements"]:
        if e["id"] not in new_ids and any(b.get("id") in new_ids for b in (e.get("boundElements") or [])):
            _touch(e)
    assign_indices(scene["elements"])
    _finish(a.file, scene)
    print("Added %d element(s)%s to %s" % (len(new), (", deleted %d" % len(deletes)) if deletes else "", a.file))


def cmd_apply(a):
    old, _ = read_scene(a.file)
    with open(a.scene, "r", encoding="utf-8") as f:
        scene = json.load(f)
    if isinstance(scene, list):
        scene = dict(old, elements=scene)
    scene.setdefault("files", old.get("files", {}))
    scene.setdefault("appState", old.get("appState", {}))
    scene.setdefault("type", "excalidraw")
    scene.setdefault("version", 2)
    scene.setdefault("source", old.get("source", SOURCE))
    old_by_id = {e["id"]: e for e in old["elements"]}
    by_id = {e["id"]: e for e in scene["elements"] if "id" in e and not e.get("isDeleted")}
    geometry = ("x", "y", "width", "height", "angle")
    edited_arrows = {e["id"] for e in scene["elements"] if e.get("type") in LINEAR
                     and old_by_id.get(e.get("id")) != e}
    moved = [e for e in scene["elements"] if e.get("type") in SHAPES and e["id"] in old_by_id
             and any(e.get(k) != old_by_id[e["id"]].get(k) for k in geometry)]
    old_geoms = {e["id"]: _geom(old_by_id[e["id"]]) for e in moved}
    for e in moved:
        # A moved/resized shape drags its label along and keeps attached arrows attached.
        for bnd in e.get("boundElements") or []:
            t = by_id.get(bnd.get("id"))
            if t is not None and t.get("type") == "text" and old_by_id.get(t["id"]) == t:
                relayout_text(t, e)
        reroute_bound_arrows(e, by_id, old_geoms, skip=edited_arrows)
    for e in scene["elements"]:
        prev = old_by_id.get(e.get("id"))
        if e.get("type") == "text":
            # Keep text/originalText in sync and re-measure when the wording changed.
            if prev is None or e.get("text") != prev.get("text") or e.get("originalText") != prev.get("originalText"):
                if prev is not None and e.get("originalText") == prev.get("originalText"):
                    e["originalText"] = e.get("text", "")
                e.setdefault("originalText", e.get("text", ""))
                relayout_text(e, by_id.get(e.get("containerId")), by_id)
        if prev is not None and prev != e:
            e["version"] = max(e.get("version", 1), prev.get("version", 1)) + 1
            e["versionNonce"] = _rand_int()
            e["updated"] = _now_ms()
    assign_indices(scene["elements"])
    _finish(a.file, scene)
    print("Applied scene to %s (%d elements)" % (a.file, len(scene["elements"])))


def cmd_set_label(a):
    scene, _ = read_scene(a.file)
    by_id = {e["id"]: e for e in scene["elements"] if not e.get("isDeleted")}
    el = by_id.get(a.id)
    if el is None:
        raise WhiteboardError("No element with id %r (run `info` to list ids)" % a.id)
    text = a.text.replace("\\n", "\n")
    if el["type"] == "text":
        target, container = el, by_id.get(el.get("containerId"))
    else:
        container = el
        target = next((by_id[b["id"]] for b in el.get("boundElements") or []
                       if b.get("type") == "text" and b.get("id") in by_id), None)
        if target is None:
            if el["type"] not in SHAPES + ("arrow",):
                raise WhiteboardError("%s elements can't carry a label" % el["type"])
            target = make_text({"text": text, "fontSize": a.font_size or (16 if el["type"] == "arrow" else 20)}, el)
            el.setdefault("boundElements", []).append({"type": "text", "id": target["id"]})
            scene["elements"].append(target)
            _touch(el)
    target["text"] = target["originalText"] = text
    if a.font_size:
        target["fontSize"] = float(a.font_size)
    by_id[target["id"]] = target
    relayout_text(target, container, by_id)
    _touch(target)
    if container is not None:
        _touch(container)
    assign_indices(scene["elements"])
    _finish(a.file, scene)
    print("Set label of %s to %r" % (a.id, text))


def _finish(path, scene):
    problems = validate_scene(scene)
    if problems:
        raise WhiteboardError("Not saved - the scene has problems:\n  " + "\n  ".join(problems))
    write_scene(path, scene)


def cmd_preview(a):
    scene, _ = read_scene(a.file)
    if file_format(a.file) != "svg":
        raise WhiteboardError("Only .excalidraw.svg files have a preview")
    write_scene(a.file, scene)
    print("Rebuilt the simplified preview of %s" % a.file)


def cmd_check(a):
    scene, raw = read_scene(a.file)
    problems = validate_scene(scene)
    live = len([e for e in scene["elements"] if not e.get("isDeleted")])
    if problems:
        print("PROBLEMS in %s:\n  %s" % (a.file, "\n  ".join(problems)))
        sys.exit(1)
    stale = PREVIEW_MARKER in raw
    print("OK: %s loads (%d elements)%s" % (a.file, live, "; preview is simplified" if stale else ""))


def cmd_render_exact(a):
    here = os.path.dirname(os.path.abspath(__file__))
    script = os.path.join(here, "excalidraw-render", "render.mjs")
    if file_format(a.file) != "svg":
        raise WhiteboardError("render-exact only applies to .excalidraw.svg files")
    read_scene(a.file)  # validate first
    try:
        res = subprocess.run(["node", script, os.path.abspath(a.file)], timeout=a.timeout)
    except FileNotFoundError:
        raise WhiteboardError("Node.js is not installed here; the simplified preview stays in place")
    except subprocess.TimeoutExpired:
        raise WhiteboardError("The Excalidraw renderer timed out; the simplified preview stays in place")
    if res.returncode != 0:
        raise WhiteboardError("The Excalidraw renderer failed (see above); the simplified preview stays in place")


def build_parser():
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = p.add_subparsers(dest="cmd")
    sub.required = True

    def cmd(name, fn, help_text):
        sp = sub.add_parser(name, help=help_text)
        sp.add_argument("file", help="a .excalidraw.svg or .excalidraw file")
        sp.set_defaults(fn=fn)
        return sp

    cmd("info", cmd_info, "outline the whiteboard")
    sp = cmd("decode", cmd_decode, "print the scene JSON")
    sp.add_argument("-o", "--output")
    sp = cmd("new", cmd_new, "create a whiteboard from element specs")
    sp.add_argument("--from", dest="spec", required=True, help="JSON file with an array of element specs ('-' for stdin)")
    sp.add_argument("--background", help="canvas color, default #ffffff")
    sp.add_argument("--force", action="store_true")
    sp = cmd("add", cmd_add, "append element specs to a whiteboard")
    sp.add_argument("--from", dest="spec", required=True)
    sp = cmd("apply", cmd_apply, "replace the scene with an edited decode output")
    sp.add_argument("--scene", required=True)
    sp = cmd("set-label", cmd_set_label, "change the text of a text element or a shape/arrow label")
    sp.add_argument("--id", required=True)
    sp.add_argument("--text", required=True, help="use \\n for line breaks")
    sp.add_argument("--font-size", type=float)
    cmd("preview", cmd_preview, "rebuild the simplified SVG preview")
    cmd("check", cmd_check, "validate the file")
    sp = cmd("render-exact", cmd_render_exact, "redraw the preview with the real Excalidraw renderer")
    sp.add_argument("--timeout", type=int, default=600)
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        args.fn(args)
    except (WhiteboardError, FileNotFoundError, ValueError) as e:
        print("ERROR: %s" % e, file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
