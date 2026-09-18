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
