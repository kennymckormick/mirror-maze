#!/usr/bin/env python3
"""Export the audited text-only Mirror Maze cohort without network access.

Run against the canonical completed run and the existing public map directory.
The public JSON contains only the four approved model families. Keep the
separate audit receipt private. Round zero is the initial pose. Each subsequent
row is a planning-round end, and pathLength includes the starting cell.
"""

import argparse
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import tempfile


PROTOCOL = "text-action-feedback-native-v1"
PUBLIC_MODELS = {
    "gpt": "GPT-6.1 Sol",
    "gemini38": "Gemini 3.8 Flash",
    "deepseek": "DeepSeek V4.1 Flash 1017",
    "kimi": "Kimi K3.1 Pro",
}
HARNESSES = {
    "gpt": "Codex",
    "gemini38": "AGY",
    "deepseek": "Claude Code",
    "kimi": "Claude Code",
}
ALL_MODELS = set(PUBLIC_MODELS) | {"seed"}
LEVELS = {"easy": 200, "medium": 400}
SIDES = {"square": {"easy": 7, "medium": 11}, "hex": {"easy": 5, "medium": 7}}
SEEDS = ("MIRROR-042", "MIRROR-043")
OUTCOMES = ("success", "planning_cap")
ROUND_FIELDS = ("cell", "heading", "actionCount", "physicalCollisions", "visited", "pathLength")


def require(condition, message):
    if not condition:
        raise ValueError(message)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def read_json(path):
    raw = path.read_bytes()
    return json.loads(raw), digest(raw)


def map_key(row):
    return f"{row['difficulty']}-{row['topology']}-{row['seed']}"


def pose(state):
    return {"cell": state["cell"], "heading": state["heading"]}


def round_end(state, path_length):
    return [state[key] for key in ("cell", "heading", "actionCount", "collisions", "visited")] + [path_length]


def verify_completion(run_dir):
    receipt, receipt_sha = read_json(run_dir / "complete.json")
    manifest, manifest_sha = read_json(run_dir / "manifest.json")
    rollup, rollup_sha = read_json(run_dir / "progress.json")
    require(receipt.get("complete") is True and receipt.get("audited") is True, "The completion gate is closed")
    require(receipt.get("skip_policy_audited") is True, "The difficulty policy is not audited")
    require(receipt["manifest_sha256"] == manifest_sha, "Completion manifest digest mismatch")
    require(receipt["progress_sha256"] == rollup_sha, "Completion progress digest mismatch")
    require(all(receipt[key] == 40 for key in ("targets", "fresh", "resolved")), "Incomplete Text40 receipt")
    require(all(receipt[key] == 0 for key in ("reused", "skipped", "withdrawn")), "Unexpected unexecuted or reused case")
    require(rollup["all_complete"] and rollup["complete_targets"] == 40, "Campaign progress is incomplete")
    require(manifest["observation_modality"] == "text" and manifest["rendering_disabled"], "The manifest is not text-only")
    require(set(manifest["public_launch_models"]) == set(PUBLIC_MODELS), "Unexpected public model policy")
    expected = {
        (model, level, shape, seed)
        for model in ALL_MODELS
        for level in LEVELS
        for shape in SIDES
        for seed in SEEDS
    }
    actual = {(r["model"], r["difficulty"], r["topology"], r["seed"]) for r in manifest["rows"]}
    require(len(manifest["rows"]) == 40 and actual == expected, "The manifest matrix differs from Text40")
    require(len({r["id"] for r in manifest["rows"]}) == 40, "Duplicate manifest case ID")
    require(len(rollup["rows"]) == 40, "The completion rollup does not cover forty cases")
    hashes = {"complete.json": receipt_sha, "manifest.json": manifest_sha, "progress.json": rollup_sha}
    return manifest, rollup, hashes


