# Building the standalone app

The desktop app can be turned into a normal double-click application that doesn't need
Python, a terminal, or `pip`. It's built with [PyInstaller](https://pyinstaller.org).

## Build it (macOS, Windows or Linux)

```bash
uv sync --all-extras                 # includes the gui and build extras
uv run python scripts/build_app.py
```

You get `dist/ContextBridge.app` on a Mac (or `dist/ContextBridge/` on Windows and Linux).
The script does four things:

1. Draws the app icon (using your `app_icon.svg` if you added one) into `.icns` / `.ico` files.
2. Saves the tokenizer data inside the app, so token counting works on the first run, offline.
3. Runs PyInstaller with `packaging/ContextBridge.spec`.
4. **Starts the finished app to check it works**: `--version`, `--cli --version`, and
   `--self-test` (which parses code in all six languages, counts tokens, finds git, and builds
   the window, all from inside the bundle). If any of that fails, the build fails.

Use `--skip-icons` to reuse icons you already made. A Mac build is about 130 MB.

Build on the platform you are targeting: a Mac build only runs on a Mac (and an Apple Silicon
build only on Apple Silicon unless you build on an Intel Mac too).

## Giving the app to someone else (macOS)

The app is **not signed**, so macOS shows a warning the first time it's opened on another Mac.
Tell people to:

1. Move `ContextBridge.app` into **Applications**.
2. **Right-click it and choose Open**, then click **Open** in the dialog. (Only needed once.)

To share it, zip it the way macOS likes: `ditto -c -k --keepParent dist/ContextBridge.app ContextBridge.zip`.

### Removing the warning for good

That needs an Apple Developer ID certificate (a paid Apple Developer membership, $99/year) to
**sign** and **notarize** the app. In outline: `codesign` the bundle with your Developer ID and
the hardened runtime, submit it with `xcrun notarytool submit ... --wait`, then `xcrun stapler
staple` the result. This repo doesn't do that yet because it needs your certificate.

## How the app and Git work together

When you turn on "Remember my commits automatically", ContextBridge adds a small helper to
the project's Git hooks. Inside the packaged app that helper runs the app itself with a
`--cli` flag (`ContextBridge --cli hook post-commit`), which behaves like the `cb` command and
**never opens a window**. If you move or delete the app later, the helper quietly does nothing;
turn the setting off and on again in Settings to point it at the new location.

## Other things worth knowing

- **Git is required** for tracking code changes. macOS prompts to install the Command Line
  Tools the first time it's needed; the app shows a friendly note if Git is missing.
- **Running tests from the app** goes through your login shell, so tools like `npm`, `pytest`
  and `cargo` are found even though apps started from Finder have a bare PATH.
- **Semantic search** (Chroma) and **cloud AI** are left out of the app to keep it small. The
  command line (`pip install 'contextbridge[vector,cloud]'`) still has them.
- **Windows and Linux builds** use the same recipe but have not been tested by hand. The
  `Build app` GitHub workflow builds and checks them.
