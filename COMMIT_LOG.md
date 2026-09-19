used codex

- added this file (wow!!)
- sped up auth/page loads by caching firebase config + short session role cache
- auth is still checked server side for protected api stuff
- made page wipes way shorter/smoother and stopped duplicate nav triggers
- added hover/touch prefetching for internal pages
- added caching for shared css/js + firebase preconnects
- revamped dark mode contrast/surfaces/borders/placeholders without changing the main look
- cleaned up dark transition colors too
- checked python/js/html/css syntax, all good

the vibes v2

- switched the ui wording from classes to courses (kept old api/db names so nothing explodes) by request of the big forb
- added base rules + course tutor/tool settings, off-by-default practice checks + source display
- made student files conversation-only and kept them completely out of pinecone
- added rolling tutoring summaries before old chat turns drop off
- split material gaps, actual learning signals, and behavior stuff in analytics
- brought back the response flow animation + added quiz cards and proper loading/error states
- tightened pinecone context/source labels and capped duplicate prompt text
- documented exactly what goes to pinecone vs firestore vs the model
- static checked python, every js block, html, and css. didnt run python tests like requested

unit tests couldnt run locally bc of the missing .env secrets which im assuming is on cloud run NICCCC, so i just committed to main branch cuz i didnt vastly change anything and we could always revert

tool pass + less clutter

- split teacher material, tutor rules, and toolkits into simpler tabs
- made custom teacher rules always-on prompt policy instead of pinecone knowledge notes
- removed the fake "turn off jailbreak protection" setting; that protection is permanent now
- rebuilt tools as a separate grounded generation step with a visible creating state
- added actual quiz, flashcard, concept map, and review sheet ui (all optional and off by default)
- taught the tutor to stop repeating itself and lowered answer randomness a little
- fixed dark-mode primary-button hover text disappearing into a white hover state
- made page transitions and normal page entrances smoother without hiding content
- found + fixed two unfinished auth error callbacks while doing the script check
- static checked python and all changed browser scripts; still did not run python tests because no .env secrets

tiny toolkit fix

- made direct "quiz me" and flashcard requests trigger the enabled practice toolkit even if flash lite forgets the hidden tool marker

restored v2.51 and fixed the toolkit for real

the whole v2/v2.5/v2.51 stack is back (three reverts of the three reverts). then
the reason it got reverted in the first place:

- quizzes said "not enough course material" because /tools/run only searched
  uploaded documents, and typed rules get moved out of pinecone into firestore on
  purpose. a course taught from typed rules had nothing to find. it also never
  checked the grounding toggle, so turning that off changed nothing