def verify_map(row, episode, map_data):
    label = row["id"]
    shape, level = row["topology"], row["difficulty"]
    side = SIDES[shape][level]
    count = side * side if shape == "square" else 1 + 3 * side * (side - 1)
    geometry = episode["geometry"]
    options = episode["options"]
    require(row["side_length"] == side and row["cells"] == count, f"{label}: unexpected geometry in manifest")
    require(options["topology"] == shape and options["seed"] == row["seed"], f"{label}: episode map options differ")
    require(geometry["sideLength"] == side and geometry["cellCount"] == count, f"{label}: episode geometry differs")
    require(map_data["shape"] == shape and map_data["headings"] == (4 if shape == "square" else 6), f"{label}: map headings differ")
    require(len(map_data["cells"]) == count and episode["maze"] == map_data["cells"], f"{label}: map cell geometry differs")
    require(episode["initial"] == map_data["initial"] and episode["goal"] == map_data["goal"], f"{label}: map start or goal differs")
    return {
        cell["id"]: {edge["neighbor"] for edge in cell["edges"] if edge["open"]}
        for cell in map_data["cells"]
    }


def compact_replay(label, events, episode, map_data, open_neighbors):
    current = events[0]["before"]
    require(pose(current) == map_data["initial"], f"{label}: initial replay pose differs")
    require(round_end(current, 1)[2:] == [0, 0, 1, 1], f"{label}: unexpected initial replay counters")
    path = [current["cell"]]
    visited = {current["cell"]}
    rounds = [round_end(current, len(path))]
    actions = forwards = walls = invalid = 0
    for number, event in enumerate(events, start=1):
        require(event["round"] == number and event["before"] == current, f"{label}: round {number} is discontinuous")
        require(event["observation_modality"] == "text" and event["input_frames"] == [], f"{label}: nontext observation")
        require(event["text_delivery_verified"] is True, f"{label}: unverified feedback delivery")
        if event["invalid"]:
            require(not event["steps"], f"{label}: rejected sequence moved the agent")
            invalid += 1
        for step in event["steps"]:
            action, result, state = step["action"], step["result"], step["state"]
            require(action in ("left", "right", "forward"), f"{label}: unknown primitive action")
            physical = episode["actions"][actions]
            require(physical["action"] == action and physical["before"] == pose(current), f"{label}: episode action start differs")
            require(physical["after"] == pose(state), f"{label}: episode action end differs")
            moved, collision = result["moved"], result["collision"]
            require(physical["moved"] == moved and physical["collision"] == collision, f"{label}: episode action outcome differs")
            require(moved == (state["cell"] != current["cell"]), f"{label}: movement flag and pose disagree")
            if moved:
                require(action == "forward" and not collision, f"{label}: unexpected movement action")
                require(state["cell"] in open_neighbors[current["cell"]], f"{label}: path crosses a closed map edge")
                path.append(state["cell"])
                visited.add(state["cell"])
            if collision:
                require(action == "forward" and not moved, f"{label}: unexpected physical wall hit")
                walls += 1
            actions += 1
            forwards += int(action == "forward")
            require(state["actionCount"] == actions and state["collisions"] == walls, f"{label}: physical counters differ")
            require(state["visited"] == len(visited) and state["moves"] == len(path) - 1, f"{label}: path counters differ")
            current = state
        require(event["after"] == current, f"{label}: round {number} end differs")
        rounds.append(round_end(current, len(path)))
    require(actions == len(episode["actions"]), f"{label}: episode action count differs")
    require(all(type(value) is int for record in rounds for value in record), f"{label}: noninteger replay value")
    return rounds, path, actions, forwards, walls, invalid


