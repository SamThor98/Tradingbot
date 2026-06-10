/* Cockpit screen controller.
 *
 * Thin adapter: the cockpit lanes live in panels/cockpit.js. This controller
 * exposes them through the same init()/prime() contract as the other screen
 * controllers so the screen registry in app.js can dispatch uniformly.
 * Rollout flag: screen_controllers (see wiki [[section-migration-map]]).
 */

export function createCockpitController(ctx) {
  const { initCockpitPanel, primeCockpitPanel } = ctx;

  function init() {
    initCockpitPanel();
  }

  function prime() {
    void primeCockpitPanel();
  }

  return { id: "cockpit", init, prime };
}
