#!/usr/bin/env -S uv run --quiet --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["playwright==1.55.0"]
# ///
"""Upload new versions of your mods to the World of Tanks Mod Hub (wgmods.net)."""

import argparse
import base64
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

SITE = 'https://wgmods.net'
MODDER_SECTION = SITE + '/modder-section/'
PROFILE_DIR = Path(os.environ.get('WGMODS_PROFILE', Path.home() / '.local/share/wgmods-cli/profile'))

MOD_STATES = {
    -1: 'default', 0: 'draft', 1: 'in review', 2: 'rejected', 3: 'published',
    4: 'published, update in review', 5: 'published, update rejected', 6: 'hidden',
}
VERSION_STATUSES = {0: 'pending review', 1: 'published', 2: 'rejected'}
LOCKED_STATES = {1, 4}

API_JS = """
async ({path, method, body}) => {
    const response = await fetch(path, {
        method,
        credentials: 'include',
        headers: {
            'Accept': 'application/json',
            'Content-Type': 'application/json; charset=utf-8',
            'X-Requested-With': 'XMLHttpRequest',
            'X-CSRFToken': window.__SETTINGS__.csrfToken,
        },
        body: body === null ? undefined : JSON.stringify(body),
    });
    return {status: response.status, text: await response.text()};
}
"""

UPLOAD_JS = """
async ({path, name, data}) => {
    const bytes = Uint8Array.from(atob(data), c => c.charCodeAt(0));
    const form = new FormData();
    form.append('source', new Blob([bytes]), name);
    const response = await fetch(path, {
        method: 'POST',
        credentials: 'include',
        headers: {'X-Requested-With': 'XMLHttpRequest', 'X-CSRFToken': window.__SETTINGS__.csrfToken},
        body: form,
    });
    return {status: response.status, text: await response.text()};
}
"""


class CliError(Exception):
    pass


def english_title(mod):
    titles = {loc['lang']['code']: loc['title'] for loc in mod.get('localizations', [])}
    return titles.get('en') or next(iter(titles.values()), '?')


def describe_mod(mod):
    lines = ['%d  %s  [%s]' % (mod['id'], english_title(mod), MOD_STATES.get(mod.get('state'), mod.get('state')))]
    for version in mod.get('versions', []):
        lines.append('      %s for WoT %s  %s' % (
            version['version'], version['game_version']['version'],
            VERSION_STATUSES.get(version['status'], version['status'])))
    return '\n'.join(lines)


def resolve_game_version(game_versions, wanted):
    if not game_versions:
        raise CliError('The site lists no game versions.')
    if wanted is None:
        return game_versions[0]
    for game_version in game_versions:
        if game_version['version'] == wanted:
            return game_version
    raise CliError('Game version %s is not offered. Choose one of: %s' % (
        wanted, ', '.join(gv['version'] for gv in game_versions)))


def version_body(mod_id, game_version_id, version, upload, changelog, comment, visible):
    body = {
        'mod': mod_id,
        'game_version': game_version_id,
        'version': version,
        'is_visible': visible,
        'temporary_version_file': upload['id'],
        'temporary_version_file_access_token': upload['access_token'],
    }
    if changelog:
        body['change_log'] = changelog
    if comment:
        body['comment'] = comment
    return body


def check_can_add_version(mod, version, force):
    if any(existing['version'] == version for existing in mod.get('versions', [])):
        raise CliError('%s already has a version %s.' % (english_title(mod), version))
    if mod.get('state') in LOCKED_STATES and not force:
        raise CliError('%s is %s; the site does not let you edit it until the review ends. '
                       'Pass --force to try anyway.' % (english_title(mod), MOD_STATES[mod['state']]))


def read_text_option(text, path):
    if path:
        return Path(path).read_text(encoding='utf-8').strip()
    return text


class Site(object):

    def __init__(self, headless=True):
        from playwright.sync_api import sync_playwright
        PROFILE_DIR.mkdir(parents=True, exist_ok=True)
        self._playwright = sync_playwright().start()
        try:
            self._context = self._playwright.chromium.launch_persistent_context(
                str(PROFILE_DIR), channel='chrome', headless=headless)
        except Exception:
            self._playwright.stop()
            raise
        self.page = self._context.pages[0] if self._context.pages else self._context.new_page()

    def close(self):
        self._context.close()
        self._playwright.stop()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def open_modder_section(self):
        self.page.goto(MODDER_SECTION, wait_until='domcontentloaded')
        self.page.wait_for_function('window.__SETTINGS__ && window.__SETTINGS__.csrfToken')

    def settings(self, key):
        return self.page.evaluate('key => window.__SETTINGS__[key]', key)

    def api(self, path, method='GET', body=None):
        result = self.page.evaluate(API_JS, {'path': path, 'method': method, 'body': body})
        return self._decode(path, result)

    def upload(self, path, file_path):
        data = base64.b64encode(file_path.read_bytes()).decode('ascii')
        result = self.page.evaluate(UPLOAD_JS, {'path': path, 'name': file_path.name, 'data': data})
        return self._decode(path, result)

    @staticmethod
    def _decode(path, result):
        status, text = result['status'], result['text']
        if status in (401, 403):
            raise CliError('Not signed in to wgmods.net. Run: wgmods login')
        if status >= 400:
            raise CliError('%s answered %d: %s' % (path, status, text[:500]))
        return json.loads(text) if text else None

    def owner_mods(self):
        mods = self.api('/api/mods/owner/')
        if not isinstance(mods, list):
            raise CliError('Not signed in to wgmods.net. Run: wgmods login')
        return mods


