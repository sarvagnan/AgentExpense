import assert from 'node:assert/strict';

// Change the real observed container, rather than racing a resize or a screenshot.
export async function runChartSizing({page,expect,go,record}) {
  await go('forecast/');
  const frame=page.locator('.forecast-chart-inner').first();
  await expect(frame).toBeVisible();
  const svg=frame.locator(':scope > svg'),control=frame.locator('.chart-focus');
  await expect(control).toHaveCount(1);
  const originalStyle=await frame.getAttribute('style');
  const totals=await page.locator('.forecast-table').allTextContents();
  const setSize=async({width,hidden=false})=>{
    await frame.evaluate((node,{width,hidden})=>{node.style.width=`${width}px`;node.style.display=hidden?'none':'';},{width,hidden});
    await expect.poll(()=>frame.evaluate(node=>node.getBoundingClientRect().width)).toBe(hidden?0:width);
    // A paint boundary delivers the real ResizeObserver even when the SVG is absent.
    await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
  };
  try {
    for(const size of [{width:0,hidden:true},{width:0},{width:1},{width:32},{width:70}]) {
      await setSize(size);
      await expect(svg,'No chart or focus target without space inside its margins').toHaveCount(0);
      await expect(frame.locator('.chart-tooltip')).toHaveCount(0);
    }
    for(const width of [71,320,520]) {
      await setSize({width});
      await expect(svg).toHaveCount(1);
      await expect.poll(()=>svg.evaluate(node=>node.viewBox.baseVal.width)).toBe(width);
      const invalid=await svg.evaluate(node=>[node,...node.querySelectorAll('*')].flatMap(element=>['width','height','r','rx','ry'].flatMap(name=>{
        const value=element.getAttribute(name);return value!==null&&(!Number.isFinite(Number(value))||Number(value)<0)?[`${element.tagName} ${name}=${value}`]:[];
      })));
      assert.deepEqual(invalid,[],'Every SVG dimension is finite and nonnegative');
      await control.focus();await page.keyboard.press('Home');
      await expect(frame.locator('.chart-tooltip-title')).toHaveText('Day 1, cumulative');
      await page.keyboard.press('End');
      await expect(frame.locator('.chart-tooltip-title')).toHaveText('Day 31, cumulative');
      await page.keyboard.press('Escape');await expect(frame.locator('.chart-tooltip')).toHaveCount(0);
    }
    assert.deepEqual(await page.locator('.forecast-table').allTextContents(),totals,'Changing chart size preserves the financial table');
  } finally {
    await frame.evaluate((node,style)=>{if(style===null)node.removeAttribute('style');else node.setAttribute('style',style);},originalStyle);
  }
  await expect(control).toBeVisible();
  record('Forecast sizing: hidden, zero and tiny measured containers omit the SVG; positive plots recover with finite dimensions, keyboard values and unchanged totals');
}