def verify_and_project_case(run_dir, row, rollup_row, map_data, map_sha):
    label = row["id"]
    expected_id = f"text-harness-{row['difficulty']}-{row['model']}-{row['topology']}-{row['seed']}"
    require(label == expected_id and row["mode"] == "harness" and row["kind"] == "fresh", f"Unexpected case identity: {label}")
    case = Path(row["case_dir"]).resolve()
    require(case.is_relative_to(run_dir / "cases"), f"{label}: case path escapes the canonical run")
    files = {}
    hashes = {}
    for name in ("result", "validation", "audited", "progress", "episode", "wire"):
        files[name], hashes[name + ".json"] = read_json(case / (name + ".json"))
    result, validation, marker = (files[key] for key in ("result", "validation", "audited"))
    progress, episode, wire = (files[key] for key in ("progress", "episode", "wire"))
    require(marker["complete"] is True and marker["audit_passed"] is True, f"{label}: case audit is incomplete")
    for name in ("result", "validation"):
        require(marker[name + "_sha256"] == hashes[name + ".json"], f"{label}: audited {name} digest differs")
    require(validation["all_checked_invariants_passed"] is True, f"{label}: validation failed")
    for key in ("all_requests_text_only", "feedback_whitelist_verified", "source_faults_never_scored"):
        require(validation[key] is True, f"{label}: missing {key} proof")
    require(result["protocol"] == validation["protocol"] == progress["protocol"] == PROTOCOL, f"{label}: protocol differs")
    require(result["images_sent"] == validation["images_sent"] == result["render_calls"] == 0, f"{label}: nonzero image or render count")
    require(not episode["renderHistory"], f"{label}: nonempty render history")
    require(all(call["images"] == [] for call in wire), f"{label}: a wire record contains images")
    require(all(call.get("no_image_payload") is True for call in wire if call.get("private_request_file")), f"{label}: missing request image exclusion")
    events = progress["events"]
    planning = len(events)
    require(0 < planning == result["planning_rounds"] == validation["replayed_planning_rounds"], f"{label}: planning count differs")
    cap = row["target_cap"]
    require(cap == result["planning_cap"] == LEVELS[row["difficulty"]] and planning <= cap, f"{label}: cap differs")
    status = result["status"]
    require(status in OUTCOMES and status == progress["status"] == rollup_row["status"], f"{label}: terminal status differs")
    require(result.get("error") is None and progress.get("error") is None, f"{label}: terminal case has an infrastructure error")
    require(rollup_row["audited"] is True and rollup_row["planning_rounds"] == planning, f"{label}: rollup is unaudited")
    require(status != "planning_cap" or planning == cap, f"{label}: limit reached early")
    neighbors = verify_map(row, episode, map_data)
    rounds, path, actions, forwards, walls, invalid = compact_replay(label, events, episode, map_data, neighbors)
    final = events[-1]["after"]
    require(final == episode["state"] == progress["state"], f"{label}: final state differs")
    reached = status == "success"
    require(reached == result["success"] == final["success"] == (final["cell"] == map_data["goal"]), f"{label}: terminal goal differs")
    require(actions == result["actions"] == rollup_row["actions"], f"{label}: action total differs")
    require(walls + invalid == result["collisions"] == progress["total_collisions"], f"{label}: collision total differs")
    require(invalid == result["invalid_sequences"] == progress["invalid_sequences"], f"{label}: invalid sequence count differs")
    calls = {call["index"]: call for call in wire}
    require(len(calls) == len(wire) and len(wire) >= planning, f"{label}: wire indices differ")
    require(all(calls[event["input_request_index"]]["observation_round"] == event["round"] for event in events), f"{label}: accepted reply wire binding differs")
    audit = {
        "id": label,
        "status": status,
        "planning": planning,
        "source_sha256": hashes,
        "public_map_key": map_key(row),
        "public_map_sha256": map_sha,
        "map_geometry_start_goal_verified": True,
        "recorded_images": 0,
    }
    model = row["model"]
    if model not in PUBLIC_MODELS:
        return None, audit
    native_agy = model == "gemini38"
    basis = "native_agy_turn_records" if native_agy else "gateway_attempt_records"
    unresolved = None if native_agy else sum(call.get("http_status") is None for call in wire)
    public = {
        "id": label,
        "model": model,
        "difficulty": row["difficulty"],
        "shape": row["topology"],
        "seed": row["seed"],
        "cells": row["cells"],
        "cap": cap,
        "status": status,
        "planning": planning,
        "actions": actions,
        "wall_hits": walls,
        "invalid": invalid,
        "forward_attempts": forwards,
        "visited": final["visited"],
        "map_key": map_key(row),
        "provider_request_overhead": {
            "basis": basis,
            "records": len(wire),
            "extra_records": len(wire) - planning,
            "unresolved_gateway_records": unresolved,
        },
        "rounds": rounds,
        "path": path,
    }
    return public, audit


