import os
import json
import xml.etree.ElementTree as ET
import re

from krita import Extension, Krita
from PyQt5.QtWidgets import QMessageBox

from .dialog import LottieExportDialog

SVG_NS = "http://www.w3.org/2000/svg"


# ---------------------------------------------------------------------------
# Color
# ---------------------------------------------------------------------------

def _read_style_attr(elem, name, default=None):
    direct = elem.get(name)
    if direct is not None:
        return direct
    style = elem.get("style", "")
    if style:
        parts = [p.strip() for p in style.split(";") if ":" in p]
        style_map = {}
        for part in parts:
            k, v = part.split(":", 1)
            style_map[k.strip()] = v.strip()
        if name in style_map:
            return style_map[name]
    return default


def _to_float_safe(value, default):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _merge_style_ctx(parent_ctx, elem):
    ctx = dict(parent_ctx) if parent_ctx else {
        "fill": "#000000",
        "stroke": "none",
        "stroke-width": 0.0,
        "fill-opacity": 1.0,
        "stroke-opacity": 1.0,
        "opacity": 1.0
    }
    for key in ("fill", "stroke", "stroke-width", "fill-opacity", "stroke-opacity"):
        v = _read_style_attr(elem, key, None)
        if v is not None:
            ctx[key] = v

    # SVG group opacity multiplies through descendants.
    local_op = _to_float_safe(_read_style_attr(elem, "opacity", 1.0), 1.0)
    ctx["opacity"] = _to_float_safe(ctx.get("opacity", 1.0), 1.0) * local_op
    return ctx


def _parse_color(color_str):
    if not color_str or color_str == "none":
        return None
    color_str = color_str.strip()
    if color_str.startswith("#"):
        h = color_str.lstrip("#")
        if len(h) == 3:
            h = "".join(c*2 for c in h)
        r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
        return [round(r/255, 4), round(g/255, 4), round(b/255, 4), 1]
    m = re.match(r"rgb\s*\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*\)", color_str)
    if m:
        return [round(int(m.group(i))/255, 4) for i in (1, 2, 3)] + [1]
    return [0, 0, 0, 1]


# ---------------------------------------------------------------------------
# Transform — viewBox-aware
# ---------------------------------------------------------------------------

def _get_viewbox_size(svg_string):
    """Return (vb_w, vb_h) from the SVG viewBox attribute."""
    m = re.search(
        r'viewBox\s*=\s*"[0-9.\-\s]+\s+([0-9.]+)\s+([0-9.]+)"', svg_string
        )
    if m:
        return float(m.group(1)), float(m.group(2))
    return 595.2, 841.92  # fallback: Krita's default A4 72dpi viewBox


def _parse_matrix(transform_str, scale_x, scale_y):
    """
    Parse SVG matrix(a,b,c,d,e,f) and convert to canvas pixel space.

    scale_x = canvas_width  / viewBox_width
    scale_y = canvas_height / viewBox_height

    The matrix encodes:
      - Local shape scale in a, d  (e.g. 0.3508)
      - Translation in e, f        (already in viewBox units)

    To get canvas pixels:
      tx_canvas = e * scale_x
      ty_canvas = f * scale_y
      sx_canvas = a * scale_x   (local scale * viewBox-to-canvas scale)
      sy_canvas = d * scale_y
    """
    m = re.search(r"matrix\s*\(([^)]+)\)", transform_str)
    if m:
        vals = [float(x) for x in re.split(r"[\s,]+", m.group(1).strip())]
        a, b, c, d, e, f = vals
        tx = e * scale_x
        ty = f * scale_y
        sx = a * scale_x
        sy = d * scale_y
        return tx, ty, sx, sy

    m = re.search(
        r"translate\s*\(\s*([0-9.\-]+)\s*,?\s*([0-9.\-]+)?\s*\)", transform_str
        )
    if m:
        tx = float(m.group(1)) * scale_x
        ty = (float(m.group(2)) if m.group(2) else 0.0) * scale_y
        return tx, ty, scale_x, scale_y

    return 0.0, 0.0, scale_x, scale_y


