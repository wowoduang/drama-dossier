import sys, unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"scripts"))
from director_engine import plan_director
from post_render_review import inspect_video

class DirectorEngineTests(unittest.TestCase):
    def test_plan_is_explainable_and_unreviewed(self):
        project={"clips":[{"clip_id":"C1","episode_id":"EP1","start_sec":0,"end_sec":4,"narrative_role":"peak"}]}
        out=plan_director(project,{"clip_observations":[{"clip_id":"C1","performance_observation_status":"PASS","action_continuity":"continuous","dialogue_complete":True,"reaction_complete":True,"visual_observation":"人物拔刀后转身"}]})
        sug=out["director_plan"]["suggestions"][0]
        self.assertEqual(sug["review_status"],"UNREVIEWED")
        self.assertEqual(sug["pacing"],"impact")
        self.assertEqual(sug["performance_observation_status"],"PASS")
        self.assertIn("不从像素自动推断微表情、情绪或动机",out["director_plan"]["limitations"][0])

    def test_unreviewed_performance_is_flagged(self):
        project={"clips":[{"clip_id":"C1","start_sec":0,"end_sec":2,"narrative_role":"reaction"}]}
        out=plan_director(project)
        self.assertIn("PERFORMANCE_OBSERVATION_NOT_REVIEWED",out["director_plan"]["suggestions"][0]["risk_flags"])

    def test_fast_profile_adjusts_target_duration(self):
        project={"clips":[{"clip_id":"C1","start_sec":0,"end_sec":5,"narrative_role":"setup"}]}
        normal=plan_director(project,profile="balanced")["director_plan"]["suggestions"][0]["target_duration_sec"]
        fast=plan_director(project,profile="fast")["director_plan"]["suggestions"][0]["target_duration_sec"]
        self.assertLess(fast,normal)

class PostRenderTests(unittest.TestCase):
    def test_post_render_review_real_video(self):
        import shutil, subprocess, tempfile
        ffmpeg=shutil.which("ffmpeg")
        if not ffmpeg: self.skipTest("FFmpeg unavailable")
        with tempfile.TemporaryDirectory() as td:
            video=Path(td)/"test.mp4"
            subprocess.run([ffmpeg,"-hide_banner","-loglevel","error","-f","lavfi","-i","color=c=blue:s=160x120:d=2:r=24","-f","lavfi","-i","sine=frequency=440:duration=2","-shortest","-c:v","libx264","-pix_fmt","yuv420p","-c:a","aac",str(video)],check=True)
            report=inspect_video(video)
            self.assertEqual(report["technical_check"],"PASS")
            self.assertEqual(report["video"]["codec"],"h264")
            self.assertEqual(report["audio"]["codec"],"aac")

if __name__=="__main__": unittest.main()