def write_atomic(path, data, mode):
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(dir=path.parent, prefix=".text-export-")
    try:
        with os.fdopen(descriptor, "wb") as file:
            file.write(data)
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", required=True, type=Path, help="Canonical completed Text40 run")
    parser.add_argument("--maps-dir", required=True, type=Path, help="Existing site-wrapup/data/maps directory")
    parser.add_argument("--public-out", required=True, type=Path, help="Sanitized JSON destination")
    parser.add_argument("--private-audit-out", required=True, type=Path, help="Private audit receipt destination")
    args = parser.parse_args()
    run_dir, maps_dir = args.run_dir.resolve(), args.maps_dir.resolve()
    public_out, audit_out = args.public_out.resolve(), args.private_audit_out.resolve()
    require(public_out != audit_out, "Public and private destinations must differ")
    for output in (public_out, audit_out):
        require(not output.is_relative_to(run_dir) and not output.is_relative_to(maps_dir), "An output path overlaps the read-only inputs")
    manifest, rollup, root_hashes = verify_completion(run_dir)
    rollup_rows = {row["id"]: row for row in rollup["rows"]}
    require(set(rollup_rows) == {row["id"] for row in manifest["rows"]}, "Rollup case IDs differ from manifest")
    maps = {key: read_json(maps_dir / (key + ".json")) for key in sorted({map_key(row) for row in manifest["rows"]})}
    rows, audit_cases = [], []
    for row in manifest["rows"]:
        map_data, map_sha = maps[map_key(row)]
        public, audit = verify_and_project_case(run_dir, row, rollup_rows[row["id"]], map_data, map_sha)
        audit_cases.append(audit)
        if public is not None:
            rows.append(public)
    counts = Counter(row["status"] for row in audit_cases)
    require(counts == Counter(rollup["status_counts"]), "Terminal counts differ from the completed rollup")
    require(sum(counts.values()) == 40 and counts["success"] == rollup["clean_passed"], "Audited terminal totals differ")
    require(len(rows) == 32 and Counter(row["model"] for row in rows) == Counter(dict.fromkeys(PUBLIC_MODELS, 8)), "Public model matrix differs")
    public = {
        "schema": "mirror-maze-text-release-public/v1",
        "models": PUBLIC_MODELS,
        "harnesses": HARNESSES,
        "maps": sorted(maps),
        "round_fields": list(ROUND_FIELDS),
        "counts": dict(Counter(row["status"] for row in rows)),
        "rows": rows,
    }
    public_bytes = (json.dumps(public, ensure_ascii=False, separators=(",", ":")) + "\n").encode()
    for private_identifier in (b"seed-2.1", b"doubao", b"-seed-", b"private_response_file", b"native_session"):
        require(private_identifier not in public_bytes.lower(), "A private identifier entered the public projection")
    for name, expected in root_hashes.items():
        require(digest((run_dir / name).read_bytes()) == expected, f"Canonical {name} changed during export")
    private = {
        "schema": "mirror-maze-text-release-private-audit/v1",
        "visibility": "private",
        "canonical_run": str(run_dir),
        "canonical_completion_sha256": root_hashes,
        "audited_cases": len(audit_cases),
        "canonical_terminal_counts": dict(counts),
        "public_cases": len(rows),
        "public_terminal_counts": public["counts"],
        "public_sha256": digest(public_bytes),
        "exporter_sha256": digest(Path(__file__).read_bytes()),
        "zero_recorded_images_verified": True,
        "public_model_allowlist_verified": True,
        "public_map_sha256": {key: value[1] for key, value in maps.items()},
        "cases": audit_cases,
    }
    audit_bytes = (json.dumps(private, indent=2) + "\n").encode()
    write_atomic(public_out, public_bytes, 0o644)
    write_atomic(audit_out, audit_bytes, 0o600)
    print(json.dumps({"public_cases": len(rows), "public_sha256": digest(public_bytes), "private_audit_sha256": digest(audit_bytes)}))


if __name__ == "__main__":
    main()
