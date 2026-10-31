import { currentAccounts } from '../scripts/account-metadata.mjs';
export type AccountMetadata = {kind?: 'bank'|'card'|'cash'|'wallet'|'loan'|'investment'|'person'; currency?: string; label?: string; category?: string; aliases?: string[]; statement?: {reader: string; identifier?: string}};
export type Accounts = Record<string, AccountMetadata>;
export const accountMetadata = (): Accounts => currentAccounts();
