# The release build is minified with R8. No app-specific keep rules are needed:
# there is no reflection and no @Serializable class (all JSON goes through
# JsonObject), and OkHttp, Firebase, coroutines and kotlinx-serialization ship
# their own consumer rules. Add rules here only for a concrete R8 failure.
