# wgmods-cli

Uploads a new version of one of your mods to the World of Tanks Mod Hub, [wgmods.net](https://wgmods.net), from the command line.

The Mod Hub has no public upload API, so this drives the same private JSON API the site's own pages use. It runs inside your installed Google Chrome, through Playwright, with a profile of its own (`~/.local/share/wgmods-cli/profile`). You sign in once in an ordinary Chrome window; later runs reuse that session headless. It never touches your normal Chrome profile.

## Setup

Needs [uv](https://docs.astral.sh/uv/) and Google Chrome. uv fetches Playwright on the first run; no browser download is needed.

```sh
ln -s "$PWD/wgmods.py" ~/.local/bin/wgmods
wgmods login
```

`login` opens a plain Chrome window on wgmods.net with the tool's profile. Sign in (Wargaming account, or Google through it), then close the window; `login` then checks the session and prints how many mods the account has. The sign-in can't run in the Playwright-controlled window because Google refuses sign-in from automated browsers. Run `login` again whenever a command says you're not signed in.

## Use

```sh
wgmods mods                       # your mods: id, title, state, versions
wgmods game-versions              # the game versions the site accepts
wgmods upload 8085 dist/maksforge.autoloader_timer_0.2.0.wotmod \
    --version 0.2.0 --changelog-file CHANGELOG.txt --dry-run
```

`upload` adds the file as a new version and sends it to moderation, as the site's *Add update* form does. It defaults to the newest game version; pick another with `--game-version`. `--comment` is a note for the moderators, `--hidden` uploads the version without showing it, and `--dry-run` checks the sign-in, the mod, the version and the game version without uploading anything.

It refuses a version number the mod already has, and refuses while the mod is in review (state *in review* or *published, update in review*), because the site won't let you edit a mod then either. `--force` tries anyway.

## How it works

Each command opens `https://wgmods.net/modder-section/` and calls the API from inside that page, so the request carries the session cookie and the page's CSRF token exactly as the site's own requests do:

1. `GET /api/mods/owner/` lists your mods.
2. `POST /api/mods/mod_file_upload` (multipart field `source`) stores the file and returns its `id` and `access_token`.
3. `POST /api/mods/versions/` creates the version from the mod id, the game version id, the version number, the changelog and that file.

The game versions come from `window.__SETTINGS__.gameVersions` on the same page. If Wargaming changes these endpoints, the commands fail with the status code and the start of the response.

## Tests

```sh
python3 tests/test_wgmods.py
```