def _strip_svg_ns(tag):
    return tag.split("}", 1)[1] if "}" in tag else tag


def _mat_mul(m1, m2):
    a1, b1, c1, d1, e1, f1 = m1
    a2, b2, c2, d2, e2, f2 = m2
    return [
        a1*a2 + c1*b2,
        b1*a2 + d1*b2,
        a1*c2 + c1*d2,
        b1*c2 + d1*d2,
        a1*e2 + c1*f2 + e1,
        b1*e2 + d1*f2 + f1
    ]


def _transform_to_matrix(transform_str):
    if not transform_str:
        return [1, 0, 0, 1, 0, 0]

    result = [1, 0, 0, 1, 0, 0]
    for fn, raw_args in re.findall(r"([a-zA-Z]+)\s*\(([^)]*)\)", transform_str):
        vals = [float(x) for x in re.split(r"[\s,]+", raw_args.strip()) if x]
        fn = fn.lower()
        if fn == "matrix" and len(vals) == 6:
            cur = vals
        elif fn == "translate" and len(vals) >= 1:
            tx = vals[0]
            ty = vals[1] if len(vals) > 1 else 0.0
            cur = [1, 0, 0, 1, tx, ty]
        elif fn == "scale" and len(vals) >= 1:
            sx = vals[0]
            sy = vals[1] if len(vals) > 1 else sx
            cur = [sx, 0, 0, sy, 0, 0]
        else:
            continue
        result = _mat_mul(result, cur)
    return result


def _matrix_to_canvas_transform(m, scale_x, scale_y):
    a, _b, _c, d, e, f = m
    tx = e * scale_x
    ty = f * scale_y
    sx = a * scale_x
    sy = d * scale_y
    return tx, ty, sx, sy


def _split_svg_subpaths(d):
    tokens = re.findall(
        r"[AaCcHhLlMmQqSsTtVvZz]|[-+]?[0-9]*\.?[0-9]+(?:[eE][-+]?[0-9]+)?",
        d or ""
    )
    if not tokens:
        return []

    parts = []
    current = []
    seen_move = False
    for tok in tokens:
        if tok in ("M", "m"):
            if current and seen_move:
                parts.append(" ".join(current))
            current = [tok]
            seen_move = True
        else:
            if not seen_move:
                # Ignore malformed prefix before first move.
                continue
            current.append(tok)
    if current and seen_move:
        parts.append(" ".join(current))
    return parts or [d]


def _is_full_canvas_white_rect(shape_group, canvas_w, canvas_h):
    if shape_group.get("ty") != "gr":
        return False
    items = shape_group.get("it", [])
    rect_item = next((it for it in items if it.get("ty") == "rc"), None)
    fill_item = next((it for it in items if it.get("ty") == "fl"), None)
    if not rect_item or not fill_item:
        return False

    size = rect_item.get("s", {}).get("k", [0, 0])
    if not isinstance(size, list) or len(size) < 2:
        return False
    w, h = float(size[0]), float(size[1])
    # Krita/Figma background cards can be slightly inset, so use a relaxed check.
    if w < canvas_w * 0.90 or h < canvas_h * 0.90:
        return False

    color = fill_item.get("c", {}).get("k", [])
    if not isinstance(color, list) or len(color) < 3:
        return False
    r, g, b = float(color[0]), float(color[1]), float(color[2])
    if not (r >= 0.98 and g >= 0.98 and b >= 0.98):
        return False

    pos = rect_item.get("p", {}).get("k", [])
    if isinstance(pos, list) and len(pos) >= 2:
        cx = float(pos[0])
        cy = float(pos[1])
        if abs(cx - canvas_w / 2) > canvas_w * 0.1:
            return False
        if abs(cy - canvas_h / 2) > canvas_h * 0.1:
            return False

    return True