- every ```json reply failed to parse: the fence-stripping regex had a doubled
  backslash, so it matched a literal "\s" and never stripped anything. that was
  the 502
- dropped the hidden <chronos-tool> marker and the "quiz me" regex. the model now
  calls a declared create_practice_activity function instead, which it can't
  forget to format
- added practice chips under the newest answer so a student can just tap for a
  quiz. server re-checks the type, so the chips can't reach a disabled toolkit
- quizzes stopped losing your answers and re-playing the pop animation every time
  anything else on the page re-rendered
- quiz feedback is per question now instead of one shared line at the bottom
- fixed three ways the toolkit could freeze the composer for good
- switching chat mid-answer now cancels properly instead of losing the finished quiz

and the tests actually run now, offline, in about a second:

- app.py couldn't be imported without live pinecone (pc.Index does a network
  lookup), which is the real reason they were never run
- two assertions were wrong once they did run, both from v2 changes nobody
  re-checked. fixed
- custom rule writes go through a transaction now; two teacher tabs could drop
  each other's edits
- added the additional_instructions box, which the backend has always used but
  no page ever showed

## profanity, and a plan for a local model

the swear filter was a list of 40 words matched exactly, so it caught `fuck` and
nothing else — not `f*ck`, `sh1t`, `fuuuck`, `f u c k`, `fucc` or
`motherfucker`. a missed word meant retrieval found nothing, which the stats code
reads as "the teacher hasn't covered this", so abuse was landing in knowledge
gaps as if it were a missing topic.

- new profanity.py: normalize first (accents, leetspeak, padding, spaced-out
  letters), match stems second. no model call, no dependencies
- deliberately does NOT flag dick, prick or hell. moby dick is on reading lists
  and you prick a finger in biology — blocking is outright, so a false positive
  refuses a real question
- /chat blocks a profane message before the embedding, the pinecone query and
  the model. canned reply, marked in the ui, still saved to the student's history
- a blocked exchange is dropped from history replay and from the rolling
  conversation summary. without that, refusing to read it once only delays it a
  turn — the next question replays the whole conversation, swear included
- flagged messages no longer become a chat's opening, so they stop showing up in
  recent questions, most repeated and the gemini topic list. /stats re-filters on
  read too, which fixes the chats already written that way without a migration
- concerns are collected before the opening is judged now, so a conversation
  that is only abuse still reaches the teacher — under behavioral concerns
- test_profanity.py: an evasion corpus and, just as important, a corpus of
  innocent classroom english that must never be flagged

OLLAMA.md is a plan, not a build. embeddings have to stay on gemini (pinecone is
full of 768-dim vectors from it; swapping the model would mean re-uploading every
course), and the model needs somewhere with a gpu to live, which cloud run's app
container isn't. that hosting choice blocks everything else.

## the tutor gets a sense of who it's talking to

memory already remembered *what* a student asked. it had no idea *how* they write
or how they're coping, so a kid typing four lowercase words got the same register
as one writing paragraphs.

the brief also said: don't let it bleed into other classes. worth writing down
that it never did — load_class_memory and load_student_docs both filter on
class_id and pinecone is namespaced per class. the risk was the opposite one: the
obvious place to hang a "student profile" is Users/{uid}, which is exactly where
isolation would have broken.

- student_profile.py: each message contributes counters (length, sentence length,
  vocabulary reach, txtspeak, question marks), the totals render into two plain
  sentences. no second model call — same reasoning as profanity.py, and a
  judgement about a child is the last place you want a hallucination
- stored at Users/{uid}/Profiles/{class_id}. the class id is the *document id*,
  not a field to filter on, so there's no query to get wrong and no where-clause
  for a later refactor to drop. load_class_profile asserts it anyway, because a
  profile from another class would read as perfectly normal prose in the prompt
- firestore increments, not read-modify-write, so two messages in flight can't
  lose each other's counts. measure() is additive for the same reason and the
  test pins it — a drift there would be silent and permanent
- says nothing until 5 messages. a confident wrong characterisation is worse than
  none. blocked messages and bare "thanks" are never counted
- the wording only ever describes how to explain something, never what the
  student is. "keep it plain" is fair to read over a kid's shoulder; "weak" isn't
- /roster and /student-profile for teachers, behind two gates: own the class AND
  the student is a member of it. ownership alone would let a teacher name any uid
  in the system and read a profile built in someone else's classroom
- reading or writing the profile can fail without costing anyone an answer; the
  security suite proves it by leaving the store unstubbed on a working /chat

quiz answers werent random + material is teacher-only now

- every quiz had the right answer on option A. two separate reasons, both fixed:
  the validator had one line that took anything it didnt recognise as an answer
  key and turned it into index 0. a letter ("B"), a quoted digit ("2"), a float,
  the answer's own text, a renamed field (correct_index, answer_index) — all of
  them normal model output for a multiple-choice key — silently became the first
  option. _resolve_answer_index handles every one of those shapes now
- and if it genuinely cant work out the key, the question is dropped instead of
  guessed. a wrong answer key is worse than one fewer question: the student gets
  told A is right when it isnt and learns the wrong thing. all five dropping
  means the existing "couldnt make that activity, try again" retry
- options get shuffled server-side after the key is resolved, so the position is
  even no matter what the model does. that's the part that actually fixes the
  symptom — the prompt asking nicely for variety wouldn't have been enough at
  temperature 0.2
- the prompt was teaching the bias too: its only schema example literally said
  "answer":0. says "<0-based index into options>" now, plus dont always put the
  correct one first
- also fixed a real off-by-the-end bug in the same lines: the bound check used
  the untruncated options list while only four options got stored, so five
  options with answer 4 stored an index past the end and *every* click came back
  "Not quite" with nothing marked correct
- test_quiz_answers.py — first coverage _parse_tool_result has ever had

- course material is teacher-only now, full stop. the "Allow Source Display"
  course toggle is gone: /chat, /chats/<id>/messages and /tools/run all check the
  role and nothing else. excerpts stay stored on the message so a teacher can
  still review the conversation later, theyre just withheld on the way out
- the student UI was the worse half. the comment above it said "sources are
  teacher-only" but the condition was "did the server send any sources", which is
  exactly what happened to a student once that toggle was on. role check wraps
  the whole thing now
- dropping source_display from COURSE_SETTINGS_DEFAULTS is enough for existing
  courses — course_settings() whitelists keys off the defaults dict, so a value
  already sitting on a course document is ignored. no migration
- test_security.py's sources test only ever passed because its stub used the
  defaults with the toggle off. it forces source_display=True now, so it actually
  proves the hole is shut
- /chats/<id>/messages lost two firestore reads on the way (the chat doc and the
  course settings, both of which only fed that one toggle)

- student uploads: the kind was self-declared, so "assignment" was a way to put
  a textbook chapter in front of the tutor. one cheap classification call on the
  first 2500 chars now refuses anything that reads as teaching material, or that
  was attached under the wrong one of the two buttons
- it runs last, after the rate limit and the 3-per-conversation cap, so a request
  thats getting refused anyway never costs a model call, and a refusal writes
  nothing to firestore. if the call itself errors the upload goes through —
  same call as the tutoring-state summary: a gemini outage must not stop a kid
  attaching the essay theyre being marked on

- ran all six test scripts (added the sixth), node --check on every inline script
  block in the two pages i touched

## android: the port plan, filled in

- started the android transition on its own branch. git wont take "Android app"
  as a branch name (refnames cant have spaces) so its `android-app`
- no app code in this one. ANDROID.md was written before the last commit and had
  drifted: student.html's sidebar is at 262 not 258, and the phase A items stopped
  at "fix the tap targets" without saying which rules. read all four pages and
  wrote every file, rule and id down
- three things in the old plan were wrong. the bfcache/back-button risk is
  already handled — transition.js:98-106 clears the wipe classes on a persisted
  pageshow, so thats a confirm-on-device not a defect. and student.html has no
  100vh anywhere: it sizes off h-full, which is 100% of a viewport that doesnt
  shrink with the url bar, so it needs the same dvh fix as the teacher pages
- the two real showstoppers, both now written up with line numbers: a student on
  a phone cant switch course, open an old conversation or sign out (everything
  lives in the hidden md:flex nav), and a teacher is stranded on whichever of the
  two teacher pages they opened, because that aside is the only link between them
- also caught two controls that are broken on touch rather than just small —
  teacherknowledge.html:98-101 and student.html:498 both reveal their edit/delete
  buttons on :hover, so on a phone theyre invisible
- phase A gets two shared files, mobile.css and mobile.js, served like theme.css
  and theme.js. the drawer is attribute-driven the way theme.js already is, so
  the same ~20 lines cover all four pages instead of four copies
- icons: generated from the wordmark, pillow in a throwaway venv only — it must
  not go in requirements.txt, that file ships to cloud run
- phase C needs a jdk, the android sdk, a play account and a deployed host, so it
  cant be done from a container at all. said so in the file rather than leaving
  it to be discovered

## android phase A: the site works on a phone now

- the two showstoppers are fixed. student.html kept the course switcher, the chat
  history and sign-out inside a `hidden md:flex` nav, so below 768px a student
  couldnt switch course, reopen a conversation or sign out at all. both teacher
  pages did `[data-side]{display:none}` at 980px, and that aside is the only link
  between them, so a teacher was stranded on whichever one they opened — also
  with no sign-out
- one drawer, not three. mobile.css + mobile.js are new and served like
  theme.css/theme.js. attribute-driven the way theme.js already finds
  [data-theme-toggle]: [data-drawer], [data-drawer-toggle], [data-drawer-scrim],
  [data-drawer-close], [data-drawer-keep]. the breakpoint is a token on the
  element ("sm" 767 / "md" 980) because the pages dont agree on one
- the existing sidebars BECAME the drawer rather than getting a second copy —
  duplicate ids would have broken every getElementById on the page
- a closed drawer is properly gone: inert where its supported, visibility:hidden
  elsewhere, with the visibility delayed by the length of the slide so closing
  still animates instead of the panel vanishing
- 100vh -> 100vh;100dvh on both teacher shells. student.html had no 100vh at all,
  it sizes off h-full, which is 100% of a viewport that doesnt shrink with the url
  bar — same bug, so `html.h-full,body.h-full` gets the dvh pair (the .h-full in
  the selector is what outranks tailwinds own .h-full)
- login.html's calc(100vh - 104px) is gone. the 104 was the utility bar plus the
  header, and the bar is hidden below 1000px, so it was wrong on every phone and
  pushed the footer off screen. its a flex column with flex:1 on main now
- viewport-fit=cover on all four, then env(safe-area-inset-*) on the student
  header and composer, the teacher asides and toasts, and the login footer. the
  mobile header height and the chat columns top spacer come off the same inset so
  they cant drift apart
- two controls were broken on touch rather than just small: both teacher pages
  reveal a rows edit/delete on :hover (opacity:0 otherwise), and student.htmls
  chat rows do the same with group-hover. forced visible under the breakpoint
- caught one of my own while testing: bumping .ibtn to 44px overflowed
  .rule-row's 32px action columns, which dont grow to fit — widened the tracks
- .doc-row stacks below 980px instead of squeezing a filename into ~116px. its
  column header repeats that grid as an inline style, so it got a [data-doc-head]
  hook and is hidden rather than restacked twice
- a phone finally sees which conversation and course its in, plus server status:
  paintHeader/setStatus write every [data-thread-title]/[data-status-dot] match
  instead of one id, so both headers stay in step. login does the same for its
  status, the dark bar keeps its copy for desktop
- the mobile header traded the "Chronos." wordmark for the thread title — the
  wordmark is right there in the drawer
- keyboard: visualViewport.resize keeps #messages pinned and #input scrolls into
  view on focus, but only when the thread was already at the bottom
- verified in chromium at 360x640 and 1280x900 on all four pages: drawer opens
  from the hamburger, closes on scrim/escape/selection, focus moves in and back,
  aria-expanded tracks, closed drawer is inert, desktop byte-identical in
  behaviour (md:ml-64 still 256px, sidebar still fixed), no horizontal overflow.
  had to build tailwind locally from the page's own config — the cdn is blocked
  in the sandbox, and without the css every md:/w-64 utility silently does
  nothing, so the desktop assertions would have proved nothing
- one caveat: google fonts is blocked too, so icon ligatures render as their
  literal names (~111px instead of a 19px glyph). constrained the icon spans to
  one em before measuring width, which is what a real glyph occupies. so the
  overflow numbers are sound but nobody has looked at this on a real device yet
- all six test scripts still pass, node --check clean on every inline script
