#!/usr/bin/env python3
"""Gemini App handoff. Local preparation and collection only: no API calls or browser automation."""
import argparse
import json
import shutil
import sys
from fractions import Fraction
from pathlib import Path
from types import SimpleNamespace

from recast import ident, map_video, now, probe, read, reviewed, resolve, run, save, sha, source_info, tool, video, frames


def prepare(a):
    task_path = Path(a.task).resolve()
    t = read(task_path)
    ident(t["segment_id"])
    ident(t["version"])
    reviewed(t.get("action_review"), "complete source action")
    reviewed(t.get("reference_review"), "target references")
    source = resolve(task_path.parent, t["source"])
    info = source_info(source)
    start, end = t["start"], t["end"]
    if type(start) is not int or type(end) is not int or not 0 <= start < end <= info["frame_count"]:
        raise ValueError("Invalid source-frame interval")
    duration = Fraction(end - start, 1) / Fraction(info["fps"])
    if duration > 10:
        raise ValueError("Gemini App handoff: split into reviewed clips of at most 10 seconds; keep full actions when possible")
    references = t.get("reference_images", [])
    if not isinstance(references, list) or not 1 <= len(references) <= 5:
        raise ValueError("Give 1 to 5 identity / scene reference images")
    for key in ("source_actor", "target_character", "action_notes"):
        if not isinstance(t.get(key), str) or not t[key].strip():
            raise ValueError("Missing descriptive task field: " + key)
    dest = Path(a.output).resolve()
    if dest.exists():
        raise ValueError("Immutable output packet already exists; use a new version")
    dest.mkdir(parents=True)
    inputs = dest / "inputs"
    inputs.mkdir()
    copied = []
    for index, path in enumerate(references, 1):
        original = resolve(task_path.parent, path)
        if not original.is_file() or original.suffix.lower() not in (".png", ".jpg", ".jpeg", ".webp"):
            raise ValueError("Missing or unsupported reference image: " + str(original))
        target = inputs / ("reference_" + str(index) + original.suffix.lower())
        shutil.copyfile(original, target)
        copied.append({"path":str(target.relative_to(dest)), "sha256":sha(target)})
    silent = inputs / "segment_silent.mp4"
    clip = inputs / "segment_for_gemini.mp4"
    run([tool("ffmpeg"), "-v", "error", "-n", "-i", source,
         "-vf", "trim=start_frame=" + str(start) + ":end_frame=" + str(end) + ",setpts=PTS-STARTPTS",
         "-an", "-c:v", "libx264", "-crf", "18", "-pix_fmt", "yuv420p", silent])
    if frames(video(probe(silent))) != end - start:
        raise ValueError("Extracted segment frame count mismatch")
    # Mux the source's audio interval, when present. Original audio remains the final authority.
    seconds = float(Fraction(start, 1) / Fraction(info["fps"]))
    run([tool("ffmpeg"), "-v", "error", "-n", "-i", silent,
         "-ss", "%.6f" % seconds, "-t", "%.6f" % float(duration), "-i", source,
         "-map", "0:v:0", "-map", "1:a:0?", "-c:v", "copy", "-c:a", "aac", clip])
    silent.unlink()
    if frames(video(probe(clip))) != end - start:
        raise ValueError("Muxed segment frame count mismatch")
    prompt = ("请对附件源视频做人物替换（video-to-video edit），以 reference_1 等参考图为新人物形象依据。\n"
              "源片人物：" + t["source_actor"] + "\n"
              "目标人物：" + t["target_character"] + "\n"
              "需要保留的场景服装：" + t.get("wardrobe", "以原视频为准") + "\n"
              "经过人工核对的完整动作与镜头细节：" + t["action_notes"] + "\n"
              "尽可能保持原镜头的动作时序、运镜、背景、光照、遮挡与画幅；不增加镜头或人物。"
              "如无法准确保持动作，不要声称逐帧一致。注意面部身份、手部、嘴形和接段连续性。"
              "输出时长尽量接近原视频。最终交付时会使用原片音轨，不要依赖生成的音频。\n"
              "若无法完成精确人物替换，请说明能力限制，而不是输出无关场景。\n")
    (dest / "prompt.txt").write_text(prompt, encoding="utf-8")
    packet = {
        "schema_version": 1, "backend": "gemini-app-manual", "segment_id": t["segment_id"],
        "version": t["version"], "source": str(source), "source_sha256": info["sha256"],
        "source_fps": info["fps"], "source_start": start, "source_end": end,
        "source_duration_seconds": float(duration),
        "input_video": "inputs/segment_for_gemini.mp4",
        "input_video_sha256": sha(clip), "images": copied,
        "prompt_sha256": sha(dest / "prompt.txt"), "created_at": now(),
        "state": "awaiting_user_upload_and_download", "visual_acceptance": False,
        "action_review": t["action_review"], "reference_review": t["reference_review"]
    }
    save(dest / "handoff.json", packet)
    print(json.dumps({"packet": str(dest), "upload_video": str(clip),
                      "images": copied, "prompt": str(dest / "prompt.txt"),
                      "next": "Manually upload the video + reference images in Gemini App, paste prompt.txt, download an MP4, then run collect."},
                     ensure_ascii=False, indent=2))


