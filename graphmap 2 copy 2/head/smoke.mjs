// Drives the real page in a real browser. The claims here are the ones a user would
// notice breaking — and the first one is the whole point of the thing.

import { chromium } from "playwright";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const here = dirname(fileURLToPath(import.meta.url));
const url = "file://" + join(here, "dist/eidola.html");
const fails = [];
const ok = (name, cond, extra = "") => {
  console.log(`  ${cond ? "." : "x"} ${name}${cond ? "" : "  << " + extra}`);
  if (!cond) fails.push(name);
};

const browser = await chromium.launch({ executablePath: "/opt/pw-browsers/chromium-1194/chrome-linux/chrome" });
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
const errors = [];
page.on("pageerror", (e) => errors.push(String(e)));
// the font stylesheet is unreachable offline; the fallback stack is the design, not a bug
page.on("console", (m) => { if (m.type() === "error" && !/net::|fonts\.googleapis/.test(m.text())) errors.push(m.text()); });
await page.goto(url);
await page.waitForTimeout(400);

const hash = () => page.$eval(".hashbox code", (e) => e.textContent.trim());
const paneCount = () => page.$$eval(".pane", (e) => e.length);
const msg = () => page.$eval(".msg", (e) => e.textContent.trim());
const valueOf = (occ, port) => page.evaluate(
  ([o, p]) => {
    const a = window.app;
    const v = a.valueAt(`${a.pathOf(o)}#${p}`);
    return v === undefined ? null : String(v);
  }, [occ, port]);

// ---------------------------------------------------------------- it loads
ok("the seeded workspace renders", (await paneCount()) === 5, `${await paneCount()} panes`);
ok("no page errors on load", errors.length === 0, errors[0]);

// ---------------------------------------------------------------- first light
// Drag a pane. The graph hash must not move. This is Law 8, watchable.
const before = await hash();
const box = await page.$eval('.pane[data-occ="total"]', (e) => {
  const r = e.getBoundingClientRect();
  return { x: r.x + r.width / 2, y: r.y + 8 };
});
await page.mouse.move(box.x, box.y);
await page.mouse.down();
await page.mouse.move(box.x + 130, box.y + 60, { steps: 12 });
await page.mouse.up();
await page.waitForTimeout(120);
const after = await hash();
ok("dragging a pane leaves the graph hash byte-identical", before === after, `${before} -> ${after}`);
ok("...and the head noticed it was a gauge op",
  (await page.$eval(".gauge", (e) => e.textContent)).startsWith("1 "));

// folding is gauge too — and it stops the computation behind it
await page.click('.pane[data-occ="dbl"] .badge.fold');
await page.waitForTimeout(120);
ok("folding a pane changes no hash", (await hash()) === before);
ok("...but does stop the computation behind it", (await valueOf("dbl", "out")) === null);
await page.click('.pane[data-occ="dbl"] .badge.fold');
await page.waitForTimeout(120);
ok("...and unfolding brings it back", (await valueOf("dbl", "out")) !== null);

// ---------------------------------------------------------------- the tick
const t0 = await valueOf("total", "out");
await page.click("#b-step");
await page.waitForTimeout(80);
const t1 = await valueOf("total", "out");
ok("stepping advances the counter through the delay", Number(t1) === Number(t0) + 1, `${t0} -> ${t1}`);

// ---------------------------------------------------------------- open a pane
const place = (posMap) => page.evaluate((m) => {
  Object.assign(window.app.present.pos, m);
  window.app.render();
}, posMap);

const n0 = await paneCount();
await page.click('button.pal[data-unit]:has-text("add")');
await page.waitForTimeout(120);
ok("clicking a unit opens a pane", (await paneCount()) === n0 + 1);

// ---------------------------------------------------------------- wire it
const dot = async (occ, port, dir) => page.evaluate(
  ([o, p, d]) => {
    const el = document.querySelector(`.pane[data-occ="${o}"] .dot.${d}[data-port="${p}"]`);
    const r = el.getBoundingClientRect();
    return { x: r.x + r.width / 2, y: r.y + r.height / 2 };
  }, [occ, port, dir]);

await place({ step: [40, 40], prev: [40, 200], total: [260, 40], two: [40, 380],
              dbl: [700, 200], add: [500, 380] });
