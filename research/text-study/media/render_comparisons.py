"""Render synchronized vision and text reviewer maps from a numeric packet."""

import argparse
import concurrent.futures
import functools
import hashlib
import json
import math
import subprocess
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


BACKGROUND = "#111b24"
PANEL = "#1c2b36"
MAP_BACKGROUND = "#14232d"
WHITE = "#f0f5f7"
MUTED = "#a7bdc9"
WALL = "#637f90"
START = "#d9ad7e"
GOAL = "#a7e0a0"
PALETTES = {"vision": ("#69d9c9", "#224743"), "text": ("#ffc28a", "#49372c")}
MAP_SIZE = 326
COLUMN_WIDTH = 460
PANEL_HEIGHT = 398


def font_pair(regular, bold):
    paths = [
        (regular, bold),
        ("/usr/share/fonts/truetype/lato/Lato-Regular.ttf", "/usr/share/fonts/truetype/lato/Lato-Bold.ttf"),
        ("/System/Library/Fonts/Supplemental/Arial.ttf", "/System/Library/Fonts/Supplemental/Arial Bold.ttf"),
    ]
    for candidate in paths:
        if all(path and Path(path).is_file() for path in candidate):
            return tuple(str(path) for path in candidate)
    raise RuntimeError("Install Lato or pass --font and --bold-font")


@functools.lru_cache(maxsize=64)
def font(path, size):
    return ImageFont.truetype(path, size)


class MapView:
    def __init__(self, board):
        self.board = board
        points = [point for segment in board["walls"] for point in segment]
        xmin = min(point[0] for point in points)
        xmax = max(point[0] for point in points)
        ymin = min(point[1] for point in points)
        ymax = max(point[1] for point in points)
        scale = min((MAP_SIZE - 30) / (xmax - xmin), (MAP_SIZE - 30) / (ymax - ymin))

        def project(point):
            return (
                MAP_SIZE / 2 + (point[0] - (xmin + xmax) / 2) * scale,
                MAP_SIZE / 2 + (point[1] - (ymin + ymax) / 2) * scale,
            )

        self.centers = [project(point) for point in board["centers"]]
        self.base = Image.new("RGB", (MAP_SIZE, MAP_SIZE), MAP_BACKGROUND)
        draw = ImageDraw.Draw(self.base)
        for first, second in board["walls"]:
            draw.line((project(first), project(second)), fill=WALL, width=2)

    def markers(self, draw, cell, heading):
        for key, fill, radius in ((self.board["initial"]["cell"], START, 4), (self.board["goal"], GOAL, 5)):
            x, y = self.centers[key]
            draw.ellipse((x - radius, y - radius, x + radius, y + radius), fill=fill,
                         outline=MAP_BACKGROUND, width=1)
        x, y = self.centers[cell]
        angle = heading * 2 * math.pi / self.board["headings"]
        if self.board["shape"] == "hex":
            angle += math.pi / 6
        arrow = [
            (x + radius * math.cos(angle + offset), y + radius * math.sin(angle + offset))
            for radius, offset in ((10, 0), (7, 2.48), (7, -2.48))
        ]
        draw.polygon(arrow, fill=WHITE, outline=MAP_BACKGROUND)


class PanelView:
    def __init__(self, panel, map_view):
        self.panel = panel
        self.map_view = map_view
        self.color, self.badge_background = PALETTES[panel["mode"]]
        self.map_image = map_view.base.copy()
        self.map_draw = ImageDraw.Draw(self.map_image)
        self.visited_edges = set()
        self.path_length = 1

    def map_frame(self, state):
        cell, heading, _, _, _, length = state
        assert self.path_length <= length
        path = self.panel["path"]
        for index in range(self.path_length, length):
            first, second = path[index - 1], path[index]
            edge = (min(first, second), max(first, second))
            if edge not in self.visited_edges:
                self.visited_edges.add(edge)
                self.map_draw.line((self.map_view.centers[first], self.map_view.centers[second]),
                                   fill=self.color, width=3)
        self.path_length = length
        image = self.map_image.copy()
        self.map_view.markers(ImageDraw.Draw(image), cell, heading)
        return image


