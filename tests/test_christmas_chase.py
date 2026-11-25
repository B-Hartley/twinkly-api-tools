"""Acceptance checks for spoken instructions and the physical lighting score."""
import itertools
import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]

class ChristmasScoreTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.plan=json.loads((ROOT/'custom_components/twinkly_area_state/christmas_show.json').read_text())
        cls.front='light.twinkly_front'
        cls.bushes=set(cls.plan['zones']['bushes'])
        cls.windows=set(cls.plan['zones']['windows'])
        cls.tree=set(cls.plan['zones']['tree'])
        cls.icicles=set(cls.plan['zones']['icicles'])
        absolute=[]
        start=0
        for chapter in cls.plan['sections']:
            for cue in chapter['cues']:
                absolute.append((round(start+cue['at'],4),cue))
            start=round(start+chapter['duration'],4)
        cls.scenes=[(at,[item[1] for item in group]) for at,group in
                    itertools.groupby(sorted(absolute,key=lambda item:item[0]),key=lambda item:item[0])]

    def active_scenes(self):
        active=set()
        for at,cues in self.scenes:
            for cue in cues:
                if cue.get('off'):
                    active.clear() if self.front in cue['targets'] else active.difference_update(cue['targets'])
                elif not cue.get('stored_movie'):
                    active.update(cue['targets'])
            yield at,active.copy()

    def test_zone_identity_matches_display(self):
        self.assertEqual(len(self.bushes),6)
        self.assertEqual(len(self.windows),17)
        self.assertEqual(len(self.plan['targets']),26)
        self.assertEqual(len(set(self.plan['targets'])),26)
        self.assertTrue({'light.twinkly_front_left_bush','light.twinkly_back_right_bush',
                         'light.twinkly_front_right_left_bush','light.twinkly_front_right_right_bush'}<=self.bushes)
        self.assertFalse(self.bushes & (self.tree | self.icicles | self.windows))
        self.assertFalse(any(name.endswith(('_top','_bottom','_front_right_bush')) for name in self.plan['targets']))

    def test_spoken_bushes_then_windows_are_correct_solo_lights(self):
        bushes_seen,windows_seen=set(),set()
        for at,active in self.active_scenes():
            if 9.0093<=at<19.3654:
                self.assertEqual(len(active),1,at)
                self.assertTrue(active<=self.bushes,at)
                bushes_seen.update(active)
            if 19.3654<=at<=27.446:
                self.assertEqual(len(active),1,at)
                self.assertTrue(active<=self.windows,at)
                windows_seen.update(active)
        self.assertEqual(bushes_seen,self.bushes)
        self.assertEqual(windows_seen,self.windows)

    def test_tree_and_icicles_are_separate_introductions(self):
        for at,active in self.active_scenes():
            if 32.299<=at<36.4553: self.assertEqual(active,self.tree)
            if 36.4553<=at<42.0629: self.assertEqual(active,self.icicles)

    def test_trigger_words_and_narration_slots_match_score(self):
        by_id={entry['id']:entry for entry in self.plan['narration']}
        scenes=dict(self.active_scenes())
        for ident,word,allowed in [
            ('02_bushes','Bushes',self.bushes),('03_windows','Windows',self.windows),
            ('04_tree','Christmas tree',self.tree),('05_icicles','Icicles',self.icicles),
            ('08_window_chase','Windows',self.windows),('09_garden_chase','Bushes',self.bushes),
            ('10_return_windows','Windows',self.windows),
        ]:
            line=by_id[ident]
            self.assertTrue(line['text'].startswith(word))
            self.assertTrue(scenes[line['at']]<=allowed)
            self.assertTrue(scenes[line['at']])
        for line,following in zip(self.plan['narration'],self.plan['narration'][1:]):
            self.assertLessEqual(line['until'],following['at'])

    def test_solids_progress_to_rainbow_in_every_pane(self):
        first_rainbow=None
        reached=set()
        for at,cues in self.scenes:
            for cue in cues:
                if 'rainbow' in cue.get('effect','').lower():
                    first_rainbow=at if first_rainbow is None else first_rainbow
                    self.assertTrue(set(cue['targets'])<=self.windows)
                    self.assertNotIn('fallback_rgb',cue)
                    if at<=63.2233: reached.update(cue['targets'])
                elif at<58.7233 and not cue.get('off'):
                    self.assertIn('rgb',cue)
        self.assertEqual(first_rainbow,58.7233)
        self.assertEqual(reached,self.windows)
        self.assertEqual(dict(self.active_scenes())[42.0629],self.windows|self.bushes|self.tree|self.icicles)

    def test_window_chase_completes_both_directions_on_beats(self):
        selected=[next(iter(active)) for at,active in self.active_scenes() if 74.1181<=at<=86.1181 and len(active)==1]
        self.assertEqual(len(selected),33)
        self.assertEqual(set(selected),self.windows)
        self.assertEqual(selected[:17],selected[16:][::-1])
        times=[at for at,active in self.active_scenes() if 74.1181<=at<=86.1181]
        self.assertTrue(all(abs(b-a-.375)<.00001 for a,b in zip(times,times[1:])))

    def test_garden_loop_visits_every_zone_and_returns_on_spoken_cue(self):
        selected=[next(iter(active)) for at,active in self.active_scenes() if 87.9572<=at<97.3322 and len(active)==1]
        self.assertEqual(len(selected),25)
        self.assertEqual(set(selected[:6]),self.bushes)
        self.assertEqual(set(selected[6:8]),self.tree|self.icicles)
        self.assertEqual(set(selected[8:]),self.windows)
        self.assertEqual(len(set(selected)),25)
        self.assertTrue(dict(self.active_scenes())[90.9572]<=self.windows)

    def test_two_movies_end_show_without_later_realtime_cues(self):
        movies=[(at,cue) for at,cues in self.scenes for cue in cues if cue.get('stored_movie')]
        self.assertEqual([at for at,_ in movies],[102.2026,119.0603])
        self.assertEqual([cue['movie_index'] for _,cue in movies],[0,1])
        self.assertTrue(all(cue['targets']==[self.front] for _,cue in movies))
        self.assertTrue(self.plan['requires_stored_movies'])
        self.assertTrue(all(cue.get('stored_movie') for at,cues in self.scenes if at>=102.2026 for cue in cues))
        self.assertEqual(self.plan['sections'][-1]['cues'],[])

    def test_track_and_chapters_are_140_seconds(self):
        self.assertEqual(self.plan['soundtrack']['file'],'christmas_show_v2.mp3')
        self.assertEqual(self.plan['soundtrack']['duration'],140)
        self.assertAlmostEqual(sum(s['duration'] for s in self.plan['sections']),140)
        targets=set(self.plan['targets'])
        for section in self.plan['sections']:
            for cue in section['cues']:
                self.assertGreaterEqual(cue['at'],0)
                self.assertLess(cue['at'],section['duration'])
                self.assertTrue(set(cue['targets'])<=targets)
                if not cue.get('off'): self.assertTrue(0<cue['brightness']<=255)

if __name__=='__main__': unittest.main()
