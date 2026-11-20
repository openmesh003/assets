"""Render catalog thumbnails for every model in catalog.json.

Run headless through Blender (needs Blender 4.2+):

    blender --background --factory-startup --python scripts/render_thumbnails.py -- [options]

Options (after the `--`):
    --force            Re-render even if the thumbnail is newer than the model.
    --only ID [ID...]  Only render these catalog ids.
    --size N           Square output size in pixels (default 512).
    --samples N        Cycles samples per pixel (default 64).
    --cpu              Do not try GPU rendering.

Each model is imported, framed from a fixed 3/4 angle, lit with a simple
studio rig and rendered on a transparent background (no floor shadow).
Output goes to thumbnails/<asset basename>.png and the model's
"thumbnail" field in catalog.json is filled in when it is missing. Each
entry's "formats" map is also refreshed first (see sync_catalog.py).
"""

import argparse
import math
import os
import sys
import time

import bpy
from mathutils import Vector

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from sync_catalog import ROOT, load_catalog, save_catalog, sync_formats  # noqa: E402

# Camera looks down at the model from the front-right, like a product shot.
VIEW_AZIMUTH_DEG = 35.0
VIEW_ELEVATION_DEG = 25.0
CAMERA_LENS_MM = 70.0
FRAME_MARGIN = 1.2  # >1 leaves padding around the model
CLAY_COLOR = (0.42, 0.45, 0.5, 1.0)  # linear RGB for meshes without materials


def parse_args():
    argv = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []
    p = argparse.ArgumentParser(prog="render_thumbnails.py")
    p.add_argument("--force", action="store_true")
    p.add_argument("--only", nargs="+", metavar="ID")
    p.add_argument("--size", type=int, default=512)
    p.add_argument("--samples", type=int, default=64)
    p.add_argument("--cpu", action="store_true")
    return p.parse_args(argv)


def enable_gpu(scene):
    """Use the first available GPU backend for Cycles; fall back to CPU."""
    try:
        prefs = bpy.context.preferences.addons["cycles"].preferences
    except KeyError:
        return "CPU"
    for backend in ("OPTIX", "CUDA", "HIP", "ONEAPI", "METAL"):
        try:
            prefs.compute_device_type = backend
        except TypeError:
            continue
        prefs.get_devices()
        gpus = [d for d in prefs.devices if d.type == backend]
        if gpus:
            for d in prefs.devices:
                d.use = d.type == backend
            scene.cycles.device = "GPU"
            return backend
    prefs.compute_device_type = "NONE"
    scene.cycles.device = "CPU"
    return "CPU"


def reset_scene():
    bpy.ops.wm.read_factory_settings(use_empty=True)
    return bpy.context.scene


def setup_render(scene, args):
    scene.render.engine = "CYCLES"
    scene.cycles.samples = args.samples
    scene.cycles.use_denoising = True
    scene.cycles.use_adaptive_sampling = True
    scene.render.resolution_x = args.size
    scene.render.resolution_y = args.size
    scene.render.resolution_percentage = 100
    scene.render.film_transparent = True
    scene.render.image_settings.file_format = "PNG"
    scene.render.image_settings.color_mode = "RGBA"
    scene.render.image_settings.compression = 90
    scene.view_settings.view_transform = "AgX"
    scene.view_settings.look = "AgX - Base Contrast"
    return "CPU" if args.cpu else enable_gpu(scene)


def import_model(path):
    before = set(bpy.data.objects)
    bpy.ops.import_scene.gltf(filepath=path)
    return [o for o in bpy.data.objects if o not in before]


def apply_fallback_material(objects):
    """Give untextured meshes a neutral clay material; models with materials keep theirs."""
    clay = None
    for obj in objects:
        if obj.type != "MESH" or any(slot.material for slot in obj.material_slots):
            continue
        if clay is None:
            clay = bpy.data.materials.new("ThumbClay")
            if not clay.node_tree:
                clay.use_nodes = True
            bsdf = clay.node_tree.nodes["Principled BSDF"]
            bsdf.inputs["Base Color"].default_value = CLAY_COLOR
            bsdf.inputs["Roughness"].default_value = 0.55
        obj.data.materials.clear()
        obj.data.materials.append(clay)


def world_bounds(objects):
    depsgraph = bpy.context.evaluated_depsgraph_get()
    points = []
    for obj in objects:
        if obj.type != "MESH":
            continue
        ev = obj.evaluated_get(depsgraph)
        points.extend(ev.matrix_world @ Vector(c) for c in ev.bound_box)
    if not points:
        raise RuntimeError("model contains no mesh geometry")
    lo = Vector((min(p.x for p in points), min(p.y for p in points), min(p.z for p in points)))
    hi = Vector((max(p.x for p in points), max(p.y for p in points), max(p.z for p in points)))
    return lo, hi


def bbox_corners(lo, hi):
    return [Vector((x, y, z)) for x in (lo.x, hi.x) for y in (lo.y, hi.y) for z in (lo.z, hi.z)]


