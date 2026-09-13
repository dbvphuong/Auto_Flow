import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from core.capcut_tools import (
    TimelineVideoJob,
    _scene_video_map,
    _video_segment_command,
    build_timeline_video_project,
    prepare_timeline_clip_jobs,
)


class CapcutVideoTimelineTests(unittest.TestCase):
    def _timeline_pair(self, root, scenes=(1, 2)):
        timeline = root / "story_full.json"
        audio = root / "story_full.mp3"
        entries = [
            {
                "start": float(index - 1), "end": float(index), "duration": 1.0,
                "scene": scene, "text": f"Scene {scene}",
            }
            for index, scene in enumerate(scenes, 1)
        ]
        timeline.write_text(json.dumps(entries), encoding="utf-8")
        audio.touch()
        return timeline, audio

    def test_scene_video_map_uses_numeric_prefix_and_prefers_higher_quality(self):
        with tempfile.TemporaryDirectory() as temporary_root:
            folder = Path(temporary_root)
            low = folder / "2_720p.mp4"
            high = folder / "2_1080p.mp4"
            scene_one = folder / "001_clip.mov"
            ignored = folder / "error_2_fail.mp4"
            for path in (low, high, scene_one, ignored):
                path.touch()

            mapping = _scene_video_map(folder)

            self.assertEqual(mapping[1], scene_one)
            self.assertEqual(mapping[2], high)
            self.assertEqual(set(mapping), {1, 2})

    @patch("core.capcut_tools.MP3")
    def test_prepare_clip_jobs_resolves_direct_video_folder(self, mp3):
        mp3.return_value = SimpleNamespace(info=SimpleNamespace(length=2.2))
        with tempfile.TemporaryDirectory() as temporary_root:
            root = Path(temporary_root)
            timeline, audio = self._timeline_pair(root)
            videos = root / "Video"
            videos.mkdir()
            (videos / "1_1080p.mp4").touch()
            (videos / "2_720p.mp4").touch()

            jobs, skipped, errors = prepare_timeline_clip_jobs(
                [timeline], [audio], videos, root / "Output"
            )

            self.assertEqual(errors, [])
            self.assertEqual(skipped, [])
            self.assertEqual(len(jobs), 1)
            self.assertEqual(jobs[0].media_type, "video")
            self.assertTrue(jobs[0].entries[0]["video"].endswith("1_1080p.mp4"))
            self.assertEqual(jobs[0].output_path, root / "Output" / "story_full.mp4")

    @patch("core.capcut_tools.MP3")
    def test_prepare_clip_jobs_reports_all_missing_scenes_before_render(self, mp3):
        mp3.return_value = SimpleNamespace(info=SimpleNamespace(length=2.2))
        with tempfile.TemporaryDirectory() as temporary_root:
            root = Path(temporary_root)
            timeline, audio = self._timeline_pair(root, scenes=(4, 68))
            videos = root / "Video"
            videos.mkdir()
            (videos / "1_720p.mp4").touch()

            jobs, skipped, errors = prepare_timeline_clip_jobs([timeline], [audio], videos)

            self.assertEqual(jobs, [])
            self.assertEqual(skipped, [])
            self.assertEqual(len(errors), 1)
            self.assertIn("4, 68", errors[0][1])

    def test_video_draft_uses_video_material_without_photo_motion(self):
        with tempfile.TemporaryDirectory() as temporary_root:
            root = Path(temporary_root)
            draft = root / "draft_content.json"
            draft.write_text(json.dumps({"materials": {}, "tracks": []}), encoding="utf-8")
            clip = root / "1.mp4"
            clip.touch()
            job = TimelineVideoJob(
                timeline_path=root / "story.json", audio_path=root / "story.mp3",
                output_path=root / "story.mp4", audio_duration=2.0,
                entries=[{"start": 0.0, "end": 1.0, "duration": 1.0,
                          "scene": 1, "video": str(clip)}],
                media_type="video",
            )

            build_timeline_video_project(draft, job)
            data = json.loads(draft.read_text(encoding="utf-8"))

            material = data["materials"]["videos"][0]
            self.assertEqual(material["type"], "video")
            self.assertEqual(material["path"], str(clip))

    def test_video_ffmpeg_command_loops_and_has_no_zoom_filter(self):
        command = _video_segment_command(
            "ffmpeg", "clip.mp4", "segment.mp4", 1920, 1080, 180, 30,
            ["-c:v", "libx264"], 2,
        )
        command_text = " ".join(command)

        self.assertIn("-stream_loop -1", command_text)
        self.assertIn("scale=1920:1080", command_text)
        self.assertIn("crop=1920:1080", command_text)
        self.assertNotIn("zoompan", command_text)
        self.assertNotIn("affine", command_text)


if __name__ == "__main__":
    unittest.main()
