const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const { chromium } = require('playwright');

(async () => {
  const out = path.resolve(__dirname, '../data/demo-preview');
  fs.mkdirSync(out, { recursive: true });
  const browser = await chromium.launch({ headless: true, ...(process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE ? { executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE } : {}) });
  const context = await browser.newContext({ viewport: { width: 1440, height: 1120 }, deviceScaleFactor: 1, reducedMotion: 'reduce' });
  const page = await context.newPage();
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  page.on('console', message => { if (message.type() === 'error') errors.push(message.text()); });
  const until = fn => page.waitForFunction(fn, null, { timeout: 20000 });
  const result = { checks: [], screenshots: [] };
  try {
    await page.goto(process.env.XIAQI_DEMO_URL || 'http://127.0.0.1:8000', { waitUntil: 'networkidle' });
    await until(() => state.data && !document.querySelector('#start').disabled);
    assert.equal(await page.locator('#stat-rate').innerText(), '69.5');
    assert.equal(await page.locator('#stat-positions').innerText(), '21,300');
    result.checks.push('Live model health and frozen evaluation data loaded');

    await page.click('#start');
    await until(() => state.game && !state.busy && state.game.move_count === 0);
    await page.locator('#board').scrollIntoViewIfNeeded();
    const rect = await page.locator('#board').boundingBox();
    const spot = await page.evaluate(() => point(0, 0));
    const size = await page.evaluate(() => geometry.size);
    await page.mouse.click(rect.x + spot.x * rect.width / size, rect.y + spot.y * rect.width / size);
    await until(() => !state.busy && state.game.move_count === 2);
    assert.equal(await page.evaluate(() => state.game.board[0][0]), 1);
    assert.equal(await page.evaluate(() => state.game.moves[1].color), 'white');
    result.checks.push('Human A1 move and actual trained-model reply, correct coordinates');

    await page.click('[data-color="white"]');
    await page.click('#start');
    await until(() => !state.busy && state.game.move_count === 1 && state.game.to_play === 'white');
    result.checks.push('Human as white, AI automatically opens as black');

    await page.click('[data-mode="battle"]');
    await page.click('#start');
    await until(() => state.game.mode === 'ai_vs_ai' && state.game.move_count >= 4);
    await page.click('#autoplay');
    await until(() => !state.busy && !state.auto);
    const count = await page.evaluate(() => state.game.move_count);
    await page.click('#step');
    await until(() => !state.busy && state.game.move_count > 4);
    assert.equal(await page.evaluate(() => state.game.move_count), count + 1);
    result.checks.push('Trained-versus-baseline autoplay, pause, and manual step');

    await page.click('[data-mode="replay"]');
    await until(() => !state.busy && state.game.readonly);
    assert.equal(await page.locator('#replay-select option').count(), 200);
    await page.selectOption('#replay-filter', 'loss');
    await until(() => !state.busy);
    assert.equal(await page.locator('#replay-select option').count(), 61);
    await page.selectOption('#replay-filter', 'win');
    await until(() => !state.busy);
    assert.equal(await page.locator('#replay-select option').count(), 139);
    await page.selectOption('#replay-filter', 'all');
    await until(() => !state.busy);
    await page.selectOption('#replay-speed', '230');
    await page.click('#start');
    await until(() => state.frame >= 3);
    await page.click('#autoplay');
    assert.equal(await page.evaluate(() => state.auto), false);
    await page.evaluate(() => { const t = document.querySelector('#timeline'); t.value = 24; t.dispatchEvent(new Event('input', { bubbles: true })); });
    await page.screenshot({ path: path.join(out, 'desktop.png'), fullPage: true });
    result.screenshots.push('desktop.png');
    await page.click('#latest');
    assert.equal(await page.locator('#result').isVisible(), true);
    const downloadPromise = page.waitForEvent('download');
    await page.click('#export');
    const download = await downloadPromise;
    const sgfPath = path.join(out, 'evaluation.sgf');
    await download.saveAs(sgfPath);
    assert(fs.readFileSync(sgfPath, 'utf8').includes('KM[7.5]'));
    result.checks.push('All 200 replays, 139 wins / 61 losses, playback, seek, score, SGF export');

    await page.click('.nav-item[data-page="research"]');
    assert.equal(await page.locator('#game-grid button').count(), 200);
    await page.screenshot({ path: path.join(out, 'research.png'), fullPage: true });
    result.screenshots.push('research.png');
    await page.locator('#game-grid button').nth(17).click();
    await until(() => !state.busy && state.game.replay_id === 17);
    result.checks.push('Evidence charts and direct navigation from an arbitrary evaluation game');
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);

    // Reopen a saved live game: history is reconstructed rather than replaced.
    await page.locator('[data-history]').first().click();
    await until(() => !state.busy && !state.game.readonly);
    const savedId = await page.evaluate(() => state.game.id);
    await page.reload({ waitUntil: 'networkidle' });
    await until(() => !state.busy && state.game && state.game.id === localStorage.getItem('xiaqi-demo-last-game'));
    assert.equal(await page.evaluate(() => state.game.id), savedId);
    result.checks.push('Saved live game survives browser reload');

    const mobileContext = await browser.newContext({ viewport: { width: 390, height: 844 }, deviceScaleFactor: 1, isMobile: true, hasTouch: true, reducedMotion: 'reduce' });
    const mobile = await mobileContext.newPage();
    mobile.on('pageerror', error => errors.push(error.message));
    await mobile.goto(process.env.XIAQI_DEMO_URL || 'http://127.0.0.1:8000', { waitUntil: 'networkidle' });
    await mobile.waitForFunction(() => state.data && !document.querySelector('#start').disabled);
    assert.equal(await mobile.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
    await mobile.locator('[data-mode="replay"]').tap();
    await mobile.waitForFunction(() => !state.busy && state.game?.readonly);
    await mobile.evaluate(() => { const t = document.querySelector('#timeline'); t.value = 20; t.dispatchEvent(new Event('input', { bubbles: true })); });
    await mobile.screenshot({ path: path.join(out, 'mobile.png'), fullPage: true });
    result.screenshots.push('mobile.png');
    result.checks.push('390px mobile layout, touch mode selection, replay, no horizontal overflow');
    await mobileContext.close();
    assert.deepEqual(errors, []);
    result.checks.push('No browser console or JavaScript errors');
    result.passed = true;
  } catch (error) {
    result.passed = false;
    result.error = error.stack;
    await page.screenshot({ path: path.join(out, 'failure.png'), fullPage: true });
    throw error;
  } finally {
    result.errors = errors;
    fs.writeFileSync(path.join(out, 'browser-checks.json'), JSON.stringify(result, null, 2));
    await browser.close();
    console.log(JSON.stringify(result, null, 2));
  }
})();
