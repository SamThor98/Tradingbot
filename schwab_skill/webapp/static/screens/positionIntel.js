/* Position Intel screen controller.
 *
 * Thin adapter: the four Intel tables live in panels/positionIntel.js. This
 * controller exposes them through the same init()/prime() contract as the
 * other screen controllers so the screen registry in app.js can dispatch
 * uniformly. Rollout flag: screen_controllers (see wiki
 * [[section-migration-map]]).
 */

export function createPositionIntelController(ctx) {
  const { initPositionIntelPanel, primePositionIntelPanel } = ctx;

  function init() {
    initPositionIntelPanel();
  }

  function prime() {
    void primePositionIntelPanel();
  }

  return { id: "intel", init, prime };
}