def chrome_login_command(chrome):
    # Google refuses sign-in in a browser Playwright controls, so the sign-in runs in plain Chrome.
    # --password-store=basic matches Playwright's launch, or the headless runs can't decrypt the cookies.
    return [chrome, '--user-data-dir=%s' % PROFILE_DIR, '--password-store=basic',
            '--no-first-run', '--no-default-browser-check', '--new-window', SITE + '/']


def cmd_login(args):
    chrome = shutil.which(args.chrome)
    if chrome is None:
        raise CliError('Chrome not found as %r. Pass its path with --chrome.' % args.chrome)
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    print('Sign in to wgmods.net in the Chrome window that opened, then close that window.')
    subprocess.run(chrome_login_command(chrome), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    with Site() as site:
        site.open_modder_section()
        mods = site.owner_mods()
    print('Signed in. %d mod(s) on this account.' % len(mods))


def cmd_mods(args):
    with Site() as site:
        site.open_modder_section()
        mods = site.owner_mods()
        if args.json:
            print(json.dumps(mods, indent=2, ensure_ascii=False))
            return
        for mod in mods:
            print(describe_mod(mod))


def cmd_game_versions(_args):
    with Site() as site:
        site.open_modder_section()
        for game_version in site.settings('gameVersions'):
            print('%s  (id %d)' % (game_version['version'], game_version['id']))


def cmd_upload(args):
    file_path = Path(args.file)
    if not file_path.is_file():
        raise CliError('No such file: %s' % file_path)
    changelog = read_text_option(args.changelog, args.changelog_file)
    comment = read_text_option(args.comment, args.comment_file)
    with Site() as site:
        site.open_modder_section()
        mod = next((m for m in site.owner_mods() if m['id'] == args.mod), None)
        if mod is None:
            raise CliError('Mod %d is not one of yours. Run: wgmods mods' % args.mod)
        check_can_add_version(mod, args.version, args.force)
        game_version = resolve_game_version(site.settings('gameVersions'), args.game_version)
        summary = '%s %s for WoT %s, %s, %s (%d bytes)' % (
            english_title(mod), args.version, game_version['version'],
            'hidden' if args.hidden else 'visible', file_path.name, file_path.stat().st_size)
        if args.dry_run:
            print('Would upload ' + summary)
            if changelog:
                print('Changelog:\n' + changelog)
            return
        upload = site.upload('/api/mods/mod_file_upload', file_path)
        upload = upload[0] if isinstance(upload, list) else upload
        body = version_body(mod['id'], game_version['id'], args.version, upload, changelog, comment, not args.hidden)
        created = site.api('/api/mods/versions/', 'POST', body)
        print('Uploaded ' + summary)
        state = created.get('mod_state') if isinstance(created, dict) else None
        if state is not None:
            print('Mod is now: %s' % MOD_STATES.get(state, state))


def parser():
    root = argparse.ArgumentParser(prog='wgmods', description=__doc__)
    commands = root.add_subparsers(dest='command', required=True)
    login = commands.add_parser('login', help='sign in once in a Chrome window; the session is kept for later runs')
    login.add_argument('--chrome', default='google-chrome', help='Chrome executable (default: google-chrome)')
    mods = commands.add_parser('mods', help='list your mods, their ids, states and versions')
    mods.add_argument('--json', action='store_true', help='print the raw API response')
    commands.add_parser('game-versions', help='list the game versions the site accepts')
    upload = commands.add_parser('upload', help='add a new version to one of your mods')
    upload.add_argument('mod', type=int, help='mod id, from: wgmods mods')
    upload.add_argument('file', help='the .wotmod or .zip to upload')
    upload.add_argument('--version', required=True, help='the mod version, e.g. 0.2.0')
    upload.add_argument('--game-version', help='e.g. 2.4.1.1; defaults to the newest the site offers')
    changelog = upload.add_mutually_exclusive_group()
    changelog.add_argument('--changelog', help='changelog text')
    changelog.add_argument('--changelog-file', help='read the changelog from a file')
    comment = upload.add_mutually_exclusive_group()
    comment.add_argument('--comment', help='note for the moderators')
    comment.add_argument('--comment-file', help='read the note for the moderators from a file')
    upload.add_argument('--hidden', action='store_true', help='upload the version without showing it')
    upload.add_argument('--force', action='store_true', help='try even while the mod is in review')
    upload.add_argument('--dry-run', action='store_true', help='check everything, upload nothing')
    return root


COMMANDS = {'login': cmd_login, 'mods': cmd_mods, 'game-versions': cmd_game_versions, 'upload': cmd_upload}


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        COMMANDS[args.command](args)
    except CliError as error:
        print('wgmods: %s' % error, file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
