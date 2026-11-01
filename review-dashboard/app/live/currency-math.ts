// Decimal strings avoid binary floating point at a half-minor-unit boundary.
export function positiveDecimal(value:string){return /^(0|[1-9][0-9]*)(\.[0-9]+)?$/.test(value)&&Number(value)>0&&Number(value)<=1e12;}
function fraction(value:string){const [whole,part='']=value.split('.');return [BigInt(whole+part),BigInt(10)**BigInt(part.length)] as const;}
function rounded(n:bigint,d:bigint,digits:number){const scale=BigInt(10)**BigInt(digits),value=(n*scale*BigInt(2)+d)/(d*BigInt(2));return digits?`${value/scale}.${String(value%scale).padStart(digits,'0')}`:String(value);}
export function currenciesLinked(original:string,value:string,from:'amount'|'rate',digits:number){
 if(!positiveDecimal(original)||!positiveDecimal(value)||value.length>100)return {amount:'',rate:''};
 const [a,b]=fraction(original),[c,d]=fraction(value);
 return from==='rate'?{amount:rounded(a*c,b*d,digits),rate:value}:{amount:rounded(c,d,digits),rate:rounded(c*b,d*a,4)};
}
