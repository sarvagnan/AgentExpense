import { writeFile, mkdir } from 'node:fs/promises';
import { loadExpenseStore } from './expense-store.mjs';
import { buildPatterns } from './add-patterns.mjs';
const { transactions, accounts } = await loadExpenseStore();
await mkdir(new URL('../app/data/', import.meta.url), {recursive:true});
await writeFile(new URL('../app/data/add-patterns.json', import.meta.url), JSON.stringify(buildPatterns(transactions, accounts), null, 2) + '\n');
console.log('Built Add pattern statistics from the expense store.');
