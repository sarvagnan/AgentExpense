import assert from 'node:assert/strict';

// The capture API must leave a gesture alone even if native screenshot replies
// are held across pointerdown. This isolates the harness from application timing.
export async function runCaptureInterference({page,expect,snap,record,quiesce}){
 await quiesce();await page.goto('about:blank');
 await page.setContent(`<body style="margin:0;height:2400px;font-family:system-ui"><button id="date" style="position:fixed;top:30px;left:20px" onclick="document.querySelector('dialog').showModal()">Change date</button><textarea id="words" style="position:fixed;top:90px;left:20px" aria-label="Words">Fictional newer words</textarea><div class="add-savebar" style="position:fixed;bottom:64px;left:20px"><button id="upload">Upload</button></div><div class="phone-live-nav" style="position:fixed;bottom:0;height:64px">Views</div><dialog aria-label="Change date"><label>Date<input type="date"></label></dialog></body>`);
 const upload=page.locator('#upload'),date=page.locator('#date'),before=await upload.boundingBox(),dateBox=await date.boundingBox();
 let releaseViewport,releaseFull,signalViewport,signalFull;
 const viewportReady=new Promise(resolve=>signalViewport=resolve),viewportGate=new Promise(resolve=>releaseViewport=resolve),fullReady=new Promise(resolve=>signalFull=resolve),fullGate=new Promise(resolve=>releaseFull=resolve);
 const screenshot=page.screenshot.bind(page);let first=true;
 page.screenshot=async options=>{if(options.fullPage){signalFull();await fullGate;return screenshot(options);}const result=await screenshot(options);if(first){first=false;signalViewport();await viewportGate;}return result;};
 const capturing=snap('capture-interference-background',{background:true});capturing.catch(()=>{});
 try{
  await Promise.race([viewportReady,capturing]);await page.mouse.move(dateBox.x+dateBox.width/2,dateBox.y+dateBox.height/2);await page.mouse.down();releaseViewport();
  await Promise.race([fullReady,capturing]);const after=await upload.boundingBox();
  for(const key of ['x','y'])assert.ok(Math.abs(before[key]-after[key])<=2,'A background capture must not move Upload during a pointer gesture: '+key+': '+before[key]+' → '+after[key]);
  await page.mouse.up();const dialog=page.getByRole('dialog',{name:'Change date',exact:true});await dialog.getByLabel('Date',{exact:true}).fill('2026-11-03');await expect(dialog.getByLabel('Date',{exact:true})).toHaveValue('2026-11-03');await expect(page.getByLabel('Words',{exact:true})).toHaveValue('Fictional newer words');
  record('held native screenshot: background capture preserves a pointer gesture, Upload geometry, date editing and newer words without a late scroll restore');
 }finally{releaseViewport();releaseFull();await page.mouse.up();await capturing;page.screenshot=screenshot;await page.goto('about:blank');}
}
