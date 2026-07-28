import { describe, it } from "node:test";
import assert from "node:assert/strict";
import { buildPeadCanarySleeve } from "../../webapp/static/panels/peadCanary.js";

describe("buildPeadCanarySleeve", () => {
  it("prefers capacity_top_n rows and caps at rank_top_n", () => {
    const sleeve = buildPeadCanarySleeve({
      strategy_pead_primary_effective_mode: "shadow",
      pead_primary_capacity_rank_top_n: 5,
      pead_primary_capacity_rank_arm: "top5_by_edge_score",
      pead_primary_evaluated: 40,
      pead_primary_admitted: 12,
      overlap_with_stage2: 2,
      data_quality: "ok",
      pead_primary_shadow_names: [
        { ticker: "AAA", edge_score: 90, capacity_top_n: true, pead_beat: true, pead_surprise_pct: 4.2 },
        { ticker: "BBB", edge_score: 80, capacity_top_n: true },
        { ticker: "CCC", edge_score: 70, capacity_top_n: true },
        { ticker: "DDD", edge_score: 60, capacity_top_n: true },
        { ticker: "EEE", edge_score: 50, capacity_top_n: true },
        { ticker: "FFF", edge_score: 40, capacity_top_n: true },
        { ticker: "GGG", edge_score: 10, capacity_top_n: false },
      ],
    });
    assert.equal(sleeve.executable, false);
    assert.equal(sleeve.n_names, 5);
    assert.deepEqual(sleeve.tickers, ["AAA", "BBB", "CCC", "DDD", "EEE"]);
    assert.equal(sleeve.rank_arm, "top5_by_edge_score");
    assert.equal(sleeve.evaluated, 40);
  });

  it("falls back to first N when capacity_top_n flags are missing", () => {
    const sleeve = buildPeadCanarySleeve({
      pead_primary_capacity_rank_top_n: 3,
      pead_primary_shadow_names: [
        { ticker: "x", edge_score: 1 },
        { ticker: "y", edge_score: 2 },
        { ticker: "z", edge_score: 3 },
        { ticker: "w", edge_score: 4 },
      ],
    });
    assert.deepEqual(sleeve.tickers, ["X", "Y", "Z"]);
  });

  it("returns empty names when shadow list is absent", () => {
    const sleeve = buildPeadCanarySleeve({ strategy_pead_primary_mode: "shadow" });
    assert.equal(sleeve.n_names, 0);
    assert.equal(sleeve.mode, "shadow");
  });
});
