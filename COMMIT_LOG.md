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

unit tests couldnt run locally bc of the missing .env secrets which im assuming is on cloud run NICCCC, so i just committed to main branch cuz i didnt vastly change anything and we could always revert

used codex again

- teacher rules arent tossed into the pinecone similarity lottery anymore lol. they live in firestore and get included every time
- old typed rules move into custom rules automatically (kept the old vectors as a fallback copy, but search ignores them)
- checked the attachment flow: assignments + rubrics already skipped pinecone. kept that, added checkboxes so students pick what goes into the next message
- split context into course passages, private teacher rules, student files, recent chat + summarized memory
- added rolling summaries every 4 exchanges, saved pending turns, and a warning if memory updates fail. old course recollections were basically just opening questions before
- base rule switches start ON: teacher material only, tutor instead of answers, no worked examples, gentle hints, understanding checks, no generated study sheets
- turning off teacher-only knowledge gets a real warning + server confirmation check
- toolkit switches start OFF. practice, tutoring, visuals, study, files, sources, learning-gap reports
- tools use a hidden activation tag, then get their actual instructions in a second model call. server checks permissions and citations before returning cards
- added quizzes, flashcards, matching, ordering, checks, guided prompts, hints, scratchpads, diagrams/maps, graphs, timelines, tables, study sheets + txt downloads
- source viewer shows real excerpts and pdf page numbers for new uploads. old uploads dont magically get page numbers
- learning gaps now come from student-submitted reports, not every random retrieval miss. stopped old swear-word flags from leaking back into the feed
- fixed cross-course chat replay, kept tool cards in saved chats, and rechecked toolkit permissions when loading them
- simplified shared cards/controls a little, fixed dark hover contrast, added reduced-motion support
- Classes -> Courses in the UI
- added account settings for display name + password reset emails
- js syntax checks passed. DID NOT run the python app or python tests bc the secrets arent here
- no live firebase/gemini/pinecone or browser end-to-end verification here, so those still need checking in the configured environment
- updated the existing regression checks + added policy permission/citation checks for CI. left the python checks unrun like requested
