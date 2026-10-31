// True in-game color selection + local cloud save/restore regression.
const {chromium}=require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const assert=require('node:assert/strict');
const base=process.env.GL_TEST_URL || 'http://127.0.0.1:8094';
assert(['127.0.0.1','localhost'].includes(new URL(base).hostname));
(async()=>{
  const browser=await chromium.launch({channel:'msedge',headless:true});
  const page=await browser.newPage({viewport:{width:1394,height:786},hasTouch:true});
  const errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.route('https://girllife.mewcat.me/api/**',async r=>{
    const u=new URL(r.request().url());await r.fulfill({response:await r.fetch({url:base+u.pathname+u.search})});
  });
  const action=n=>page.locator('[data-qsp=action]').filter({hasText:new RegExp('^'+n+'$')});
  const tool=n=>page.locator('.gl-dock [data-command='+n+']');
  const account=page.locator('.cloud-account-dialog');
  async function palette(){return page.evaluate(()=>{
    const game=getComputedStyle(document.querySelector('qsp-game-root'));
    const main=getComputedStyle(document.querySelector('qsp-main'));
    const text=getComputedStyle(document.querySelector('qsp-main-content'));
    const a=document.querySelector('qsp-main-content a');
    return {engineBG:game.getPropertyValue('--background-color').trim(),engineText:game.getPropertyValue('--color').trim(),engineLink:game.getPropertyValue('--link-color').trim(),mainBG:main.backgroundColor,text:text.color,link:a&&getComputedStyle(a).color,chrome:getComputedStyle(document.querySelector('.gl-header')).backgroundColor};
  });}
  async function checkPalette(){
    await page.waitForFunction(()=>{
      const g=getComputedStyle(document.querySelector('qsp-game-root'));
      const normalized=v=>{const e=document.createElement('span');e.style.color=v;document.body.append(e);const c=getComputedStyle(e).color;e.remove();return c;};
      return getComputedStyle(document.querySelector('qsp-main')).backgroundColor===normalized(g.getPropertyValue('--background-color')) && getComputedStyle(document.querySelector('.gl-header')).backgroundColor===normalized(g.getPropertyValue('--background-color')) && getComputedStyle(document.querySelector('qsp-main-content')).color===normalized(g.getPropertyValue('--color'));
    });
    return palette();
  }
  try{
    await page.goto(base);await page.locator('.gl-primary:not([disabled])').waitFor({timeout:90000});await page.locator('.gl-primary').click();
    await action('Start').click();await action('Quick Start').click();
    if(await page.locator('qsp-view').count()){await page.touchscreen.tap(8,80);await page.locator('qsp-view').waitFor({state:'detached'});}
    await action('Skip Intro').click();await account.getByRole('button',{name:'Cancel',exact:true}).click();await action('Start the game').click();
    await tool('stats').click();
    await page.locator('qsp-stats-content a:has(img[src$="/icon_menu.png"])').click();
    await page.locator('qsp-main-content').getByRole('link',{name:'Display',exact:true}).click();
    const selector=page.locator('qsp-main-content').getByRole('link',{name:'Show theme selector',exact:true});
    if(await selector.count())await selector.click();
    await page.locator('qsp-main-content').getByRole('link',{name:'Frappé',exact:true}).click();
    await action('Leave').click();
    const dark=await checkPalette();
    assert.equal(dark.mainBG,'rgb(48, 52, 70)','Real Frappé BCOLOR');
    assert.equal(dark.text,'rgb(198, 208, 245)','Real Frappé FCOLOR');
    assert.equal(dark.link,'rgb(153, 209, 219)','Real Frappé LCOLOR');
    await tool('saves').click();
    await account.locator('[name=username]').fill('colors_'+Date.now());
    await account.locator('[name=password]').fill('x');
    await account.getByRole('button',{name:'Create account',exact:true}).click();
    await page.getByRole('button',{name:'Save',exact:true}).first().click();
    await page.locator('qsp-pause-screen').waitFor({state:'detached'});
    await page.locator('qsp-main-content').getByRole('link',{name:'Latte',exact:true}).click();
    const light=await checkPalette();
    assert.notEqual(light.engineBG,dark.engineBG,'Game switches to light theme');
    assert.notEqual(light.engineText,dark.engineText);
    await tool('saves').click();
    await page.getByRole('button',{name:'Load',exact:true}).first().click();
    await page.locator('qsp-pause-screen').waitFor({state:'detached'});
    await page.waitForFunction(bg=>getComputedStyle(document.querySelector('qsp-game-root')).getPropertyValue('--background-color').trim()===bg,dark.engineBG);
    const restored=await checkPalette();
    assert.deepEqual(restored,dark,'Save/load restores all engine and rendered colors, including chrome');
    const panels=await page.evaluate(()=>{
      const stats=document.querySelector('qsp-stats').getBoundingClientRect();
      const actions=document.querySelector('qsp-actions').getBoundingClientRect();
      const content=document.querySelector('qsp-stats-content').getBoundingClientRect();
      const scroll=document.querySelector('qsp-stats [data-radix-scroll-area-viewport]');
      return {stats:stats.toJSON(),actions:actions.toJSON(),content:content.toJSON(),overflow:getComputedStyle(scroll).overflowX};
    });
    assert(panels.stats.bottom<=panels.actions.top,'Desktop Stats must not cover game actions');
    assert(panels.content.width<panels.stats.width,'Content fits panel padding');
    assert.equal(panels.overflow,'auto','Any wider original game tables remain scrollable');
    if(process.env.GL_SCREENSHOTS)await page.screenshot({path:process.env.GL_SCREENSHOTS+'/game-colors-desktop.png'});
    for(const [width,height] of [[320,568],[390,844],[430,932]]){
      await page.setViewportSize({width,height});await checkPalette();
      assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth),width);
    }
    await page.setViewportSize({width:390,height:844});
    if(process.env.GL_SCREENSHOTS)await page.screenshot({path:process.env.GL_SCREENSHOTS+'/game-colors-mobile.png'});
    assert.deepEqual(errors,[]);
    console.log(JSON.stringify({status:'PASS',dark,light,restored,panels,checks:'real game Frappé/Latte selection, local save/load colors, game links, chrome sync, non-overlapping desktop Stats/actions, phone widths'},null,2));
  }catch(e){console.error('UI:',(await page.locator('body').innerText()).slice(-4000),'errors:',errors);throw e;}
  finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
