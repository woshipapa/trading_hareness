import json
import re
import unittest
from unittest import mock

import local_worker
from local_worker import make_single_note_prompt


class SingleNotePromptTests(unittest.TestCase):
    def test_prompt_preserves_provenance_and_requires_evidence_boundaries(self):
        prompt = make_single_note_prompt({
            'job_id': 'xhs-single-test',
            'notes': [{
                'title': 'Serving systems',
                'author': 'Researcher',
                'published_at': '2026-10-08T00:00:00Z',
                'url': 'https://www.xiaohongshu.com/explore/' + 'f' * 24,
                'text': 'A benchmark claim.',
            }],
        })
        self.assertIn('Serving systems', prompt)
        self.assertIn('原文事实、作者观点、编辑推断', prompt)
        self.assertIn('待核验', prompt)
        self.assertIn('如果文章与这些领域弱相关', prompt)

    def test_single_note_jobs_use_the_dedicated_analyzer(self):
        job = {'job_id': 'single', 'lease_token': 'lease', 'lease_seconds': 30,
               'job_type': 'single_note_analysis'}
        with mock.patch.object(local_worker, 'run_with_heartbeat',
                               side_effect=lambda value, task: task(value)) as heartbeat:
            with mock.patch.object(local_worker, 'analyze_single_note',
                                   return_value={'summary': 'ok'}) as analyzer:
                result = local_worker.process_job(job)
                self.assertIs(heartbeat.call_args.args[1], analyzer)
        self.assertEqual(result, {'summary': 'ok'})

    def test_note_screening_jobs_use_the_shared_classifier(self):
        job = {'job_id': 'screen', 'job_type': 'classify_notes'}
        with mock.patch.object(local_worker, 'run_with_heartbeat',
                               side_effect=lambda value, task: task(value)):
            with mock.patch.object(local_worker, 'classify', return_value={'decisions': []}) as classifier:
                result = local_worker.process_job(job)
        self.assertEqual(classifier.call_count, 1)
        self.assertEqual(result, {'decisions': []})

    def test_each_worker_loop_claims_only_its_lane(self):
        calls = []

        def fake_call(path, payload=None, timeout=30):  # noqa: ARG001
            calls.append((path, payload))
            raise RuntimeError('stop-test-loop')

        with mock.patch.object(local_worker, 'call_edge', side_effect=fake_call), \
             mock.patch.object(local_worker.time, 'sleep', side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                local_worker.loop('interactive')
        self.assertEqual(calls[0][1]['lane'], 'interactive')
        self.assertTrue(calls[0][1]['worker'].endswith(':interactive'))

    def test_recommendation_filter_splits_large_candidate_batches_and_merges_all_decisions(self):
        job = {
            'job_id': 'filter-large',
            'policy': {'topics': []},
            'notes': [
                {'_candidate_id': f'candidate-{index}', 'title': f'Note {index}', 'text': 'CUDA'}
                for index in range(21)
            ],
        }

        def fake_request(prompt, **_kwargs):
            ids = re.findall(r'candidate_id：([^\n]+)', prompt)
            return json.dumps({
                'decisions': [
                    {'candidate_id': value, 'decision': 'include', 'topics': [],
                     'relevance_score': .9, 'confidence': .9, 'reason': '系统内容'}
                    for value in ids
                ]
            }), 'fake-model'

        with mock.patch.object(local_worker.codex_provider, 'load_endpoint', return_value=('http://test', '', '')), \
             mock.patch.object(local_worker.codex_provider, 'ordered_keys', return_value=['fixture']), \
             mock.patch.object(local_worker.codex_provider, 'default_chain', return_value=[('fake-model', 'high')]), \
             mock.patch.object(local_worker.codex_provider, 'request', side_effect=fake_request) as request:
            result = local_worker.classify(job)

        self.assertEqual(request.call_count, 3)
        self.assertEqual({item['candidate_id'] for item in result['decisions']},
                         {note['_candidate_id'] for note in job['notes']})


if __name__ == '__main__':
    unittest.main()
