/** Deliberately unconventional paths, shared only by offline regression suites. */
export const bank = 'Assets:Checking:Alder';
export const card = 'Liabilities:Plastic:Main';
export const person = 'Casey:Balances:Coffee';
export const wallet = 'Stored:Lunch';
export const metadata = {
 [bank]: {kind:'bank', currency:'INR', label:'Day account', aliases:['day account','checking']},
 [card]: {kind:'card', currency:'INR', label:'Travel card', aliases:['travel'], statement:{reader:'example-card-pdf',identifier:'5678'}},
 [wallet]: {kind:'wallet', currency:'INR', label:'Lunch balance', aliases:['lunch balance']},
 [person]: {kind:'person', currency:'INR', label:'Casey', aliases:['casey']},
 'Holdings:Index': {kind:'investment', currency:'FUND', label:'Index'},
 'Notes:Loan': {kind:'loan', currency:'INR', label:'Loan'},
 'Pocket': {kind:'cash', currency:'INR', label:'Cash', aliases:['cash']},
};
