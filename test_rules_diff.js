/* Self-check for the rules textarea diff.  Run:  node test_rules_diff.js
 *
 * teacherknowledge.html edits the class's typed rules as one textarea, one rule
 * per line, and turns that into /ingest + /delete_rule calls. Get the diff wrong
 * and saving silently deletes a teacher's rules, so this pulls the real
 * rulesDiff() out of the page and exercises it — no browser, no framework.
 */
const fs = require("fs");
const assert = require("assert");

const src = fs.readFileSync(require("path").join(__dirname, "teacherknowledge.html"), "utf8");
const m = /\n  function rulesDiff\(lines,existing\)\{[\s\S]*?\n  \}\n/.exec(src);
assert(m, "rulesDiff() not found in teacherknowledge.html — did it get renamed?");
const rulesDiff = new Function(m[0] + "; return rulesDiff;")();

const R = (id, text) => ({ id, text });

// no change
let d = rulesDiff(["a", "b"], [R("1", "a"), R("2", "b")]);
assert.deepStrictEqual(d, { adds: [], removes: [] });

// pure add
d = rulesDiff(["a", "b", "c"], [R("1", "a"), R("2", "b")]);
assert.deepStrictEqual(d, { adds: ["c"], removes: [] });

// pure delete
d = rulesDiff(["a"], [R("1", "a"), R("2", "b")]);
assert.deepStrictEqual(d, { adds: [], removes: ["2"] });

// edit = one add + one delete
d = rulesDiff(["a", "B!"], [R("1", "a"), R("2", "b")]);
assert.deepStrictEqual(d, { adds: ["B!"], removes: ["2"] });

// emptied textarea removes everything, adds nothing
d = rulesDiff([], [R("1", "a"), R("2", "b")]);
assert.deepStrictEqual(d, { adds: [], removes: ["1", "2"] });

// stored text is whitespace-normalised before comparing, so a rule that was
// ingested with a newline in it still matches its one-line form and is NOT
// churned into a delete+add on every save.
d = rulesDiff(["a b"], [R("1", "a\n  b")]);
assert.deepStrictEqual(d, { adds: [], removes: [] });

// duplicates match one-for-one: two stored, one kept => exactly one delete
d = rulesDiff(["a"], [R("1", "a"), R("2", "a")]);
assert.deepStrictEqual(d, { adds: [], removes: ["2"] });

// ...and three lines against two stored duplicates adds exactly one
d = rulesDiff(["a", "a", "a"], [R("1", "a"), R("2", "a")]);
assert.deepStrictEqual(d, { adds: ["a"], removes: [] });

// first line stays put when an earlier one is deleted (ids, not positions)
d = rulesDiff(["b"], [R("1", "a"), R("2", "b")]);
assert.deepStrictEqual(d, { adds: [], removes: ["1"] });

console.log("rules diff: all checks passed");
