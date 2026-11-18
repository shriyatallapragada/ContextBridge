# Project guide and chat bridge

## Project guide

Choose a project and open **Project guide**. **Refresh guide** renders up to 128,000 characters of its README locally, with formatted headings, lists, tables, and code. Use **Open Markdown…** to read other project documents (including architecture docs), click local Markdown links to navigate, and **A- / A+** to change text size. Remote images and embedded scripts are not loaded. The documentation describes intended behavior, not verified implementation. Progress lists recorded commits and changed files; Next steps lists To-do and Blocker notes. Suggestions come from explicit rules about missing tests, unfinished changes, and missing plans. None of these screens sends a model request or requires Ollama. This is a browsable evidence guide, not a code-understanding engine; it cannot explain arbitrary algorithms automatically.

## Chat bridge

Use **Transfer a chatbot conversation** on the welcome screen, or **Chat bridge** in the sidebar. No project setup or Git repository is needed.

1. Paste chat text or import a UTF-8 `.txt` / `.md` file or a text-based PDF (maximum 100,000 extracted characters). Keep speaker labels if available.
2. Enter the next question and pin essential facts, decisions, and unresolved questions. Each field allows up to 1,000 characters.
3. Choose a size and prepare the handoff. The app keeps your goal, pinned facts, and a recent excerpt. It explicitly reports omitted text and may begin mid-message. It does not generate a semantic summary.
4. Review and edit the preview; copy it into a new chatbot conversation, save Markdown, or choose **Save PDF…** to export the reviewed preview as a paginated PDF. Editing can exceed the original size limit. Original text stays in memory; only Save writes a file. Clear removes the screen contents, not saved files or clipboard data.

The existing secret sanitizer runs when preparing a handoff. It cannot identify every personal detail; review the preview. Token estimates use characters divided by four without a tokenizer download. A small chat can get larger because of handoff instructions. Character reductions do not establish billed-credit savings or retained meaning.

## Browser helper (Chrome / compatible Chromium browsers)

The optional `browser-extension` folder contains a Manifest V3 extension. Open the browser's Extensions page, enable Developer mode, select **Load unpacked**, and choose that folder. Pin its toolbar icon.

Select the messages you want on a chatbot webpage. Click the extension, choose **Capture selected text**, review the preview, then **Save text file**. Import that file in ContextBridge. The helper reads only selected text on your explicit click, uses temporary active-tab access, and uploads nothing. Browser selection must contain the messages; hidden, unloaded, image, and attachment contents are not exported. No provider-specific export format or endpoint is used. This is not automatic account-history sync and has not been verified against live Gemini or ChatGPT sessions.

Extension exports are not sanitized until imported and prepared in the desktop app. Review private details before saving. The extension is a local unpacked prototype, not a published store extension.

## Fit and next steps

The project guide improves onboarding and makes evidence visible during handoff. Chat transfer extends the same continuity problem to a larger audience. Both are useful without a subscription. More ambitious automatic semantic summaries need an optional model and quality checks. Future work: role-aware provider export importers, user-selected complete message capture, saved chat sessions with explicit retention controls, and measurements comparing task success with full chats versus compact handoffs.

## Guide diagrams

The **Flowcharts** section renders native interactive diagrams without a browser engine, model, subscription, or network request. Pan by dragging; use + / - and Fit to zoom, or Expand for a larger window. The six section buttons stay visible in two rows; the active section has white text on dark maroon. Select a box to see its full path and source files.

**Project architecture** maps static Python imports in the selected project. It scans at most 180 Python modules, 200 directories, and approximately 2 MB of source, skips hidden/dependency/build/test folders, symlinks and oversized files, and shows the nine most connected component groups. Arrows indicate imports; only the selected component's outgoing arrows are shown. This is a partial static dependency map, not a verified runtime architecture. Unsupported languages and dynamic imports are not guessed.

**ContextBridge handoff workflow** explains capture → local storage → preparation → review → continuation. It is explicitly labelled as ContextBridge's workflow, not an inferred workflow for every project. Existing Mermaid fences are displayed as code; this release does not execute Mermaid or render arbitrary diagram languages from Markdown.

## PDF support in Chat bridge

**Import chat (PDF or text)…** reads PDFs locally in the background. Limits: 20 MB, 100 pages, 100,000 extracted characters, and 2 MB of decoded content per page. Page markers preserve source page numbers. Password-protected files must be unlocked before importing. Scanned/image-only PDFs need OCR elsewhere; OCR is not included. Mixed PDFs import available text and report pages skipped for having no extractable text. Images, attachments, and exact multi-column layout are not transferred. Review speaker labels, reading order, and essential facts before preparing a handoff. Imports do not silently truncate over-limit PDFs.

**Save PDF…** exports the editable handoff preview, with formatted Markdown, A4 pages, margins, and page numbers. It uses the desktop app's existing font/printing support and writes atomically, so an unsuccessful save does not replace an existing file. The export does not include the entire original conversation unless you put it in the preview. The preview is sanitized when prepared; any later manual additions are exported as reviewed. No subscription, external API, or Ollama is involved. The PDF reader is installed automatically with the GUI extra; standalone distributions must be rebuilt to include new features.
