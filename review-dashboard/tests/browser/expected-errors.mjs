// An expected refusal belongs to one exact intercepted URL and status. Others remain errors.
const injected=new WeakMap();
export const expectInjected422=(page,url)=>expectInjectedHttp(page,url,422);
export function expectInjectedHttp(page,url,status){let urls=injected.get(page);if(!urls)injected.set(page,urls=new Map());const key=url+' '+status;urls.set(key,(urls.get(key)||0)+1);}
export function consumeInjected422(page,message){
 const status=/^Failed to load resource: the server responded with a status of 413 \((?:Request Entity Too Large|Payload Too Large|Content Too Large)\)$/.test(message.text())?413:message.text()==='Failed to load resource: the server responded with a status of 404 (Not Found)'?404:message.text()==='Failed to load resource: the server responded with a status of 409 (Conflict)'?409:message.text()==='Failed to load resource: the server responded with a status of 422 (Unprocessable Entity)'?422:message.text()==='Failed to load resource: the server responded with a status of 500 (Internal Server Error)'?500:null;if(!status)return false;
 const urls=injected.get(page),url=message.location().url+' '+status,count=urls?.get(url)||0;if(!count)return false;
 if(count===1)urls.delete(url);else urls.set(url,count-1);return true;
}
