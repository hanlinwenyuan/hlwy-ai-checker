import json
import unittest
from unittest.mock import Mock, patch

import start


class ProxyReasoningTests(unittest.TestCase):
    def setUp(self):
        self.handler = object.__new__(start.ProxyHandler)
        self.handler.path = '/chat/completions'
        self.success = Mock(status_code=200)

    def post(self, body):
        return self.handler._post_adaptive(
            'https://example.invalid/v1' + self.handler.path,
            json.dumps(body).encode(), {'content-type': 'application/json'},
        )

    def test_reasoning_timeout_and_body_preserved_for_all_protocols(self):
        cases = [
            ('/chat/completions', {'reasoning_effort': 'high', 'max_completion_tokens': 16384}),
            ('/responses', {'reasoning': {'effort': 'xhigh'}, 'max_output_tokens': 16384}),
            ('/messages', {'thinking': {'type': 'adaptive'}, 'output_config': {'effort': 'high'}}),
            ('/messages', {'thinking': {'type': 'enabled', 'budget_tokens': 4096}, 'max_tokens': 4352}),
        ]
        for path, body in cases:
            with self.subTest(path=path, body=body):
                self.handler.path = path
                with patch.object(start._session, 'post', return_value=self.success) as post:
                    self.assertIs(self.post(body), self.success)
                    self.assertEqual(json.loads(post.call_args.kwargs['data']), body)
                    self.assertEqual(post.call_args.kwargs['timeout'], (10, 180))

    def test_default_timeout_is_unchanged(self):
        with patch.object(start._session, 'post', return_value=self.success) as post:
            self.post({'max_tokens': 256})
            self.assertEqual(post.call_args.kwargs['timeout'], 30)

    def test_token_parameter_retry_preserves_reasoning_and_timeout(self):
        error = Mock(status_code=400, text=(
            '{"error":{"code":"unsupported_parameter","message":'
            '"max_completion_tokens is unsupported, use max_tokens"}}'
        ))
        body = {'model': 'test', 'reasoning_effort': 'high', 'max_completion_tokens': 16384}
        with patch.object(start._session, 'post', side_effect=[error, self.success]) as post:
            self.assertIs(self.post(body), self.success)
            self.assertEqual(post.call_count, 2)
            retry = post.call_args.kwargs
            self.assertEqual(json.loads(retry['data']), {
                'model': 'test', 'reasoning_effort': 'high', 'max_tokens': 16384,
            })
            self.assertEqual(retry['timeout'], (10, 180))

    def test_unsupported_effort_is_not_silently_removed(self):
        error = Mock(status_code=400, text=(
            '{"error":{"code":"unsupported_parameter","message":"reasoning_effort is unsupported"}}'
        ))
        with patch.object(start._session, 'post', return_value=error) as post:
            self.assertIs(self.post({'reasoning_effort': 'high'}), error)
            post.assert_called_once()


if __name__ == '__main__':
    unittest.main()