class CaseView:
    def __init__(self, case, models, settings, fonts):
        self.case = case
        self.models = models
        self.settings = settings
        self.fonts = fonts
        self.map = MapView(case["map"])
        self.panels = [PanelView(panel, self.map) for panel in case["panels"]]
        self.template = self.build_template()
        self.previous_round = None
        self.previous_frame = None

    def text(self, draw, xy, value, size, color=WHITE, bold=False, anchor=None):
        draw.text(xy, str(value), fill=color, font=font(self.fonts[int(bold)], size), anchor=anchor)

    def build_template(self):
        image = Image.new("RGB", (self.settings["width"], self.settings["height"]), BACKGROUND)
        draw = ImageDraw.Draw(image)
        difficulty, shape, identifier = self.case["key"].split("-", 2)
        shape_name = "Square" if shape == "square" else "Hexagonal"
        self.text(draw, (32, 22), "Mirror Maze / Vision and text", 42, bold=True)
        subtitle = f"{difficulty.title()} / {shape_name} / {identifier} / {len(self.map.centers)} cells"
        self.text(draw, (34, 81), subtitle, 24, MUTED)
        self.text(draw, (1884, 31), "SHARED PLANNING ROUND", 16, MUTED, True, "ra")
        draw.line((22, 118, 1898, 118), fill="#3d5260", width=1)

        for column, (name, client) in enumerate(self.models.values()):
            x = 22 + column * 472
            self.text(draw, (x + 12, 129), name, 25, bold=True)
            self.text(draw, (x + 12, 160), client, 18, MUTED)

        for index, panel in enumerate(self.panels):
            x, y = self.panel_origin(index)
            draw.rounded_rectangle((x, y, x + COLUMN_WIDTH, y + PANEL_HEIGHT), radius=12, fill=PANEL)
            draw.rounded_rectangle((x + 14, y + 12, x + 100, y + 41), radius=6,
                                   fill=panel.badge_background)
            self.text(draw, (x + 25, y + 17), panel.panel["mode"].upper(), 17, panel.color, True)
            if panel.panel["source_label"]:
                self.text(draw, (x + 111, y + 18), panel.panel["source_label"], 16, MUTED)
            draw.line((x + 346, y + 62, x + 346, y + 365), fill="#405361", width=1)
            for number, label in enumerate(("PLANS", "ACTIONS", "WALL HITS", "VISITED")):
                self.text(draw, (x + 357, y + 66 + number * 73), label, 14, MUTED, True)

        self.text(draw, (32, 1035), "Reviewer maps. Vision receives first-person images and action outcomes. Text receives action outcomes only.",
                  17, MUTED)
        for x, color, label in ((1474, START, "Start"), (1578, GOAL, "Goal")):
            draw.ellipse((x, 1041, x + 10, 1051), fill=color)
            self.text(draw, (x + 17, 1036), label, 16, MUTED)
        draw.polygon([(1706, 1046), (1693, 1039), (1693, 1053)], fill=WHITE)
        self.text(draw, (1715, 1036), "Agent heading", 16, MUTED)
        return image

    @staticmethod
    def panel_origin(index):
        return 22 + index % 4 * 472, 188 + index // 4 * 408

    def frame(self, number):
        if number == self.previous_round:
            return self.previous_frame
        image = self.template.copy()
        draw = ImageDraw.Draw(image)
        cap = self.case["cap"]
        self.text(draw, (1884, 53), f"{number:,} / {cap:,}", 39, bold=True, anchor="ra")
        for index, panel in enumerate(self.panels):
            x, y = self.panel_origin(index)
            record = panel.panel
            stop = record["planning"]
            shown = min(stop, number)
            state = record["rounds"][shown]
            image.paste(panel.map_frame(state), (x + 13, y + 50))
            status, color = "Exploring", MUTED
            if number >= stop:
                status, color = ("Goal reached", GOAL) if record["status"] == "success" else ("Limit reached", "#f4bc89")
            self.text(draw, (x + COLUMN_WIDTH - 13, y + 16), status, 19, color, True, "ra")
            metrics = (f"{shown:,}", f"{state[2]:,}", f"{state[3]:,}", f"{state[4]}/{len(self.map.centers)}")
            for position, value in enumerate(metrics):
                self.text(draw, (x + 357, y + 86 + position * 73), value, 24, WHITE, True)

        draw.rounded_rectangle((28, 1009, 1892, 1017), radius=4, fill="#3b5260")
        if number:
            draw.rounded_rectangle((28, 1009, 28 + round(1864 * number / cap), 1017), radius=4, fill="#aac6dc")
        self.previous_round, self.previous_frame = number, image
        return image