# ---------------------------------------------------------------------------
# SVG element → Lottie shapes
# ---------------------------------------------------------------------------

def _svg_elem_to_lottie_shapes(
    elem, scale_x, scale_y, combined_matrix=None, style_ctx=None
):
    tag = _strip_svg_ns(elem.tag)
    shapes = []

    style_ctx = style_ctx or {}
    fill_val = style_ctx.get("fill", "#000000")
    stroke_val = style_ctx.get("stroke", "none")

    fill_color = _parse_color(fill_val)
    stroke_color = _parse_color(stroke_val)
    stroke_w = _to_float_safe(style_ctx.get("stroke-width", 0), 0.0)
    stroke_op = _to_float_safe(style_ctx.get("stroke-opacity", 1), 1.0)
    fill_op = _to_float_safe(style_ctx.get("fill-opacity", 1), 1.0)
    group_op = _to_float_safe(style_ctx.get("opacity", 1), 1.0)
    fill_op *= group_op
    stroke_op *= group_op

    if combined_matrix is None:
        transform = elem.get("transform", "")
        tx, ty, sx, sy = _parse_matrix(transform, scale_x, scale_y)
    else:
        tx, ty, sx, sy = _matrix_to_canvas_transform(
            combined_matrix, scale_x, scale_y
            )

    if tag == "rect":
        x = float(elem.get("x", 0) or 0)
        y = float(elem.get("y", 0) or 0)
        w = float(elem.get("width",  0) or 0)
        h = float(elem.get("height", 0) or 0)
        rx = float(elem.get("rx", 0) or 0)
        ry = float(elem.get("ry", 0) or rx)

        # Local coords scaled by sx/sy, then translated by tx/ty.
        x = x * sx + tx
        y = y * sy + ty
        w = w * sx
        h = h * sy
        rx = rx * sx
        ry = ry * sy

        shapes.append({
            "ty": "rc",
            "nm": "rect",
            "d":  1,
            "p":  {"a": 0, "k": [x + w/2, y + h/2]},
            "s":  {"a": 0, "k": [w, h]},
            "r":  {"a": 0, "k": max(rx, ry)}
        })

    elif tag in ("ellipse", "circle"):
        cx = float(elem.get("cx", 0) or 0)
        cy = float(elem.get("cy", 0) or 0)
        if tag == "circle":
            rx = ry = float(elem.get("r", 0) or 0)
        else:
            rx = float(elem.get("rx", 0) or 0)
            ry = float(elem.get("ry", 0) or 0)

        cx = cx * sx + tx
        cy = cy * sy + ty
        rx = rx * sx
        ry = ry * sy

        shapes.append({
            "ty": "el",
            "nm": "ellipse",
            "d":  1,
            "p":  {"a": 0, "k": [cx, cy]},
            "s":  {"a": 0, "k": [rx*2, ry*2]}
        })

    elif tag == "path":
        d = elem.get("d", "")
        if d:
            subpaths = _split_svg_subpaths(d)
            for idx, sub_d in enumerate(subpaths):
                path_data = _svg_path_to_lottie_path(sub_d, tx, ty, sx, sy)
                if path_data and path_data.get("v"):
                    # SVG fills implicitly close open subpaths; match behavior.
                    if fill_val != "none" and len(path_data.get("v", [])) >= 3:
                        path_data["c"] = True
                    shapes.append({
                        "ty": "sh",
                        "nm": f"path_{idx}",
                        "ks": {
                            "a": 0,
                            "k": path_data
                        }
                    })

    elif tag in ("polygon", "polyline"):
        points = (elem.get("points", "") or "").strip()
        if points:
            nums = [
                float(n) for n in re.findall(
                    r"[-+]?[0-9]*\.?[0-9]+(?:[eE][-+]?[0-9]+)?", points
                    )
            ]
            if len(nums) >= 4:
                dparts = [f"M {nums[0]} {nums[1]}"]
                for i in range(2, len(nums) - 1, 2):
                    dparts.append(f"L {nums[i]} {nums[i+1]}")
                if tag == "polygon":
                    dparts.append("Z")
                d = " ".join(dparts)
                path_data = _svg_path_to_lottie_path(d, tx, ty, sx, sy)
                if path_data and path_data.get("v"):
                    if fill_val != "none" and len(path_data.get("v", [])) >= 3:
                        path_data["c"] = True
                    shapes.append({
                        "ty": "sh",
                        "nm": tag,
                        "ks": {"a": 0, "k": path_data}
                    })

    elif tag == "line":
        x1 = float(elem.get("x1", 0) or 0)
        y1 = float(elem.get("y1", 0) or 0)
        x2 = float(elem.get("x2", 0) or 0)
        y2 = float(elem.get("y2", 0) or 0)
        d = f"M {x1} {y1} L {x2} {y2}"
        path_data = _svg_path_to_lottie_path(d, tx, ty, sx, sy)
        if path_data and path_data.get("v"):
            shapes.append({
                "ty": "sh",
                "nm": "line",
                "ks": {"a": 0, "k": path_data}
            })

    if not shapes:
        return []

    result = list(shapes)

    if fill_color and fill_val != "none":
        result.append({
            "ty": "fl",
            "nm": "fill",
            "o":  {"a": 0, "k": round(fill_op * 100, 2)},
            "c":  {"a": 0, "k": fill_color},
            "r":  1
        })

    if stroke_color and stroke_w > 0 and stroke_val != "none":
        result.append({
            "ty": "st",
            "nm": "stroke",
            "o":  {"a": 0, "k": round(stroke_op * 100, 2)},
            "c":  {"a": 0, "k": stroke_color},
            "w":  {"a": 0, "k": stroke_w * sx},
            "lc": 2,
            "lj": 2,
            "ml": 4
        })

    return [{
        "ty": "gr",
        "nm": tag,
        "it": result + [{
            "ty": "tr",
            "p":  {"a": 0, "k": [0, 0]},
            "a":  {"a": 0, "k": [0, 0]},
            "s":  {"a": 0, "k": [100, 100]},
            "r":  {"a": 0, "k": 0},
            "o":  {"a": 0, "k": 100},
            "sk": {"a": 0, "k": 0},
            "sa": {"a": 0, "k": 0}
        }]
    }]


