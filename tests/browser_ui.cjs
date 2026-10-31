// Run against a locally served test package + throwaway save database only.
// PLAYWRIGHT_MODULE may point at an existing Playwright installation.
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const base = process.env.GL_TEST_URL || 'http://127.0.0.1:8094';
assert(['127.0.0.1', 'localhost'].includes(new URL(base).hostname), 'Local fixture required');
const screenshots = process.env.GL_SCREENSHOTS;
const failures = [];
const results = [];
async function main() {
  const browser = await chromium.launch({channel:'msedge', headless:true});
  let diagnosticPage;
  try {
    const context = await browser.newContext({viewport:{width:390,height:844},hasTouch:true});
    // Browser never contacts production: forward only save-service API to local fixture.
    await context.route('https://girllife.mewcat.me/api/**', async route => {
      const url = new URL(route.request().url());
      const response = await route.fetch({url:base + url.pathname + url.search});
      await route.fulfill({response});
    });
    const page = await context.newPage();
    diagnosticPage = page;
    page.on('pageerror', error => failures.push(error.message));
    const action = name => page.locator('[data-qsp="action"]').filter({hasText:new RegExp('^' + name + '$')});
    const tool = name => page.locator('.gl-dock [data-command="' + name + '"]');
    const account = page.locator('.cloud-account-dialog');
    async function dismissMedia() {
      if (await page.locator('qsp-view').count()) {
        const media = page.locator('qsp-view img, qsp-view video').first();
        const box = await media.boundingBox();
        if (box && box.width>0 && box.height>0) {
          await page.touchscreen.tap(box.x+box.width/2,box.y+box.height/2);
          assert.equal(await page.locator('qsp-view').count(),1,'Tapping media keeps it open');
        }
        await page.touchscreen.tap(8,80);
        await page.locator('qsp-view').waitFor({state:'detached'});
      }
    }
    async function capture(name) {
      if (screenshots) { fs.mkdirSync(screenshots,{recursive:true}); await page.locator('.gl-toast').waitFor({state:'hidden'}); await page.screenshot({path:screenshots+'/'+name+'.png'}); }
    }
    await page.goto(base);
    await page.locator('.gl-primary:not([disabled])').waitFor({timeout:90000});
    assert(await page.locator('qsp-game-root').evaluate(e=>e.inert));
    await capture('launcher-mobile');
    await page.setViewportSize({width:1440,height:1000});
    await capture('launcher-desktop');
    await page.setViewportSize({width:390,height:844});
    await page.getByRole('tab',{name:'Play',exact:true}).focus();
    await page.keyboard.press('ArrowRight');
    assert.equal(await page.getByRole('tab',{name:'Settings',exact:true}).getAttribute('aria-selected'),'true');
    const range = page.locator('#gl-page-settings [data-setting="text"]');
    await range.fill('22');
    assert.equal(await page.evaluate(()=>getComputedStyle(document.documentElement).getPropertyValue('--gl-text').trim()),'22px');
    await page.reload();
    await page.locator('.gl-primary:not([disabled])').waitFor({timeout:90000});
    assert.equal(await page.evaluate(()=>getComputedStyle(document.documentElement).getPropertyValue('--gl-text').trim()),'22px');
    await page.getByRole('tab',{name:'Settings',exact:true}).click();
    await page.locator('#gl-page-settings [data-command="reset"]').click();
    assert.equal(await page.evaluate(()=>getComputedStyle(document.documentElement).getPropertyValue('--gl-text').trim()),'18px');
    await page.getByRole('tab',{name:'Play',exact:true}).click();
    await page.locator('.gl-home [data-command="saves"]').click();
    await account.waitFor({state:'visible'});
    await capture('cloud-account');
    assert.equal(await page.locator('qsp-pause-screen').count(),0,'No save panel before authentication');
    await account.getByRole('button',{name:'Cancel',exact:true}).click();
    await action('Start').click();
    await action('Quick Start').click();
    await dismissMedia();
    for(const width of [320,390,430]) {
      await page.setViewportSize({width,height:844});
      const table = await page.locator('qsp-main-content table').first().evaluate(e=>({width:e.scrollWidth,columns:[...e.rows[0].cells].map(c=>c.getBoundingClientRect().width)}));
      assert(table.width >= 340, JSON.stringify(table));
      assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth),width);
    }
    await action('Skip Intro').click();
    await account.waitFor({state:'visible'});
    await account.getByRole('button',{name:'Cancel',exact:true}).click();
    await action('Start the game').click();
    await page.locator('qsp-objects').waitFor({state:'attached'});
    const story = await page.locator('qsp-main-content').innerText();
    await tool('home').click();
    assert(await page.locator('qsp-game-root').evaluate(e=>e.inert));
    await page.locator('.gl-primary').click();
    assert.equal(await page.locator('qsp-main-content').innerText(),story,'Home must not restart/load');
    for(const [width,height] of [[320,568],[390,844],[430,932],[768,1024],[901,700],[1440,1000]]) {
      await page.setViewportSize({width,height});
      assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth),width);
      const main = await page.locator('qsp-main').boundingBox();
      const actions = await page.locator('qsp-actions').boundingBox();
      assert(main && actions);
      if(width <= 900) {
        assert(Math.abs(main.width-width)<1 && Math.abs(actions.width-width)<1,'Full-width panes');
        assert(Math.abs(main.height/(main.height+actions.height)-0.7)<0.01,'70/30 content split');
        assert.equal(Math.round(main.y),48);
      } else assert(main.x+main.width <= actions.x,'Desktop story/actions must not overlap');
      for(const key of ['objects','stats']) {
        await tool(key).click();
        await page.waitForFunction(k=>document.querySelector('.gl-dock [data-command="'+k+'"]').getAttribute('aria-pressed')==='true',key);
        const box = await page.locator(key==='objects'?'qsp-objects':'qsp-stats').boundingBox();
        assert(box && box.width>0 && box.height>0 && box.y>=48 && box.y+box.height<=height-60,JSON.stringify(box));
        await tool(key==='objects'?'stats':'objects').click();
        await page.waitForFunction(k=>document.querySelector('.gl-dock [data-command="'+k+'"]').getAttribute('aria-pressed')==='false',key);
        await tool(key==='objects'?'stats':'objects').click();
      }
      results.push({width,height,main,actions});
    }
    await capture('game-desktop');
    await page.setViewportSize({width:390,height:844});
    await capture('game-mobile');
    await tool('menu').click();
    await page.locator('.gl-dialog [data-setting="share"]').fill('80');
    await page.locator('.gl-dialog [data-setting="text"]').fill('24');
    await capture('interface-settings');
    await page.locator('.gl-dialog [data-command="close"]').click();
    const changed = await page.locator('qsp-main').boundingBox();
    assert(changed.height > 570,'Mobile story share applies');
    await tool('menu').click();
    await page.locator('.gl-dialog [data-command="reset"]').click();
    await page.locator('.gl-dialog details').evaluate(e=>e.open=true);
    const report = await page.locator('.gl-dialog textarea').inputValue();
    assert(!/token|password|username|Corridor|Svetlana/.test(report),'Report must not contain private data');
    await page.locator('.gl-dialog [data-command="native-menu"]').click();
    await page.locator('qsp-pause-screen').waitFor({state:'visible'});
    await page.locator('[data-qsp-command="resume"]').click();
    await tool('saves').click();
    await account.waitFor({state:'visible'});
    await account.locator('input[name="username"]').fill('ui_'+Date.now());
    await account.locator('input[name="password"]').fill('x');
    await account.getByRole('button',{name:'Create account',exact:true}).click();
    await page.locator('qsp-pause-screen').waitFor({state:'visible'});
    const save = page.getByRole('button',{name:'Save',exact:true}).first();
    await save.click();
    await page.locator('qsp-pause-screen').waitFor({state:'detached'});
    await tool('saves').click();
    await page.getByRole('button',{name:'Load',exact:true}).first().waitFor({state:'visible'});
    await page.getByRole('button',{name:'Load',exact:true}).first().click();
    await page.locator('qsp-pause-screen').waitFor({state:'detached'});
    assert.equal(await page.locator('qsp-main-content').innerText(),story,'Load restores same scene');
    await tool('saves').click();
    await page.locator('qsp-pause-screen').waitFor({state:'visible'});
    await page.locator('[data-qsp-command="cloud-account"]').click();
    await account.getByRole('button',{name:'Sign out',exact:true}).click();
    await tool('saves').click();
    await account.waitFor({state:'visible'});
    await account.getByRole('button',{name:'Cancel',exact:true}).click();
    assert.equal(await page.evaluate(()=>localStorage.getItem('girllife.cloud.session')),null);
    await page.locator('qsp-pause-screen').waitFor({state:'detached'});
    await page.locator('.gl-header [data-command="interface"]').click();
    await page.keyboard.press('Escape');
    assert.equal(await page.locator('.gl-dialog').evaluate(e=>e.open),false);
    assert.equal(await page.locator('qsp-pause-screen').count(),0,'Escape in shell must not open engine pause screen');
    assert.deepEqual(failures,[]);
    console.log(JSON.stringify({status:'PASS',checks:'launcher, keyboard tabs, persistent/reset settings, creation tables, real gameplay, six viewports, Items/Stats mutual exclusion, Home preservation, native preferences, local save/load, signout, Escape isolation',geometry:results},null,2));
  } catch (error) {
    if (diagnosticPage) {
      console.error('Page errors:',failures,'Visible UI:',(await diagnosticPage.locator('body').innerText()).slice(-4500));
      console.error('Account DOM:',await diagnosticPage.locator('.cloud-account-dialog').evaluate(e=>e.outerHTML));
      if (screenshots) await diagnosticPage.screenshot({path:screenshots+'/failure.png'});
    }
    throw error;
  } finally { await browser.close(); }
}
main().catch(e=>{console.error(e);process.exitCode=1;});
