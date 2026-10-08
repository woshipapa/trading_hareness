import tempfile
import unittest
from pathlib import Path
from unittest import mock

from store import Store, Conflict
from collector import (InvalidNoteLink, collect_single_note, ephemeral_note_link,
                       normalize, parse_note_reference, remember_note_link)
from local_worker import _load_note_images, _multimodal_items


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

    def test_note_detail_keeps_full_text_and_canonical_image_references(self):
        value = normalize(
            {'id': 'f' * 24}, 'GPU',
            {'note_card': {
                'title': 'KV cache', 'desc': '正文第一段\n正文第二段',
                'image_list': [{
                    'file_id': 'image-1',
                    'info_list': [{}, {'url': 'https://sns-webpic-qc.xhscdn.com/2026/abc/notes_pre_post/image-1!nd_dft_wlteh_webp_3?foo=transient'}],
                }],
            }},
        )
        self.assertEqual(value['text'], '正文第一段\n正文第二段')
        self.assertEqual(value['image_count'], 1)
        self.assertEqual(value['coverage'], 'note_text_and_image_refs')
        self.assertEqual(value['image_urls'], [
            'https://ci.xiaohongshu.com/notes_pre_post/image-1?imageView2/format/jpeg'
        ])
        self.assertNotIn('transient', str(value))

    def test_signed_note_links_are_memory_only_and_expire(self):
        note_id = '1' * 24
        fetch_url = ('https://www.xiaohongshu.com/explore/' + note_id
                     + '?xsec_token=short-lived&xsec_source=pc_feed')
        self.assertTrue(remember_note_link(note_id, fetch_url, ttl=30))
        self.assertIn('xsec_token=short-lived', ephemeral_note_link(note_id))
        self.assertEqual(ephemeral_note_link('2' * 24), '')

    def test_worker_builds_in_memory_multimodal_inputs_without_persisting_media(self):
        note_value = normalize(
            {'id': '0' * 24}, 'GPU',
            {'note_card': {
                'title': 'Vision note', 'desc': '图文正文',
                'image_list': [{'url_default': 'https://ci.xiaohongshu.com/notes_pre_post/image-2?imageView2/format/jpeg'}],
            }},
        )
        with mock.patch('local_worker._download_image', return_value=b'fake-image'), \
                mock.patch('local_worker._image_data_url', return_value='data:image/jpeg;base64,ZmFrZQ=='):
            media, stats = _load_note_images([note_value], max_images=1)
        self.assertEqual(stats, {'requested': 1, 'downloaded': 1, 'errors': 0})
        self.assertEqual(len(media), 1)
        payload = _multimodal_items('分析正文', media)
        self.assertIsInstance(payload, list)
        self.assertEqual(payload[0]['content'][-1]['type'], 'input_image')
        self.assertTrue(payload[0]['content'][-1]['image_url'].startswith('data:image/jpeg;base64,'))
        self.assertNotIn('image-2', str(payload))

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
        self.assertEqual(self.store.list_watch_users(enabled=False)[0]['user_id'], 'user-123')
        self.assertEqual(self.store.list_watch_users(enabled=None)[0]['enabled'], 0)

    def test_job_listing_does_not_expose_payload_result_or_lease(self):
        job_id = self.enqueue()
        row = self.store.list_jobs()[0]
        self.assertEqual(row['job_id'], job_id)
        self.assertNotIn('payload', row)
        self.assertNotIn('result', row)
        self.assertNotIn('lease_token', row)

    def test_manual_feishu_message_is_idempotent_and_content_is_not_listed(self):
        first = self.store.enqueue_manual_message('人工消息', 'request-1')
        second = self.store.enqueue_manual_message('人工消息', 'request-1')
        self.assertFalse(first['duplicate'])
        self.assertTrue(second['duplicate'])
        listing = self.store.list_delivery_jobs()
        self.assertEqual(len(listing), 1)
        self.assertNotIn('result', listing[0])
        self.assertNotIn('payload', listing[0])

    def test_single_note_analysis_stays_local_until_delivery_is_requested(self):
        first = self.store.enqueue_single_note(note('KV cache paging'), deliver_to_feishu=False)
        job = self.store.claim('single-worker')
        self.assertEqual(job['job_id'], first['job_id'])
        self.assertEqual(job['job_type'], 'single_note_analysis')
        self.store.complete(job['job_id'], job['lease_token'], {
            'summary': '单篇分析结果', 'model': 'fake-model',
        })
        self.assertEqual(self.store.list_single_note_jobs()[0]['status'], 'completed')
        self.assertEqual(self.store.list_single_note_jobs()[0]['summary'], '单篇分析结果')
        self.assertEqual(self.store.ready_deliveries(), [])
        self.assertEqual(self.store.list_delivery_jobs(), [])

        second = self.store.enqueue_single_note(note('KV cache paging'), deliver_to_feishu=True)
        self.assertTrue(second['duplicate'])
        self.assertEqual(second['job_status'], 'ready')
        self.assertEqual(self.store.ready_deliveries()[0]['job_id'], first['job_id'])
        self.assertEqual(self.store.list_delivery_jobs()[0]['job_type'], 'single_note_analysis')

    def test_single_note_analysis_has_priority_over_batch_work(self):
        self.enqueue()
        interactive = normalize({
            'id': '9' * 24,
            'note_card': {'title': 'Interactive note', 'desc': 'serving'},
        }, 'single:' + '9' * 24)
        expected = self.store.enqueue_single_note(interactive)
        claimed = self.store.claim('mac')
        self.assertEqual(claimed['job_id'], expected['job_id'])
        self.assertEqual(claimed['job_type'], 'single_note_analysis')

    def test_worker_lanes_claim_interactive_and_batch_jobs_independently(self):
        batch_id = self.enqueue()
        interactive = normalize({
            'id': '8' * 24,
            'note_card': {'title': 'Interactive note', 'desc': 'compiler'},
        }, 'single:' + '8' * 24)
        interactive_id = self.store.enqueue_single_note(interactive)['job_id']
        batch = self.store.claim('mac:batch', lane='batch')
        single = self.store.claim('mac:interactive', lane='interactive')
        self.assertEqual(batch['job_id'], batch_id)
        self.assertEqual(single['job_id'], interactive_id)

    def test_note_reference_accepts_share_text_and_rejects_external_hosts(self):
        reference = parse_note_reference(
            '21 【梁文锋署名新作：突破大模型KV缓存压缩极限 - 大模型知识分享 | '
            '小红书 - 你的生活兴趣社区】 😆 share-code 😆 '
            'https://www.xiaohongshu.com/discovery/item/' + 'A' * 24
            + '?source=webshare&xhsshare=pc_web&xsec_token=private-token&xsec_source=pc_share')
        self.assertEqual(reference['note_id'], 'a' * 24)
        self.assertIn('xsec_token=private-token', reference['fetch_url'])
        self.assertIn('xsec_source=pc_share', reference['fetch_url'])
        self.assertNotIn('source=webshare', reference['fetch_url'])
        self.assertNotIn('xhsshare=', reference['fetch_url'])
        short = parse_note_reference(
            'https://xhslink.com/a/example',
            short_resolver=lambda _url: 'https://www.xiaohongshu.com/explore/' + 'b' * 24,
        )
        self.assertEqual(short['note_id'], 'b' * 24)
        with self.assertRaises(InvalidNoteLink):
            parse_note_reference('https://example.com/explore/' + 'c' * 24)

    def test_single_note_collection_never_persists_signed_link_material(self):
        class FakeApi:
            def get_note_info(self, url):
                self.url = url
                return True, 'ok', {'data': {'items': [{'note_card': {
                    'title': 'Paged attention', 'desc': 'KV cache systems',
                    'user': {'nickname': 'Infra Author'},
                }}]}}

        cookie = Path(self.tmp.name) / 'cookie'
        cookie.write_text('a1=fake; web_session=fake', encoding='utf-8')
        api = FakeApi()
        submitted = ('https://www.xiaohongshu.com/explore/' + 'd' * 24
                     + '?xsec_token=private-token&xsec_source=pc_share')
        with mock.patch('collector._pc_api', return_value=api):
            result = collect_single_note(self.store, '/tmp/source', cookie, submitted)
        self.assertIn('private-token', api.url)
        visible = {'result': result, 'jobs': self.store.list_single_note_jobs()}
        self.assertNotIn('private-token', str(visible))
        self.assertEqual(visible['jobs'][0]['url'], 'https://www.xiaohongshu.com/explore/' + 'd' * 24)

    def test_single_note_collection_reuses_cached_content_when_unsigned_fetch_fails(self):
        class MissingApi:
            def get_note_info(self, _url):
                return False, 'not found', None

        cached = normalize({
            'id': 'e' * 24,
            'note_card': {'title': 'Cached systems note', 'desc': 'training infrastructure'},
        }, 'AI infra')
        self.store.add_note(cached, 'AI infra')
        cookie = Path(self.tmp.name) / 'cookie-cache'
        cookie.write_text('a1=fake; web_session=fake', encoding='utf-8')
        with mock.patch('collector._pc_api', return_value=MissingApi()):
            result = collect_single_note(
                self.store, '/tmp/source', cookie,
                'https://www.xiaohongshu.com/explore/' + 'e' * 24,
            )
        self.assertEqual(result['fetch_source'], 'cache')
        self.assertEqual(self.store.list_single_note_jobs()[0]['fetch_source'], 'cache')

    def test_recommendation_filter_covers_all_candidates_and_queues_only_selected_summary(self):
        first = note('CUDA distributed training')
        second = normalize({'id': 'b' * 24, 'note_card': {'title': '美食', 'desc': '无关',
                             'time': 1750000000000}}, 'recommendation:r')
        self.store.add_note(first, 'recommendation:r')
        self.store.add_note(second, 'recommendation:r')
        self.store.create_recommendation_run('r', requested=2)
        first_id, _ = self.store.add_recommendation_item('r', first, 1)
        second_id, _ = self.store.add_recommendation_item('r', second, 2)
        self.store.queue_recommendation_filter('r')
        job = self.store.claim('classifier')
        result = self.store.complete(job['job_id'], job['lease_token'], {
            'decisions': [
                {'candidate_id': first_id, 'decision': 'include', 'topics': [{'topic_id': 'ai_infra', 'score': .9}],
                 'relevance_score': .9, 'confidence': .9, 'reason': '系统内容'},
                {'candidate_id': second_id, 'decision': 'exclude', 'topics': [],
                 'relevance_score': .1, 'confidence': .9, 'reason': '无关'},
            ],
            'model': 'fake', 'input_sha256': 'test',
        })
        self.assertEqual(result['include'], 1)
        self.assertEqual(result['exclude'], 1)
        self.assertEqual(self.store.recommendation_run('r')['status'], 'summary_queued')
        summary = self.store.claim('summary-worker')
        self.assertEqual(summary['job_type'], 'summary')
        self.assertEqual(len(summary['notes']), 1)
        self.assertEqual(summary['notes'][0]['title'], 'GPU serving')
        self.store.complete(summary['job_id'], summary['lease_token'], {'summary': '摘要', 'model': 'fake'})
        self.assertEqual(self.store.recommendation_run('r')['status'], 'summary_ready')
        self.store.delivered(summary['job_id'])
        self.assertEqual(self.store.recommendation_run('r')['status'], 'sent')

    def test_failed_recommendation_filter_is_visible_and_retryable(self):
        candidate = note('CUDA distributed training')
        self.store.add_note(candidate, 'recommendation:failed-run')
        self.store.create_recommendation_run('failed-run', requested=1)
        self.store.add_recommendation_item('failed-run', candidate, 1)
        self.store.queue_recommendation_filter('failed-run')
        job = self.store.claim('classifier', lane='batch')
        with self.store.connect() as db:
            db.execute('UPDATE jobs SET attempts=5 WHERE job_id=?', (job['job_id'],))

        self.store.fail(job['job_id'], job['lease_token'], 'HTTPError:502')
        run = self.store.recommendation_run('failed-run')
        self.assertEqual(run['status'], 'filter_failed')
        self.assertEqual(self.store.list_recommendation_items('failed-run')[0]['state'], 'filter_failed')

        self.assertTrue(self.store.retry_recommendation_filter('failed-run'))
        self.assertEqual(self.store.recommendation_run('failed-run')['status'], 'filter_queued')
        retried = self.store.claim('classifier', lane='batch')
        self.assertEqual(retried['job_id'], job['job_id'])

    def test_following_snapshot_is_idempotent(self):
        accounts = [{'user_id': 'user-1', 'nickname': 'Systems Lab', 'raw': {'rid': 'user-1'}}]
        self.assertEqual(self.store.upsert_following_accounts(accounts, 'test-endpoint'), 1)
        self.assertEqual(self.store.upsert_following_accounts(accounts, 'test-endpoint'), 1)
        self.assertEqual(len(self.store.list_following_accounts()), 1)

    def test_following_listing_supports_count_and_offset(self):
        accounts = [
            {'user_id': 'user-1', 'nickname': 'one'},
            {'user_id': 'user-2', 'nickname': 'two'},
        ]
        self.store.upsert_following_accounts(accounts, 'test-endpoint')
        self.assertEqual(self.store.count_following_accounts(), 2)
        self.assertEqual(len(self.store.list_following_accounts(limit=1, offset=1)), 1)

    def test_following_profile_filter_requires_full_candidate_coverage(self):
        self.store.upsert_following_accounts([{'user_id': 'user-1', 'nickname': 'Systems Lab'}], 'test-endpoint')
        queued = self.store.queue_following_filter([{
            'user_id': 'user-1', 'nickname': 'Systems Lab', 'recent_notes': [], 'recent_note_ids': [],
        }])
        job = self.store.claim('profile-classifier')
        result = self.store.complete(job['job_id'], job['lease_token'], {
            'profiles': [{'user_id': 'user-1', 'decision': 'include', 'topics': [{'topic_id': 'research', 'score': .8}],
                          'score': .8, 'confidence': .8, 'reason': '系统科研账号'}],
            'model': 'fake', 'input_sha256': 'test',
        })
        self.assertEqual(queued['job_id'], job['job_id'])
        self.assertEqual(result['include'], 1)
        self.assertEqual(self.store.list_profile_candidates()[0]['decision'], 'include')

    def test_topic_versions_are_additive_and_disable_is_explicit(self):
        topic = self.store.upsert_topic({'slug': 'accelerator', 'name': '加速器',
                                         'include_keywords': ['GPU', 'NPU']})
        self.assertEqual(topic['active_version'], 1)
        topic = self.store.upsert_topic({'slug': 'accelerator', 'name': '加速器与互联',
                                         'include_keywords': ['GPU', 'NPU', '互联'], 'enabled': False})
        self.assertEqual(topic['active_version'], 2)
        self.assertEqual(topic['enabled'], 0)
        self.assertEqual(self.store.list_topics(enabled=False)[0]['policy']['include_keywords'][-1], '互联')
        self.assertTrue(self.store.set_topic_enabled('accelerator', False))
        self.assertTrue(any(row['slug'] == 'accelerator' for row in self.store.list_topics(enabled=False)))


if __name__ == '__main__':
    unittest.main()