# ---------------------------------------------------------------------------
# SVG path → Lottie bezier
# ---------------------------------------------------------------------------

def _svg_path_to_lottie_path(d, tx, ty, sx, sy):
    vertices = []
    in_tangents = []
    out_tangents = []
    closed = False

    tokens = re.findall(
        r"[AaCcHhLlMmQqSsTtVvZz]|[-+]?[0-9]*\.?[0-9]+(?:[eE][-+]?[0-9]+)?",
        d
    )
    i = 0
    current = [0.0, 0.0]
    start_point = [0.0, 0.0]

    def is_num(tok):
        return re.match(r"^[-+]?[0-9]*\.?[0-9]+(?:[eE][-+]?[0-9]+)?$", tok)

    def ap(x, y):
        return [x * sx + tx, y * sy + ty]

    def append_line_to(x, y):
        nonlocal current
        current = [x, y]
        vertices.append(ap(x, y))
        in_tangents.append([0, 0])
        out_tangents.append([0, 0])

    last_cmd = None
    while i < len(tokens):
        cmd = tokens[i]
        if is_num(cmd):
            if last_cmd in ("M", "m"):
                cmd = "L" if last_cmd == "M" else "l"
            else:
                cmd = last_cmd
            if not cmd:
                i += 1
                continue
        else:
            i += 1
            last_cmd = cmd

        if cmd in ("M", "m"):
            x, y = float(tokens[i]), float(tokens[i+1])
            i += 2
            if cmd == "m":
                x += current[0]
                y += current[1]
            current = [x, y]
            start_point = [x, y]
            vertices.append(ap(x, y))
            in_tangents.append([0, 0])
            out_tangents.append([0, 0])
            while i + 1 < len(tokens) and is_num(tokens[i]) and is_num(tokens[i+1]):
                x, y = float(tokens[i]), float(tokens[i+1])
                i += 2
                if cmd == "m":
                    x += current[0]
                    y += current[1]
                append_line_to(x, y)

        elif cmd in ("L", "l"):
            while i + 1 < len(tokens) and is_num(tokens[i]) and is_num(tokens[i+1]):
                x, y = float(tokens[i]), float(tokens[i+1])
                i += 2
                if cmd == "l":
                    x += current[0]
                    y += current[1]
                append_line_to(x, y)

        elif cmd in ("H", "h"):
            while i < len(tokens) and is_num(tokens[i]):
                x = float(tokens[i])
                i += 1
                if cmd == "h":
                    x += current[0]
                append_line_to(x, current[1])

        elif cmd in ("V", "v"):
            while i < len(tokens) and is_num(tokens[i]):
                y = float(tokens[i])
                i += 1
                if cmd == "v":
                    y += current[1]
                append_line_to(current[0], y)

        elif cmd in ("C", "c"):
            while i + 5 < len(tokens) and all(is_num(tokens[j]) for j in range(i, i+6)):
                x1, y1 = float(tokens[i]), float(tokens[i+1])
                x2, y2 = float(tokens[i+2]), float(tokens[i+3])
                x, y = float(tokens[i+4]), float(tokens[i+5])
                i += 6
                if cmd == "c":
                    x1 += current[0]
                    y1 += current[1]
                    x2 += current[0]
                    y2 += current[1]
                    x += current[0]
                    y += current[1]
                if out_tangents:
                    px, py = vertices[-1]
                    out_tangents[-1] = [x1*sx+tx - px, y1*sy+ty - py]
                current = [x, y]
                pt = ap(x, y)
                vertices.append(pt)
                in_tangents.append([x2*sx+tx - pt[0], y2*sy+ty - pt[1]])
                out_tangents.append([0, 0])

        elif cmd in ("Q", "q", "S", "s", "T", "t", "A", "a"):
            # Fallback for complex commands: preserve endpoint as line so shapes stay visible.
            if cmd in ("Q", "q"):
                step = 4
            elif cmd in ("S", "s"):
                step = 4
            elif cmd in ("T", "t"):
                step = 2
            else:
                step = 7
            while i + step - 1 < len(tokens) and all(
                is_num(tokens[j]) for j in range(i, i + step)
            ):
                seg = [float(tokens[j]) for j in range(i, i + step)]
                i += step
                if cmd in ("Q", "q", "S", "s"):
                    x, y = seg[-2], seg[-1]
                elif cmd in ("T", "t"):
                    x, y = seg[0], seg[1]
                else:
                    x, y = seg[5], seg[6]
                if cmd.islower():
                    x += current[0]
                    y += current[1]
                append_line_to(x, y)

        elif cmd in ("Z", "z"):
            closed = True
            if vertices:
                current = [start_point[0], start_point[1]]

        else:
            # Unknown token, skip safely.
            continue

    if len(vertices) < 2:
        return None
    return {"v": vertices, "i": in_tangents, "o": out_tangents, "c": closed}


