import json,shutil,subprocess,sys,tempfile,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from render_series_project import build_command,load_project

@unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'),'FFmpeg/FFprobe not installed')
class MultiRenderTests(unittest.TestCase):
    def test_render_two_source_videos_with_audio(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);src=[]
            for i,color in enumerate(('red','blue')):
                p=root/f'ep{i+1}.mp4'
                subprocess.run(['ffmpeg','-hide_banner','-loglevel','error','-y','-f','lavfi','-i',f'color=c={color}:s=320x240:r=25:d=2','-f','lavfi','-i','sine=frequency=440:sample_rate=48000:duration=2','-shortest','-c:v','libx264','-pix_fmt','yuv420p','-c:a','aac',str(p)],check=True)
                src.append(str(p))
            project={'project_type':'cross_episode_compilation','clips':[{'clip_id':'C1','source_video':src[0],'start_sec':0.2,'end_sec':1.2,'enabled':True},{'clip_id':'C2','source_video':src[1],'start_sec':0.4,'end_sec':1.4,'enabled':True}]}
            pp=root/'project.json';pp.write_text(json.dumps(project),encoding='utf-8');out=root/'final.mp4'
            cmd,flt=build_command(load_project(pp),out,width=320,height=240,fps=25)
            script=root/'filter.ffscript';script.write_text(flt,encoding='utf-8')
            subprocess.run([str(script) if x=='FILTER_FILE' else x for x in cmd],check=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
            info=subprocess.run(['ffprobe','-v','error','-show_entries','format=duration:stream=codec_type,codec_name','-of','json',str(out)],capture_output=True,text=True, encoding='utf-8', errors='replace',check=True)
            meta=json.loads(info.stdout);duration=float(meta['format']['duration']);types={s['codec_type'] for s in meta['streams']}
            self.assertAlmostEqual(duration,2.0,delta=.25);self.assertEqual(types,{'video','audio'})


    def test_render_mixed_audio_and_silent_source(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);with_audio=root/'audio.mp4';silent=root/'silent.mp4'
            subprocess.run(['ffmpeg','-hide_banner','-loglevel','error','-y','-f','lavfi','-i','color=c=green:s=320x240:r=25:d=2','-f','lavfi','-i','sine=frequency=440:sample_rate=48000:duration=2','-shortest','-c:v','libx264','-pix_fmt','yuv420p','-c:a','aac',str(with_audio)],check=True)
            subprocess.run(['ffmpeg','-hide_banner','-loglevel','error','-y','-f','lavfi','-i','color=c=yellow:s=320x240:r=25:d=2','-an','-c:v','libx264','-pix_fmt','yuv420p',str(silent)],check=True)
            project={'project_type':'cross_episode_compilation','clips':[{'clip_id':'C1','source_video':str(with_audio),'start_sec':0,'end_sec':1,'enabled':True},{'clip_id':'C2','source_video':str(silent),'start_sec':0,'end_sec':1,'enabled':True}]}
            pp=root/'project.json';pp.write_text(json.dumps(project),encoding='utf-8');out=root/'mixed.mp4'
            cmd,flt=build_command(load_project(pp),out,width=320,height=240,fps=25);script=root/'filter.ffscript';script.write_text(flt,encoding='utf-8')
            subprocess.run([str(script) if x=='FILTER_FILE' else x for x in cmd],check=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
            info=subprocess.run(['ffprobe','-v','error','-show_entries','format=duration:stream=codec_type','-of','json',str(out)],capture_output=True,text=True, encoding='utf-8', errors='replace',check=True)
            meta=json.loads(info.stdout);self.assertAlmostEqual(float(meta['format']['duration']),2.0,delta=.25);self.assertEqual({x['codec_type'] for x in meta['streams']},{'video','audio'})


    def test_reject_clip_end_beyond_source_duration(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);video=root/'short.mp4'
            subprocess.run(['ffmpeg','-hide_banner','-loglevel','error','-y','-f','lavfi','-i','color=c=red:s=320x240:r=25:d=1','-an','-c:v','libx264','-pix_fmt','yuv420p',str(video)],check=True)
            project={'project_type':'cross_episode_compilation','clips':[{'clip_id':'TOO_LONG','source_video':str(video),'start_sec':0,'end_sec':2,'enabled':True}]}
            with self.assertRaisesRegex(ValueError,'超出源视频时长'):
                build_command(project,root/'bad.mp4',width=320,height=240,fps=25)


class CliEncodingTests(unittest.TestCase):
    def test_renderer_cli_error_is_safe_without_python_utf8_mode(self):
        import os
        with tempfile.TemporaryDirectory() as td:
            missing=Path(td)/'不存在的项目.json'
            output=Path(td)/'输出.mp4'
            env=os.environ.copy()
            env['PYTHONUTF8']='0'
            env.pop('PYTHONIOENCODING',None)
            result=subprocess.run(
                [sys.executable,str(ROOT/'scripts'/'render_series_project.py'),str(missing),str(output)],
                capture_output=True,text=True,encoding='utf-8',errors='replace',env=env
            )
            self.assertNotEqual(result.returncode,0)
            self.assertTrue(result.stderr)
            self.assertNotIn('UnicodeEncodeError',result.stderr)
            self.assertNotIn('charmap codec can\'t encode',result.stderr)


if __name__=='__main__':unittest.main()
