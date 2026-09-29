import tempfile
import unittest
from pathlib import Path

from store import Store, Conflict
from collector import normalize


def note(text='内容', likes='1'):
    return normalize({'id': 'a' * 24, 'note_card': {'title': 'GPU serving', 'desc': text,
                     'interact_info': {'liked_count': likes}, 'time': 1750000000000}}, 'GPU')


class QueueTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name) / 'state.db')

    def tearDown(self):
        self.tmp.cleanup()

    def enqueue(self):
        self.store.add_note(note(), 'GPU')
        return self.store.enqueue_pending()

    def test_engagement_changes_do_not_create_new_revision(self):
        self.assertTrue(self.store.add_note(note(), 'GPU'))
        self.assertFalse(self.store.add_note(note(likes='2'), 'GPU'))
        self.assertTrue(self.store.add_note(note('更新的内容'), 'GPU'))

    def test_local_offline_and_restart_keep_work(self):
        job_id = self.enqueue()
        reopened = Store(self.store.path)
        job = reopened.claim('mac')
        self.assertEqual(job['job_id'], job_id)
        self.assertEqual(len(job['notes']), 1)
        self.assertIsNone(reopened.claim('another-mac'))

    def test_expired_lease_fences_old_result(self):
        self.enqueue()
        old = self.store.claim('mac', clock=1000)
        new = self.store.claim('mac2', clock=2000)
        with self.assertRaises(Conflict):
            self.store.complete(old['job_id'], old['lease_token'], {'summary': '旧结果', 'model': 'test'}, clock=2001)
        self.store.complete(new['job_id'], new['lease_token'], {'summary': '新结果', 'model': 'test'}, clock=2001)

    def test_result_retry_is_idempotent_but_conflicting_output_rejected(self):
        self.enqueue()
        job = self.store.claim('mac')
        result = {'summary': '结果', 'model': 'test'}
        self.assertEqual(self.store.complete(job['job_id'], job['lease_token'], result), 'completed')
        self.assertEqual(self.store.complete(job['job_id'], job['lease_token'], result), 'duplicate')
        with self.assertRaises(Conflict):
            self.store.complete(job['job_id'], job['lease_token'], {'summary': '不同', 'model': 'test'})

    def test_delivery_failure_does_not_require_ai_again(self):
        self.enqueue()
        job = self.store.claim('mac')
        self.store.complete(job['job_id'], job['lease_token'], {'summary': '结果', 'model': 'test'})
        self.assertIsNone(self.store.claim('mac'))
        self.assertEqual(self.store.ready_deliveries()[0]['job_id'], job['job_id'])
        self.store.record_delivery(job['job_id'], 1, 'message-1')
        self.assertTrue(self.store.delivered_part(job['job_id'], 1))

    def test_retry_collection_recovers_unassigned_notes(self):
        self.store.add_note(note(), 'GPU')
        job_id = self.store.enqueue_pending()
        self.assertIsNotNone(job_id)
        self.assertIsNone(self.store.enqueue_pending())

    def test_watch_users_are_durable_and_can_be_disabled(self):
        self.store.add_watch_user('user-123', '核心作者')
        self.assertEqual(self.store.status()['watch_users'], 1)
        self.assertEqual(self.store.list_watch_users()[0]['label'], '核心作者')
        self.store.add_watch_user('user-123', '更新备注')
        self.assertEqual(self.store.list_watch_users()[0]['label'], '更新备注')
        self.assertTrue(self.store.remove_watch_user('user-123'))
        self.assertEqual(self.store.list_watch_users(), [])


if __name__ == '__main__':
    unittest.main()
