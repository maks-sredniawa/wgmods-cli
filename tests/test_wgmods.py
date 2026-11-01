import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))

import wgmods  # noqa: E402

GAME_VERSIONS = [{'version': '2.4.1.1', 'id': 227}, {'version': '2.4.1.0', 'id': 226}]


def mod(state=3, versions=('0.1.0',)):
    return {
        'id': 8085,
        'state': state,
        'localizations': [
            {'lang': {'code': 'ru'}, 'title': 'Таймер'},
            {'lang': {'code': 'en'}, 'title': 'Autoloader Shell Timer'},
        ],
        'versions': [
            {'version': v, 'status': 0, 'game_version': {'version': '2.4.1.1'}} for v in versions
        ],
    }


class WgmodsTest(unittest.TestCase):

    def test_lists_a_mod_with_its_state_and_versions(self):
        self.assertEqual(wgmods.describe_mod(mod(state=1)),
                         '8085  Autoloader Shell Timer  [in review]\n'
                         '      0.1.0 for WoT 2.4.1.1  pending review')

    def test_defaults_to_the_newest_game_version(self):
        self.assertEqual(wgmods.resolve_game_version(GAME_VERSIONS, None)['id'], 227)
        self.assertEqual(wgmods.resolve_game_version(GAME_VERSIONS, '2.4.1.0')['id'], 226)

    def test_rejects_a_game_version_the_site_does_not_offer(self):
        with self.assertRaisesRegex(wgmods.CliError, '2.4.1.1, 2.4.1.0'):
            wgmods.resolve_game_version(GAME_VERSIONS, '1.0')

    def test_refuses_a_version_that_already_exists(self):
        with self.assertRaisesRegex(wgmods.CliError, 'already has a version 0.1.0'):
            wgmods.check_can_add_version(mod(), '0.1.0', force=True)

    def test_refuses_while_in_review_unless_forced(self):
        for state in (1, 4):
            with self.assertRaisesRegex(wgmods.CliError, 'review'):
                wgmods.check_can_add_version(mod(state=state), '0.2.0', force=False)
            wgmods.check_can_add_version(mod(state=state), '0.2.0', force=True)
        wgmods.check_can_add_version(mod(state=3), '0.2.0', force=False)

    def test_builds_the_same_version_body_as_the_site(self):
        upload = {'id': 55, 'access_token': 'token'}
        self.assertEqual(wgmods.version_body(8085, 227, '0.2.0', upload, 'notes', None, True), {
            'mod': 8085,
            'game_version': 227,
            'version': '0.2.0',
            'is_visible': True,
            'temporary_version_file': 55,
            'temporary_version_file_access_token': 'token',
            'change_log': 'notes',
        })

    def test_reads_text_from_a_file_when_given_one(self):
        with tempfile.NamedTemporaryFile('w', suffix='.md', delete=False, encoding='utf-8') as f:
            f.write('- line\n\n')
        try:
            self.assertEqual(wgmods.read_text_option(None, f.name), '- line')
        finally:
            os.unlink(f.name)
        self.assertEqual(wgmods.read_text_option('inline', None), 'inline')

    def test_dry_run_and_force_are_flags_on_upload(self):
        args = wgmods.parser().parse_args(['upload', '8085', 'x.wotmod', '--version', '0.2.0', '--dry-run'])
        self.assertTrue(args.dry_run)
        self.assertFalse(args.force)
        self.assertIsNone(args.game_version)


if __name__ == '__main__':
    unittest.main()