def add_camera(scene, lo, hi):
    cam_data = bpy.data.cameras.new("ThumbCam")
    cam_data.lens = CAMERA_LENS_MM
    cam = bpy.data.objects.new("ThumbCam", cam_data)
    scene.collection.objects.link(cam)
    scene.camera = cam

    az = math.radians(VIEW_AZIMUTH_DEG)
    el = math.radians(VIEW_ELEVATION_DEG)
    # Blender: -Y is front, +Z is up. Direction from target toward camera.
    direction = Vector((math.sin(az) * math.cos(el), -math.cos(az) * math.cos(el), math.sin(el)))
    cam.rotation_euler = (-direction).to_track_quat("-Z", "Y").to_euler()

    # Let Blender find the tightest camera position for the bounding box,
    # then back off along the view axis to leave a margin.
    center = (lo + hi) / 2
    corners = bbox_corners(lo, hi)
    flat = [c for v in corners for c in v]
    bpy.context.view_layer.update()
    location, _ = cam.camera_fit_coords(bpy.context.evaluated_depsgraph_get(), flat)
    dist = (Vector(location) - center).length
    cam.location = center + direction * dist * FRAME_MARGIN

    size = (hi - lo).length
    cam_data.clip_start = max(size * 0.001, 0.0001)
    cam_data.clip_end = size * 100 + dist * 10
    return direction


def add_area_light(scene, name, target, direction, distance, size, energy, color=(1, 1, 1)):
    data = bpy.data.lights.new(name, "AREA")
    data.shape = "DISK"
    data.size = size
    data.energy = energy
    data.color = color
    light = bpy.data.objects.new(name, data)
    light.location = target + direction.normalized() * distance
    light.rotation_euler = (-direction).to_track_quat("-Z", "Y").to_euler()
    scene.collection.objects.link(light)


def add_lighting(scene, lo, hi):
    center = (lo + hi) / 2
    radius = max((hi - lo).length / 2, 1e-4)
    dist = radius * 4
    # Light power scales with distance squared so every model gets the same exposure.
    k = dist * dist
    add_area_light(scene, "Key", center, Vector((1.0, -1.2, 1.4)), dist, radius * 3, 12 * k, (1.0, 0.97, 0.92))
    add_area_light(scene, "Fill", center, Vector((-1.4, -0.8, 0.5)), dist, radius * 4, 4 * k, (0.92, 0.95, 1.0))
    add_area_light(scene, "Rim", center, Vector((-0.4, 1.5, 1.0)), dist, radius * 2, 8 * k)

    world = bpy.data.worlds.new("ThumbWorld")
    if not world.node_tree:  # Blender < 5 needs this to create the node tree
        world.use_nodes = True
    bg = world.node_tree.nodes.get("Background")
    bg.inputs["Color"].default_value = (0.8, 0.82, 0.85, 1.0)
    bg.inputs["Strength"].default_value = 0.25
    scene.world = world


def thumbnail_path(entry):
    base = os.path.splitext(os.path.basename(entry["asset"]))[0]
    return entry.get("thumbnail") or f"thumbnails/{base}.png"


def needs_render(model_path, thumb_path, force):
    if force or not os.path.exists(thumb_path):
        return True
    return os.path.getmtime(model_path) > os.path.getmtime(thumb_path)


def render_one(entry, args, thumb_rel):
    model_path = os.path.join(ROOT, entry["asset"])
    out_path = os.path.join(ROOT, thumb_rel)
    scene = reset_scene()
    device = setup_render(scene, args)
    objects = import_model(model_path)
    apply_fallback_material(objects)
    lo, hi = world_bounds(objects)
    add_camera(scene, lo, hi)
    add_lighting(scene, lo, hi)
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    scene.render.filepath = out_path
    bpy.ops.render.render(write_still=True)
    return device


def main():
    args = parse_args()
    catalog, original = load_catalog()
    for change in sync_formats(catalog):
        print(f"[formats] {change}")

    models = catalog["models"]
    if args.only:
        known = {m["id"] for m in models}
        missing = sorted(set(args.only) - known)
        if missing:
            print(f"Unknown catalog id(s): {', '.join(missing)}", file=sys.stderr)
            sys.exit(2)
        models = [m for m in models if m["id"] in args.only]

    rendered, skipped, failed = 0, 0, []
    for entry in models:
        thumb_rel = thumbnail_path(entry)
        model_path = os.path.join(ROOT, entry["asset"])
        if not os.path.exists(model_path):
            failed.append((entry["id"], f"missing asset {entry['asset']}"))
            continue
        if not needs_render(model_path, os.path.join(ROOT, thumb_rel), args.force):
            skipped += 1
            entry.setdefault("thumbnail", thumb_rel)
            continue
        start = time.time()
        try:
            device = render_one(entry, args, thumb_rel)
        except Exception as exc:  # keep going so one broken model doesn't stop the batch
            failed.append((entry["id"], str(exc)))
            continue
        entry.setdefault("thumbnail", thumb_rel)
        rendered += 1
        print(f"[thumb] {entry['id']:<24} -> {thumb_rel}  ({time.time() - start:.1f}s, {device})")

    if save_catalog(catalog, original):
        print("[thumb] updated catalog.json")
    print(f"[thumb] rendered {rendered}, up to date {skipped}, failed {len(failed)}")
    for model_id, reason in failed:
        print(f"[thumb] FAILED {model_id}: {reason}", file=sys.stderr)
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