# ---------------------------------------------------------------------------
# Opacity keyframes
# ---------------------------------------------------------------------------

def _build_opacity_keyframes(opacity_values):
    keyframes = []
    prev = None
    for frame, val in opacity_values:
        if val != prev:
            keyframes.append((frame, val))
            prev = val

    if len(keyframes) == 1:
        return {"a": 0, "k": keyframes[0][1]}

    lottie_kfs = []
    for frame, val in keyframes:
        lottie_kfs.append({
            "t": frame,
            "s": [val],
            "h": 0,
            "i": {"x": [0.5], "y": [1]},
            "o": {"x": [0.5], "y": [0]}
        })
    # Final keyframe (no easing needed)
    last_frame, last_val = opacity_values[-1]
    lottie_kfs.append({"t": last_frame, "s": [last_val]})

    return {"a": 1, "k": lottie_kfs}


def _visible_frame_span(opacity_values, threshold=0.01):
    visible_frames = [frame for frame, val in opacity_values if val > threshold]
    if not visible_frames:
        first = opacity_values[0][0]
        return first, first + 1
    first = visible_frames[0]
    last = visible_frames[-1]
    # Lottie layer "op" is effectively end-exclusive.
    return first, last + 1


# ---------------------------------------------------------------------------
# Extension
# ---------------------------------------------------------------------------

