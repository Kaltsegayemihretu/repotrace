# 15-minute walkthrough with someone new

**Goal:** find where people hesitate, misread an arrow, or cannot find the evidence. Not a demo: let them drive.

## Before you start
- One person who has **not** seen RepoTrace or StudentManagement. A classmate is fine.
- Open `prototype/index.html` in Chrome on your laptop, full screen.
- Say only: *"This is a tool for understanding a Python project you have never seen. Think out loud. I can't help; that's the point."*
- Do not explain the colours, rings or arrows. Watch whether they work it out.

## The three tasks (read each one aloud, one at a time)
1. *"Find where this project checks that an email is valid. Tell me the file."*
   - Success: they name `studentapp/validation/rules.py` (function `is_valid_email`).
2. *"Find `check_enrollment_rules`. What does it call, and what calls it?"*
   - Success: *calls is_duplicate_enrollment, is_course_full, check_student_exists, find_course_or_fail, …; called by create_enrollment and update_enrollment.* Ask: *"Which way do the arrows point? What does 'maybe' mean?"*
3. Click **Review a change** for them (the rename at step-07), then: *"Someone renamed a function. Which line will break, and which test would you run?"*
   - Success: `enrollment_service.py:32` and the listed test with its `python3 -m unittest …` command.

## Write down, for each task
| | Task 1 | Task 2 | Task 3 |
|---|---|---|---|
| Done? (yes / with a hint / no) | | | |
| Seconds | | | |
| Where they hesitated (what they were looking at) | | | |
| Anything they misread (arrow direction, rings, "maybe", versions) | | | |
| What they said out loud that surprised you | | | |

## Afterwards, ask
1. *"What does a dashed line mean? A yellow ring? 'Not traced'?"* (checks the legend works without you)
2. *"Which screen would you trust least, and why?"*
3. *"If you had this on your own project, what would you use it for first?"*

Two or three people are enough. Fix whatever two of them stumble on, before building it into the real app.
