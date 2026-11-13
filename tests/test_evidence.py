import json
import sqlite3
import unittest
from pathlib import Path
from app.evidence import load, query

class EvidenceTests(unittest.TestCase):
    def setUp(self):
        self.db=sqlite3.connect(':memory:'); self.addCleanup(self.db.close); self.db.row_factory=sqlite3.Row
        self.data=json.loads(Path('fixtures/evidence/synthetic.json').read_text())
        load(self.db,self.data)
    def run_query(self, date, cat='А'):
        return query(self.db,'Порядок заявки',date,{'категория':cat})
    def test_date_and_point_amendment(self):
        old=self.run_query('2021-06-01'); new=self.run_query('2023-01-01')
        self.assertEqual({x['id'] for x in old['items']},{'r1','r2'})
        self.assertEqual({x['id'] for x in new['items']},{'r1','r3'})
        self.assertIn('B1',[x['basis'] for x in new['limits']])
        self.assertTrue(any('неполна' in x['text'] for x in new['limits']))
    def test_category_conflict_and_form(self):
        res=self.run_query('2023-01-01','Б')
        self.assertEqual({x['id'] for x in res['items']},{'r1','r4','r5'})
        self.assertEqual(res['conflicts'][0]['rules'],['r4','r5'])
        self.assertNotIn('99',str(res['items']))
    def test_missing_conditions_and_incomplete(self):
        self.assertTrue(query(self.db,'Порядок заявки',None)['questions'])
        self.assertTrue(query(self.db,'Порядок заявки','2023-01-01')['questions'])
        travel=query(self.db,'Порядок поездки','2023-01-01')
        self.assertTrue(any('утверждения' in x['text'] and x['basis'] for x in travel['limits']))
        self.assertTrue(any('приложение' in x['text'] and x['basis']=='M1' for x in travel['limits']))
    def test_data_change_and_atomic_rejection(self):
        self.data['rules'][2]['start']='2024-01-01'; load(self.db,self.data)
        self.assertIn('r2',{x['id'] for x in self.run_query('2023-01-01')['items']})
        self.data['relations'][0]['basis']='missing'
        with self.assertRaises(ValueError): load(self.db,self.data)
        self.assertIn('r2',{x['id'] for x in self.run_query('2023-01-01')['items']})

if __name__ == '__main__': unittest.main()
