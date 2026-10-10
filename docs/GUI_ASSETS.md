# Artwork guide for the ContextBridge app

The app ships with simple placeholder drawings so it always looks finished. Every picture
below can be replaced by dropping an SVG with the **exact file name** into
`contextbridge/gui/assets/`. No code changes are needed: if the file exists, the app uses it.
Delete the file to go back to the placeholder.

You can see your artwork by running the app (`cb gui`, or `uv run python -m contextbridge.gui.app`).

## The palette

Please keep to the four swatches (plus tints of them) so everything feels like one family.

| Name | Hex | Used for |
|---|---|---|
| Red Chai | `#632626` | Sidebar, headings, body text |
| Masala | `#9D5353` | Buttons, selected items |
| Cinnamon Toast | `#BF8B67` | Lines, icons and soft accents (never for text) |
| Cardamom | `#DBCB96` | Highlights and the soft circle behind illustrations |

The page background is a very light tint of Cardamom (`#F8F5EA`) and cards are lighter again
(`#FCFBF7`). Cream text on the dark colours is `#FBF9F2`.

## What to make

Every file is an SVG on a **transparent background**. Use outlines and flat colour, with no
gradients, shadows or tiny details, because the pictures are shown fairly small.

| File | Size / viewBox | Shown at | Where it appears |
|---|---|---|---|
| `app_icon.svg` | 1024 x 1024 | 16 px to 512 px | The app icon (Dock, window, installer), the logo in the sidebar |
| `welcome.svg` | 240 x 240 | about 190 px | The first screen, and the "set this project up first" prompts |
| `notes.svg` | 240 x 240 | about 150 px | Notes screen with no notes yet |
| `changes.svg` | 240 x 240 | about 150 px | Changes screen with nothing changed yet |
| `done.svg` | 240 x 240 | about 130 px | The "Handoff written" success screen |
| `icon_home.svg` | 24 x 24 | 22 px | Sidebar: Home |
| `icon_notes.svg` | 24 x 24 | 22 px | Sidebar: Notes |
| `icon_changes.svg` | 24 x 24 | 22 px | Sidebar: Changes |
| `icon_handoff.svg` | 24 x 24 | 22 px | Sidebar: Hand off |
| `icon_settings.svg` | 24 x 24 | 22 px | Sidebar: Settings |

### Tips

- **Sidebar icons** sit on Red Chai, so draw them in cream (`#FBF9F2`). Use a 1.7 px stroke with
  round caps to match the placeholders. The same picture is used for every state, so colour
  is baked in.
- **Illustrations** currently have a soft Cardamom circle behind a Masala and Cinnamon Toast
  line drawing. You can keep that idea or change it, as long as it stays calm.
- **App icon:** macOS expects the rounded-square shape to be part of the artwork. The
  placeholder is a Red Chai rounded square with a cream arch bridge. When you provide
  `app_icon.svg`, re-run the icon step of the build (see `docs/PACKAGING.md`) to refresh the
  `.icns` / `.ico` files.
- Keep file sizes small (under 50 KB each). The app loads these at startup.
- Please don't use the Claude, Cursor or other companies' logos for the agent cards. Those
  cards are deliberately text-only, and we shouldn't imply endorsement.

## Not sure it's picked up?

The app only looks for these exact names (case-sensitive). A file that isn't valid SVG is
ignored and the placeholder is shown instead, so a typo never breaks the app.
