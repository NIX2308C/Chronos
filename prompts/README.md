# Prompts

Every prompt the app sends a model lives here as a markdown file, one rule or fragment per file. Code never holds prompt wording; it only picks files and joins them (`prompts.py`: `get`, `has`, `fill`). Files are read once at startup. `{{name}}` is a placeholder filled by `prompts.fill`. Edit a file, restart, done. Change the code only to change which files are used or when.

| Folder | What it holds | Sent |
| --- | --- | --- |
| `tutor/core.md`, `boundaries.md`, `safety.md`, `style.md` | The general tutor prompt: persona, boundaries, safety, style | Always |
| `tutor/sources/` | Where facts come from; grounded vs open variants | Always (one variant per course setting) |
| `tutor/tutoring/` | Hints (`hints/`), assessed work, final answers, worked examples, general rules | Always (teacher toggles pick the variant) |
| `tutor/*.md` (rules, activities, outline, material, preferences, memory, summary, upload, language) | Headers for the optional sections | Only when that section has content |
| `tutor/user_turn.md`, `no_material.md` | How retrieved excerpts ride at the top of each student message | Every message |
| `preferences/` | Student style options: personalities, length, reading level, etc. | When the student chose them |
| `activities/` | Quiz / flashcards / concept map / review sheet generator: `request.md`, `guard.md`, one `instructions/` and one `shapes/` file per type, `conversation/` headings | `/tools/run` |
| `tools/<name>/` | Tool descriptions (`description.md`) and each parameter's description; `tools/instructions.md` is the call-format preamble | With the tutor's tool list |
| `background/` | Summaries, upload classifier, document catalogue, conversation categoriser | Separate small model calls |

Adding an activity type or personality: add its files here, then add the name to the matching list in code (`TOOL_SETTING_FOR_TYPE`, `ACTIVITY_SCHEMAS`, `PERSONALITIES`). Not prompts, so they stay in code: canned replies shown to students (blocked, ended, failure messages) and personality labels.
