# RepoTrace Lab: the guided prototype

> **17 Nov 2026: "Explore the project" is now the Obsidian prototype itself.** `repotrace/report/lab/explore.html` is that page, ported line for line (left controls, Physical/Logical/Conceptual, legend, ⌘K, breadcrumb bar, review mode) and shown inside the app; it replaces the step-by-step map and the short-lived Web view. Check the saved demo with `qa/browser/run_lab_saved.sh OUT`.
>
> **17 Nov 2026 (later): smoother tabs.** The explorer frame is created once and only hidden when you leave it, so coming back is instant and keeps your zoom, selection and open folders. The side rail eases between wide and narrow, pages fade in, and a project opened without a comparison no longer breaks Start or Check a change (they say no comparison was chosen). A page that fails to draw now shows a message instead of leaving the old page on screen.
>
> **Update 16 Nov 2026:** the Lab screens now live in `repotrace/report/lab/` and run **live** in the real app (`python3 -m repotrace --lab`). This folder keeps only the saved-data demo: `index.html` loads the same JavaScript and CSS from the package, and `data.js` is built by `make_lab_data.py` through the **same adapter** (`repotrace/report/lab_data.py`) the real app uses. Everything below describes the design fixes made on the copy taken from ChatGPT.


A copy of the ChatGPT-built design (`repotrace-investigation-lab.hyilmaw.chatgpt.site`), taken on 13 Nov 2026, with its
problems fixed. It runs on its own `data.js`: real RepoTrace analysis of StudentManagement, built by `make_lab_data.py`,
including the engine's test shortlist. Open `index.html` in Chrome. No install, no server, no internet.

## What was fixed (compared with the live ChatGPT site)
1. **Nothing is typed in by hand any more.** The rename sentence, the concern text, "what to check", the guided first
   steps, the project name, the commit and the review titles are all built from the data. The old page said "the
   course lookup function got a new name" and "the enrollment code still uses the old name": true for this example,
   wrong for any other project.
2. **The tests the change cut off come first.** Tests whose path to the changed code exists only in the old version
   are grouped under "Run these first", called out on the overview, and counted on the list of changes. For the
   rename at step-07 that is 9 of the 12 tests, including `test_delete_student_removes_enrollments`.
3. **Plain labels.** "Possible target" → "Maybe: depends on an assumption"; "Supported by code" → "Sure: written in
   the code"; "before + after evidence" → a sentence saying which version shows the connection. setUp hooks and
   fixtures on a path are explained in words.
4. **Search ranks by usefulness, not alphabet.** Ties are broken by how much other code uses a definition; plumbing
   (`__init__`, `to_dict`) and demo scripts rank lower; more synonyms (delete/remove, find/fetch/get, …).
   "how does a student enroll in a course?" now puts `EnrollmentService.create_enrollment` second instead of outside the
   top six.
5. **The function view shows up to 10 connections** (was 6), changed functions and places-to-check never hidden, and
   the drawing is no longer squashed into a fixed box.
6. **The function page's test list follows unittest `setUp` hooks** and, inside a review, shows the engine's shortlist
   for that function instead of its own weaker search. pytest fixtures are still not followed there, and it says so.
7. **Phones keep the navigation** as a bar across the top (it used to disappear below 600px).
8. Esc inside a search box leaves the box first, then goes back. The Cloudflare tracking script was removed.

## Still missing, on purpose
- The whole-project web of connections and the Physical/Logical/Conceptual views from `prototype/index.html`. This
  design drills down one level at a time instead. Decide which you want before building the real app.
- Live analysis: the data is baked in. Regenerate with `python3 prototype/make_data.py ../StudentManagement`.

## Check it
`node prototype/lab/check.mjs OUTDIR` drives it in headless Chrome with real clicks, typing and keys and checks each
fix above (10 checks, screenshots in `screenshots/`). `PAGE=https://… node prototype/lab/check.mjs OUTDIR` runs the
same checks against a hosted copy.