def collect(a):
    path = Path(a.packet).resolve()
    handoff = read(path / "handoff.json")
    if handoff.get("backend") != "gemini-app-manual":
        raise ValueError("Not a Gemini App handoff")
    source = Path(handoff["source"])
    if sha(source) != handoff["source_sha256"] or sha(path / handoff["input_video"]) != handoff["input_video_sha256"]:
        raise ValueError("Source or frozen video changed")
    if sha(path / "prompt.txt") != handoff["prompt_sha256"]:
        raise ValueError("Prompt changed; create a new version")
    for item in handoff["images"]:
        if sha(path / item["path"]) != item["sha256"]:
            raise ValueError("Reference image changed; create a new version")
    output = Path(a.output).resolve()
    if output.exists():
        raise ValueError("Output exists; never overwrite a selected version")
    returned = Path(a.input).resolve()
    if not returned.is_file():
        raise ValueError("Downloaded MP4 not found")
    vid = video(probe(returned))
    fps = vid["avg_frame_rate"]
    if Fraction(fps) <= 0:
        raise ValueError("Invalid returned video FPS")
    # map_video checks native coverage; it will fail instead of silently repeating missing frames.
    map_video(SimpleNamespace(input=str(returned), output=str(output),
                               start=handoff["source_start"], end=handoff["source_end"],
                               source_fps=handoff["source_fps"], native_fps=fps,
                               native_source_start=handoff["source_start"]))
    save(str(output) + ".gemini.json", {
        "backend": "gemini-app-manual", "segment_id": handoff["segment_id"],
        "version": handoff["version"], "source_start": handoff["source_start"],
        "source_end": handoff["source_end"], "source_sha256": handoff["source_sha256"],
        "raw_download_sha256": sha(returned), "mapped_sha256": sha(output),
        "collected_at": now(), "technical_mapping_only": True,
        "visual_acceptance": False, "continuous_playback_complete": False,
        "notes": "Check identity, timing, framing, wardrobe, lip motion and seams by watching original and result side by side. Trim/alignment cannot correct semantic drift."
    })
    print(json.dumps({"mapped": str(output), "sha256": sha(output),
                      "state": "mapped_pending_visual_review", "next": "Update project plan selected_file only after checking; use assemble --preview before final acceptance."},
                     ensure_ascii=False, indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    subs = parser.add_subparsers(dest="command", required=True)
    p = subs.add_parser("prepare", help="freeze source clip, image references and Gemini App prompt")
    p.add_argument("task")
    p.add_argument("output")
    p.set_defaults(fn=prepare)
    p = subs.add_parser("collect", help="verify downloaded video and map to the source timeline")
    p.add_argument("packet", help="directory made by prepare")
    p.add_argument("input", help="MP4 downloaded manually from Gemini App")
    p.add_argument("output", help="new mapped MP4 (must not exist)")
    p.set_defaults(fn=collect)
    args = parser.parse_args()
    args.fn(args)


if __name__ == "__main__":
    try:
        main()
    except (ValueError, KeyError, OSError, RuntimeError) as err:
        sys.exit(str(err))
