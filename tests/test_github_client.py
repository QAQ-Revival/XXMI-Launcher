import re
import unittest
from unittest.mock import Mock, patch

import requests

from core.utils.github_client import GitHubClient
from core.utils.security import Security


BASE = 'https://github.com/SpectrumQT/XXMI-Launcher'
VERSION = re.compile(r'^v(\d+\.\d+\.\d+)$')


def response(url, text='', data=None, status=200):
    result = Mock(url=url, text=text, status_code=status)
    result.json.return_value = data
    if status >= 400:
        result.raise_for_status.side_effect = requests.HTTPError(response=result)
    return result


class GitHubClientTests(unittest.TestCase):
    def setUp(self):
        self.client = GitHubClient()
        self.client.configure(access_token='private-token', verify_ssl=True, proxy_config=None)
        self.client.proxy_manager.proxies = {'https': 'http://127.0.0.1:8080'}
        self.security = Security()
        self.security.generate_key_pair()
        self.archive = b'official portable archive'
        self.signature = self.security.sign(self.archive)
        self.page = response(BASE + '/releases/tag/v2.4.9',
            '<div class="markdown-body"><h2>Changes</h2><ul><li>A &amp; B</li></ul>'
            '<h2>Signature</h2><ul><li>bXNp</li></ul>'
            f'<h2>Signature Extra</h2><ul><li>{self.signature}</li></ul></div>')
        self.assets = response(BASE + '/releases/expanded_assets/v2.4.9',
            '<a href="/SpectrumQT/XXMI-Launcher/releases/download/v2.4.9/XXMI-Launcher-Portable-v2.4.9.zip">ZIP</a>'
            '<a href="/SpectrumQT/XXMI-Launcher/releases/download/v2.4.9/Manifest.json">Manifest</a>'
            '<a href="https://example.com/XXMI-Launcher-Portable-v2.4.9.zip">foreign</a>')

    def fetch(self, pre_release=False):
        return self.client.fetch_latest_release('SpectrumQT', 'XXMI-Launcher', VERSION,
            'XXMI-Launcher-Portable-v%s.zip', re.compile(r'^## Signature Extra[\r\n]+- ([A-Za-z0-9+/=]+)$', re.M), pre_release)

    def test_rate_limit_uses_official_page_and_keeps_portable_signature(self):
        with patch('core.utils.github_client.requests.get', side_effect=[response('', status=403), self.page, self.assets]) as get:
            version, url, signature, notes, manifest = self.fetch()
        self.assertEqual(version, '2.4.9')
        self.assertEqual(url, BASE + '/releases/download/v2.4.9/XXMI-Launcher-Portable-v2.4.9.zip')
        self.assertEqual(manifest, BASE + '/releases/download/v2.4.9/Manifest.json')
        self.assertIn('A & B', notes)
        self.assertTrue(self.security.verify(signature, self.archive))
        self.assertFalse(self.security.verify(signature, self.archive + b'tampered'))
        for call in get.call_args_list[1:]:
            self.assertTrue(call.kwargs['verify'])
            self.assertEqual(call.kwargs['proxies'], self.client.proxy_manager.proxies)
            self.assertNotIn('headers', call.kwargs)

    def test_api_timeout_and_non_json_response_use_page(self):
        invalid = response('')
        invalid.json.side_effect = ValueError('not JSON')
        for failure in (requests.Timeout(), invalid):
            with self.subTest(failure=type(failure).__name__), patch('core.utils.github_client.requests.get', side_effect=[failure, self.page, self.assets]):
                self.assertEqual(self.fetch()[0], '2.4.9')

    def test_successful_api_does_not_fetch_page(self):
        data = {'tag_name': 'v2.4.9', 'body': '## Changes\nNotes\n## Signature Extra\n- ' + self.signature,
                'assets': [{'name': 'XXMI-Launcher-Portable-v2.4.9.zip', 'browser_download_url': BASE + '/releases/download/v2.4.9/XXMI-Launcher-Portable-v2.4.9.zip'}]}
        with patch('core.utils.github_client.requests.get', return_value=response('', data=data)) as get:
            self.assertEqual(self.fetch()[0], '2.4.9')
        self.assertEqual(get.call_count, 1)

    def test_page_connection_is_retried_once_and_offline_stays_an_error(self):
        with patch('core.utils.github_client.requests.get', side_effect=[requests.Timeout(), requests.ConnectionError(), self.page, self.assets]):
            self.assertEqual(self.fetch()[0], '2.4.9')
        with patch('core.utils.github_client.requests.get', side_effect=requests.Timeout()) as get:
            with self.assertRaises(ValueError):
                self.fetch()
            self.assertEqual(get.call_count, 3)

    def test_certificate_token_and_prerelease_errors_do_not_use_stable_page(self):
        cases = [(requests.exceptions.SSLError(), False),
                 (response('', data={'message': 'Bad credentials', 'status': '401'}, status=401), False),
                 (requests.Timeout(), True)]
        for failure, prerelease in cases:
            with self.subTest(failure=type(failure).__name__), patch('core.utils.github_client.requests.get', side_effect=[failure]) as get:
                with self.assertRaises((ValueError, ConnectionError)):
                    self.fetch(prerelease)
                self.assertEqual(get.call_count, 1)

    def test_wrong_release_or_missing_signature_is_rejected(self):
        for page in [response('https://example.com/releases/tag/v2.4.9', self.page.text),
                     response(BASE + '/releases/tag/v2.4.9', '<div class="markdown-body"><h2>Changes</h2></div>')]:
            with self.subTest(page=page.url), patch('core.utils.github_client.requests.get', side_effect=[requests.Timeout(), page, self.assets]):
                with self.assertRaises(ValueError):
                    self.fetch()


if __name__ == '__main__':
    unittest.main()