def inspect_video(path, settings):
    command = ["ffprobe", "-v", "error", "-show_entries",
               "stream=codec_name,codec_type,width,height,pix_fmt,r_frame_rate,nb_frames:format=duration",
               "-of", "json", str(path)]
    information = json.loads(subprocess.check_output(command))
    assert len(information["streams"]) == 1
    stream = information["streams"][0]
    count = settings["intro_frames"] + settings["progress_frames"] + settings["hold_frames"]
    duration = count / settings["fps"]
    assert stream["codec_name"] == "h264" and stream["codec_type"] == "video"
    assert (stream["width"], stream["height"]) == (settings["width"], settings["height"])
    assert stream["pix_fmt"] == "yuv420p" and stream["r_frame_rate"] == f"{settings['fps']}/1"
    assert int(stream["nb_frames"]) == count
    assert float(information["format"]["duration"]) == duration
    return {"codec": "h264", "pixel_format": "yuv420p", "resolution": [stream["width"], stream["height"]],
            "fps": settings["fps"], "frames": count, "duration_seconds": duration}


def render_case(job):
    case, models, settings, fonts, out = job
    view = CaseView(case, models, settings, fonts)
    destination = out / f"{case['key']}.mp4"
    temporary = destination.with_suffix(".partial.mp4")
    dimensions = f"{settings['width']}x{settings['height']}"
    command = ["ffmpeg", "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24",
               "-s", dimensions, "-r", str(settings["fps"]), "-i", "-", "-an", "-c:v", "libx264",
               "-preset", "veryfast", "-threads", "2", "-crf", str(settings["crf"]), "-pix_fmt", "yuv420p",
               "-map_metadata", "-1", "-movflags", "+faststart", str(temporary)]
    progress = settings["progress_frames"]
    rounds = [0] * settings["intro_frames"]
    rounds.extend(math.ceil(case["cap"] * tick / progress) for tick in range(1, progress + 1))
    rounds.extend([case["cap"]] * settings["hold_frames"])
    process = subprocess.Popen(command, stdin=subprocess.PIPE)
    try:
        for number in rounds:
            process.stdin.write(view.frame(number).tobytes())
        process.stdin.close()
        if process.wait() != 0:
            raise RuntimeError(f"FFmpeg failed for {case['key']}")
        encoding = inspect_video(temporary, settings)
        temporary.replace(destination)
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()
        temporary.unlink(missing_ok=True)

    result = {
        "case": case["key"],
        "path": f"videos/{destination.name}",
        "bytes": destination.stat().st_size,
        "sha256": hashlib.sha256(destination.read_bytes()).hexdigest(),
        "planning_axis": [0, case["cap"]],
        **encoding,
    }
    print(json.dumps(result), flush=True)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--font")
    parser.add_argument("--bold-font")
    parser.add_argument("--preview-case")
    parser.add_argument("--preview-round", type=int, default=0)
    args = parser.parse_args()
    source = args.input.read_bytes()
    packet = json.loads(source)
    assert packet["schema"] == "mirror-maze-comparison-video/v1"
    fonts = font_pair(args.font, args.bold_font)
    settings = packet["settings"]
    assert (settings["width"], settings["height"]) == (1920, 1080)
    args.out.mkdir(parents=True, exist_ok=True)
    if args.preview_case:
        case = next(case for case in packet["cases"] if case["key"] == args.preview_case)
        assert 0 <= args.preview_round <= case["cap"]
        view = CaseView(case, packet["models"], settings, fonts)
        view.frame(args.preview_round).save(args.out / "preview.png")
        return
    video_directory = args.out / "videos"
    video_directory.mkdir(exist_ok=True)
    jobs = [(case, packet["models"], settings, fonts, video_directory) for case in packet["cases"]]
    with concurrent.futures.ProcessPoolExecutor(max_workers=args.workers) as pool:
        records = list(pool.map(render_case, jobs))
    manifest = {
        "schema": "mirror-maze-comparison-media/v1",
        "input_sha256": hashlib.sha256(source).hexdigest(),
        "renderer_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "settings": settings,
        "models": packet["models"],
        "video_count": len(records),
        "videos": records,
    }
    (args.out / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")


if __name__ == "__main__":
    main()
