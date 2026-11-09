import json,sys,tempfile,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from film_understanding import init_session,material_decision,make_prompt,add_answer,followup_prompt,review_answer,status,aggregate,synthesis_prompt

class FilmWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.scan=self.root/'scan.json';self.folder=self.root/'session'
        self.scan.write_text(json.dumps({'media':{'duration_sec':601},'frame_manifest':[],'cues':[]}),encoding='utf-8');init_session(self.scan,self.folder,300)
        self.answer=self.root/'answer.txt';self.answer.write_text('尚未核实身份',encoding='utf-8')
    def tearDown(self): self.tmp.cleanup()
    def test_optional_review_and_coverage(self):
        with self.assertRaises(ValueError):make_prompt(self.folder,'S0001',self.root/'p.txt')
        material_decision(self.folder,'skipped');make_prompt(self.folder,'S0001',self.root/'p.txt');add_answer(self.folder,'S0001',self.answer)
        s=status(self.folder);self.assertEqual(s['segments_with_result'],1);self.assertFalse(s['all_segments_have_results']);self.assertEqual(s['material_decision']['choice'],'skipped')
        self.assertEqual(s['segments'][-1]['end_sec'],601)
    def test_feedback_and_satisfaction_are_versioned(self):
        material_decision(self.folder,'reviewed','检查字幕及片源')
        add_answer(self.folder,'S0001',self.answer);review_answer(self.folder,'S0001',True,'满意')
        self.assertTrue(status(self.folder)['segments'][0]['satisfied'])
        followup_prompt(self.folder,'S0001','补充证据',self.root/'followup.txt')
        with self.assertRaises(ValueError):add_answer(self.folder,'S0001',self.answer)
        add_answer(self.folder,'S0001',self.answer,'补充证据')
        self.assertFalse(status(self.folder)['segments'][0]['satisfied']);self.assertTrue((self.folder/'segments/S0001/round-0001.json').exists())
    def test_synthesis_discloses_missing_and_does_not_auto_pass(self):
        material_decision(self.folder,'skipped');add_answer(self.folder,'S0001',self.answer)
        synthesis_prompt(self.folder,self.root/'synthesis.txt');self.assertIn('null',(self.root/'synthesis.txt').read_text())
        aggregate(self.folder,self.root/'aggregate.json');data=json.loads((self.root/'aggregate.json').read_text());self.assertIsNone(data['sections'][1]['latest_ai_answer']);self.assertFalse(data['status']['all_satisfied'])
    def test_film_report_supports_multiple_rounds(self):
        material_decision(self.folder,'skipped');add_answer(self.folder,'FILM',self.answer)
        review_answer(self.folder,'FILM',False,'人物关系需要补充')
        followup_prompt(self.folder,'FILM','补充人物关系',self.root/'follow-film.txt')
        add_answer(self.folder,'FILM',self.answer,'补充人物关系')
        review_answer(self.folder,'FILM',True,'已满足需求')
        self.assertEqual(status(self.folder)['film_report_rounds'],2);self.assertTrue(status(self.folder)['film_report_satisfied'])
        aggregate(self.folder,self.root/'report.json');self.assertEqual(json.loads((self.root/'report.json').read_text())['latest_film_report'],'尚未核实身份')

    def test_scan_change_invalidates_session(self):
        self.scan.write_text('{}')
        with self.assertRaises(ValueError):status(self.folder)
    def test_cannot_overwrite_or_skip_reviewer_note(self):
        with self.assertRaises(ValueError):material_decision(self.folder,'reviewed')
        material_decision(self.folder,'skipped')
        with self.assertRaises(FileExistsError):material_decision(self.folder,'skipped')

if __name__=='__main__':unittest.main()
