/* Kronos screen controller.
 *
 * Thin adapter: the Kronos workspace already lives in
 * panels/kronosWorkspace.js. This controller exposes it through the same
 * init()/prime() contract as the other screen controllers so the screen
 * registry in app.js can dispatch uniformly.
 * Rollout flag: screen_controllers (see wiki [[section-migration-map]]).
 */

export function createKronosController(ctx) {
  const { initKronosWorkspace, primeKronosWorkspace } = ctx;

  function init() {
    initKronosWorkspace();
  }

  function prime() {
    void primeKronosWorkspace();
  }

  return { id: "kronos", init, prime };
}