def _has_tag(node_name, tag):
    lower = node_name.lower()
    return f"({tag.lower()})" in lower or f"[{tag.lower()}]" in lower


class LottieExportExtension(Extension):
    def __init__(self, parent):
        super().__init__(parent)

    def setup(self):
        pass

    def createActions(self, window):
        action = window.createAction(
            "lottieExport", "Export as Lottie JSON", "tools/scripts"
        )
        action.triggered.connect(self.run)

    def run(self):
        doc = Krita.instance().activeDocument()
        if doc is None:
            QMessageBox.warning(None, "Lottie Export", "No document is open.")
            return

        dlg = LottieExportDialog()
        if dlg.exec_() != LottieExportDialog.Accepted:
            return

        output_folder = dlg.get_output_folder()
        output_filename = dlg.get_output_filename()
        fps_override = dlg.get_fps_override()
        skip_invisible = dlg.should_skip_invisible()

        if not output_folder or not os.path.isdir(output_folder):
            QMessageBox.warning(
                None, "Lottie Export", "Please choose a valid output folder."
                )
            return
        dlg.save_settings()

        try:
            result_path = self._export(
                doc,
                output_folder,
                output_filename,
                fps_override,
                skip_invisible
                )
            QMessageBox.information(
                None, "Lottie Export", f"Export complete!\n\n{result_path}"
                )
        except Exception:
            import traceback
            QMessageBox.critical(
                None, "Lottie Export Error", traceback.format_exc()
                )

    def _export(
        self, doc, output_folder, output_filename, fps_override, skip_invisible
    ):
        width = doc.width()
        height = doc.height()
        fps = fps_override if fps_override > 0 else doc.framesPerSecond()
        start = doc.fullClipRangeStartTime()
        end = doc.fullClipRangeEndTime()

        if end <= start:
            raise ValueError(
                "Animation has no frames. Check your playback range in Krita."
                )

        root = doc.rootNode()
        layers = self._collect_layers(root, skip_invisible)

        if not layers:
            raise ValueError("No exportable vector layers found.")

        original_time = doc.currentTime()
        lottie_layers = []
        ind = 0

        for node in layers:
            layer_name = node.name()
            opacity_vals = []
            svg_at_start = None

            for frame in range(start, end + 1):
                doc.setCurrentTime(frame)
                doc.waitForDone()
                raw_op = node.opacity()
                opacity_vals.append((frame, round((raw_op / 255) * 100, 2)))
                if frame == start:
                    svg_at_start = node.toSvg()

            doc.setCurrentTime(original_time)

            if not svg_at_start:
                continue

            shape_items = self._parse_svg_shapes(svg_at_start, width, height)
            if not shape_items:
                continue

            opacity_prop = _build_opacity_keyframes(opacity_vals)
            layer_ip, layer_op = _visible_frame_span(opacity_vals)

            lottie_layers.append({
                "ddd":    0,
                "ty":     4,
                "nm":     layer_name,
                "ind":    ind,
                "st":     0,
                "ip":     layer_ip,
                "op":     layer_op,
                "sr":     1,
                "bm":     0,
                "shapes": shape_items,
                "ks": {
                    "a": {"a": 0, "k": [0, 0, 0]},
                    "p": {"a": 0, "k": [0, 0, 0]},
                    "s": {"a": 0, "k": [100, 100, 100]},
                    "r": {"a": 0, "k": 0},
                    "o": opacity_prop
                }
            })
            ind += 1

        if not lottie_layers:
            raise ValueError(
                "No shapes could be extracted. Make sure layers are Vector."
            )

        # Preserve Krita collection order directly.
        # In this document set, reversing placed the dense scene layer on top.
        for i, layer in enumerate(lottie_layers):
            layer["ind"] = i + 1
        lottie = {
            "v":      "5.7.1",
            "nm":     doc.name() or "Krita Export",
            "fr":     fps,
            "ip":     start,
            "op":     end,
            "w":      width,
            "h":      height,
            "ddd":    0,
            "assets": [],
            "layers": lottie_layers
        }

        out_path = os.path.join(output_folder, output_filename)
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(lottie, f, indent=2)

        return out_path

    def _parse_svg_shapes(self, svg_string, canvas_w, canvas_h):
        svg_string = re.sub(r"<!DOCTYPE[^>]*>", "", svg_string)
        try:
            root = ET.fromstring(svg_string)
        except ET.ParseError as e:
            raise ValueError(f"Could not parse SVG: {e}")

        vb_w, vb_h = _get_viewbox_size(svg_string)
        scale_x = canvas_w / vb_w
        scale_y = canvas_h / vb_h
        elems = list(root.iter())
        tag_counts = {}
        id_map = {}
        for elem in elems:
            tag = _strip_svg_ns(elem.tag)
            tag_counts[tag] = tag_counts.get(tag, 0) + 1
            elem_id = elem.get("id")
            if elem_id:
                id_map[elem_id] = elem

        shapes = []
        sample_logged = False

        def walk(elem, parent_matrix, parent_style):
            nonlocal sample_logged
            tag = _strip_svg_ns(elem.tag)
            local_matrix = _transform_to_matrix(elem.get("transform", ""))
            combined = _mat_mul(parent_matrix, local_matrix)
            local_style = _merge_style_ctx(parent_style, elem)

            if tag == "use":
                href = elem.get("href") or elem.get("{http://www.w3.org/1999/xlink}href")
                if href and href.startswith("#"):
                    ref = id_map.get(href[1:])
                    if ref is not None:
                        ux = float(elem.get("x", 0) or 0)
                        uy = float(elem.get("y", 0) or 0)
                        use_shift = _mat_mul(combined, [1, 0, 0, 1, ux, uy])
                        walk(ref, use_shift, local_style)

            if tag in ("rect", "path", "ellipse", "circle", "polygon", "polyline", "line"):
                converted = _svg_elem_to_lottie_shapes(
                    elem,
                    scale_x,
                    scale_y,
                    combined_matrix=combined,
                    style_ctx=local_style
                    )
                shapes.extend(converted)
                if converted and not sample_logged:
                    sample_logged = True

            for child in list(elem):
                child_tag = _strip_svg_ns(child.tag)
                if child_tag == "defs":
                    continue
                walk(child, combined, local_style)

        walk(root, [1, 0, 0, 1, 0, 0], None)

        if len(shapes) > 1:
            shapes = [
                s for s in shapes
                if not _is_full_canvas_white_rect(s, canvas_w, canvas_h)
            ]

        # Lottie shape stack order is opposite of SVG paint order in many players.
        # Reverse here so intra-layer overlap matches the source SVG.
        shapes.reverse()

        return shapes

    def _collect_layers(self, root, skip_invisible):
        result = []
        for node in root.childNodes():
            if _has_tag(node.name(), "Ignore"):
                continue
            if skip_invisible and not node.visible():
                continue
            if node.type() == "vectorlayer":
                result.append(node)
            elif node.type() == "grouplayer":
                result.extend(self._collect_layers(node, skip_invisible))
        return result
