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


if __name__ == '__main__':
    unittest.main()
