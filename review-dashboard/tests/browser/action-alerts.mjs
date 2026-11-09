/** The owning form may disappear on success. Its old failure may also have been
 * rendered outside a portal, so check that exact reason in visible error nodes. */
export async function assertOwnAlertsClear(page,expect,owner,reason='This change could not be saved. Your words are kept.'){
 const visibleOwn=async()=>owner?owner.evaluate(root=>root.isConnected?[...root.querySelectorAll('[role="alert"]')].filter(node=>node.getClientRects().length&&getComputedStyle(node).visibility!=='hidden'&&node.textContent.trim()).map(node=>node.textContent.trim()):[]):[];
 await expect.poll(visibleOwn,{message:'A committed action must not retain its own visible alert'}).toEqual([]);
 await expect.poll(()=>page.locator('[role="alert"],.live-error,.setup-error').evaluateAll((nodes,reason)=>nodes.filter(node=>node.getClientRects().length&&getComputedStyle(node).visibility!=='hidden'&&node.textContent.includes(reason)).map(node=>node.textContent.trim()),reason),{message:'Saved must not coexist with this action’s earlier refusal'}).toEqual([]);
}