await page.waitForTimeout(80);
const src = await dot("two", "out", "out");
const dst = await dot("add", "a", "in");
await page.mouse.move(src.x, src.y);
await page.mouse.down();
await page.mouse.move(dst.x, dst.y, { steps: 14 });
await page.mouse.up();
await page.waitForTimeout(150);
ok("dragging port to port makes a wire", (await page.$$eval("path.wire", (e) => e.length)) === 6);
ok("...and wiring DOES move the hash, because it is content", (await hash()) !== before);

// ---------------------------------------------------------------- refusal is visible
// bool -> num has no route: the head must show the refusal rather than swallow it
await page.click('button.pal[data-unit]:has-text("gt")');
await page.waitForTimeout(120);
await place({ gt: [260, 560] });
await page.waitForTimeout(80);
const gtOut = await dot("gt", "out", "out");
const addB = await dot("add", "b", "in");
await page.mouse.move(gtOut.x, gtOut.y);
await page.mouse.down();
await page.mouse.move(addB.x, addB.y, { steps: 14 });
await page.mouse.up();
await page.waitForTimeout(150);
ok("a shape-mismatched wire is refused at wire time, and said so",
  /shape mismatch/.test(await msg()), await msg());

// ---------------------------------------------------------------- closure
await page.evaluate(() => { window.app.selected = new Set(["two", "add"]); window.app.render(); });
await page.evaluate(() => { window.prompt = () => "doubler"; });
await page.click("#b-group");
await page.waitForTimeout(200);
ok("grouping replaces the selection with one pane",
  !(await page.$('.pane[data-occ="two"]')) && !!(await page.$(".pane.cls-container")));
ok("the new composite appears in the palette as an ordinary unit",
  !!(await page.$('button.pal.own:has-text("doubler")')));

// ---------------------------------------------------------------- descend
await page.evaluate(() => {
  const occ = Object.keys(window.app.graph.panes).find((o) => window.app.unitOf(o).cls === "container");
  window.app.present.pos[occ] = [880, 460];   // clear of every other pane, so the dblclick lands
  window.app.render();
});
await page.waitForTimeout(80);
await page.dblclick(".pane.cls-container");
await page.waitForTimeout(200);
ok("descending shows the interior", (await page.$$eval(".pane", (e) => e.length)) === 2,
  `${await paneCount()} panes inside`);
ok("...and says there is no hash yet, because a draft is not addressed",
  /liminal/.test(await hash()), await hash());
const unitsBefore = await page.evaluate(() => window.app.store.persist.unitIds().length);
await page.click('button.pal[data-unit]:has-text("neg")');
await page.waitForTimeout(150);
ok("editing inside the draft stores no new unit — composing is quarantined",
  (await page.evaluate(() => window.app.store.persist.unitIds().length)) === unitsBefore);

await page.click("#b-up");
await page.waitForTimeout(250);
ok("declaring on ascend does store one",
  (await page.evaluate(() => window.app.store.persist.unitIds().length)) > unitsBefore);
ok("...and lineage was recorded beside the store",
  (await page.evaluate(() => window.app.store.lineage.size)) >= 1);
ok("back at the workspace", /workspace/.test(await page.$eval("#crumb", (e) => e.textContent)));

// ---------------------------------------------------------------- round trip
const saved = await page.evaluate(() => JSON.stringify(window.app.session()));
const reHash = await page.evaluate((s) => {
  const a = window.app;
  a.restore(JSON.parse(s));
  a.tick();
  return a.levels[0].engine.graphHash();
}, saved);
const liveHash = await page.evaluate(() => window.app.levels[0].engine.graphHash());
ok("a saved workspace reloads to the same graph hash", reHash === liveHash, `${reHash} vs ${liveHash}`);

ok("still no page errors", errors.length === 0, errors[0]);

await page.screenshot({ path: join(here, "dist/shot-light.png"), fullPage: false });
await page.emulateMedia({ colorScheme: "dark" });
await page.waitForTimeout(150);
await page.screenshot({ path: join(here, "dist/shot-dark.png"), fullPage: false });

await browser.close();
console.log(fails.length ? `\n${fails.length} FAILED` : "\nhead: all green");
process.exit(fails.length ? 1 : 0);
