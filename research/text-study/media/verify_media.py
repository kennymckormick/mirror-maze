"""Decode each comparison film and build reviewer contact sheets."""

import argparse
import hashlib
import io
import json
import re
import subprocess
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw

from render_comparisons import font, font_pair, inspect_video


EXCLUDED_TEXT = re.compile(r"Seed[\s._-]*2[\s._-]*1|Kenny|[\u3400-\u9fff]", re.I)


def sample(path, timestamp):
    command = ["ffmpeg", "-v", "error", "-threads", "2", "-ss", str(timestamp), "-i", str(path),
               "-frames:v", "1", "-vf", "scale=640:360", "-f", "image2pipe", "-vcodec", "png", "-"]
    with Image.open(io.BytesIO(subprocess.check_output(command))) as image:
        return image.convert("RGB")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--contact-dir", type=Path, required=True)
    args = parser.parse_args()
    args.contact_dir.mkdir(parents=True, exist_ok=True)

    manifest_path = args.root / "manifest.json"
    assert not EXCLUDED_TEXT.search(manifest_path.read_text())
    assert not EXCLUDED_TEXT.search(args.input.read_text())
    manifest = json.loads(manifest_path.read_text())
    assert manifest["input_sha256"] == hashlib.sha256(args.input.read_bytes()).hexdigest()
    assert manifest["video_count"] == len(manifest["videos"]) == 8
    assert len(manifest["models"]) == 4
    checks = []
    expected_files = {str(path.relative_to(args.root)) for path in (args.root / "videos").glob("*.mp4")}
    assert expected_files == {record["path"] for record in manifest["videos"]}

    for difficulty in ("easy", "medium"):
        records = [record for record in manifest["videos"] if record["case"].startswith(difficulty + "-")]
        assert len(records) == 4
        sheet = Image.new("RGB", (1920, 4 * 386), "#f0f4f6")
        draw = ImageDraw.Draw(sheet)
        title_font = font(font_pair(None, None)[0], 19)
        for row, record in enumerate(records):
            path = args.root / record["path"]
            assert hashlib.sha256(path.read_bytes()).hexdigest() == record["sha256"]
            assert path.stat().st_size == record["bytes"]
            encoding = inspect_video(path, manifest["settings"])
            assert all(record[key] == value for key, value in encoding.items())
            subprocess.run(["ffmpeg", "-v", "error", "-xerror", "-threads", "2", "-i", str(path),
                            "-map", "0:v:0", "-f", "null", "-"], check=True)
            images = []
            for column, (label, timestamp) in enumerate((("start", 0.25), ("middle", 10), ("end", 19.5))):
                image = sample(path, timestamp)
                assert image.size == (640, 360) and image.getbbox()
                sheet.paste(image, (column * 640, row * 386 + 26))
                draw.text((column * 640 + 7, row * 386 + 2), f"{record['case']} / {label}",
                          font=title_font, fill="#142b33")
                images.append(image)
            assert ImageChops.difference(images[0], images[-1]).getbbox()
            checks.append({"case": record["case"], "full_decode_passed": True,
                           "sample_seconds": [0.25, 10, 19.5], "sha256": record["sha256"]})
        sheet.save(args.contact_dir / f"{difficulty}-start-middle-end.jpg", quality=91)

    result = {"verified_videos": len(checks), "decoded_frame_samples": 24,
              "all_file_hashes_match": True, "checks": checks}
    (args.root / "qa.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({key: value for key, value in result.items() if key != "checks"}))


if __name__ == "__main__":
    main()
