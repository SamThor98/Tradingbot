import test from "node:test";
import assert from "node:assert/strict";

import {
  GLOSSARY,
  DECORATE_TERMS,
  decorateGlossary,
  glossaryTitle,
} from "../../webapp/static/modules/glossary.js";

test("glossaryTitle returns definitions for known terms", () => {
  assert.match(glossaryTitle("PF"), /Profit factor/);
  assert.match(glossaryTitle("Stage 2"), /uptrend/i);
  assert.equal(glossaryTitle("NOPE"), "");
});

test("decorateGlossary wraps terms in abbr with title", () => {
  const out = decorateGlossary("Stage 2: NO, VCP: NO, PF 1.1");
  assert.match(out, /<abbr class="glossary-term" title="[^"]+">Stage 2<\/abbr>/);
  assert.match(out, /<abbr class="glossary-term" title="[^"]+">VCP<\/abbr>/);
  assert.match(out, /<abbr class="glossary-term" title="[^"]+">PF<\/abbr>/);
});

test("decorateGlossary does not match partial words", () => {
  const out = decorateGlossary("PFX and ECEX stay untouched", ["PF", "ECE"]);
  assert.equal(out.includes("<abbr"), false);
});

test("definitions never contain other decoratable terms", () => {
  for (const term of DECORATE_TERMS) {
    for (const other of DECORATE_TERMS) {
      if (term === other) continue;
      const re = new RegExp(`\\b${other}\\b`);
      assert.equal(
        re.test(GLOSSARY[term]),
        false,
        `definition of ${term} contains term ${other}`,
      );
    }
  }
});

test("decorateGlossary handles empty input", () => {
  assert.equal(decorateGlossary(""), "");
  assert.equal(decorateGlossary(null), "");
});
